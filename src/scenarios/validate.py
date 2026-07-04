"""`scenarios validate` — schema validation of Gherkin scenario files (ADR-056).

This module is the FOUNDATION slice (1a) of the validate subsystem. It
establishes the architecture the later slices depend on:

- A structured ``Violation`` value with a stable ``rule`` code and the fields
  the later ``--json`` slice needs (file, line, scenario_title, scenario_hash,
  bc, origin).
- A ``ValidationResult`` collecting a list of violations; the run's exit code
  is 0 iff the list is empty.
- A ``Validator`` whose resolution roots (bc-manifest path, origin-resolution
  roots) are INJECTABLE via constructor args / CLI flags, so slice 1b's
  @bc/@origin legal-set lookups plug in without refactoring this seam.

This slice implements only these schema dimensions:

- ``E_GHERKIN_PARSE`` — the file does not parse under off-the-shelf
  @cucumber/gherkin (gherkin-official).
- ``E_NO_FEATURE`` — the file declares zero ``Feature:`` keywords.
- ``E_MULTI_FEATURE`` — the file declares more than one ``Feature:`` keyword.
- happy path — a fully conformant file yields zero violations (exit 0).

The @bc/@origin/@scenario_hash dimension rules, @service, --json, --aggregate,
hash-reconcile, and create/consolidate helpers are LATER slices. The violation
model and Validator structure are designed so they slot in cleanly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml
from gherkin.errors import CompositeParserException, ParserException
from gherkin.parser import Parser
from gherkin.token_scanner import TokenScanner

from scenarios.hash import compute_scenario_hash
from scenarios.outstanding import _iter_scenario_blocks, compute_block_only_hash

# ----------------------------------------------------------------------------
# Stable rule codes.
#
# These strings are load-bearing: they appear in diagnostics, in the later
# --json output, and in this BC's own scenario assertions. Treat them as a
# stable public vocabulary — later slices ADD codes here, they do not rename
# these.
# ----------------------------------------------------------------------------
E_GHERKIN_PARSE = "E_GHERKIN_PARSE"
E_NO_FEATURE = "E_NO_FEATURE"
E_MULTI_FEATURE = "E_MULTI_FEATURE"

# Slice 1b — tag-dimension rules.
E_MISSING_BC = "E_MISSING_BC"
E_MULTI_BC = "E_MULTI_BC"
E_UNKNOWN_BC = "E_UNKNOWN_BC"
E_MISSING_ORIGIN = "E_MISSING_ORIGIN"
E_MULTI_ORIGIN = "E_MULTI_ORIGIN"
E_UNKNOWN_ORIGIN = "E_UNKNOWN_ORIGIN"
E_MISSING_HASH = "E_MISSING_HASH"
E_HASH_MISMATCH = "E_HASH_MISMATCH"
E_UNKNOWN_SERVICE = "E_UNKNOWN_SERVICE"

# Slice 2 — aggregate-level transitional markers (ADR-056 D8). These are NOT
# per-file E_ error codes: @bc:unassigned and @origin:unresolved are LEGAL
# per-file placeholder values (they do not trip E_UNKNOWN_BC / E_UNKNOWN_ORIGIN
# at the per-file level). They are TRANSITIONAL forcing markers surfaced ONLY by
# the --aggregate system-consistency gate, which stays RED until every such
# placeholder has been resolved to a real owner / provenance. Hence the W_
# (warning/marker) prefix, distinct from the per-file E_ codes.
W_BC_UNASSIGNED = "W_BC_UNASSIGNED"
W_ORIGIN_UNRESOLVED = "W_ORIGIN_UNRESOLVED"

# Protocol @bc tokens that are legal owners but are NOT Bounded Contexts, so
# they live in code rather than in the bc-manifest.yaml bcs registry (ADR-056
# D10): the lead product token and the unassigned sentinel.
PRODUCT_TOKEN = "shopsystem-product"
UNASSIGNED_TOKEN = "unassigned"
_EXTRA_LEGAL_BCS = frozenset({PRODUCT_TOKEN, UNASSIGNED_TOKEN})

# The @origin placeholder sentinel that is LEGAL per-file (it resolves without a
# file lookup, like a lead bead id) but is a TRANSITIONAL marker the aggregate
# gate surfaces as W_ORIGIN_UNRESOLVED. Mirrors UNASSIGNED_TOKEN on the @bc
# side: a valid placeholder per-file (ADR-056 D1/D10) that the system-consistency
# gate nonetheless forces to zero.
ORIGIN_UNRESOLVED_TOKEN = "unresolved"

# A ref naming a lead bead id (e.g. ``lead-vzxd.1``) is accepted as a legal
# @origin without a file lookup — the provenance points at a tracked bead
# rather than a decision-record file. Detection is deliberately narrow and
# pluggable: a ref is treated as a lead bead only when it carries one of these
# known lead/shop prefixes followed by a bead suffix. A bare decision-record
# ref like ``adr-056`` does NOT match (it resolves, or fails, on the file
# path), so this pattern never swallows an unknown-origin case. The
# file-resolution + genuine-unknown case is what the scenarios pin.
_LEAD_BEAD_PREFIXES = ("lead-", "shopsystem-")
_LEAD_BEAD_SUFFIX_RE = re.compile(r"^[a-z0-9]+(\.[0-9]+)*$", re.IGNORECASE)


# The feature-level tag that marks a whole file as BC-INTERNAL and thus EXEMPT
# from the ADR-056 three-dimension schema gate (lead-vzxd.3 RULING). A file
# whose Feature carries this tag is a guard for the BC's own infra (the
# editable-install/stale-wheel guard, the release-workflow CI guard, etc.), not
# a lead-pinned product scenario: its @bc/@origin/@scenario_hash checks are
# WAIVED and the --aggregate gate SKIPS it entirely. The file must still be
# valid Gherkin (E_GHERKIN_PARSE / cardinality still apply). Ships in v0.3.1.
BC_INTERNAL_TAG = "@bc_internal"

# A Feature keyword at the start of a (stripped) line. Mirrors the line-start
# discipline feature.py uses for Scenario:/tags — a "Feature:" appearing
# mid-step as substring is not a Feature declaration.
_FEATURE_LINE_RE = re.compile(r"^\s*Feature:")


@dataclass
class Violation:
    """One schema violation found in a scenario file.

    ``rule`` is the stable rule code (e.g. ``E_GHERKIN_PARSE``). The remaining
    fields are the diagnostic context the later ``--json`` slice serializes;
    they are Optional because not every rule can populate every field (a parse
    failure, for instance, has no resolvable scenario_title). Foundation-slice
    rules populate ``file`` and ``rule``; the richer fields are wired here so
    later slices set them without changing this shape.
    """

    rule: str
    file: Optional[str] = None
    line: Optional[int] = None
    scenario_title: Optional[str] = None
    scenario_hash: Optional[str] = None
    bc: Optional[str] = None
    origin: Optional[str] = None
    detail: Optional[str] = None

    def render(self) -> str:
        """A one-line human diagnostic naming the file and the rule code."""
        where = self.file or "<input>"
        if self.line is not None:
            where = f"{where}:{self.line}"
        msg = f"{where}: {self.rule}"
        if self.detail:
            msg = f"{msg}: {self.detail}"
        return msg


@dataclass
class ValidationResult:
    """The outcome of validating one file: the violations collected.

    ``ok`` is True iff no violations were collected; ``exit_code`` is the
    process exit status a CLI run should surface (0 iff ok).
    """

    file: Optional[str] = None
    violations: List[Violation] = field(default_factory=list)
    # Feature-level @bc / @origin values captured during tag resolution, so the
    # --json diagnostic can name the owning context and provenance even when the
    # violation itself did not populate those fields (e.g. an E_MISSING_HASH
    # violation carries a scenario_title but no bc/origin).
    feature_bc: Optional[str] = None
    feature_origin: Optional[str] = None

    def add(self, violation: Violation) -> None:
        if violation.file is None:
            violation.file = self.file
        self.violations.append(violation)

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def render(self) -> str:
        return "\n".join(v.render() for v in self.violations)

    def to_json_diagnostic(self) -> dict:
        """A machine-readable diagnostic object for the ``--json`` output.

        The object names the offending ``file`` together with the diagnostic
        context a downstream reader needs to locate the defect —
        ``line``, ``scenario_title``, ``scenario_hash``, ``bc``, ``origin`` —
        and a ``violations`` array of the stable rule-code strings that fired.

        Each scalar field is sourced from the first violation that populated it
        (a per-scenario rule supplies ``scenario_title``/``line``; a hash rule
        supplies ``scenario_hash``; an @bc/@origin rule supplies ``bc``/
        ``origin``), falling back to the feature-level @bc/@origin captured
        during tag resolution. This keeps the object's named fields honestly
        populated regardless of which single rule fired.
        """

        def _first(attr: str) -> Optional[object]:
            for v in self.violations:
                value = getattr(v, attr)
                if value is not None:
                    return value
            return None

        return {
            "file": self.file,
            "line": _first("line"),
            "scenario_title": _first("scenario_title"),
            "scenario_hash": _first("scenario_hash"),
            "bc": _first("bc") if _first("bc") is not None else self.feature_bc,
            "origin": (
                _first("origin")
                if _first("origin") is not None
                else self.feature_origin
            ),
            "violations": [v.rule for v in self.violations],
        }


# Conventional default locations the resolution roots fall back to when a CLI
# run does not override them. These files/dirs do NOT exist in this repo yet
# (slice 1b introduces the @bc/@origin resolution that reads them); the seam is
# built now so slice 1b plugs in without touching the constructor signature.
DEFAULT_MANIFEST_PATH = "bc-manifest.yaml"
DEFAULT_ORIGIN_ROOTS = ("adr", "pdr", "briefs")


class Validator:
    """Validates a scenario file against the ADR-056 schema.

    Resolution roots are injectable so tests can supply fixtures and slice 1b's
    @bc/@origin legal-set lookups can resolve against a manifest / origin roots
    without a refactor:

    - ``manifest_path`` — path to the ``bc-manifest.yaml`` (``bcs:``/
      ``services:`` sections) the @bc/@service legal set resolves against.
    - ``origin_roots`` — directories (adr/ pdr/ briefs/) the @origin legal set
      resolves against, alongside lead bead ids.

    Slice 1b reads these roots for its @bc/@origin/@service legal-set lookups;
    the manifest is loaded lazily on first use so a run that never reaches the
    tag checks (an un-parseable file) does not require the manifest to exist.
    """

    def __init__(
        self,
        *,
        manifest_path: Optional[str] = None,
        origin_roots: Optional[List[str]] = None,
        origin_index: Optional[str] = None,
    ) -> None:
        self.manifest_path = (
            manifest_path if manifest_path is not None else DEFAULT_MANIFEST_PATH
        )
        self.origin_roots = (
            list(origin_roots)
            if origin_roots is not None
            else list(DEFAULT_ORIGIN_ROOTS)
        )
        # The optional generated origin-index (one identifier per line) the
        # @origin legal set resolves against by MEMBERSHIP. Real ADR files are
        # ``NNN-slug.md`` and a BC container carries no ``adr/`` dir (ADR-018),
        # so the dir-scan path misses a real ``@origin:adr-056``; the index is
        # the real-data resolution path, with the dir-scan retained as a
        # fixture fallback. ``None`` means no index configured (dir-scan +
        # lead-bead rules only).
        self.origin_index = origin_index
        self._legal_bcs: Optional[frozenset] = None
        self._legal_services: Optional[frozenset] = None
        self._origin_ids: Optional[frozenset] = None

    # -- manifest-backed legal sets -----------------------------------------

    @staticmethod
    def _manifest_names(entries: object) -> List[str]:
        """Name-extract a manifest ``bcs:``/``services:`` section.

        The real ``bc-manifest.yaml`` carries DICT entries
        (``- name: <token>`` with optional ``remote``/``role``/``status``/
        ``deferred_to`` keys), while the legacy shape carries bare strings.
        Accept BOTH: a dict entry contributes its ``name`` value (extra keys
        are tolerated and ignored; a provisional entry's name IS a legal
        value), a bare string contributes itself, and any other entry shape
        (including a dict missing its ``name`` key) is skipped rather than
        crashing the run. A non-list section yields no names.
        """
        names: List[str] = []
        if not isinstance(entries, list):
            return names
        for entry in entries:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str):
                    names.append(name)
            # Any other entry shape (nameless dict, int, None) is skipped.
        return names

    def _load_manifest(self) -> None:
        """Load the bc-manifest.yaml bcs/services lists (once, lazily).

        Legal @bc = the manifest's ``bcs`` names PLUS the protocol tokens
        (product / unassigned). Legal @service = the manifest's ``services``
        names. Entries may be dict-shaped (real corpus) or bare strings
        (legacy); ``_manifest_names`` name-extracts either. A missing,
        empty, or non-dict/garbage manifest yields empty registries (the
        tokens still stand for @bc) rather than crashing the run.
        """
        if self._legal_bcs is not None:
            return
        data: dict = {}
        path = Path(self.manifest_path)
        if path.exists():
            # A malformed manifest must degrade to empty registries with a
            # clean fallback rather than crash the run: catch both an
            # unparseable YAML document (yaml.YAMLError) and an unreadable file
            # (OSError). A parsed-but-non-dict top level (a bare list, a
            # scalar) is handled by the isinstance check below.
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError):
                loaded = None
            if isinstance(loaded, dict):
                data = loaded
        bcs = self._manifest_names(data.get("bcs"))
        services = self._manifest_names(data.get("services"))
        self._legal_bcs = frozenset(bcs) | _EXTRA_LEGAL_BCS
        self._legal_services = frozenset(services)

    @property
    def legal_bcs(self) -> frozenset:
        self._load_manifest()
        assert self._legal_bcs is not None
        return self._legal_bcs

    @property
    def legal_services(self) -> frozenset:
        self._load_manifest()
        assert self._legal_services is not None
        return self._legal_services

    # -- origin resolution --------------------------------------------------

    def _load_origin_index(self) -> frozenset:
        """Load the generated origin-index (once, lazily).

        The index is a plain identifier list — one id per line (e.g.
        ``adr-056``, ``pdr-003``, ``brief-foo``). Blank and whitespace-only
        lines are ignored. A missing, empty, or unreadable index file yields
        an empty membership set (the other resolution paths still apply)
        rather than crashing the run. When no ``--origin-index`` is
        configured the set is empty.
        """
        if self._origin_ids is not None:
            return self._origin_ids
        ids: set = set()
        if self.origin_index is not None:
            path = Path(self.origin_index)
            if path.exists():
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    text = ""
                for line in text.splitlines():
                    ident = line.strip()
                    if ident:
                        ids.add(ident)
        self._origin_ids = frozenset(ids)
        return self._origin_ids

    def _origin_resolves(self, ref: str) -> bool:
        """True iff ``ref`` names a known decision record or a lead bead id.

        A ref resolves when it is a MEMBER of the configured ``--origin-index``
        identifier list, OR when a file named ``<ref>.md`` (or ``<ref>``)
        exists under any configured origin root (adr/ pdr/ briefs/ — a fixture
        fallback), OR when it is shaped like a lead bead id, OR when it is the
        ``unresolved`` placeholder sentinel. Otherwise the @origin is unknown.
        """
        # The ``unresolved`` placeholder is a LEGAL per-file @origin value: it
        # stands for provenance that has not yet been assigned (ADR-056 D1/D10),
        # so it must NOT trip E_UNKNOWN_ORIGIN at the per-file level. The
        # --aggregate gate is what surfaces it (as W_ORIGIN_UNRESOLVED) and forces
        # it to zero; the per-file check treats it as resolving.
        if ref == ORIGIN_UNRESOLVED_TOKEN:
            return True
        # Real-data resolution path (GAP-2): membership in the generated
        # origin-index identifier list. Real ADR files are ``NNN-slug.md`` and
        # a BC container carries no ``adr/`` dir (ADR-018), so the dir-scan
        # below misses a real ``@origin:adr-056``; the index is how the real
        # corpus resolves provenance.
        if ref in self._load_origin_index():
            return True
        # An origin root may be named either as a decision-record directory
        # itself (the ``adr``/``pdr``/``briefs`` default model) or as a parent
        # dir that CONTAINS those subdirs. Search both shapes: the root
        # directly, and each of its adr/pdr/briefs subdirs.
        search_dirs: List[Path] = []
        for root in self.origin_roots:
            base = Path(root)
            search_dirs.append(base)
            for sub in ("adr", "pdr", "briefs"):
                search_dirs.append(base / sub)
        for base in search_dirs:
            if (base / f"{ref}.md").exists() or (base / ref).exists():
                return True
        for prefix in _LEAD_BEAD_PREFIXES:
            if ref.startswith(prefix):
                suffix = ref[len(prefix):]
                if suffix and _LEAD_BEAD_SUFFIX_RE.match(suffix):
                    return True
        return False

    # -- parsing -------------------------------------------------------------

    @staticmethod
    def _count_feature_lines(text: str) -> int:
        return sum(1 for line in text.splitlines() if _FEATURE_LINE_RE.match(line))

    @staticmethod
    def _parse(text: str):
        """Parse ``text`` with off-the-shelf gherkin-official.

        Returns the parsed GherkinDocument dict on success, or raises the
        parser's own exception on failure — the caller maps that to a
        violation so the CLI never crashes on bad input.
        """
        return Parser().parse(TokenScanner(text))

    # -- validation ----------------------------------------------------------

    def validate_text(
        self, text: str, *, file: Optional[str] = None
    ) -> ValidationResult:
        result = ValidationResult(file=file)

        # Feature cardinality is decided by a line scan BEFORE trusting the
        # parser, because off-the-shelf strict Gherkin raises on both zero
        # Feature (a Scenario with no enclosing Feature) and a second Feature.
        # A raw parse error would collapse both distinct schema violations
        # (E_NO_FEATURE, E_MULTI_FEATURE) into E_GHERKIN_PARSE and lose the
        # distinction the schema requires. The line scan recovers the intended
        # cardinality diagnostic; a single-Feature file then goes to the parser
        # for the genuine E_GHERKIN_PARSE path.
        # Feature cardinality is decided by a line scan BEFORE trusting the
        # parser. Off-the-shelf strict Gherkin raises on a file with scenarios
        # but no enclosing Feature, which would collapse the intended
        # E_NO_FEATURE diagnostic into a generic E_GHERKIN_PARSE. The line scan
        # recovers the schema-level cardinality diagnostic; a single-Feature
        # file still goes to the parser below for the genuine parse path.
        feature_count = self._count_feature_lines(text)
        if feature_count == 0:
            result.add(
                Violation(
                    rule=E_NO_FEATURE,
                    detail="file declares no Feature keyword",
                )
            )
            return result
        if feature_count > 1:
            result.add(
                Violation(
                    rule=E_MULTI_FEATURE,
                    detail=(
                        f"file declares {feature_count} Feature keywords "
                        "(expected exactly one)"
                    ),
                )
            )
            return result

        # Off-the-shelf gherkin-official raises CompositeParserException (or a
        # bare ParserException) on un-parseable input. Catch it and map it to a
        # single E_GHERKIN_PARSE violation so the CLI reports a clean diagnostic
        # instead of crashing with a traceback (ADR-056). The parser's first
        # error line is carried as detail for the reader.
        try:
            document = self._parse(text)
        except (CompositeParserException, ParserException) as exc:
            first_line = str(exc).splitlines()[0] if str(exc) else None
            result.add(Violation(rule=E_GHERKIN_PARSE, detail=first_line))
            return result

        # A feature-level @bc_internal tag marks the whole file as BC-INTERNAL
        # and EXEMPT from the ADR-056 three-dimension schema gate (lead-vzxd.3
        # RULING). The file has already cleared the gherkin-validity bar above
        # (cardinality + parse); the @bc/@origin/@scenario_hash/@service checks
        # are WAIVED for it, so it yields zero violations regardless of whether
        # it carries those tags. Detection is at the parsed-feature level so a
        # "@bc_internal" appearing as a substring in a step body is not
        # mistaken for the exemption tag.
        if self._feature_is_bc_internal(document):
            return result

        # Exactly one Feature and the file parses. Now the tag-dimension rules
        # (slice 1b) run on the parsed GherkinDocument, ACCRETING every
        # violation they find (they do not early-return) so a file with several
        # independent defects reports them all in one run. The raw ``text`` is
        # threaded through so the per-scenario hash recompute can hash each
        # scenario's RAW block (Examples table retained, keyword preserved) via
        # the SAME block-extraction/canonicalization path ``scenarios hash``
        # uses — guaranteeing recompute == ``scenarios hash`` on the raw block
        # for both ``Scenario`` and ``Scenario Outline`` (lead-vzxd.7 defect A).
        self._check_tags(document, result, text=text)
        return result

    def _feature_is_bc_internal(self, document: dict) -> bool:
        """True iff the parsed document's Feature carries the @bc_internal tag.

        The exemption is decided on the PARSED feature-level tag list, so it is
        exact (a bare ``@bc_internal`` feature tag, not a substring match
        against raw text). A document with no feature is not exempt.
        """
        feature = document.get("feature")
        if feature is None:
            return False
        return BC_INTERNAL_TAG in self._tag_names(feature)

    # -- tag-dimension rules (slice 1b) -------------------------------------

    @staticmethod
    def _tag_names(node: dict) -> List[str]:
        return [t["name"] for t in node.get("tags", [])]

    @staticmethod
    def _values_for(tag_names: List[str], dimension: str) -> List[str]:
        """Extract the values of ``@<dimension>:<value>`` tags, in order."""
        prefix = f"@{dimension}:"
        return [t[len(prefix):] for t in tag_names if t.startswith(prefix)]

    def _check_tags(
        self, document: dict, result: ValidationResult, *, text: str = ""
    ) -> None:
        feature = document.get("feature")
        if feature is None:
            return
        feature_tags = self._tag_names(feature)
        feature_line = feature.get("location", {}).get("line")

        # -- @bc: exactly one, naming a known context -----------------------
        bc_values = self._values_for(feature_tags, "bc")
        if len(bc_values) == 0:
            result.add(
                Violation(
                    rule=E_MISSING_BC,
                    line=feature_line,
                    detail="Feature carries no @bc owner tag",
                )
            )
        elif len(bc_values) > 1:
            result.add(
                Violation(
                    rule=E_MULTI_BC,
                    line=feature_line,
                    detail=(
                        f"Feature carries {len(bc_values)} @bc tags "
                        "(expected exactly one)"
                    ),
                )
            )
        else:
            (bc_value,) = bc_values
            # Capture the feature-level owner so the --json diagnostic can name
            # the owning context even when the firing violation is not itself an
            # @bc rule (e.g. an E_MISSING_HASH per-scenario violation).
            result.feature_bc = bc_value
            if bc_value not in self.legal_bcs:
                result.add(
                    Violation(
                        rule=E_UNKNOWN_BC,
                        line=feature_line,
                        bc=bc_value,
                        detail=f"@bc value {bc_value!r} is not a known context",
                    )
                )

        # -- @origin: exactly one, resolving to a known record --------------
        origin_values = self._values_for(feature_tags, "origin")
        if len(origin_values) == 0:
            result.add(
                Violation(
                    rule=E_MISSING_ORIGIN,
                    line=feature_line,
                    detail="Feature carries no @origin provenance tag",
                )
            )
        elif len(origin_values) > 1:
            result.add(
                Violation(
                    rule=E_MULTI_ORIGIN,
                    line=feature_line,
                    detail=(
                        f"Feature carries {len(origin_values)} @origin tags "
                        "(expected exactly one)"
                    ),
                )
            )
        else:
            (origin_value,) = origin_values
            # Capture the feature-level provenance for the --json diagnostic,
            # for the same reason feature_bc is captured above.
            result.feature_origin = origin_value
            if not self._origin_resolves(origin_value):
                result.add(
                    Violation(
                        rule=E_UNKNOWN_ORIGIN,
                        line=feature_line,
                        origin=origin_value,
                        detail=(
                            f"@origin value {origin_value!r} resolves to no known "
                            "decision record or lead bead id"
                        ),
                    )
                )

        # -- @service: OPTIONAL; when present must name a known service -----
        # @service is optional (a Feature may carry none) and does NOT
        # substitute for the mandatory @bc owner — the @bc rules above stand
        # regardless of @service. A present @service value that is absent from
        # the manifest's services list is rejected.
        for service_value in self._values_for(feature_tags, "service"):
            if service_value not in self.legal_services:
                result.add(
                    Violation(
                        rule=E_UNKNOWN_SERVICE,
                        line=feature_line,
                        detail=(
                            f"@service value {service_value!r} is not a known service"
                        ),
                    )
                )

        # -- @scenario_hash: per-scenario, present -------------------------
        # Extract each scenario's RAW block from the file text via the SAME
        # block-extraction path ``scenarios hash`` uses (``_iter_scenario_blocks``
        # opens on ``Scenario:``/``Scenario Outline:`` and runs to the next
        # boundary, so a Scenario Outline's Examples table is part of its block).
        # The raw blocks come in file order and pair positionally with the
        # parser's scenario children (also file order; a Background is neither a
        # raw block nor a scenario child, so the two sequences stay aligned).
        raw_blocks = list(_iter_scenario_blocks(text)) if text else []
        scenario_children = [
            child["scenario"]
            for child in feature.get("children", [])
            if child.get("scenario") is not None
        ]
        for index, scenario in enumerate(scenario_children):
            raw_block = raw_blocks[index] if index < len(raw_blocks) else None
            self._check_scenario_hash(scenario, result, raw_block=raw_block)

    @staticmethod
    def _reconstruct_block(scenario: dict) -> str:
        """The block-only body of a parsed scenario reconstructed from the
        parser node: the scenario keyword line plus one ``<keyword> <text>``
        line per step. This is a FALLBACK used only when the raw file text is
        unavailable to the per-scenario hash check; the primary path hashes the
        scenario's RAW block (via ``compute_block_only_hash``), which is what
        ``scenarios hash`` does.

        This fallback preserves the parsed scenario's own keyword
        (``Scenario`` vs ``Scenario Outline``) rather than normalizing it, so
        that even without the raw text it does not silently disagree with the
        canonical hash on the keyword. It still cannot reproduce a Scenario
        Outline's Examples table (the parser node does not carry it verbatim),
        which is exactly why the raw-block path is the primary one."""
        keyword = (scenario.get("keyword") or "Scenario").strip()
        lines = [f"{keyword}: {scenario['name']}"]
        for step in scenario.get("steps", []):
            lines.append(f"{step['keyword'].strip()} {step['text']}")
        return "\n".join(lines)

    def _check_scenario_hash(
        self,
        scenario: dict,
        result: ValidationResult,
        *,
        raw_block: Optional[str] = None,
    ) -> None:
        scenario_tags = self._tag_names(scenario)
        title = scenario.get("name")
        line = scenario.get("location", {}).get("line")
        hash_values = self._values_for(scenario_tags, "scenario_hash")
        if len(hash_values) == 0:
            result.add(
                Violation(
                    rule=E_MISSING_HASH,
                    line=line,
                    scenario_title=title,
                    detail=(
                        f"scenario {title!r} carries no @scenario_hash tag"
                    ),
                )
            )
            return

        # Compare the embedded hash against the block-only hash recomputed over
        # the scenario's RAW block — the SAME value ``scenarios hash`` produces
        # on that block. Hashing the raw block via ``compute_block_only_hash``
        # RETAINS a Scenario Outline's Examples table and preserves the
        # ``Scenario Outline`` keyword, so the recompute EQUALS ``scenarios
        # hash`` on the raw block for both ``Scenario`` and ``Scenario Outline``
        # (lead-vzxd.7 defect A). When no raw block is available (a caller that
        # did not thread the file text), fall back to the parser-node
        # reconstruction. On mismatch the diagnostic names the scenario together
        # with BOTH the embedded and the recomputed hash.
        embedded = hash_values[0]
        if raw_block is not None:
            recomputed = compute_block_only_hash(raw_block)
        else:
            recomputed = compute_scenario_hash(self._reconstruct_block(scenario))
        if embedded != recomputed:
            result.add(
                Violation(
                    rule=E_HASH_MISMATCH,
                    line=line,
                    scenario_title=title,
                    scenario_hash=embedded,
                    detail=(
                        f"scenario {title!r} @scenario_hash embedded={embedded} "
                        f"but recomputed={recomputed}"
                    ),
                )
            )

    def validate_file(self, path: str) -> ValidationResult:
        text = Path(path).read_text(encoding="utf-8")
        return self.validate_text(text, file=path)


# ----------------------------------------------------------------------------
# Slice 2 — the --aggregate system-consistency gate (ADR-056 D8).
#
# Where ``Validator`` decides whether a SINGLE file is schema-valid, the
# aggregate gate decides whether a whole CORPUS is system-consistent. It stays
# RED while ANY of:
#   - any file carries a per-file schema violation (reusing ``Validator``), OR
#   - any Feature carries the @bc:unassigned transitional marker
#     (surfaced as W_BC_UNASSIGNED), OR
#   - any Feature carries the @origin:unresolved transitional marker
#     (surfaced as W_ORIGIN_UNRESOLVED).
# It is GREEN (exit 0) ONLY when every file is schema-valid AND zero
# transitional markers remain.
# ----------------------------------------------------------------------------


@dataclass
class AggregateFinding:
    """One aggregate-level finding: a marker/code plus the file that carries it.

    ``code`` is a stable string — either a per-file rule code (E_*) surfaced by
    the reused per-file Validator, or one of the transitional aggregate markers
    (W_BC_UNASSIGNED / W_ORIGIN_UNRESOLVED). ``file`` names the offending file so
    a reader can locate it; ``detail`` carries an optional human note.
    """

    code: str
    file: str
    detail: Optional[str] = None

    def render(self) -> str:
        msg = f"{self.file}: {self.code}"
        if self.detail:
            msg = f"{msg}: {self.detail}"
        return msg


@dataclass
class AggregateResult:
    """The outcome of the aggregate gate over a corpus of scenario files.

    ``findings`` collects every per-file violation code and transitional marker
    across the corpus. ``ok`` is True iff the corpus is fully consistent (no
    findings); ``exit_code`` is 0 iff ok.
    """

    findings: List[AggregateFinding] = field(default_factory=list)

    def add(self, finding: AggregateFinding) -> None:
        self.findings.append(finding)

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def render(self) -> str:
        return "\n".join(f.render() for f in self.findings)


# The glob the aggregate gate harvests corpus files by. A corpus is a directory
# tree of ``.feature`` files; the walk is recursive so a nested corpus layout is
# gated as one whole.
_FEATURE_GLOB = "*.feature"


def _corpus_file_is_bc_internal(path: Path) -> bool:
    """True iff the feature file at ``path`` carries the @bc_internal exemption.

    Parses the file with off-the-shelf gherkin and checks the parsed
    feature-level tag list — the same exact tag test the per-file Validator
    uses. A file that cannot be read or does not parse returns False: it is NOT
    treated as exempt, so it falls through to the per-file Validator in the
    aggregate loop and surfaces its E_GHERKIN_PARSE (or cardinality) finding
    rather than being silently skipped. This keeps the exemption a property of
    a VALID @bc_internal feature, never a way to hide a broken file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    # A file with anything other than exactly one Feature is left to the
    # per-file Validator (it surfaces E_NO_FEATURE / E_MULTI_FEATURE); only a
    # single-Feature file is a candidate for the exemption.
    if Validator._count_feature_lines(text) != 1:
        return False
    try:
        document = Parser().parse(TokenScanner(text))
    except (CompositeParserException, ParserException):
        return False
    feature = document.get("feature")
    if feature is None:
        return False
    return BC_INTERNAL_TAG in Validator._tag_names(feature)


def validate_corpus(
    corpus_dir: str,
    *,
    manifest_path: Optional[str] = None,
    origin_roots: Optional[List[str]] = None,
    origin_index: Optional[str] = None,
) -> AggregateResult:
    """Run the aggregate system-consistency gate over a corpus directory.

    Every ``.feature`` file under ``corpus_dir`` (recursively) is run through
    the per-file ``Validator`` (so a per-file schema violation keeps the gate
    RED and is reported with its stable E_ code and file), AND scanned for the
    two transitional markers (@bc:unassigned / @origin:unresolved), which are
    legal per-file placeholders but keep the aggregate gate RED as
    W_BC_UNASSIGNED / W_ORIGIN_UNRESOLVED. The corpus is gated GREEN (exit 0)
    only when it is entirely free of both per-file violations and transitional
    markers.
    """
    result = AggregateResult()
    # Deterministic order so the diagnostic is reproducible run-to-run.
    files = sorted(Path(corpus_dir).rglob(_FEATURE_GLOB))
    for path in files:
        file_str = str(path)
        validator = Validator(
            manifest_path=manifest_path,
            origin_roots=list(origin_roots) if origin_roots is not None else None,
            origin_index=origin_index,
        )

        # A @bc_internal file is BC-INTERNAL and EXEMPT (lead-vzxd.3 RULING):
        # the aggregate gate SKIPS it entirely — it contributes no per-file
        # violation and no transitional marker, whether or not it carries
        # @bc/@origin/@scenario_hash. Detect the exemption on the parsed
        # feature-level tag list; a file that does not parse is NOT skipped
        # here (it falls through to validate_file below and surfaces its
        # E_GHERKIN_PARSE finding, so a broken exempt-looking file is not
        # silently swallowed).
        if _corpus_file_is_bc_internal(path):
            continue

        file_result = validator.validate_file(file_str)

        # Per-file schema violations keep the gate RED and are surfaced with
        # their stable rule code and the offending file.
        for violation in file_result.violations:
            result.add(
                AggregateFinding(
                    code=violation.rule,
                    file=file_str,
                    detail=violation.detail,
                )
            )

        # Transitional markers: @bc:unassigned / @origin:unresolved are LEGAL
        # per-file placeholders (they produced no violation above) but keep the
        # aggregate gate RED. The per-file Validator captured the sole @bc /
        # @origin value on the result during tag resolution, so reuse those
        # rather than re-parsing.
        if file_result.feature_bc == UNASSIGNED_TOKEN:
            result.add(
                AggregateFinding(
                    code=W_BC_UNASSIGNED,
                    file=file_str,
                    detail=(
                        "Feature carries the @bc:unassigned transitional marker "
                        "(owner not yet assigned)"
                    ),
                )
            )
        if file_result.feature_origin == ORIGIN_UNRESOLVED_TOKEN:
            result.add(
                AggregateFinding(
                    code=W_ORIGIN_UNRESOLVED,
                    file=file_str,
                    detail=(
                        "Feature carries the @origin:unresolved transitional "
                        "marker (provenance not yet resolved)"
                    ),
                )
            )
    return result
