"""Conformant create helper for grouped Gherkin scenario files (ADR-056 D12).

This is the scoped, conformant surface for *emitting* a Feature-headed
grouped scenario file. Where ``scenarios.validate`` is the read-side gate
that decides whether a file is schema-valid, this module is the write-side
helper that produces files which pass that gate by construction:

- exactly one ``Feature:`` line carrying the feature-level ``@bc:<owner>``
  and ``@origin:<ref>`` tags, and
- each scenario tagged with ``@scenario_hash:<H>`` where ``H`` is that
  scenario's PARSER-PATH block-only hash (``compute_block_only_hash`` over
  the ``Scenario:`` keyword + step lines — the same canonical form the
  validator recomputes and the ``@scenario_hash`` schema dimension checks).

Emitting the parser-path block-only hash — rather than a hash that retains
the surrounding tags — is what makes the output survive ``scenarios validate``
E_HASH_MISMATCH: the validator recomputes the block-only hash over the parsed
scenario body and compares it to the embedded tag, so the embedded value must
be exactly that block-only hash.

The block-only hash is HASH-STABLE under grouping: it drops every tag line and
the surrounding ``Feature:`` line, so a scenario's ``@scenario_hash`` is
invariant whether the body stands alone or is grouped under a Feature. Slice
4's consolidate helper (``scenarios.consolidate``) relies on exactly this
invariant to be hash-preserving.
"""
from __future__ import annotations

from typing import Sequence

from scenarios.outstanding import compute_block_only_hash


def _indent_block(body: str, indent: str = "  ") -> str:
    """Indent every non-blank line of ``body`` by ``indent``.

    Scenario bodies are authored flush-left (``Scenario:`` + step lines with
    their own relative indentation); grouping them under a Feature indents the
    whole block one level. Blank lines are left empty rather than indented.
    """
    out = []
    for line in body.splitlines():
        out.append(indent + line if line.strip() else line)
    return "\n".join(out)


def render_scenario(body: str, *, indent: str = "  ") -> str:
    """Render one scenario block: its ``@scenario_hash`` tag line then the body.

    ``body`` is a bare scenario block (``Scenario:`` keyword + step lines, no
    tags). The emitted ``@scenario_hash`` is the parser-path block-only hash
    of that body — the value ``scenarios validate`` recomputes and checks.
    """
    scenario_hash = compute_block_only_hash(body)
    tag_line = f"{indent}@scenario_hash:{scenario_hash}"
    return tag_line + "\n" + _indent_block(body, indent)


def create_feature_text(
    *,
    feature_name: str,
    bc: str,
    origin: str,
    scenario_bodies: Sequence[str],
) -> str:
    """Emit a Feature-headed grouped Gherkin file from scenario bodies.

    The output is conformant by construction (ADR-056 D12): a feature-level
    tag line carrying exactly one ``@bc:<bc>`` and one ``@origin:<origin>``,
    a single ``Feature:`` line, then each supplied bare scenario body grouped
    under it and tagged with its parser-path block-only ``@scenario_hash``.
    Supplying a ``bc`` that names a known context and an ``origin`` that
    resolves lets the emitted file pass ``scenarios validate`` (exit 0).
    """
    lines: list[str] = []
    lines.append(f"@bc:{bc} @origin:{origin}")
    lines.append(f"Feature: {feature_name}")
    for body in scenario_bodies:
        lines.append("")
        lines.append(render_scenario(body))
    return "\n".join(lines) + "\n"
