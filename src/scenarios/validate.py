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

# Protocol @bc tokens that are legal owners but are NOT Bounded Contexts, so
# they live in code rather than in the bc-manifest.yaml bcs registry (ADR-056
# D10): the lead product token and the unassigned sentinel.
PRODUCT_TOKEN = "shopsystem-product"
UNASSIGNED_TOKEN = "unassigned"
_EXTRA_LEGAL_BCS = frozenset({PRODUCT_TOKEN, UNASSIGNED_TOKEN})

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
    ) -> None:
        self.manifest_path = (
            manifest_path if manifest_path is not None else DEFAULT_MANIFEST_PATH
        )
        self.origin_roots = (
            list(origin_roots)
            if origin_roots is not None
            else list(DEFAULT_ORIGIN_ROOTS)
        )
        self._legal_bcs: Optional[frozenset] = None
        self._legal_services: Optional[frozenset] = None

    # -- manifest-backed legal sets -----------------------------------------

    def _load_manifest(self) -> None:
        """Load the bc-manifest.yaml bcs/services lists (once, lazily).

        Legal @bc = the manifest's ``bcs`` list PLUS the protocol tokens
        (product / unassigned). Legal @service = the manifest's ``services``
        list. A missing manifest yields empty registries (the tokens still
        stand for @bc) rather than crashing the run.
        """
        if self._legal_bcs is not None:
            return
        data: dict = {}
        path = Path(self.manifest_path)
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        bcs = data.get("bcs") or []
        services = data.get("services") or []
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

    def _origin_resolves(self, ref: str) -> bool:
        """True iff ``ref`` names a known decision record or a lead bead id.

        A ref resolves when a file named ``<ref>.md`` (or ``<ref>``) exists
        under any configured origin root (adr/ pdr/ briefs/), OR when it is
        shaped like a lead bead id. Otherwise the @origin is unknown.
        """
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

        # Exactly one Feature and the file parses. Now the tag-dimension rules
        # (slice 1b) run on the parsed GherkinDocument, ACCRETING every
        # violation they find (they do not early-return) so a file with several
        # independent defects reports them all in one run.
        self._check_tags(document, result)
        return result

    # -- tag-dimension rules (slice 1b) -------------------------------------

    @staticmethod
    def _tag_names(node: dict) -> List[str]:
        return [t["name"] for t in node.get("tags", [])]

    @staticmethod
    def _values_for(tag_names: List[str], dimension: str) -> List[str]:
        """Extract the values of ``@<dimension>:<value>`` tags, in order."""
        prefix = f"@{dimension}:"
        return [t[len(prefix):] for t in tag_names if t.startswith(prefix)]

    def _check_tags(self, document: dict, result: ValidationResult) -> None:
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

        # -- @scenario_hash: per-scenario, present -------------------------
        for child in feature.get("children", []):
            scenario = child.get("scenario")
            if scenario is None:
                continue
            self._check_scenario_hash(scenario, result)

    @staticmethod
    def _reconstruct_block(scenario: dict) -> str:
        """The block-only body of a parsed scenario: ``Scenario: <name>`` plus
        one ``<keyword> <text>`` line per step. This is the parser-path input
        to ``compute_scenario_hash`` — the same canonical form the block-only
        hash is defined over."""
        lines = [f"Scenario: {scenario['name']}"]
        for step in scenario.get("steps", []):
            lines.append(f"{step['keyword'].strip()} {step['text']}")
        return "\n".join(lines)

    def _check_scenario_hash(self, scenario: dict, result: ValidationResult) -> None:
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

    def validate_file(self, path: str) -> ValidationResult:
        text = Path(path).read_text(encoding="utf-8")
        return self.validate_text(text, file=path)
