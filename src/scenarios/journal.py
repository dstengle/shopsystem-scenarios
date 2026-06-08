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
from typing import Union


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
