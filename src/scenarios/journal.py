"""On-disk scenario journal of serviced block-only hashes.

A *scenario journal* is a flat UTF-8 text file recording which canonical
scenarios a BC has serviced. Each line is exactly one **block-only**
canonical hash (16 lowercase hex chars, as produced by
``outstanding.compute_block_only_hash``) — nothing else. There is no bead
id, scenario title, dispatch record, or message-bus row stored alongside a
hash; membership in the journal is keyed *solely* on the block-only hash.

That deliberate minimalism is the journal's contract: a yes/no answer about
whether a behavior has been serviced is a pure function of the journal file's
contents and the queried block-only hash. This module owns the read and
membership helpers so the format stays in one place; the journal-rebuild
behavior writes the same format these helpers read.

The package declares no runtime dependencies, and this module keeps that
property.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Union

from scenarios.feature import iter_scenarios


def harvest_features_tree(features_tree: Union[str, Path]) -> list[str]:
    """Harvest the as-committed ``@scenario_hash`` tag values under a tree.

    Walks ``features_tree`` recursively for ``*.feature`` files and collects
    the ``@scenario_hash:`` tag value attached to each scenario block, reading
    the tag *as committed* — no hash is recomputed. Scenarios carrying no
    ``@scenario_hash`` tag contribute nothing. The values are returned in
    file-walk order (callers de-duplicate and order when writing); the walk is
    sorted by path so the harvest itself is deterministic.
    """
    harvested: list[str] = []
    for feature_path in sorted(Path(features_tree).rglob("*.feature")):
        text = feature_path.read_text(encoding="utf-8")
        for scenario_hash, _title in iter_scenarios(text):
            if scenario_hash:
                harvested.append(scenario_hash)
    return harvested


def write_journal_entries(
    journal_path: Union[str, Path], entries: Iterable[str]
) -> list[str]:
    """Write ``entries`` to ``journal_path`` in the journal's on-disk format.

    The entries are de-duplicated and emitted in a stable, deterministic
    (sorted) order, one per line, UTF-8 — exactly the format
    ``read_journal_entries`` reads back. Sorting plus de-duplication makes
    the write *idempotent over an entry set*: rewriting the same set of
    hashes yields a byte-identical file, so re-running a rebuild neither
    duplicates nor drops an entry. The returned list is the written entry
    order, so callers need not re-derive it.
    """
    ordered = sorted(set(entries))
    Path(journal_path).write_text(
        "".join(entry + "\n" for entry in ordered),
        encoding="utf-8",
    )
    return ordered


def read_journal_entries(journal_path: Union[str, Path]) -> list[str]:
    """Read a journal file and return its block-only hash entries.

    Every non-blank line is one block-only canonical hash. Surrounding
    whitespace is stripped and blank lines are dropped, so a trailing newline
    or stray blank line does not become a phantom entry. Entries are returned
    in file order; the file is read as UTF-8 to match how the journal is
    written.
    """
    text = Path(journal_path).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


def is_recorded(journal_path: Union[str, Path], block_only_hash: str) -> bool:
    """Return whether ``block_only_hash`` is recorded in the journal file.

    Membership is keyed solely on block-only-hash equality against the
    journal's entries — there is no other field to key on, since the journal
    stores only hashes. A hash absent from the file yields ``False`` rather
    than an error: "not serviced" is a definite answer, not a failure.
    """
    return block_only_hash in read_journal_entries(journal_path)
