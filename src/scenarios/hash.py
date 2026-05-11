"""Stable hash for a Gherkin scenario.

The canonicalization rule is part of the scenario contract, not the
messaging contract — so it lives here rather than in the catalog
package. Messages carry scenarios; messages do not define what a
scenario is.

Normalization rules:
- Strip whitespace per line.
- Drop blank lines.
- Drop any line whose stripped form starts with ``@scenario_hash:``
  so embedding the hash as a tag does not perturb subsequent
  recomputation.
"""
from __future__ import annotations

import hashlib


def compute_scenario_hash(gherkin_text: str) -> str:
    """Stable short (16-hex-char) hash of a Gherkin scenario.

    See module docstring for the canonicalization rule.
    """
    canonical = []
    for line in gherkin_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("@scenario_hash:"):
            continue
        canonical.append(s)
    return hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()[:16]
