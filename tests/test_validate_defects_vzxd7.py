"""Validator defects found dogfooding the ADR-056 backfill (lead-vzxd.7).

Four defects were found running `scenarios validate` / `--aggregate` against
the real corpus during the ADR-056 backfill. Defect C (@bc_internal exemption)
is pinned by ``test_validate_bc_internal.py``; this module pins A, B, and D:

- DEFECT A — Scenario Outline hash: the per-scenario recompute that decides
  E_HASH_MISMATCH must equal ``scenarios hash`` on the scenario's RAW block
  for a ``Scenario Outline`` (Examples table RETAINED, keyword preserved),
  not a reconstruction that drops the Examples and normalizes the keyword.

- DEFECT B — E_MULTI_FEATURE false positive: a single valid Feature whose
  DESCRIPTION prose contains the substring "Feature:" must NOT trip
  E_MULTI_FEATURE. Cardinality is the gherkin-official parser's Feature-node
  count, not a raw ``Feature:`` line scan.

- DEFECT D — stray-.gherkin guard: ``--aggregate`` must go RED while ANY
  ``.gherkin`` file remains under the corpus dir (an unmigrated corpus must
  not satisfy the gate by being invisible to the ``*.feature`` glob).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from scenarios.hash import compute_scenario_hash
from scenarios.outstanding import parse_then_block_only_hash
from scenarios.validate import (
    E_HASH_MISMATCH,
    E_MULTI_FEATURE,
    Validator,
    validate_corpus,
)


# ---------------------------------------------------------------------------
# Shared fixtures.
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "bc-manifest.yaml"
    path.write_text(
        "bcs:\n  - name: shopsystem-scenarios\nservices: []\n",
        encoding="utf-8",
    )
    return path


def _validator(tmp_path: Path) -> Validator:
    # @bc:shopsystem-scenarios is legal; @origin:lead-vzxd.1 resolves as a
    # lead-bead id without a file lookup.
    return Validator(manifest_path=str(_write_manifest(tmp_path)))


def _scenarios_hash(raw_block: str) -> str:
    """The CANONICAL hash: pipe the raw scenario block through the real
    ``scenarios hash`` CLI (block-only canonicalization). This is the ground
    truth every recompute must agree with."""
    result = subprocess.run(
        ["scenarios", "hash"],
        input=raw_block,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# The raw Scenario Outline block (keyword + steps + Examples), exactly as it
# appears in a feature file. Its canonical hash INCLUDES the Examples table.
_OUTLINE_BLOCK = (
    "  Scenario Outline: watch listen drop reconnect\n"
    "    Given a subscriber on channel <chan>\n"
    "    When the connection drops\n"
    "    Then it reconnects on <chan>\n"
    "\n"
    "    Examples:\n"
    "      | chan |\n"
    "      | a    |\n"
    "      | b    |"
)


def _outline_feature(on_disk_hash: str) -> str:
    """A single-Feature file carrying one Scenario Outline whose on-disk
    @scenario_hash is ``on_disk_hash``."""
    return (
        "@bc:shopsystem-scenarios @origin:lead-vzxd.1\n"
        "Feature: an outline feature\n"
        "\n"
        f"  @scenario_hash:{on_disk_hash}\n"
        f"{_OUTLINE_BLOCK}\n"
    )


# ---------------------------------------------------------------------------
# DEFECT A — Scenario Outline recompute == `scenarios hash` on the raw block.
# ---------------------------------------------------------------------------


def test_scenario_outline_ondisk_hash_matches_scenarios_hash_cli(tmp_path):
    # Ground-truth guard for the fixture: the block-only `scenarios hash` of
    # the raw Outline block (via both the CLI and the in-proc parse-then-hash
    # path) agree. This is the canonical value the on-disk tag carries.
    canonical_cli = _scenarios_hash(_OUTLINE_BLOCK)
    canonical_inproc = parse_then_block_only_hash(_OUTLINE_BLOCK)
    assert canonical_cli == canonical_inproc, (canonical_cli, canonical_inproc)


def test_scenario_outline_correct_tag_does_not_trip_hash_mismatch(tmp_path):
    # A Scenario Outline whose on-disk @scenario_hash EQUALS `scenarios hash`
    # on its raw block (the canonical value) must validate clean: no false
    # E_HASH_MISMATCH. Before the defect-A fix the validator recomputes a
    # DIFFERENT value (Examples dropped, keyword normalized) and wrongly fires
    # E_HASH_MISMATCH on a correct on-disk tag.
    canonical = _scenarios_hash(_OUTLINE_BLOCK)
    text = _outline_feature(canonical)
    result = _validator(tmp_path).validate_text(text, file="outline.feature")
    codes = {v.rule for v in result.violations}
    assert E_HASH_MISMATCH not in codes, (
        "a Scenario Outline whose on-disk @scenario_hash equals `scenarios "
        f"hash` on its raw block ({canonical}) must not trip E_HASH_MISMATCH; "
        f"got violations: {[(v.rule, v.detail) for v in result.violations]}"
    )
    assert result.ok, [v.rule for v in result.violations]


def test_validator_outline_recompute_equals_scenarios_hash_raw_block(tmp_path):
    # The mechanism (vi) checks locally: the validator's per-scenario recompute
    # for a Scenario Outline must EQUAL `scenarios hash` on that scenario's raw
    # block. Plant a deliberately WRONG on-disk hash so E_HASH_MISMATCH fires,
    # then assert the recomputed value the diagnostic names is the canonical
    # `scenarios hash` value (Examples retained), not the Examples-dropped one.
    canonical = _scenarios_hash(_OUTLINE_BLOCK)
    wrong = "0000000000000000"
    assert wrong != canonical
    text = _outline_feature(wrong)
    result = _validator(tmp_path).validate_text(text, file="outline.feature")
    mismatches = [v for v in result.violations if v.rule == E_HASH_MISMATCH]
    assert len(mismatches) == 1, [v.rule for v in result.violations]
    # The recomputed value the diagnostic carries must be the canonical one.
    assert canonical in (mismatches[0].detail or ""), (
        "the validator's Scenario Outline recompute must equal `scenarios "
        f"hash` on the raw block ({canonical}); diagnostic was: "
        f"{mismatches[0].detail!r}"
    )


# ---------------------------------------------------------------------------
# DEFECT B — E_MULTI_FEATURE uses the parser Feature-node count.
# ---------------------------------------------------------------------------


# A SINGLE valid Feature whose DESCRIPTION prose (the free-text lines between
# the ``Feature:`` header and the first Scenario) contains the substring
# "Feature:". A naive ``Feature:`` line scan counts two; the gherkin-official
# parser correctly sees ONE Feature.
_SINGLE_FEATURE_PROSE_MENTIONS_FEATURE = (
    "@bc:shopsystem-scenarios @origin:lead-vzxd.1\n"
    "Feature: the block-only hash\n"
    "  The block-only hash is defined over the scenario block alone.\n"
    "  Feature: header line is NOT part of it.\n"
    "\n"
    "  @scenario_hash:PLACEHOLDER\n"
    "  Scenario: a representative scenario\n"
    "    Given a precondition\n"
    "    When an action occurs\n"
    "    Then an outcome is observed\n"
)


def _prose_feature_with_correct_hash() -> str:
    raw_block = (
        "  Scenario: a representative scenario\n"
        "    Given a precondition\n"
        "    When an action occurs\n"
        "    Then an outcome is observed"
    )
    canonical = parse_then_block_only_hash(raw_block)
    return _SINGLE_FEATURE_PROSE_MENTIONS_FEATURE.replace(
        "PLACEHOLDER", canonical
    )


def test_description_prose_containing_feature_keyword_is_not_multi_feature(
    tmp_path,
):
    # A single valid Feature whose description prose contains "Feature:" must
    # NOT trip E_MULTI_FEATURE: cardinality is the parser's Feature-node count,
    # not a raw line scan. Before the defect-B fix the line scan counts two
    # "Feature:" lines and wrongly fires E_MULTI_FEATURE.
    text = _prose_feature_with_correct_hash()
    result = _validator(tmp_path).validate_text(text, file="prose.feature")
    codes = {v.rule for v in result.violations}
    assert E_MULTI_FEATURE not in codes, (
        "a single valid Feature whose description prose contains 'Feature:' "
        "must not trip E_MULTI_FEATURE; got violations: "
        f"{[(v.rule, v.detail) for v in result.violations]}"
    )
    # With a valid @bc/@origin and a correct @scenario_hash it validates clean.
    assert result.ok, [v.rule for v in result.violations]


def test_genuine_two_feature_file_still_trips_multi_feature(tmp_path):
    # The control: two REAL Feature nodes (each a header at line start) must
    # still trip E_MULTI_FEATURE — the parser-node count is >= 2. This pins the
    # fix honest: it distinguishes prose mentioning "Feature:" from a genuine
    # second Feature declaration.
    two = (
        "@bc:shopsystem-scenarios @origin:lead-vzxd.1\n"
        "Feature: the first feature\n"
        "  Scenario: s1\n"
        "    Given a precondition\n"
        "    Then an outcome\n"
        "\n"
        "Feature: the second feature\n"
        "  Scenario: s2\n"
        "    Given another precondition\n"
        "    Then another outcome\n"
    )
    result = _validator(tmp_path).validate_text(two, file="two.feature")
    codes = {v.rule for v in result.violations}
    assert E_MULTI_FEATURE in codes, (
        "two genuine Feature nodes must still trip E_MULTI_FEATURE; got: "
        f"{[(v.rule, v.detail) for v in result.violations]}"
    )
