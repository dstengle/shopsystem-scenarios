"""@bc_internal exemption for `scenarios validate` (lead-vzxd.3, Phase 1).

The lead's lead-vzxd.3 RULING: files/scenarios tagged ``@bc_internal`` are
BC-INTERNAL structural tests (guards for the BC's own infra — the
editable-install/stale-wheel guard, the release-workflow CI guard, etc.), NOT
lead-pinned ADR-056 product scenarios. They are EXEMPT from the ADR-056 schema
gate and must be left as-is:

- a feature-level ``@bc_internal`` tag makes the whole file EXEMPT;
- the three-dimension schema checks (@bc / @origin / @scenario_hash, @service)
  are WAIVED — an exempt file need not carry @bc/@origin/@scenario_hash and
  must NOT trip E_MISSING_BC / E_MISSING_ORIGIN / E_MISSING_HASH / E_UNKNOWN_*;
- the file must still be valid Gherkin (E_GHERKIN_PARSE / cardinality still
  apply — an exempt file must parse);
- ``--aggregate`` SKIPS ``@bc_internal`` files entirely: they contribute no
  per-file violations and no W_ transitional markers.

Ships in scenarios v0.3.1. Phase 2 migrates/re-tags the corpus.
"""
from __future__ import annotations

from pathlib import Path

from scenarios.validate import Validator, validate_corpus


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------

# A feature carrying @bc_internal and NONE of @bc / @origin / @scenario_hash.
# Without the exemption this trips E_MISSING_BC, E_MISSING_ORIGIN, and
# E_MISSING_HASH; with the exemption it is clean.
_BC_INTERNAL_FEATURE = (
    "@bc_internal\n"
    "Feature: the editable-install / stale-wheel guard (BC-internal)\n"
    "  Scenario: bare pytest aborts under the stale-wheel guard\n"
    "    Given a stale wheel is installed\n"
    "    When bare pytest runs\n"
    "    Then the guard aborts the run\n"
)

# The SAME feature body WITHOUT @bc_internal — the control that must still fail
# the three-dimension schema gate (proves the exemption, not a fixture that is
# vacuously conformant).
_NON_EXEMPT_SAME_BODY = (
    "Feature: the editable-install / stale-wheel guard (BC-internal)\n"
    "  Scenario: bare pytest aborts under the stale-wheel guard\n"
    "    Given a stale wheel is installed\n"
    "    When bare pytest runs\n"
    "    Then the guard aborts the run\n"
)

# A conformant product feature (known @bc, resolving @origin, auto-hashable).
# The hash is auto-computed by the fixture builder in the aggregate tests; here
# we resolve @bc against a fixture manifest and @origin as a lead bead id.
def _write_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "bc-manifest.yaml"
    path.write_text(
        "bcs:\n  - name: shopsystem-scenarios\nservices: []\n",
        encoding="utf-8",
    )
    return path


def _validator(tmp_path: Path) -> Validator:
    # A validator whose @bc legal set includes shopsystem-scenarios; @origin
    # resolves lead bead ids without a file lookup, so @origin:lead-vzxd.1
    # resolves.
    return Validator(manifest_path=str(_write_manifest(tmp_path)))


# ---------------------------------------------------------------------------
# Behavior (a): per-file exemption.
# ---------------------------------------------------------------------------


def test_bc_internal_feature_is_exempt_from_schema_gate(tmp_path):
    # A @bc_internal feature carrying NO @bc/@origin/@scenario_hash is exempt:
    # it yields zero violations (exit 0).
    result = _validator(tmp_path).validate_text(
        _BC_INTERNAL_FEATURE, file="stale_wheel_guard.feature"
    )
    assert result.ok, (
        f"a @bc_internal feature must be exempt from the schema gate; "
        f"got violations: {[v.rule for v in result.violations]}"
    )
    assert result.exit_code == 0


def test_same_feature_without_bc_internal_still_fails(tmp_path):
    # The control: the SAME body WITHOUT @bc_internal is non-exempt and trips
    # the three-dimension schema gate. This pins the exemption honest — the
    # exempt outcome above is because of @bc_internal, not a vacuous fixture.
    result = _validator(tmp_path).validate_text(
        _NON_EXEMPT_SAME_BODY, file="stale_wheel_guard.feature"
    )
    codes = {v.rule for v in result.violations}
    assert "E_MISSING_BC" in codes, codes
    assert "E_MISSING_ORIGIN" in codes, codes
    assert "E_MISSING_HASH" in codes, codes


def test_bc_internal_file_must_still_be_valid_gherkin(tmp_path):
    # Exemption waives the three-dimension checks but NOT gherkin validity: an
    # exempt file with a broken body still trips E_GHERKIN_PARSE.
    broken = (
        "@bc_internal\n"
        "Feature: an exempt file with a broken body\n"
        "  Scenario: s\n"
        "    Given a step\n"
        '      """\n'
        "      an unterminated doc string that off-the-shelf gherkin rejects\n"
    )
    result = _validator(tmp_path).validate_text(broken, file="broken.feature")
    codes = {v.rule for v in result.violations}
    assert "E_GHERKIN_PARSE" in codes, codes


# ---------------------------------------------------------------------------
# Behavior (b): --aggregate skips @bc_internal files.
# ---------------------------------------------------------------------------


def _write_conformant_product_file(corpus: Path, name: str) -> None:
    from scenario_fixtures import build_feature_text, default_scenario

    text = build_feature_text(
        feature_name=f"conformant product feature {name}",
        feature_tags=("@bc:shopsystem-scenarios", "@origin:lead-vzxd.1"),
        scenarios=[default_scenario(f"product scenario {name}")],
    )
    (corpus / f"product_{name}.feature").write_text(text, encoding="utf-8")


def test_aggregate_skips_bc_internal_file(tmp_path):
    # A corpus of one conformant product file PLUS a @bc_internal file that
    # would otherwise be non-conformant (no @bc/@origin/@scenario_hash). The
    # aggregate gate must SKIP the @bc_internal file entirely: exit 0, no
    # findings.
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_conformant_product_file(corpus, "a")
    (corpus / "bc_internal.feature").write_text(
        _BC_INTERNAL_FEATURE, encoding="utf-8"
    )
    result = validate_corpus(
        str(corpus), manifest_path=str(_write_manifest(tmp_path))
    )
    assert result.ok, (
        f"aggregate must skip @bc_internal files; got findings: "
        f"{[(f.code, f.file) for f in result.findings]}"
    )
    assert result.exit_code == 0


def test_aggregate_still_red_on_nonexempt_product_file(tmp_path):
    # A corpus containing a @bc_internal file AND a NON-exempt product file that
    # is genuinely non-conformant (a scenario with no @scenario_hash ->
    # E_MISSING_HASH). The @bc_internal file is skipped, but the product file's
    # violation must still keep the gate RED and be named.
    from scenario_fixtures import build_feature_text, default_scenario

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "bc_internal.feature").write_text(
        _BC_INTERNAL_FEATURE, encoding="utf-8"
    )
    bad = build_feature_text(
        feature_name="a non-conformant product feature",
        feature_tags=("@bc:shopsystem-scenarios", "@origin:lead-vzxd.1"),
        scenarios=[default_scenario("a scenario with no hash", hash_tag=None)],
    )
    (corpus / "product_bad.feature").write_text(bad, encoding="utf-8")

    result = validate_corpus(
        str(corpus), manifest_path=str(_write_manifest(tmp_path))
    )
    assert not result.ok, "a non-exempt product violation must keep the gate RED"
    codes = {f.code for f in result.findings}
    assert "E_MISSING_HASH" in codes, codes
    # The @bc_internal file must NOT appear in any finding.
    assert not any(
        "bc_internal.feature" in f.file for f in result.findings
    ), [f.file for f in result.findings]
