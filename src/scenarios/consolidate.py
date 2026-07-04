"""Conformant consolidate helper for grouped Gherkin files (ADR-056 D12).

The consolidate helper merges two-or-more BARE single-scenario files — each a
``Scenario:`` keyword + step lines, with no enclosing ``Feature:`` and no tags
— into ONE Feature-headed file grouping all of their scenarios under a single
Feature with an inherited ``@bc`` / ``@origin``.

It is HASH-PRESERVING. The ``@scenario_hash`` a scenario carries in the
consolidated file is the parser-path block-only hash of its body
(``compute_block_only_hash``), and that hash is INVARIANT under grouping:
block-only canonicalization drops every tag line and every non-``Scenario:``
keyword line, so the hash of a scenario body is identical whether the body
stands alone in a bare file or is grouped under a ``Feature:``. Consolidation
never rewrites a scenario body, so each scenario's ``@scenario_hash`` after
consolidation equals its hash before consolidation.

Because the block-only hash is the same identity the create helper emits and
the validator recomputes, a consolidated file passes ``scenarios validate``
when the inherited ``@bc`` / ``@origin`` are legal — consolidation reuses
``scenarios.create.create_feature_text`` to render the grouped output, so the
create helper's conformance guarantees carry over unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from scenarios.create import create_feature_text
from scenarios.outstanding import _iter_scenario_blocks


def _bare_scenario_body(text: str, *, source: str) -> str:
    """Extract the single scenario block from a bare single-scenario file.

    A bare file holds exactly one ``Scenario:`` block (keyword + steps) and no
    enclosing Feature. Parse the block out via the shared scenario-block
    iterator so surrounding whitespace or a stray comment does not perturb the
    body that gets hashed and grouped. A file carrying zero or more than one
    scenario is a caller error the helper refuses rather than silently
    dropping or merging bodies.
    """
    blocks = list(_iter_scenario_blocks(text))
    if len(blocks) != 1:
        raise ValueError(
            f"consolidate expects each input file to carry exactly one "
            f"scenario; {source!r} carries {len(blocks)}"
        )
    return blocks[0]


def consolidate_bare_files(
    paths: Sequence[str],
    *,
    feature_name: str,
    bc: str,
    origin: str,
) -> str:
    """Merge bare single-scenario files into one Feature-headed grouped file.

    Each path names a bare single-scenario file. Their scenario bodies are
    grouped under one ``Feature: <feature_name>`` carrying the inherited
    ``@bc`` / ``@origin``, each tagged with its parser-path block-only
    ``@scenario_hash`` — which, because bodies are unchanged, equals the
    scenario's hash before consolidation (HASH-PRESERVING). Rendering reuses
    ``create_feature_text`` so the output is conformant by construction.
    """
    bodies = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        bodies.append(_bare_scenario_body(text, source=path))
    return create_feature_text(
        feature_name=feature_name,
        bc=bc,
        origin=origin,
        scenario_bodies=bodies,
    )
