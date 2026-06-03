"""Lightweight extraction of (scenario_hash, title) pairs from a feature
file.

This is deliberately a line scanner, not a full Gherkin parse: the only
structure it needs is the association between a ``Scenario:`` keyword and
the ``@scenario_hash:`` tag that precedes it. Keeping it dependency-free
mirrors ``hash.py`` — the package declares no runtime dependencies, and
the canonicalization rule it must stay consistent with is itself a simple
per-line rule.

Consistency with the canonicalization rule (see ``hash.py``): a
``@scenario_hash:`` token is only honoured when it is an actual tag —
i.e. its line's stripped form starts with ``@``. A ``@scenario_hash:``
appearing mid-step as a substring is ignored, exactly as canonicalization
ignores it when recomputing a hash.
"""
from __future__ import annotations

import re
from typing import Iterator, Optional, Tuple

# Title is everything after the keyword's colon, with surrounding
# whitespace trimmed. "Scenario Outline" is accepted alongside "Scenario".
_SCENARIO_RE = re.compile(r"^\s*Scenario(?:\s+Outline)?:\s*(?P<title>.*?)\s*$")
_HASH_TAG_RE = re.compile(r"@scenario_hash:(?P<hash>\S+)")


def iter_scenarios(feature_text: str) -> Iterator[Tuple[Optional[str], str]]:
    """Yield ``(scenario_hash, title)`` for each scenario in ``feature_text``.

    ``scenario_hash`` is the value of the most recent ``@scenario_hash:``
    tag seen on a tag line before the ``Scenario:`` keyword, or ``None`` if
    the scenario carries no such tag. The pending hash is consumed by the
    scenario it precedes, so two adjacent scenarios never share a hash.
    """
    pending_hash: Optional[str] = None
    for line in feature_text.splitlines():
        scenario = _SCENARIO_RE.match(line)
        if scenario is not None:
            yield pending_hash, scenario.group("title")
            pending_hash = None
            continue
        stripped = line.strip()
        if stripped.startswith("@"):
            tag = _HASH_TAG_RE.search(stripped)
            if tag is not None:
                pending_hash = tag.group("hash")
