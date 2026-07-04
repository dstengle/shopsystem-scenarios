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

from gherkin.errors import CompositeParserException, ParserException
from gherkin.parser import Parser
from gherkin.token_scanner import TokenScanner

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

    This foundation slice does NOT read these roots — the @bc/@origin rules are
    slice 1b. They are constructor args now purely to fix the seam.
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
        # Foundation slice, behavior 1 (happy path): a fully conformant file
        # yields zero violations. The parse-failure and Feature-cardinality
        # rules are added below in their own RED->GREEN behaviors.
        self._parse(text)

        # Exactly one Feature and the file parses. The @bc/@origin/
        # @scenario_hash dimension rules (slice 1b) will add their checks here,
        # collecting further violations before returning. For this foundation
        # slice a single-Feature parseable file is conformant.
        return result

    def validate_file(self, path: str) -> ValidationResult:
        text = Path(path).read_text(encoding="utf-8")
        return self.validate_text(text, file=path)
