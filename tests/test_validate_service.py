"""Unit tests for the optional @service dimension of `scenarios validate`
(ADR-056 slice 1b).

@service is optional. When present it must name a known service from the
bc-manifest.yaml ``services`` section; an unknown @service is a violation
(E_UNKNOWN_SERVICE). Crucially @service does NOT substitute for the mandatory
@bc owner — that half is pinned by the feature-file scenarios, while these unit
tests pin the accepted/unknown @service value behavior that makes the
"known @service is accepted" acceptance scenario a genuine code path rather
than an incidental no-op.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from scenarios.validate import E_UNKNOWN_SERVICE, Validator


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "bc-manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            {"bcs": ["shopsystem-scenarios"], "services": ["postgres"]}
        ),
        encoding="utf-8",
    )
    return path


def _origin_root(tmp_path: Path) -> Path:
    root = tmp_path / "origin"
    (root / "adr").mkdir(parents=True)
    (root / "adr" / "adr-056.md").write_text("# ADR-056\n", encoding="utf-8")
    return root


def _conformant_feature(service_tag: str) -> str:
    return (
        f"@bc:shopsystem-scenarios @origin:adr-056 {service_tag}\n"
        "Feature: a feature carrying a service tag\n"
        "\n"
        "  @scenario_hash:PLACEHOLDER\n"
        "  Scenario: A representative scenario\n"
        "    Given a precondition\n"
        "    When an action occurs\n"
        "    Then an outcome is observed\n"
    )


def _with_correct_hash(text: str) -> str:
    from scenarios.hash import compute_scenario_hash

    block = (
        "Scenario: A representative scenario\n"
        "Given a precondition\n"
        "When an action occurs\n"
        "Then an outcome is observed"
    )
    return text.replace("PLACEHOLDER", compute_scenario_hash(block))


def test_known_service_is_accepted(tmp_path: Path) -> None:
    validator = Validator(
        manifest_path=str(_manifest(tmp_path)),
        origin_roots=[str(_origin_root(tmp_path))],
    )
    text = _with_correct_hash(_conformant_feature("@service:postgres"))
    result = validator.validate_text(text, file="f.feature")
    assert result.ok, result.render()


def test_unknown_service_is_rejected(tmp_path: Path) -> None:
    validator = Validator(
        manifest_path=str(_manifest(tmp_path)),
        origin_roots=[str(_origin_root(tmp_path))],
    )
    text = _with_correct_hash(_conformant_feature("@service:not-a-service"))
    result = validator.validate_text(text, file="f.feature")
    rules = [v.rule for v in result.violations]
    assert E_UNKNOWN_SERVICE in rules, rules
    # The offending @service value is named in the diagnostic.
    assert "not-a-service" in result.render()
