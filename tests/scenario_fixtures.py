"""Reusable builder for scenario-file fixtures under a tmp_path.

Every ``scenarios validate`` slice (1a foundation here, plus the later
@bc/@origin/@scenario_hash, @service, --json, --aggregate slices) needs to
construct feature-file fixtures with configurable Feature/scenario/tag shape.
Rather than have each slice's tests hand-roll feature text, this module is the
single fixture factory they all reuse: pass the tags and scenario bodies you
want, get back a written ``.feature`` path under tmp_path.

Design notes for later slices:
- ``feature_tags`` / ``scenarios`` let a test dial in the exact @bc / @origin /
  @scenario_hash cardinality a dimension-rule scenario needs.
- ``feature_count`` and ``raw_text`` are the escape hatches for the pathological
  files this foundation slice needs (zero Feature, two Features, un-parseable
  garbage) — a rule-driven builder cannot express "no Feature keyword", so
  ``raw_text`` writes verbatim bytes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Sequence

from scenarios.hash import compute_scenario_hash


class ScenarioBlock:
    """One scenario's title + steps, plus the tags to emit above it.

    ``hash_tag`` controls the @scenario_hash tag:
    - ``"auto"`` (default): compute the block-only hash of this scenario's
      ``Scenario:``+steps body and emit ``@scenario_hash:<computed>`` — the
      conformant case.
    - a literal 16-hex string: emit that value verbatim (for the later
      hash-mismatch rule slice).
    - ``None``: emit no @scenario_hash tag at all.
    """

    def __init__(
        self,
        title: str,
        steps: Sequence[str],
        *,
        hash_tag: Optional[str] = "auto",
        extra_tags: Sequence[str] = (),
    ) -> None:
        self.title = title
        self.steps = list(steps)
        self.hash_tag = hash_tag
        self.extra_tags = list(extra_tags)

    def block_text(self) -> str:
        """The ``Scenario:``+steps body alone (no tags) — what gets hashed."""
        lines = [f"  Scenario: {self.title}"]
        lines.extend(f"    {step}" for step in self.steps)
        return "\n".join(lines)

    def render(self) -> str:
        tags: list[str] = list(self.extra_tags)
        if self.hash_tag == "auto":
            tags.append(f"@scenario_hash:{compute_scenario_hash(self.block_text())}")
        elif self.hash_tag is not None:
            tags.append(f"@scenario_hash:{self.hash_tag}")
        parts = []
        if tags:
            parts.append("  " + " ".join(tags))
        parts.append(self.block_text())
        return "\n".join(parts)


def default_scenario(
    title: str = "A representative scenario",
    *,
    hash_tag: Optional[str] = "auto",
) -> ScenarioBlock:
    return ScenarioBlock(
        title,
        [
            "Given a precondition",
            "When an action occurs",
            "Then an outcome is observed",
        ],
        hash_tag=hash_tag,
    )


def build_feature_text(
    *,
    feature_name: str = "A representative feature",
    feature_tags: Iterable[str] = ("@bc:shopsystem-scenarios", "@origin:adr-056"),
    scenarios: Optional[Sequence[ScenarioBlock]] = None,
) -> str:
    """Assemble conformant-by-default feature text.

    A single Feature carrying the given feature-level tags, followed by the
    given scenario blocks (defaulting to one auto-hashed scenario).
    """
    if scenarios is None:
        scenarios = [default_scenario()]
    lines: list[str] = []
    tag_line = " ".join(feature_tags).strip()
    if tag_line:
        lines.append(tag_line)
    lines.append(f"Feature: {feature_name}")
    lines.append("")
    for i, block in enumerate(scenarios):
        if i:
            lines.append("")
        lines.append(block.render())
    return "\n".join(lines) + "\n"


def write_feature_file(
    tmp_path: Path,
    *,
    name: str = "fixture.feature",
    raw_text: Optional[str] = None,
    **kwargs,
) -> Path:
    """Write a feature-file fixture under tmp_path and return its path.

    Pass ``raw_text`` to write verbatim bytes (for the un-parseable / no-Feature
    / two-Feature pathological files); otherwise the file is assembled from the
    conformant-by-default builder via ``build_feature_text(**kwargs)``.
    """
    text = raw_text if raw_text is not None else build_feature_text(**kwargs)
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path
