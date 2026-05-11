"""Discovers Gherkin features under shopsystem-scenarios/features/ and
registers them as pytest-bdd scenarios. No-op when no .feature files are
present, so the suite stays green during a future drop-everything reset.
"""
from pathlib import Path

from pytest_bdd import scenarios

_features_dir = Path(__file__).resolve().parent.parent / "features"
if any(_features_dir.glob("*.feature")):
    scenarios(str(_features_dir))
