"""Real-data hardening tests for `scenarios validate` (lead-vzxd.2).

Two hardening fixes against the REAL corpus shapes the earlier slices did
not exercise:

- GAP-1 — the real ``bc-manifest.yaml`` carries DICT entries under ``bcs:``
  and ``services:`` (``- name: ...`` with optional ``remote``/``role``/
  ``status``/``deferred_to`` keys), not bare strings. ``frozenset([{...}])``
  raised ``TypeError: unhashable type: 'dict'``; the manifest loader must
  name-extract each entry (dict -> its ``name``; bare string -> itself),
  tolerate extra keys, and accept a provisional entry as a legal @bc value.
  Both the dict-entry and the legacy bare-string manifest shapes are
  supported.

- GAP-2 — real ADR files are ``NNN-slug.md`` and a BC container carries no
  ``adr/`` dir (ADR-018), so the dir-scan @origin resolution misses a real
  ``@origin:adr-056``. A ``--origin-index`` seam resolves @origin by
  MEMBERSHIP in a generated identifier list (one id per line), alongside the
  retained lead-bead prefix rule and the retained dir-scan fixture fallback.

Both fixes must degrade GRACEFULLY (a clean diagnostic, never a TypeError or
traceback) on missing / empty / malformed reference inputs.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scenarios.hash import compute_scenario_hash
from scenarios.validate import (
    E_UNKNOWN_BC,
    E_UNKNOWN_ORIGIN,
    Validator,
)


# ---------------------------------------------------------------------------
# Fixtures matching the REAL corpus shapes (this BC carries no lead source).
# ---------------------------------------------------------------------------


def _dict_entry_manifest(tmp_path: Path) -> Path:
    """A manifest in the REAL dict-entry shape, including a provisional entry
    and entries carrying extra keys (remote / role / status / deferred_to)."""
    path = tmp_path / "bc-manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "bcs": [
                    {
                        "name": "shopsystem-messaging",
                        "remote": "https://github.com/dstengle/shopsystem-messaging",
                        "role": "bc",
                    },
                    {
                        "name": "shopsystem-test-harness",
                        "role": "bc",
                        "status": "provisional",
                        "deferred_to": "lead-bh2m",
                    },
                    {"name": "shopsystem-scenarios", "role": "bc"},
                ],
                "services": [
                    {"name": "agent-vault-broker"},
                    {"name": "postgres"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _origin_index(tmp_path: Path, ids: list[str]) -> Path:
    """A generated origin-index: one identifier per line (adr-056, pdr-003…)."""
    path = tmp_path / "origin-index.txt"
    path.write_text("\n".join(ids) + "\n", encoding="utf-8")
    return path


def _conformant_feature(bc: str, origin: str) -> str:
    text = (
        f"@bc:{bc} @origin:{origin}\n"
        "Feature: a real-data feature\n"
        "\n"
        "  @scenario_hash:PLACEHOLDER\n"
        "  Scenario: A representative scenario\n"
        "    Given a precondition\n"
        "    When an action occurs\n"
        "    Then an outcome is observed\n"
    )
    block = (
        "Scenario: A representative scenario\n"
        "Given a precondition\n"
        "When an action occurs\n"
        "Then an outcome is observed"
    )
    return text.replace("PLACEHOLDER", compute_scenario_hash(block))


# ---------------------------------------------------------------------------
# GAP-1 — dict-entry manifest name-extraction.
# ---------------------------------------------------------------------------


def test_dict_entry_manifest_name_extracts_legal_bcs(tmp_path: Path) -> None:
    """A dict-entry manifest yields a legal @bc set of the extracted names —
    including the provisional entry — with no TypeError."""
    validator = Validator(manifest_path=str(_dict_entry_manifest(tmp_path)))
    legal = validator.legal_bcs
    assert "shopsystem-messaging" in legal
    assert "shopsystem-scenarios" in legal
    # A provisional entry IS a legal @bc value.
    assert "shopsystem-test-harness" in legal


def test_dict_entry_manifest_name_extracts_legal_services(tmp_path: Path) -> None:
    validator = Validator(manifest_path=str(_dict_entry_manifest(tmp_path)))
    legal = validator.legal_services
    assert "agent-vault-broker" in legal
    assert "postgres" in legal


def test_provisional_bc_value_passes_validation(tmp_path: Path) -> None:
    """A conformant feature owned by a provisional @bc validates cleanly."""
    validator = Validator(
        manifest_path=str(_dict_entry_manifest(tmp_path)),
        origin_index=str(_origin_index(tmp_path, ["adr-056"])),
    )
    text = _conformant_feature("shopsystem-test-harness", "adr-056")
    result = validator.validate_text(text, file="f.feature")
    assert result.ok, result.render()


def test_legacy_bare_string_manifest_still_supported(tmp_path: Path) -> None:
    """The legacy bare-string manifest shape keeps working alongside dicts."""
    path = tmp_path / "bc-manifest.yaml"
    path.write_text(
        yaml.safe_dump({"bcs": ["shopsystem-scenarios"], "services": ["postgres"]}),
        encoding="utf-8",
    )
    validator = Validator(manifest_path=str(path))
    assert "shopsystem-scenarios" in validator.legal_bcs
    assert "postgres" in validator.legal_services


# ---------------------------------------------------------------------------
# GAP-2 — @origin resolves by membership in a --origin-index list.
# ---------------------------------------------------------------------------


def test_origin_resolves_by_index_membership(tmp_path: Path) -> None:
    validator = Validator(
        manifest_path=str(_dict_entry_manifest(tmp_path)),
        origin_index=str(_origin_index(tmp_path, ["adr-056", "pdr-003", "brief-foo"])),
    )
    text = _conformant_feature("shopsystem-scenarios", "adr-056")
    result = validator.validate_text(text, file="f.feature")
    assert result.ok, result.render()


def test_origin_absent_from_index_is_unknown(tmp_path: Path) -> None:
    validator = Validator(
        manifest_path=str(_dict_entry_manifest(tmp_path)),
        origin_index=str(_origin_index(tmp_path, ["adr-056"])),
    )
    text = _conformant_feature("shopsystem-scenarios", "adr-999")
    result = validator.validate_text(text, file="f.feature")
    assert E_UNKNOWN_ORIGIN in [v.rule for v in result.violations], result.render()


def test_lead_bead_origin_still_resolves_with_index(tmp_path: Path) -> None:
    """The lead-/shopsystem- bead-id prefix rule is retained alongside the
    index seam."""
    validator = Validator(
        manifest_path=str(_dict_entry_manifest(tmp_path)),
        origin_index=str(_origin_index(tmp_path, ["adr-056"])),
    )
    text = _conformant_feature("shopsystem-scenarios", "lead-vzxd.1")
    result = validator.validate_text(text, file="f.feature")
    assert result.ok, result.render()


# ---------------------------------------------------------------------------
# Graceful degradation — NEVER a TypeError / traceback on bad reference input.
# ---------------------------------------------------------------------------


def test_missing_manifest_degrades_gracefully(tmp_path: Path) -> None:
    validator = Validator(manifest_path=str(tmp_path / "does-not-exist.yaml"))
    # No crash; protocol tokens still stand for @bc.
    legal = validator.legal_bcs
    assert "shopsystem-product" in legal
    assert validator.legal_services == frozenset()


def test_dict_manifest_missing_name_key_degrades_gracefully(tmp_path: Path) -> None:
    path = tmp_path / "bc-manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            {"bcs": [{"role": "bc"}, {"name": "shopsystem-scenarios"}], "services": []}
        ),
        encoding="utf-8",
    )
    validator = Validator(manifest_path=str(path))
    # The nameless entry is skipped, not fatal; the named one is extracted.
    legal = validator.legal_bcs
    assert "shopsystem-scenarios" in legal


def test_garbage_manifest_degrades_gracefully(tmp_path: Path) -> None:
    path = tmp_path / "bc-manifest.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    validator = Validator(manifest_path=str(path))
    # Non-dict top-level yaml yields empty registries, no crash.
    legal = validator.legal_bcs
    assert "shopsystem-product" in legal


def test_unparseable_yaml_manifest_degrades_gracefully(tmp_path: Path) -> None:
    """A manifest whose YAML does not even parse (a malformed document) yields
    empty registries with a clean fallback, never a yaml.YAMLError traceback."""
    path = tmp_path / "bc-manifest.yaml"
    path.write_text("garbage: [nope\n", encoding="utf-8")
    validator = Validator(manifest_path=str(path))
    legal = validator.legal_bcs
    assert "shopsystem-product" in legal
    assert validator.legal_services == frozenset()


def test_empty_manifest_degrades_gracefully(tmp_path: Path) -> None:
    path = tmp_path / "bc-manifest.yaml"
    path.write_text("", encoding="utf-8")
    validator = Validator(manifest_path=str(path))
    assert "shopsystem-product" in validator.legal_bcs


def test_missing_origin_index_degrades_gracefully(tmp_path: Path) -> None:
    validator = Validator(
        manifest_path=str(_dict_entry_manifest(tmp_path)),
        origin_index=str(tmp_path / "no-such-index.txt"),
    )
    # A missing index file is not fatal: @origin falls back to the other
    # resolution paths (dir-scan / lead-bead). An adr-056 ref simply fails to
    # resolve (clean E_UNKNOWN_ORIGIN), never a crash.
    text = _conformant_feature("shopsystem-scenarios", "adr-056")
    result = validator.validate_text(text, file="f.feature")
    assert E_UNKNOWN_ORIGIN in [v.rule for v in result.violations], result.render()


def test_repo_root_manifest_loads_without_crash() -> None:
    """The checked-in repo-root bc-manifest.yaml loads through the parser and
    yields a non-empty legal @bc set with no TypeError, regardless of whether
    it is stored in the dict-entry or bare-string shape."""
    repo_root = Path(__file__).resolve().parent.parent
    manifest = repo_root / "bc-manifest.yaml"
    assert manifest.exists(), manifest
    validator = Validator(manifest_path=str(manifest))
    legal = validator.legal_bcs
    assert "shopsystem-scenarios" in legal
    # Protocol tokens always stand for @bc.
    assert "shopsystem-product" in legal


def test_malformed_nonlist_origin_index_degrades_gracefully(tmp_path: Path) -> None:
    """A garbage / non-list index yields a clean diagnostic, never a crash."""
    path = tmp_path / "origin-index.txt"
    # Blank lines and whitespace-only lines are tolerated (ignored).
    path.write_text("\n   \nadr-056\n\n", encoding="utf-8")
    validator = Validator(
        manifest_path=str(_dict_entry_manifest(tmp_path)),
        origin_index=str(path),
    )
    text = _conformant_feature("shopsystem-scenarios", "adr-056")
    result = validator.validate_text(text, file="f.feature")
    assert result.ok, result.render()
