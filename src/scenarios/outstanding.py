"""System-wide outstanding view over canonical scenarios.

A canonical scenario is **outstanding** when no BC has serviced it: its
*block-only* canonical hash has no journal record and no landed
``work_done``. This module enumerates every canonical scenario under a
features directory, computes each scenario's block-only hash, and reports
which hashes are outstanding against a supplied record set.

The block-only hash is deliberately distinct from
``hash.compute_scenario_hash``. ``compute_scenario_hash`` canonicalizes the
scenario while *keeping* ``@bc:`` and other tags (it only drops
``@scenario_hash:``), so its output is sensitive to which BC owns the
scenario. The block-only hash drops **every** tag line — anything whose
stripped form starts with ``@`` — leaving only the ``Scenario:`` keyword and
the step lines. It therefore identifies the *behavior block* independent of
ownership tags, which is the right identity for a system-wide outstanding
tally that spans BCs.

The canonicalization idiom (strip per-line whitespace, drop blank lines)
mirrors ``hash.py``; the line scanner mirrors ``feature.py``. The package
declares no runtime dependencies, and this module keeps that property.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Iterator, Union

# A scenario block opens on a ``Scenario:`` (or ``Scenario Outline:``)
# keyword line, mirroring feature.py's keyword discipline.
_SCENARIO_RE = re.compile(r"^\s*Scenario(?:\s+Outline)?:")
# A Feature/Background keyword closes any open scenario block.
_BLOCK_BOUNDARY_RE = re.compile(r"^\s*(?:Feature|Background):")


def compute_block_only_hash(gherkin_text: str) -> str:
    """Stable short (16-hex-char) hash of a scenario's behavior block.

    Canonicalization (see module docstring): strip whitespace per line, drop
    blank lines, and drop every tag line (one whose stripped form starts with
    ``@``) — including ``@bc:`` and ``@scenario_hash:``. The remaining
    ``Scenario:`` keyword and step lines are joined and hashed.
    """
    canonical = []
    for line in gherkin_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("@"):
            continue
        canonical.append(s)
    return hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()[:16]


def _iter_scenario_blocks(feature_text: str) -> Iterator[str]:
    """Yield each scenario block (keyword + step lines) as raw text.

    A block starts at a ``Scenario:``/``Scenario Outline:`` keyword line and
    runs until the next ``Scenario``, ``Feature``, or ``Background`` keyword.
    Tag lines preceding a ``Scenario:`` keyword are *not* part of the block —
    block-only hashing drops them anyway, and excluding them here keeps the
    boundary unambiguous.
    """
    block: list[str] = []
    for line in feature_text.splitlines():
        if _SCENARIO_RE.match(line):
            if block:
                yield "\n".join(block)
            block = [line]
            continue
        if _BLOCK_BOUNDARY_RE.match(line):
            if block:
                yield "\n".join(block)
            block = []
            continue
        if block:
            block.append(line)
    if block:
        yield "\n".join(block)


def _iter_canonical_block_hashes(features_dir: Path) -> Iterator[str]:
    """Yield the block-only hash of every canonical scenario under a dir."""
    for feature_path in sorted(Path(features_dir).rglob("*.feature")):
        text = feature_path.read_text(encoding="utf-8")
        for block in _iter_scenario_blocks(text):
            yield compute_block_only_hash(block)


@dataclass(frozen=True)
class OutstandingView:
    """The computed outstanding tally.

    ``outstanding`` is the set of block-only hashes that have no record;
    ``denominator`` is the count of canonical scenarios considered.
    """

    outstanding: FrozenSet[str]
    denominator: int


def compute_outstanding_view(
    features_dir: Union[str, Path], records
) -> OutstandingView:
    """Compute the system-wide outstanding view over canonical scenarios.

    Enumerate every canonical scenario under ``features_dir``, compute each
    scenario's block-only hash, and treat a scenario as outstanding when its
    block-only hash is absent from ``records`` (the journal/``work_done``
    record set). ``denominator`` counts all canonical scenarios considered,
    so a never-dispatched scenario — present under ``features_dir`` but absent
    from ``records`` — is both listed as outstanding and counted.
    """
    record_set = set(records)
    block_hashes = list(_iter_canonical_block_hashes(features_dir))
    outstanding = frozenset(h for h in block_hashes if h not in record_set)
    return OutstandingView(outstanding=outstanding, denominator=len(block_hashes))
