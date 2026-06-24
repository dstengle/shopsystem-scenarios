"""Collection-time guard against a stale non-editable wheel shadowing src/.

When a non-editable ``scenarios`` wheel installed under site-packages
shadows the workspace ``src/scenarios/`` checkout, ``import scenarios``
resolves to the stale wheel — which may LACK modules the workspace copy
ships (e.g. ``journal.py``). Tests then run against the wrong, lossy copy
and fail in confusing ways far from the root cause.

:func:`check_editable_install` is the single extracted guard the conftest
collection hook calls. It is path-based and dependency-light: ``pytest`` is
imported lazily inside the function so importing this module has no
import-time coupling to pytest.
"""
from __future__ import annotations

from pathlib import Path


def check_editable_install(
    package_name: str,
    resolved_package_file: Path,
    workspace_src_dir: Path,
) -> None:
    """Abort collection when the package resolves outside the workspace src/.

    ``resolved_package_file`` is the on-disk file ``import <package_name>``
    actually resolved to (its ``__file__``); ``workspace_src_dir`` is the
    workspace ``src`` directory the package is expected to resolve under.

    Returns ``None`` (raises nothing) when ``resolved_package_file`` is
    located under ``workspace_src_dir`` — the correct editable/src-on-path
    resolution. Otherwise a non-editable site-packages copy is shadowing
    ``src/``; raises :class:`pytest.UsageError` (pytest's collection-abort
    signal) whose message names the package and its resolved site-packages
    path, states the workspace ``src/`` path the package was expected to
    resolve under, and includes the literal remediation ``pip install -e .``.
    """
    resolved = Path(resolved_package_file).resolve()
    src_dir = Path(workspace_src_dir).resolve()

    if src_dir in resolved.parents:
        # Correct resolution: the package resolved under the workspace src/.
        return

    import pytest

    raise pytest.UsageError(
        f"stale-wheel shadow detected: the {package_name!r} package resolved "
        f"to {resolved} (a non-editable site-packages copy), not the workspace "
        f"checkout. It was expected to resolve under the workspace src path "
        f"{src_dir}. A stale wheel shadowing src/ runs the test suite against "
        f"the wrong copy (which may lack modules present in src/). Remediate by "
        f"installing the workspace package in editable mode: pip install -e ."
    )
