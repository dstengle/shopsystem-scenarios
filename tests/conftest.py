"""Shared fixtures and pytest-bdd step definitions for the scenarios BC.

Step definitions exercise the BC's interfaces from a third-party
perspective: the `scenarios hash` and `scenarios verify` CLIs are
invoked via subprocess (the same boundary downstream callers use),
and the `scenarios.hash.compute_scenario_hash` function is imported
for the in-process canonicalization scenarios.

Style mirrors shopsystem-messaging/tests/conftest.py — subprocess +
tmp_path fixtures, with the `context` dict carrying cross-step state.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from pytest_bdd import given, parsers, then, when

from scenarios.hash import compute_scenario_hash


# -----------------------------------------------------------------------
# Collection-time editable-install / stale-wheel guard (bead cdi / hb2.2)
# -----------------------------------------------------------------------
#
# A real collection-time hook that fails fast when a stale, non-editable
# `scenarios` wheel under site-packages shadows this repo's workspace
# `src/scenarios/` checkout. It computes the real `scenarios` package's
# resolved `__file__` and the workspace `src/` dir and delegates to the
# single extracted guard the editable_install_guard.feature scenarios
# unit-test, so the hook and those tests share one implementation.
#
# Under the correct `PYTHONPATH=src` invocation `import scenarios` resolves
# to <repo>/src/scenarios/__init__.py, which IS under <repo>/src — so the
# guard returns cleanly and this hook does NOT abort the suite's own
# collection. It only aborts when the resolved file lies outside the
# workspace src/ (i.e. a genuine site-packages shadow).


def pytest_configure(config) -> None:  # noqa: ARG001 — pytest hook signature
    import importlib.util

    import scenarios

    repo_root = Path(__file__).resolve().parent.parent
    workspace_src_dir = repo_root / "src"

    # Load the guard FROM the workspace src/ file by path — NOT via the
    # `scenarios` package namespace. Were we to `from scenarios._editable_guard
    # import ...`, a stale site-packages wheel shadowing src/ (the exact
    # failure this guard exists to catch) would lack the module and raise an
    # opaque ModuleNotFoundError before the guard could run. Loading by path
    # keeps the guard authoritative even when `import scenarios` resolves to
    # the shadow.
    guard_src = workspace_src_dir / "scenarios" / "_editable_guard.py"
    spec = importlib.util.spec_from_file_location(
        "scenarios._editable_guard_conftest", guard_src
    )
    guard_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard_mod)

    try:
        guard_mod.check_editable_install(
            "scenarios",
            Path(scenarios.__file__).resolve(),
            workspace_src_dir,
        )
    except pytest.UsageError as exc:
        # The extracted guard raises pytest.UsageError (the type the
        # editable_install_guard.feature scenarios pin). From within
        # pytest_configure, render that remediation message and abort the
        # session cleanly before any test runs (returncode 4 = usage error)
        # rather than letting it surface as an opaque INTERNALERROR.
        pytest.exit(str(exc), returncode=4)


# -----------------------------------------------------------------------
# Shared cross-step state
# -----------------------------------------------------------------------


@pytest.fixture
def context() -> dict:
    return {}


# -----------------------------------------------------------------------
# Reference bodies used by multiple scenarios
# -----------------------------------------------------------------------


_REFERENCE_BODY_A = (
    "Scenario: Boiling water in Fahrenheit\n"
    "    Given a temperature of 100 degrees Celsius\n"
    "    When I convert it to Fahrenheit\n"
    "    Then I get 212 degrees Fahrenheit"
)


# =======================================================================
# hash_cli.feature — `scenarios hash` CLI contract
# =======================================================================


@given("a Gherkin scenario body on stdin")
def given_gherkin_body_on_stdin(context: dict) -> None:
    # A well-formed but otherwise arbitrary Gherkin body. The scenario
    # asserts shape (16-hex-char output, exit 0, empty stderr), not the
    # specific hash value, so any non-degenerate body works.
    context["stdin_body"] = _REFERENCE_BODY_A


@when(parsers.parse('I run "scenarios hash"'))
def when_run_scenarios_hash(context: dict) -> None:
    result = subprocess.run(
        ["scenarios", "hash"],
        input=context["stdin_body"],
        capture_output=True,
        text=True,
    )
    context["cli_returncode"] = result.returncode
    context["cli_stdout"] = result.stdout
    context["cli_stderr"] = result.stderr


@then("the exit code is 0")
def then_exit_code_is_zero(context: dict) -> None:
    rc = context["cli_returncode"]
    assert rc == 0, (
        f"expected exit code 0; got {rc}; stderr:\n{context.get('cli_stderr', '')}"
    )


@then("stdout is a single line of 16 lowercase hex characters")
def then_stdout_is_16_lowercase_hex(context: dict) -> None:
    stdout = context["cli_stdout"]
    # The CLI uses `print()` which appends a newline; strip exactly one
    # trailing newline so we can assert the payload shape.
    assert stdout.endswith("\n"), (
        f"expected stdout to end with a newline; got {stdout!r}"
    )
    body = stdout[:-1]
    assert "\n" not in body, (
        f"expected a single line of output; got {stdout!r}"
    )
    assert re.fullmatch(r"[0-9a-f]{16}", body), (
        f"expected exactly 16 lowercase hex characters; got {body!r}"
    )


@then("stderr is empty")
def then_stderr_is_empty(context: dict) -> None:
    stderr = context["cli_stderr"]
    assert stderr == "", f"expected empty stderr; got {stderr!r}"


# =======================================================================
# canonicalization_rule.feature — three scenarios sharing the same
# "two bodies, compare hashes" shape, with different B-construction rules.
# =======================================================================


@given("a Gherkin body A")
def given_gherkin_body_a(context: dict) -> None:
    context["body_a"] = _REFERENCE_BODY_A


@given(
    "a Gherkin body B that is A with extra leading and trailing whitespace "
    "on every step line"
)
def given_body_b_with_extra_whitespace(context: dict) -> None:
    # Add deterministic whitespace padding to every line. The
    # canonicalization rule strips per-line whitespace, so the hash must
    # be invariant under this transformation.
    padded = []
    for line in context["body_a"].splitlines():
        padded.append("    " + line + "    ")
    context["body_b"] = "\n".join(padded)


@given(
    "a Gherkin body B that is A with one or more blank lines inserted "
    "between steps"
)
def given_body_b_with_blank_lines(context: dict) -> None:
    # Insert two blank lines between every pair of step lines. The
    # canonicalization rule drops blank lines, so the hash must be
    # invariant under this transformation.
    lines = context["body_a"].splitlines()
    joined = "\n\n\n".join(lines)
    context["body_b"] = joined


@given(
    parsers.parse(
        'a Gherkin body B that is A with one extra line '
        '"{tag_line}" prepended'
    )
)
def given_body_b_with_prepended_tag(tag_line: str, context: dict) -> None:
    # The scenario expects the supplied tag line (which starts with
    # @scenario_hash:) to be dropped by canonicalization, so prepending
    # it must not change the hash.
    context["body_b"] = tag_line + "\n" + context["body_a"]
    # Stash the inline tag-line literal so the idempotence-Then can
    # construct the @scenario_hash:<hash> line in the same shape.
    context["tag_prefix"] = "@scenario_hash:"


@given(
    'a Gherkin body A containing a step whose text includes the substring '
    '"@scenario_hash:" but does not start with it after trimming'
)
def given_body_a_with_substring_midstep(context: dict) -> None:
    # Construct a body where one step has "@scenario_hash:" as an internal
    # substring (here, embedded inside a Then-step quoted phrase). After
    # `.strip()` the line still starts with "Then ", not with the tag, so
    # canonicalization MUST retain the line. This is the discriminator that
    # separates a correct `s.startswith("@scenario_hash:")` check from an
    # incorrect `"@scenario_hash:" in s` substring check.
    body_a = (
        "Scenario: substring vs prefix\n"
        "    Given a thing\n"
        "    When something happens\n"
        '    Then the error message mentions "@scenario_hash:abc" verbatim'
    )
    # Sanity: confirm the constructed body actually satisfies the Given's
    # premise — substring present, but no line's stripped form starts with
    # the tag. Without this guard, a future edit to body_a could silently
    # invalidate the scenario's discriminator.
    saw_substring = False
    for line in body_a.splitlines():
        s = line.strip()
        if "@scenario_hash:" in s:
            saw_substring = True
            assert not s.startswith("@scenario_hash:"), (
                f"fixture invariant violated: line starts with tag: {s!r}"
            )
    assert saw_substring, (
        "fixture invariant violated: no line contains '@scenario_hash:' "
        "as a substring"
    )
    context["body_a"] = body_a
    # Stash the substring-bearing step so the B-construction step can
    # remove exactly that line without re-deriving it.
    context["substring_step"] = (
        '    Then the error message mentions "@scenario_hash:abc" verbatim'
    )


@given("a Gherkin body B that is A with that step deleted")
def given_body_b_with_substring_step_deleted(context: dict) -> None:
    # Remove the substring-bearing step from A to produce B. Because the
    # removed line does NOT start with @scenario_hash: after trimming,
    # canonicalization retains it in A's hash but not in B's — so the
    # two hashes must differ. This is the asymmetry the scenario pins.
    target = context["substring_step"]
    kept = [line for line in context["body_a"].splitlines() if line != target]
    # Guard: confirm we actually dropped exactly one line.
    assert len(kept) == len(context["body_a"].splitlines()) - 1, (
        "fixture invariant violated: expected to drop exactly one step"
    )
    context["body_b"] = "\n".join(kept)


@when("I compute the canonical hash of A and of B")
def when_compute_hash_a_and_b(context: dict) -> None:
    context["hash_a"] = compute_scenario_hash(context["body_a"])
    context["hash_b"] = compute_scenario_hash(context["body_b"])


@then("both hashes are identical")
def then_both_hashes_identical(context: dict) -> None:
    ha, hb = context["hash_a"], context["hash_b"]
    assert ha == hb, (
        f"expected identical canonical hashes; got A={ha!r} B={hb!r}"
    )


@then("the hashes are different")
def then_hashes_are_different(context: dict) -> None:
    ha, hb = context["hash_a"], context["hash_b"]
    assert ha != hb, (
        f"expected canonical hashes to differ; got A={ha!r} B={hb!r} "
        "(canonicalization may be incorrectly treating '@scenario_hash:' "
        "as a substring match instead of a line-start match)"
    )


@then(
    'embedding the resulting hash back into the body as a "@scenario_hash:" '
    "tag line does not change the hash on the next computation"
)
def then_embedding_hash_is_idempotent(context: dict) -> None:
    # Take the hash we just computed, embed it back as a @scenario_hash:
    # tag at the head of the body, recompute, and require the recomputed
    # hash to equal the original. This pins idempotence of the embed.
    original_hash = context["hash_a"]
    prefix = context.get("tag_prefix", "@scenario_hash:")
    embedded_body = f"{prefix}{original_hash}\n{context['body_a']}"
    recomputed = compute_scenario_hash(embedded_body)
    assert recomputed == original_hash, (
        f"expected embed-then-recompute to be idempotent; "
        f"original={original_hash!r} recomputed={recomputed!r}"
    )


# =======================================================================
# verify_cli.feature — `scenarios verify --hash` CLI contract
# =======================================================================


@given("a Gherkin body on stdin")
def given_gherkin_body_on_stdin_verify(context: dict) -> None:
    # Distinct phrasing from "a Gherkin scenario body on stdin" — the
    # verify scenarios use this simpler wording. Same payload semantics.
    context["stdin_body"] = _REFERENCE_BODY_A


@given("the canonical hash of that body")
def given_canonical_hash_of_body(context: dict) -> None:
    context["canonical_hash"] = compute_scenario_hash(context["stdin_body"])


@when(parsers.parse('I run "scenarios verify --hash <canonical-hash>"'))
def when_run_scenarios_verify_matching(context: dict) -> None:
    result = subprocess.run(
        ["scenarios", "verify", "--hash", context["canonical_hash"]],
        input=context["stdin_body"],
        capture_output=True,
        text=True,
    )
    context["cli_returncode"] = result.returncode
    context["cli_stdout"] = result.stdout
    context["cli_stderr"] = result.stderr


@then("stdout is empty")
def then_stdout_is_empty(context: dict) -> None:
    stdout = context["cli_stdout"]
    assert stdout == "", f"expected empty stdout; got {stdout!r}"


@given("a Gherkin body on stdin whose canonical hash is some value X")
def given_body_with_hash_x(context: dict) -> None:
    context["stdin_body"] = _REFERENCE_BODY_A
    context["hash_x"] = compute_scenario_hash(context["stdin_body"])


@given("an incorrect hash value Y that differs from X")
def given_incorrect_hash_y(context: dict) -> None:
    # A fixed all-zeros hash is guaranteed not to collide with any
    # sha256-derived 16-hex-char output for realistic bodies. Sanity-check
    # the assumption rather than trusting it blindly.
    wrong = "0000000000000000"
    assert wrong != context["hash_x"], (
        f"all-zeros hash collided with canonical hash {context['hash_x']!r}; "
        "pick another sentinel"
    )
    context["hash_y"] = wrong


@when(parsers.parse('I run "scenarios verify --hash <Y>"'))
def when_run_scenarios_verify_mismatched(context: dict) -> None:
    result = subprocess.run(
        ["scenarios", "verify", "--hash", context["hash_y"]],
        input=context["stdin_body"],
        capture_output=True,
        text=True,
    )
    context["cli_returncode"] = result.returncode
    context["cli_stdout"] = result.stdout
    context["cli_stderr"] = result.stderr


@then("the exit code is non-zero")
def then_exit_code_nonzero(context: dict) -> None:
    rc = context["cli_returncode"]
    assert rc != 0, (
        f"expected non-zero exit; got {rc}; stderr:\n{context.get('cli_stderr', '')}"
    )


@then("stderr contains both the supplied Y and the actual canonical hash X")
def then_stderr_contains_y_and_x(context: dict) -> None:
    stderr = context["cli_stderr"]
    y = context["hash_y"]
    x = context["hash_x"]
    assert y in stderr, (
        f"expected stderr to contain supplied hash {y!r}; got:\n{stderr}"
    )
    assert x in stderr, (
        f"expected stderr to contain actual canonical hash {x!r}; got:\n{stderr}"
    )


# =======================================================================
# hash_stability_regression.feature — pinned reference body
# =======================================================================


@given(parsers.parse('the reference Gherkin body "{raw_body}"'))
def given_reference_gherkin_body(raw_body: str, context: dict) -> None:
    # The Gherkin step text encodes newlines as the literal two-character
    # escape ``\n``; convert them back so the body mirrors what a user
    # would author by hand. Mirrors the same convention the messaging BC
    # uses in its `_write_scenario_body_file` helper.
    body = raw_body.replace("\\n", "\n")
    context["reference_body"] = body


@when("I compute the canonical hash of that body")
def when_compute_hash_of_reference(context: dict) -> None:
    context["reference_hash"] = compute_scenario_hash(context["reference_body"])


@then(parsers.parse('the hash is "{expected_hash}"'))
def then_hash_is_expected(expected_hash: str, context: dict) -> None:
    actual = context["reference_hash"]
    assert actual == expected_hash, (
        f"expected reference hash {expected_hash!r}; got {actual!r}"
    )


# =======================================================================
# release_workflow.feature — the BC's release pipeline must NOT fan out a
# per-repo repository_dispatch to bc-launcher. Per ADR-022, bc-base rebuilds
# are driven by shopsystem-bc-launcher's own centralized scheduled poll, not
# by a per-repo emit. This pins a property of a shipped artifact (the
# workflow YAML): the *executable body* (YAML comment lines excluded) must
# declare no repository_dispatch step targeting bc-launcher and reference no
# BC_LAUNCHER_DISPATCH_TOKEN secret. A target/token reference present only in
# a descriptive YAML comment must NOT fail the guarantee — so the steps strip
# comment lines from the workflow source before inspecting the body.
# =======================================================================


_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"
_DISPATCH_TARGET = "dstengle/shopsystem-bc-launcher"
_DISPATCH_TOKEN_SECRET = "BC_LAUNCHER_DISPATCH_TOKEN"


def _strip_yaml_comments(raw: str) -> str:
    """Return the workflow's executable body with YAML comment lines removed.

    A line whose first non-whitespace character is ``#`` is a standalone
    comment and is dropped entirely. A trailing ``#`` comment on an otherwise
    executable line is also stripped, but only when the ``#`` is not inside a
    quoted scalar — so a literal ``#`` within a quoted string (e.g. a
    client-payload JSON value) survives. This yields the "executable body,
    with YAML comment lines excluded" the scenario inspects, so that a
    repository_dispatch target or BC_LAUNCHER_DISPATCH_TOKEN reference present
    only in a descriptive comment is absent from the body under inspection.
    """
    out_lines: list[str] = []
    for line in raw.splitlines():
        if line.lstrip().startswith("#"):
            # Whole-line comment: drop it.
            continue
        out_lines.append(_strip_trailing_comment(line))
    return "\n".join(out_lines)


def _strip_trailing_comment(line: str) -> str:
    """Strip a trailing ``# ...`` comment from a single line, respecting
    single- and double-quoted scalars so a ``#`` inside a quoted value is
    preserved."""
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            # A YAML inline comment must be preceded by whitespace (or be at
            # the start). Treat it as a comment only then.
            if i == 0 or line[i - 1] in (" ", "\t"):
                return line[:i].rstrip()
    return line


def _workflow_triggers(parsed: dict) -> dict:
    # PyYAML parses the bare key `on` as the boolean True — YAML 1.1
    # treats on/off/yes/no as booleans — so a GitHub Actions `on:` block
    # lands under the True key, not the string "on". Accept either form.
    if not isinstance(parsed, dict):
        return {}
    for key in ("on", True):
        if key in parsed:
            return parsed[key] or {}
    return {}


def _push_tag_patterns(parsed: dict) -> list[str]:
    push = _workflow_triggers(parsed).get("push")
    if not isinstance(push, dict):
        return []
    tags = push.get("tags")
    if isinstance(tags, str):
        return [tags]
    if isinstance(tags, list):
        return [t for t in tags if isinstance(t, str)]
    return []


def _iter_steps(parsed: dict):
    if not isinstance(parsed, dict):
        return
    for job in (parsed.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if isinstance(step, dict):
                yield step


def _step_targets_dispatch(step: dict) -> bool:
    """True if the step performs a repository_dispatch to the bc-launcher
    repo, satisfied by either the peter-evans action or a raw REST call to
    the GitHub repository-dispatches API (the two forms the scenario
    permits)."""
    uses = str(step.get("uses") or "")
    if uses.startswith("peter-evans/repository-dispatch"):
        with_block = step.get("with") or {}
        return str(with_block.get("repository") or "") == _DISPATCH_TARGET
    run = str(step.get("run") or "")
    if run:
        text = run.lower()
        hits_api = "dispatches" in text and (
            "api.github.com" in text or "gh api" in text
        )
        return hits_api and _DISPATCH_TARGET in run
    return False


@given(
    parsers.parse('the shopsystem-scenarios release workflow at "{workflow_path}"')
)
def given_release_workflow_at(workflow_path: str, context: dict) -> None:
    # The repository under test is this checkout; the scenario inspects the
    # release workflow artifact it ships at the named path.
    path = _REPO_ROOT / workflow_path
    assert path.is_file(), (
        f"expected the release workflow at {path}; none found"
    )
    context["release_workflow_path"] = path
    context["release_workflow_raw"] = path.read_text(encoding="utf-8")


@given(
    "bc-base rebuilds are driven by shopsystem-bc-launcher's own centralized "
    "scheduled poll per ADR-022, not by a per-repo repository_dispatch emit"
)
def given_rebuilds_driven_by_central_poll(context: dict) -> None:
    # Documents the ADR-022 rationale for the guarantee; no state to set up
    # beyond the workflow already loaded by the prior Given.
    assert "release_workflow_raw" in context, (
        "release workflow must be loaded before this Given"
    )


@when(
    "the release workflow's executable body, with YAML comment lines "
    "excluded, is inspected on a version-tag release"
)
def when_inspect_executable_body(context: dict) -> None:
    # Strip YAML comment lines so that a repository_dispatch target or token
    # reference living only in a descriptive comment is absent from the body
    # under inspection. Parse the stripped body so the structural Then steps
    # inspect the same comment-free executable body the text assertions do.
    raw = context["release_workflow_raw"]
    executable_body = _strip_yaml_comments(raw)
    context["executable_body"] = executable_body
    context["executable_parsed"] = yaml.safe_load(executable_body)


@then(
    parsers.parse(
        "the executable body declares no step performing a "
        'repository_dispatch targeting "{target}"'
    )
)
def then_no_dispatch_step_targeting(target: str, context: dict) -> None:
    assert target == _DISPATCH_TARGET, target
    parsed = context["executable_parsed"]
    dispatch_steps = [s for s in _iter_steps(parsed) if _step_targets_dispatch(s)]
    assert not dispatch_steps, (
        f"release workflow's executable body declares "
        f"{len(dispatch_steps)} step(s) performing a repository_dispatch "
        f"targeting {target!r}; ADR-022 requires none. Offending step(s):\n"
        + "\n".join(yaml.safe_dump(s) for s in dispatch_steps)
    )
    # Belt-and-suspenders: the raw dispatch target string must also be absent
    # from the comment-stripped executable body text (covers REST forms the
    # structural scan might not classify as a step).
    body = context["executable_body"]
    assert target not in body, (
        f"release workflow's executable body references the "
        f"repository_dispatch target {target!r}; ADR-022 requires none.\n"
        f"executable body was:\n{body}"
    )


@then(
    parsers.parse('the executable body references no secret named "{secret}"')
)
def then_no_token_secret_reference(secret: str, context: dict) -> None:
    assert secret == _DISPATCH_TOKEN_SECRET, secret
    body = context["executable_body"]
    assert secret not in body, (
        f"release workflow's executable body references the secret "
        f"{secret!r}; ADR-022 requires no per-repo dispatch token. "
        f"executable body was:\n{body}"
    )


@then(
    "a repository_dispatch target or BC_LAUNCHER_DISPATCH_TOKEN reference "
    "present only in a descriptive YAML comment, absent from the executable "
    "body, does not fail this guarantee"
)
def then_comment_only_reference_is_tolerated(context: dict) -> None:
    # Prove the comment-stripping is load-bearing: inject a comment line that
    # names both the dispatch target and the token secret into the loaded
    # workflow source, re-derive the executable body, and confirm neither the
    # target nor the secret survives into the comment-stripped body. A
    # reference confined to a comment must NOT fail the guarantee.
    raw = context["release_workflow_raw"]
    probe = (
        f"# historical note: this repo once dispatched to {_DISPATCH_TARGET} "
        f"using the {_DISPATCH_TOKEN_SECRET} secret; removed per ADR-022\n"
    )
    augmented = probe + raw
    body = _strip_yaml_comments(augmented)
    assert _DISPATCH_TARGET not in body, (
        f"comment-only mention of {_DISPATCH_TARGET!r} leaked into the "
        f"executable body; comment stripping is not load-bearing.\n"
        f"body was:\n{body}"
    )
    assert _DISPATCH_TOKEN_SECRET not in body, (
        f"comment-only mention of {_DISPATCH_TOKEN_SECRET!r} leaked into the "
        f"executable body; comment stripping is not load-bearing.\n"
        f"body was:\n{body}"
    )


# =======================================================================
# list_cli.feature — `scenarios list FILE` reads a feature file and emits
# one line per scenario pairing the scenario title with the value of the
# @scenario_hash tag that precedes it. Invoked via subprocess (the same
# boundary downstream callers use), reusing the shared exit-code/stderr
# Then steps.
# =======================================================================


_FIRST_SCENARIO_BODY = (
    "Scenario: First listed scenario\n"
    "    Given a precondition\n"
    "    When an action occurs\n"
    "    Then an outcome holds"
)
_SECOND_SCENARIO_BODY = (
    "Scenario: Second listed scenario\n"
    "    Given another precondition\n"
    "    When a different action occurs\n"
    "    Then a different outcome holds"
)


@given(
    "a feature file containing two scenarios, each preceded by a "
    '"@scenario_hash:" tag line carrying that scenario\'s hash'
)
def given_feature_file_with_two_hashed_scenarios(context: dict, tmp_path) -> None:
    # Use the scenarios' real canonical hashes for the tag values so the
    # fixture is internally honest: the @scenario_hash tag really is the
    # hash of the body it precedes. `scenarios list` echoes the tag value
    # verbatim, so the test still passes regardless, but an honest fixture
    # documents intent and would catch a parser that recomputed instead of
    # reading the tag.
    titles = ["First listed scenario", "Second listed scenario"]
    hashes = [
        compute_scenario_hash(_FIRST_SCENARIO_BODY),
        compute_scenario_hash(_SECOND_SCENARIO_BODY),
    ]
    # Distinct hashes guard against a fixture where both lines would pass
    # the "title + hash on one line" check by coincidence.
    assert hashes[0] != hashes[1], "fixture invariant: scenario hashes collided"

    def _indent(body: str) -> str:
        return "\n".join("  " + line for line in body.splitlines())

    feature_text = (
        "Feature: a fixture feature with two hashed scenarios\n\n"
        f"  @scenario_hash:{hashes[0]}\n"
        f"{_indent(_FIRST_SCENARIO_BODY)}\n\n"
        f"  @scenario_hash:{hashes[1]}\n"
        f"{_indent(_SECOND_SCENARIO_BODY)}\n"
    )
    feature_path = tmp_path / "two_scenarios.feature"
    feature_path.write_text(feature_text, encoding="utf-8")
    context["feature_file"] = feature_path
    context["titles"] = titles
    context["hashes"] = hashes


@when(parsers.parse('I run "scenarios list" against that feature file'))
def when_run_scenarios_list(context: dict) -> None:
    result = subprocess.run(
        ["scenarios", "list", str(context["feature_file"])],
        capture_output=True,
        text=True,
    )
    context["cli_returncode"] = result.returncode
    context["cli_stdout"] = result.stdout
    context["cli_stderr"] = result.stderr


_ORDINALS = {"first": 0, "second": 1}


@then(
    parsers.parse(
        "stdout contains a line pairing the {ordinal} scenario's title "
        "with its @scenario_hash value"
    )
)
def then_stdout_pairs_title_and_hash(ordinal: str, context: dict) -> None:
    idx = _ORDINALS[ordinal]
    title = context["titles"][idx]
    scenario_hash = context["hashes"][idx]
    lines = context["cli_stdout"].splitlines()
    assert any(title in line and scenario_hash in line for line in lines), (
        f"expected a stdout line pairing title {title!r} with hash "
        f"{scenario_hash!r}; stdout was:\n{context['cli_stdout']}"
    )


# =======================================================================
# count_cli.feature — `scenarios count FILE` prints the scenario count.
# Reuses the shared exit-code/stderr Then steps; the count fixture needs
# no @scenario_hash tags since counting is tag-independent.
# =======================================================================


@given("a feature file containing two scenarios")
def given_feature_file_with_two_scenarios(context: dict, tmp_path) -> None:
    # No @scenario_hash tags: `scenarios count` counts Scenario keywords
    # regardless of tagging, and an untagged fixture keeps this scenario
    # honest to its prose ("two scenarios", nothing about hashes).
    feature_text = (
        "Feature: a fixture feature with two scenarios\n\n"
        "  Scenario: First\n"
        "    Given a precondition\n"
        "    Then an outcome holds\n\n"
        "  Scenario: Second\n"
        "    Given another precondition\n"
        "    Then a different outcome holds\n"
    )
    feature_path = tmp_path / "two_scenarios_count.feature"
    feature_path.write_text(feature_text, encoding="utf-8")
    context["feature_file"] = feature_path


@when(parsers.parse('I run "scenarios count" against that feature file'))
def when_run_scenarios_count(context: dict) -> None:
    result = subprocess.run(
        ["scenarios", "count", str(context["feature_file"])],
        capture_output=True,
        text=True,
    )
    context["cli_returncode"] = result.returncode
    context["cli_stdout"] = result.stdout
    context["cli_stderr"] = result.stderr


@then(parsers.parse('stdout is the single line "{value}"'))
def then_stdout_is_single_line(value: str, context: dict) -> None:
    stdout = context["cli_stdout"]
    assert stdout.endswith("\n"), (
        f"expected stdout to end with a newline; got {stdout!r}"
    )
    body = stdout[:-1]
    assert "\n" not in body, f"expected a single line of output; got {stdout!r}"
    assert body == value, f"expected stdout line {value!r}; got {body!r}"


# =======================================================================
# titles_cli.feature — `scenarios titles FILE` reads a feature file and
# emits one line per scenario carrying just the scenario title (no hash
# column). Invoked via subprocess (the same boundary downstream callers
# use), reusing the shared exit-code/stderr Then steps. The distinguishing
# property from `scenarios list` is that titles emits ONLY the title — so
# the per-ordinal Then steps assert the line EQUALS the title rather than
# merely containing it, which would also pass for the tab-joined `list`
# output.
# =======================================================================


@given("a feature file containing two scenarios with distinct titles")
def given_feature_file_with_two_distinct_titles(context: dict, tmp_path) -> None:
    # Two scenarios with deliberately different titles so an "each line is a
    # title, in file order" contract is observable. @scenario_hash tags are
    # present (and honest) but irrelevant to `titles`; including them guards
    # against an implementation that accidentally leaks the hash into the
    # title line.
    titles = ["First titled scenario", "Second titled scenario"]
    assert titles[0] != titles[1], "fixture invariant: scenario titles collided"

    first_body = (
        f"Scenario: {titles[0]}\n"
        "    Given a precondition\n"
        "    When an action occurs\n"
        "    Then an outcome holds"
    )
    second_body = (
        f"Scenario: {titles[1]}\n"
        "    Given another precondition\n"
        "    When a different action occurs\n"
        "    Then a different outcome holds"
    )
    hashes = [
        compute_scenario_hash(first_body),
        compute_scenario_hash(second_body),
    ]

    def _indent(body: str) -> str:
        return "\n".join("  " + line for line in body.splitlines())

    feature_text = (
        "Feature: a fixture feature with two distinctly titled scenarios\n\n"
        f"  @scenario_hash:{hashes[0]}\n"
        f"{_indent(first_body)}\n\n"
        f"  @scenario_hash:{hashes[1]}\n"
        f"{_indent(second_body)}\n"
    )
    feature_path = tmp_path / "two_titled_scenarios.feature"
    feature_path.write_text(feature_text, encoding="utf-8")
    context["feature_file"] = feature_path
    context["titles"] = titles
    context["hashes"] = hashes


@when(parsers.parse('I run "scenarios titles" against that feature file'))
def when_run_scenarios_titles(context: dict) -> None:
    result = subprocess.run(
        ["scenarios", "titles", str(context["feature_file"])],
        capture_output=True,
        text=True,
    )
    context["cli_returncode"] = result.returncode
    context["cli_stdout"] = result.stdout
    context["cli_stderr"] = result.stderr


@then(parsers.parse("stdout's {ordinal} line is the {same_ordinal} scenario's title"))
def then_stdout_line_is_scenario_title(
    ordinal: str, same_ordinal: str, context: dict
) -> None:
    # The two ordinals in the Gherkin always agree ("first line is the first
    # scenario's title"); requiring them to match keeps the step honest and
    # catches a malformed scenario line.
    assert ordinal == same_ordinal, (
        f"expected matching ordinals; got line={ordinal!r} scenario={same_ordinal!r}"
    )
    idx = _ORDINALS[ordinal]
    lines = context["cli_stdout"].splitlines()
    assert len(lines) > idx, (
        f"expected at least {idx + 1} line(s) of output; "
        f"stdout was:\n{context['cli_stdout']}"
    )
    # `titles` emits ONLY the title — assert equality, not containment, so a
    # `list`-style "<hash>\t<title>" line would fail this step.
    assert lines[idx] == context["titles"][idx], (
        f"expected {ordinal} line to be {context['titles'][idx]!r}; "
        f"got {lines[idx]!r}; full stdout:\n{context['cli_stdout']}"
    )


# =======================================================================
# tags_cli.feature — `scenarios tags FILE` reads a feature file and emits
# the DISTINCT @-tags carried by its scenarios, one tag per line. Invoked
# via subprocess (the same boundary downstream callers use), reusing the
# shared exit-code/stderr Then steps. The distinguishing property is
# de-duplication: a tag carried by more than one scenario appears exactly
# ONCE in the output, so the fixture deliberately repeats one tag and the
# Then step asserts a multiset count of exactly one per distinct tag.
# =======================================================================


@given(
    "a feature file whose scenarios carry two distinct @-tags, "
    "one of them repeated"
)
def given_feature_file_with_repeated_tag(context: dict, tmp_path) -> None:
    # Two scenarios. A shared @-tag (@smoke) is carried by both — the
    # repeated tag — and a second @-tag (@slow) is carried by only the
    # second scenario. The distinct set is therefore {@smoke, @slow}, each
    # of which `scenarios tags` must emit exactly once even though @smoke
    # appears twice in the file. @scenario_hash tags are intentionally
    # NOT among the expected output (they are hash plumbing, not semantic
    # @-tags) — but the fixture omits them entirely so this scenario stays
    # honest to its prose, which speaks only of the two distinct @-tags.
    expected_tags = ["@slow", "@smoke"]
    assert expected_tags[0] != expected_tags[1], (
        "fixture invariant: the two distinct tags collided"
    )

    feature_text = (
        "Feature: a fixture feature whose scenarios carry @-tags\n\n"
        "  @smoke\n"
        "  Scenario: First tagged scenario\n"
        "    Given a precondition\n"
        "    Then an outcome holds\n\n"
        "  @smoke @slow\n"
        "  Scenario: Second tagged scenario\n"
        "    Given another precondition\n"
        "    Then a different outcome holds\n"
    )
    feature_path = tmp_path / "tagged_scenarios.feature"
    feature_path.write_text(feature_text, encoding="utf-8")
    context["feature_file"] = feature_path
    # Sorted, since the Then step compares as a de-duplicated set.
    context["expected_tags"] = sorted(expected_tags)


@when(parsers.parse('I run "scenarios tags" against that feature file'))
def when_run_scenarios_tags(context: dict) -> None:
    result = subprocess.run(
        ["scenarios", "tags", str(context["feature_file"])],
        capture_output=True,
        text=True,
    )
    context["cli_returncode"] = result.returncode
    context["cli_stdout"] = result.stdout
    context["cli_stderr"] = result.stderr


@then("stdout lists each distinct @-tag exactly once, one tag per line")
def then_stdout_lists_distinct_tags_once(context: dict) -> None:
    lines = context["cli_stdout"].splitlines()
    # One tag per line: every emitted line is a single @-token with no
    # surrounding whitespace or embedded separators.
    for line in lines:
        assert line.startswith("@"), (
            f"expected each line to be a single @-tag; got {line!r}; "
            f"full stdout:\n{context['cli_stdout']}"
        )
        assert line == line.strip() and " " not in line, (
            f"expected one bare @-tag per line; got {line!r}"
        )
    # Exactly once: no duplicates in the output, even though the fixture
    # repeats a tag across scenarios.
    assert len(lines) == len(set(lines)), (
        f"expected each distinct @-tag exactly once; got duplicates in:\n"
        f"{context['cli_stdout']}"
    )
    # The emitted set equals the expected distinct set (order-independent).
    assert sorted(lines) == context["expected_tags"], (
        f"expected distinct tags {context['expected_tags']!r}; "
        f"got {sorted(lines)!r}; full stdout:\n{context['cli_stdout']}"
    )


# =======================================================================
# outstanding_view.feature — the system-wide outstanding view. A canonical
# scenario is "outstanding" when its BLOCK-ONLY canonical hash (a hash over
# the Scenario keyword + step lines, excluding ALL tag lines — distinct from
# compute_scenario_hash, which keeps @bc: tags) has no BC journal record and
# no landed work_done. Exercised IN-PROCESS via the (not-yet-existing)
# scenarios.outstanding API; the canonical features live under a hermetic
# tmp_path directory so the assertion does not depend on the repo's own
# evolving feature set. The "h6" symbol in the Gherkin is the scenario's
# stable handle within the test; the actual hash is computed by the new
# block-only hash function over the authored body.
# =======================================================================


_OUTSTANDING_SCENARIO_BODY = (
    "Scenario: a never-dispatched canonical scenario\n"
    "    Given a precondition that no BC has ever serviced\n"
    "    When the outstanding view is computed\n"
    "    Then this scenario is counted as outstanding"
)


@given(
    parsers.parse(
        "a canonical scenario authored under this repo's features with "
        'block-only canonical hash "{handle}"'
    )
)
def given_canonical_scenario_with_block_only_hash(
    handle: str, context: dict, tmp_path
) -> None:
    # Author a single canonical scenario into a hermetic features directory.
    # The scenario carries an @bc: tag (which compute_scenario_hash would
    # KEEP) precisely so that the block-only hash — which must drop ALL tag
    # lines — is observably distinct from compute_scenario_hash's output.
    from scenarios.outstanding import compute_block_only_hash

    feature_text = (
        "Feature: a hermetic feature with one canonical scenario\n\n"
        "  @bc:shopsystem-scenarios\n"
        f"  {_OUTSTANDING_SCENARIO_BODY.splitlines()[0]}\n"
        + "\n".join(
            "  " + line for line in _OUTSTANDING_SCENARIO_BODY.splitlines()[1:]
        )
        + "\n"
    )
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "hermetic.feature").write_text(feature_text, encoding="utf-8")

    block_only_hash = compute_block_only_hash(_OUTSTANDING_SCENARIO_BODY)

    # The block-only hash must differ from the @bc:-retaining canonical hash:
    # this is what makes "block-only" a distinct, load-bearing concept rather
    # than an alias of compute_scenario_hash.
    tagged_body = (
        "@bc:shopsystem-scenarios\n" + _OUTSTANDING_SCENARIO_BODY
    )
    assert block_only_hash != compute_scenario_hash(tagged_body), (
        "fixture invariant violated: block-only hash must drop @bc: tags and "
        "so must differ from the @bc:-retaining canonical hash"
    )

    context["features_dir"] = features_dir
    context["block_only_hashes"] = {handle: block_only_hash}


@given(
    parsers.parse(
        'no BC journal records "{handle}" and no work_done has ever landed '
        'for "{handle2}"'
    )
)
def given_no_records_for_hash(handle: str, handle2: str, context: dict) -> None:
    assert handle == handle2, (
        f"expected the same handle on both sides; got {handle!r} / {handle2!r}"
    )
    # The empty record set: no journal entries, no landed work_done. With no
    # records, every canonical scenario under features must be outstanding.
    context["records"] = set()


@when(
    "the system-wide outstanding view is computed over all canonical "
    "scenarios under features"
)
def when_compute_outstanding_view(context: dict) -> None:
    from scenarios.outstanding import compute_outstanding_view

    context["view"] = compute_outstanding_view(
        context["features_dir"], context["records"]
    )


@then(
    parsers.parse(
        'the outstanding view lists the scenario with block-only canonical '
        'hash "{handle}" as outstanding'
    )
)
def then_view_lists_scenario_as_outstanding(handle: str, context: dict) -> None:
    block_only_hash = context["block_only_hashes"][handle]
    view = context["view"]
    assert block_only_hash in view.outstanding, (
        f"expected block-only hash {block_only_hash!r} (handle {handle!r}) to "
        f"appear in the outstanding listing; listing was {view.outstanding!r}"
    )


@then(
    parsers.parse(
        'the scenario with hash "{handle}" is counted in the outstanding '
        "denominator despite never having been dispatched to any BC"
    )
)
def then_scenario_counted_in_denominator(handle: str, context: dict) -> None:
    view = context["view"]
    # The single authored canonical scenario, never dispatched and with no
    # records, must contribute to the outstanding denominator.
    assert view.denominator >= 1, (
        f"expected the outstanding denominator to count the never-dispatched "
        f"scenario (handle {handle!r}); denominator was {view.denominator!r}"
    )


# =======================================================================
# journal_query.feature — `scenarios journal query <journal-file> <hash>`
# reads an on-disk journal file (whose entries are block-only canonical
# hashes) and reports a definite yes/no for the queried block-only hash.
# Exit status is success in BOTH the yes and no cases (success ≠ "found");
# the yes/no is reported on stdout. The answer is keyed SOLELY on the
# block-only canonical hash — not on any bead id, scenario title, dispatch
# record, or message-bus row. Exercised via subprocess against the
# `scenarios` binary (the same boundary downstream callers use), mirroring
# the hash/list/count/titles/tags CLI scenarios. The "h1"/"h2" symbols in
# the Gherkin are stable test handles; each maps to a REAL block-only
# canonical hash computed by compute_block_only_hash over a distinct
# scenario body, so the query is genuinely keyed on the block-only hash.
#
# Journal-file format (established by THIS behavior, which the rebuild
# behavior must conform to): a UTF-8 text file with one block-only
# canonical hash per line. A present entry is exactly that hash on its own
# line; nothing else (no bead id, title, dispatch record, or bus row) is
# stored.
# =======================================================================


_JOURNAL_BODY_H1 = (
    "Scenario: a journalled behavior that is present\n"
    "    Given a behavior that some BC has serviced\n"
    "    When the journal is queried for it\n"
    "    Then the journal answers yes"
)
_JOURNAL_BODY_H2 = (
    "Scenario: a behavior that is absent from the journal\n"
    "    Given a behavior no BC has serviced\n"
    "    When the journal is queried for it\n"
    "    Then the journal answers no"
)


@given(
    "a scenario journal stored as a file on disk under the "
    "shopsystem-scenarios bounded context"
)
def given_scenario_journal_file(context: dict, tmp_path) -> None:
    from scenarios.outstanding import compute_block_only_hash

    # The journal lives on disk; its entries are block-only canonical
    # hashes, one per line. Resolve the symbolic handles h1/h2 to REAL
    # block-only hashes over distinct bodies so "keyed on the block-only
    # hash" is observable rather than asserted against a literal string.
    h1 = compute_block_only_hash(_JOURNAL_BODY_H1)
    h2 = compute_block_only_hash(_JOURNAL_BODY_H2)
    # Distinct handles guard against a fixture where present/absent collide.
    assert h1 != h2, "fixture invariant: h1 and h2 block-only hashes collided"
    context["block_only_hashes"] = {"h1": h1, "h2": h2}
    context["journal_path"] = tmp_path / "scenarios.journal"
    # Start with an empty journal; the present-entry Given populates it.
    context["journal_entries"] = []


@given(
    parsers.parse(
        'the journal file records the block-only canonical hash "{handle}" '
        "as a present entry"
    )
)
def given_journal_records_present(handle: str, context: dict) -> None:
    block_only_hash = context["block_only_hashes"][handle]
    # A present entry is exactly the block-only hash on its own line, with
    # NO bead id, title, dispatch record, or message-bus row alongside it —
    # the journal stores only hashes. This is what makes the eventual yes
    # answer attributable solely to block-only-hash membership.
    context["journal_entries"].append(block_only_hash)
    context["journal_path"].write_text(
        "".join(line + "\n" for line in context["journal_entries"]),
        encoding="utf-8",
    )


@given(
    parsers.parse(
        'the journal file contains no entry for the block-only canonical '
        'hash "{handle}"'
    )
)
def given_journal_absent(handle: str, context: dict) -> None:
    block_only_hash = context["block_only_hashes"][handle]
    # The journal exists on disk but holds no entry equal to the queried
    # block-only hash. Write whatever entries are accumulated (here, none)
    # and guard that the queried hash is genuinely absent.
    assert block_only_hash not in context["journal_entries"], (
        f"fixture invariant: expected {handle!r} ({block_only_hash!r}) "
        "absent from the journal, but it is present"
    )
    context["journal_path"].write_text(
        "".join(line + "\n" for line in context["journal_entries"]),
        encoding="utf-8",
    )


@when(
    parsers.parse(
        'the "scenarios journal query" CLI command is run against that '
        'journal file for the block-only canonical hash "{handle}"'
    )
)
def when_run_journal_query(handle: str, context: dict) -> None:
    block_only_hash = context["block_only_hashes"][handle]
    result = subprocess.run(
        [
            "scenarios",
            "journal",
            "query",
            str(context["journal_path"]),
            block_only_hash,
        ],
        capture_output=True,
        text=True,
    )
    context["cli_returncode"] = result.returncode
    context["cli_stdout"] = result.stdout
    context["cli_stderr"] = result.stderr
    context["queried_handle"] = handle


@then(
    parsers.parse(
        'the command exits with a success status and reports a definite '
        '"{answer}" for "{handle}"'
    )
)
def then_command_reports_definite_answer(
    answer: str, handle: str, context: dict
) -> None:
    rc = context["cli_returncode"]
    # Success status in BOTH the yes and no cases: success != "found".
    assert rc == 0, (
        f"expected success exit status (0) for a definite {answer!r} answer; "
        f"got {rc}; stderr:\n{context.get('cli_stderr', '')}"
    )
    # The definite yes/no is reported on stdout as its own line.
    lines = [line.strip() for line in context["cli_stdout"].splitlines()]
    assert answer in lines, (
        f"expected a definite {answer!r} line on stdout for {handle!r}; "
        f"stdout was:\n{context['cli_stdout']}"
    )
    # A definite answer is exactly one of yes/no — guard against a CLI that
    # emits both or neither.
    assert ("yes" in lines) != ("no" in lines), (
        f"expected exactly one definite yes/no line; stdout was:\n"
        f"{context['cli_stdout']}"
    )


@then(
    parsers.parse(
        'the answer is keyed solely on the block-only canonical hash '
        '"{handle}", not on any bead id, scenario title, dispatch record, '
        "or message-bus row"
    )
)
def then_answer_keyed_solely_on_block_only_hash(
    handle: str, context: dict
) -> None:
    # The journal file on disk holds ONLY block-only hashes — one per line,
    # nothing else. Re-querying the same on-disk journal for the same
    # block-only hash must reproduce the same definite answer, with no bead
    # id, title, dispatch record, or bus row available to key on (none are
    # stored). Re-run the CLI to pin that the answer is a pure function of
    # (journal file contents, queried block-only hash).
    block_only_hash = context["block_only_hashes"][handle]

    # The journal stores only hashes: every non-empty line is exactly a
    # 16-hex-char block-only hash (the format this behavior establishes).
    journal_lines = [
        line.strip()
        for line in context["journal_path"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for line in journal_lines:
        assert re.fullmatch(r"[0-9a-f]{16}", line), (
            f"journal must store only block-only hashes, one per line; "
            f"found a non-hash entry {line!r}"
        )

    rerun = subprocess.run(
        [
            "scenarios",
            "journal",
            "query",
            str(context["journal_path"]),
            block_only_hash,
        ],
        capture_output=True,
        text=True,
    )
    assert rerun.returncode == context["cli_returncode"], (
        "re-querying the same journal for the same block-only hash changed "
        f"the exit status ({context['cli_returncode']} -> {rerun.returncode}); "
        "the answer must be keyed solely on block-only-hash membership"
    )
    first = [l.strip() for l in context["cli_stdout"].splitlines()]
    second = [l.strip() for l in rerun.stdout.splitlines()]
    first_answer = "yes" if "yes" in first else "no"
    second_answer = "yes" if "yes" in second else "no"
    assert first_answer == second_answer, (
        "re-querying the same journal for the same block-only hash changed "
        f"the definite answer ({first_answer!r} -> {second_answer!r}); "
        "the answer must be keyed solely on block-only-hash membership"
    )


# =======================================================================
# journal_rebuild.feature — `scenarios journal rebuild <features-tree>
# <journal-file>` walks a features tree, harvests the as-committed
# @scenario_hash tag values, and writes them as a journal file in the
# format the journal-query behavior established (one block-only canonical
# hash per line, nothing else). The entries are derived from the committed
# @scenario_hash tags ALONE — no recomputation, no work_done, and no
# message-bus event is required. The command is idempotent: running it a
# second time over the same features tree leaves an entry SET identical
# hash-for-hash, neither duplicating nor dropping an entry. Exercised via
# subprocess against the `scenarios` binary (the same boundary downstream
# callers use); the features tree and journal output path are built under
# tmp_path. The "h8a"/"h8b" symbols in the Gherkin are stable test handles;
# each maps to a REAL block-only canonical hash computed by
# compute_block_only_hash over a distinct scenario body, and each is
# written into the fixture AS that scenario block's @scenario_hash tag so
# the harvest reads the as-committed tag value honestly.
# =======================================================================


_REBUILD_BODY_H8A = (
    "Scenario: a first serviced behavior to harvest from the tree\n"
    "    Given a behavior that some BC has serviced\n"
    "    When the journal is rebuilt from the features tree\n"
    "    Then its block-only hash appears as a journal entry"
)
_REBUILD_BODY_H8B = (
    "Scenario: a second serviced behavior to harvest from the tree\n"
    "    Given another behavior that some BC has serviced\n"
    "    When the journal is rebuilt from the features tree\n"
    "    Then its block-only hash appears as a journal entry too"
)


@given(
    parsers.parse(
        "a features tree containing scenario blocks tagged with the "
        '@scenario_hash tags "{handle_a}" and "{handle_b}", each tag equal '
        "to its block's block-only canonical hash"
    )
)
def given_features_tree_with_hashed_blocks(
    handle_a: str, handle_b: str, context: dict, tmp_path
) -> None:
    from scenarios.outstanding import compute_block_only_hash

    # Resolve the symbolic handles h8a/h8b to REAL block-only canonical
    # hashes over distinct scenario bodies, then write each hash into the
    # fixture AS its block's @scenario_hash tag. This makes the fixture
    # internally honest: each as-committed tag value really equals the
    # block-only canonical hash of the block it precedes, so a rebuild that
    # harvests the committed tag values reproduces exactly {h8a, h8b}.
    h8a = compute_block_only_hash(_REBUILD_BODY_H8A)
    h8b = compute_block_only_hash(_REBUILD_BODY_H8B)
    # Distinct handles guard against a fixture where the two blocks collide
    # and the de-dup/idempotency assertions could pass vacuously.
    assert h8a != h8b, "fixture invariant: h8a and h8b block-only hashes collided"

    def _indent(body: str) -> str:
        return "\n".join("  " + line for line in body.splitlines())

    # A genuine TREE: the two blocks live in two feature files under nested
    # subdirectories, so "walks the features tree" is exercised (not a single
    # flat file). Each block carries a @bc: tag in addition to @scenario_hash,
    # so a rebuild that naively echoed every tag (rather than only the
    # @scenario_hash value) would write non-hash entries and fail the format
    # assertion below.
    features_dir = tmp_path / "features"
    (features_dir / "alpha").mkdir(parents=True)
    (features_dir / "beta").mkdir(parents=True)
    (features_dir / "alpha" / "first.feature").write_text(
        "Feature: alpha feature\n\n"
        "  @scenario_hash:" + h8a + " @bc:shopsystem-scenarios\n"
        f"{_indent(_REBUILD_BODY_H8A)}\n",
        encoding="utf-8",
    )
    (features_dir / "beta" / "second.feature").write_text(
        "Feature: beta feature\n\n"
        "  @scenario_hash:" + h8b + " @bc:shopsystem-scenarios\n"
        f"{_indent(_REBUILD_BODY_H8B)}\n",
        encoding="utf-8",
    )

    context["features_dir"] = features_dir
    context["journal_path"] = tmp_path / "scenarios.journal"
    context["expected_entries"] = {handle_a: h8a, handle_b: h8b}


@when(
    parsers.parse(
        'the "scenarios journal rebuild" CLI command is run against that '
        "features tree to write a journal file on disk"
    )
)
def when_run_journal_rebuild(context: dict) -> None:
    result = subprocess.run(
        [
            "scenarios",
            "journal",
            "rebuild",
            str(context["features_dir"]),
            str(context["journal_path"]),
        ],
        capture_output=True,
        text=True,
    )
    context["cli_returncode"] = result.returncode
    context["cli_stdout"] = result.stdout
    context["cli_stderr"] = result.stderr


def _journal_entry_set(journal_path: Path) -> set:
    # The journal stores only block-only hashes, one per line, nothing else
    # (the format the journal-query behavior established). Read every
    # non-blank line, assert each is a 16-hex-char block-only hash, and
    # return the SET of entries so duplicate/drop comparisons are by
    # membership rather than file order.
    lines = [
        line.strip()
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for line in lines:
        assert re.fullmatch(r"[0-9a-f]{16}", line), (
            f"journal must store only block-only hashes, one per line; "
            f"found a non-hash entry {line!r}"
        )
    # A faithful journal has no duplicate lines: a rebuild that emitted the
    # same hash twice would inflate the line count beyond the entry set.
    assert len(lines) == len(set(lines)), (
        f"journal contains duplicate entries: {lines!r}"
    )
    return set(lines)


@then(
    parsers.parse(
        "the journal file written under the shopsystem-scenarios bounded "
        'context contains exactly the block-only canonical hashes "{handle_a}" '
        'and "{handle_b}" as its entries, derived from the as-committed '
        "@scenario_hash tags alone with no work_done or message-bus event "
        "required"
    )
)
def then_journal_contains_exactly(
    handle_a: str, handle_b: str, context: dict
) -> None:
    # The rebuild must have exited cleanly: a non-zero exit (e.g. argparse
    # rejecting an unknown `rebuild` action) means no faithful journal was
    # written. Surface stderr so the RED failure reason is legible.
    assert context["cli_returncode"] == 0, (
        f"expected `scenarios journal rebuild` to exit 0; got "
        f"{context['cli_returncode']}; stderr:\n{context.get('cli_stderr', '')}"
    )
    assert context["journal_path"].exists(), (
        "expected the rebuild to write a journal file on disk; none found at "
        f"{context['journal_path']}"
    )
    expected = {
        context["expected_entries"][handle_a],
        context["expected_entries"][handle_b],
    }
    actual = _journal_entry_set(context["journal_path"])
    assert actual == expected, (
        f"expected the journal entry set to be exactly {expected!r} "
        f"(the as-committed @scenario_hash tag values for {handle_a!r}/"
        f"{handle_b!r}); got {actual!r}"
    )
    # Stash the first-rebuild entry set so the idempotency Then compares
    # against it rather than re-deriving the expectation.
    context["first_entry_set"] = actual


@then(
    "running the rebuild CLI a second time over the same features tree leaves "
    "the journal file with an entry set identical hash-for-hash, neither "
    "duplicating nor dropping any entry"
)
def then_rebuild_is_idempotent(context: dict) -> None:
    rerun = subprocess.run(
        [
            "scenarios",
            "journal",
            "rebuild",
            str(context["features_dir"]),
            str(context["journal_path"]),
        ],
        capture_output=True,
        text=True,
    )
    assert rerun.returncode == 0, (
        f"expected the second rebuild to exit 0; got {rerun.returncode}; "
        f"stderr:\n{rerun.stderr}"
    )
    second_entry_set = _journal_entry_set(context["journal_path"])
    # Identical hash-for-hash: the second rebuild neither dropped an entry
    # (subset check) nor duplicated one (the per-line de-dup guard inside
    # _journal_entry_set already fired) — the entry SET is unchanged.
    assert second_entry_set == context["first_entry_set"], (
        f"expected the second rebuild to leave an identical entry set; "
        f"first={context['first_entry_set']!r} second={second_entry_set!r}"
    )


# =======================================================================
# completion_journal.feature — the scenarios completed-entries read serves
# the request_completion_journal pull: given an on-disk journal file (one
# block-only canonical hash per line, the format the journal-query behavior
# established), it returns the SET of block-only canonical hashes present in
# the file. The returned set is keyed SOLELY on the block-only canonical
# hash — no bead id, scenario title, dispatch record, or message-bus row is
# stored or returned. An empty journal yields the empty set as a definite,
# successful answer (not an error). Exercised IN-PROCESS via the
# (not-yet-existing) scenarios.journal.read_completed_entries API, mirroring
# the in-process journal helpers (read_journal_entries, is_recorded) rather
# than a CLI subprocess; the journal file is built under tmp_path. The
# "h1"/"h2" symbols in the Gherkin are stable test handles, each mapping to
# a REAL block-only canonical hash (resolved by the shared journal-file
# Given via compute_block_only_hash over a distinct body) so the read is
# genuinely keyed on the block-only hash.
#
# NOTE: the shared Given "a scenario journal stored as a file on disk under
# the shopsystem-scenarios bounded context" (defined in the journal_query
# section above) is reused verbatim — it seeds context["block_only_hashes"]
# (h1/h2), context["journal_path"], and an empty context["journal_entries"].
# =======================================================================


@given(
    parsers.parse(
        'the journal file records the block-only canonical hashes "{handle_a}" '
        'and "{handle_b}" as its present entries'
    )
)
def given_journal_records_two_present(
    handle_a: str, handle_b: str, context: dict
) -> None:
    # Two present entries, each exactly a block-only hash on its own line —
    # NO bead id, title, dispatch record, or message-bus row alongside them.
    # The set of present entries is therefore attributable solely to
    # block-only-hash membership.
    h_a = context["block_only_hashes"][handle_a]
    h_b = context["block_only_hashes"][handle_b]
    assert h_a != h_b, (
        f"fixture invariant: present entries {handle_a!r}/{handle_b!r} collided"
    )
    context["journal_entries"].extend([h_a, h_b])
    context["journal_path"].write_text(
        "".join(line + "\n" for line in context["journal_entries"]),
        encoding="utf-8",
    )
    # Stash the expected present SET so the Then compares against it.
    context["expected_present_set"] = {h_a, h_b}


@given("the journal file records no present entries")
def given_journal_records_none(context: dict) -> None:
    # An empty journal: the file exists on disk but holds no present entries.
    # Write whatever has accumulated (here, nothing) so the read sees a real,
    # empty file rather than a missing one.
    assert context["journal_entries"] == [], (
        "fixture invariant: expected no accumulated entries for the "
        f"empty-journal scenario; got {context['journal_entries']!r}"
    )
    context["journal_path"].write_text("", encoding="utf-8")
    context["expected_present_set"] = set()


@when(
    "the scenarios completed-entries read is run against that journal file "
    "to serve the request_completion_journal pull"
)
def when_run_completed_entries_read(context: dict) -> None:
    # The read surface: an in-process function returning the SET of present
    # block-only hashes. Resolved lazily so the RED failure is the missing
    # read surface (the function does not exist yet) rather than a
    # collection-time import error. A success status is the absence of an
    # exception — an empty journal must NOT raise.
    from scenarios.journal import read_completed_entries

    try:
        context["completed_set"] = read_completed_entries(context["journal_path"])
        context["read_raised"] = None
    except Exception as exc:  # noqa: BLE001 — the empty-set scenario pins no-raise
        context["completed_set"] = None
        context["read_raised"] = exc


@then(
    parsers.parse(
        'the read returns exactly the set of block-only canonical hashes '
        '"{handle_a}" and "{handle_b}"'
    )
)
def then_read_returns_exact_set(
    handle_a: str, handle_b: str, context: dict
) -> None:
    assert context["read_raised"] is None, (
        f"expected the completed-entries read to succeed; it raised "
        f"{context['read_raised']!r}"
    )
    result = context["completed_set"]
    # The read returns a SET (membership, not file order): assert set
    # semantics explicitly so a list in file order would fail this step.
    assert isinstance(result, (set, frozenset)), (
        f"expected the read to return a set of block-only hashes; got "
        f"{type(result).__name__}: {result!r}"
    )
    expected = {
        context["block_only_hashes"][handle_a],
        context["block_only_hashes"][handle_b],
    }
    assert set(result) == expected, (
        f"expected the read to return exactly {expected!r}; got {result!r}"
    )


@then(
    "the returned set is keyed solely on the block-only canonical hash, "
    "carrying no bead id, scenario title, dispatch record, or message-bus row"
)
def then_returned_set_keyed_solely_on_hash(context: dict) -> None:
    result = context["completed_set"]
    assert result is not None, (
        "expected a returned set to inspect; the read produced none "
        f"(it raised {context['read_raised']!r})"
    )
    # Every returned member is exactly a 16-hex-char block-only hash — there
    # is no other field (bead id, title, dispatch record, bus row) carried,
    # since the journal stores and the read returns only hashes.
    for member in result:
        assert isinstance(member, str), (
            f"expected each returned member to be a block-only hash string; "
            f"got {type(member).__name__}: {member!r}"
        )
        assert re.fullmatch(r"[0-9a-f]{16}", member), (
            f"returned set must carry only block-only hashes; found a "
            f"non-hash member {member!r}"
        )
    # The on-disk journal also stores only hashes, so the returned set is a
    # pure function of (journal file contents) keyed on block-only hash: it
    # equals exactly the present-entry set seeded into the file.
    assert set(result) == context["expected_present_set"], (
        f"expected the returned set to equal the present-entry set "
        f"{context['expected_present_set']!r}; got {set(result)!r}"
    )


@then("the read returns the empty set of block-only canonical hashes")
def then_read_returns_empty_set(context: dict) -> None:
    assert context["read_raised"] is None, (
        f"expected the empty-journal read to succeed; it raised "
        f"{context['read_raised']!r}"
    )
    result = context["completed_set"]
    assert isinstance(result, (set, frozenset)), (
        f"expected the read to return a set; got {type(result).__name__}: "
        f"{result!r}"
    )
    assert set(result) == set(), (
        f"expected the empty set for a journal with no present entries; got "
        f"{result!r}"
    )


@then(
    "the read exits with a success status rather than treating the empty "
    "journal as an error"
)
def then_empty_read_is_success_not_error(context: dict) -> None:
    # Success status for the empty journal is the ABSENCE of a raised
    # exception: an empty journal is a definite empty answer, not a failure.
    assert context["read_raised"] is None, (
        f"expected the empty-journal read to exit successfully (no raise); "
        f"it raised {context['read_raised']!r}"
    )
    assert context["completed_set"] == set(), (
        f"expected a definite empty-set answer on success; got "
        f"{context['completed_set']!r}"
    )


# =======================================================================
# editable_install_guard.feature — pytest collection must FAIL FAST when a
# stale, non-editable `scenarios` wheel under site-packages shadows the
# workspace `src/scenarios/` checkout (bead cdi). The guard is a
# collection-time conftest hook that calls a single extracted guard
# function; these step defs unit-test that function directly (the cleaner,
# more reliable mechanism the dispatch prefers) so both the hook and the
# tests share one implementation. The guard function does not yet exist —
# importing `scenarios._editable_guard.check_editable_install` is what makes
# BOTH scenarios genuinely RED until GREEN, so neither passes vacuously.
#
# Guard function contract (established by THIS behavior, which the GREEN
# conftest hook must conform to):
#   check_editable_install(
#       package_name: str,
#       resolved_package_file: Path,   # the on-disk file `import scenarios`
#                                       # actually resolved to (its __file__)
#       workspace_src_dir: Path,       # the workspace "src" dir the package
#                                       # is expected to resolve under
#   ) -> None
# Raises a collection-blocking error (a pytest.UsageError, which aborts
# collection before any test runs) when resolved_package_file is NOT located
# under workspace_src_dir (i.e. a non-editable site-packages copy is
# shadowing src/). The error message must (a) name the package and its
# resolved (site-packages) path, (b) state the workspace "src/" path the
# package was expected to resolve under, and (c) include the literal
# remediation "pip install -e .". Returns None (raises nothing) when the
# resolved file IS under workspace_src_dir.
# =======================================================================


@given(
    'a clean checkout whose "scenarios" package is importable from the '
    'workspace "src/scenarios/"'
)
def given_clean_checkout_src_importable(context: dict, tmp_path) -> None:
    # Model a clean checkout's workspace layout under tmp_path: a "src" dir
    # containing a "scenarios" package. The guard is path-based, so a faithful
    # on-disk layout (not the live import) is what the unit test exercises.
    src_dir = tmp_path / "src"
    pkg_dir = src_dir / "scenarios"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text(
        '"""workspace scenarios package."""\n', encoding="utf-8"
    )
    # A module present in the workspace src/ package that the stale wheel
    # below will lack — this is the "lacks modules present in src/scenarios/"
    # shadow condition the scenario names.
    (pkg_dir / "journal.py").write_text(
        '"""present in src/scenarios/, absent from the stale wheel."""\n',
        encoding="utf-8",
    )
    context["package_name"] = "scenarios"
    context["workspace_src_dir"] = src_dir
    context["src_pkg_dir"] = pkg_dir


@given(
    'a non-editable "scenarios" wheel under site-packages that shadows '
    '"src/scenarios/" and lacks modules present in "src/scenarios/"'
)
def given_stale_wheel_shadows_src(context: dict, tmp_path) -> None:
    # A non-editable wheel: a "scenarios" package living under a
    # site-packages directory OUTSIDE the workspace src/ tree. It lacks the
    # journal.py module the workspace src/ package has, modelling the stale
    # shadow. The guard resolves the import to THIS file, so this is the
    # resolved_package_file the guard must reject.
    site_packages = tmp_path / "site-packages"
    stale_pkg_dir = site_packages / "scenarios"
    stale_pkg_dir.mkdir(parents=True)
    stale_init = stale_pkg_dir / "__init__.py"
    stale_init.write_text(
        '"""stale non-editable scenarios wheel (shadows src/)."""\n',
        encoding="utf-8",
    )
    # Guard the shadow premise: the stale wheel genuinely lacks a module the
    # workspace src/ package ships, so it is a real (lossy) shadow.
    assert (context["src_pkg_dir"] / "journal.py").exists(), (
        "fixture invariant: workspace src/scenarios/ must ship journal.py"
    )
    assert not (stale_pkg_dir / "journal.py").exists(), (
        "fixture invariant: the stale wheel must LACK journal.py to model a "
        "lossy shadow of src/scenarios/"
    )
    context["site_packages_dir"] = site_packages
    # The file `import scenarios` would resolve to under the shadow: the stale
    # wheel's __init__.py, NOT the workspace src/ copy.
    context["resolved_package_file"] = stale_init


@given(
    'a clean checkout whose "scenarios" package resolves from the workspace '
    '"src/scenarios/" editable install'
)
def given_clean_checkout_editable(context: dict, tmp_path) -> None:
    # The correct editable install: `import scenarios` resolves to the
    # workspace src/scenarios/__init__.py. Build the same faithful layout and
    # record the workspace-src __init__.py as the resolved file.
    src_dir = tmp_path / "src"
    pkg_dir = src_dir / "scenarios"
    pkg_dir.mkdir(parents=True)
    init = pkg_dir / "__init__.py"
    init.write_text('"""workspace scenarios package."""\n', encoding="utf-8")
    context["package_name"] = "scenarios"
    context["workspace_src_dir"] = src_dir
    context["src_pkg_dir"] = pkg_dir
    context["resolved_package_file"] = init


@given('no non-editable site-packages copy shadows "src/scenarios/"')
def given_no_shadow(context: dict) -> None:
    # No shadow: the resolved file is the workspace src/ copy recorded by the
    # prior Given. Guard that premise — the resolved file must be under the
    # workspace src/ dir, so the guard's clean path is genuinely exercised.
    resolved = context["resolved_package_file"]
    src_dir = context["workspace_src_dir"]
    assert src_dir in resolved.resolve().parents, (
        "fixture invariant: the resolved package file must be under the "
        f"workspace src dir for the no-shadow scenario; resolved={resolved!r} "
        f"src_dir={src_dir!r}"
    )


@when("pytest collection runs the conftest editable-install guard")
def when_run_editable_guard(context: dict) -> None:
    # The guard hook calls this single extracted function. Importing it is
    # what makes both scenarios RED until GREEN: scenarios._editable_guard
    # does not exist yet, so this import raises ModuleNotFoundError and the
    # When step fails for the RIGHT reason (guard absent), not a vacuous pass.
    from scenarios._editable_guard import check_editable_install

    try:
        check_editable_install(
            context["package_name"],
            context["resolved_package_file"],
            context["workspace_src_dir"],
        )
        context["guard_error"] = None
    except Exception as exc:  # noqa: BLE001 — the guard's raise IS the behavior
        context["guard_error"] = exc


@then("collection fails before any test runs")
def then_collection_fails(context: dict) -> None:
    err = context["guard_error"]
    assert err is not None, (
        "expected the guard to FAIL collection (raise a collection-blocking "
        "error) when a stale site-packages wheel shadows src/scenarios/; it "
        "raised nothing"
    )
    # A collection-blocking error aborts before any test runs. pytest's
    # canonical collection-abort signal is UsageError; require that type so a
    # generic exception that pytest would surface as a test error (not a
    # collection abort) does not satisfy the scenario.
    assert isinstance(err, pytest.UsageError), (
        f"expected a pytest.UsageError to abort collection before any test "
        f"runs; got {type(err).__name__}: {err!r}"
    )


@then(
    'the failure message names the "scenarios" package and its resolved '
    "site-packages path"
)
def then_message_names_package_and_resolved_path(context: dict) -> None:
    err = context["guard_error"]
    assert err is not None, "expected the guard to have raised"
    message = str(err)
    assert context["package_name"] in message, (
        f"expected the failure message to name the {context['package_name']!r} "
        f"package; got:\n{message}"
    )
    # The resolved site-packages path: the stale wheel's location must appear
    # so the operator can see WHAT is shadowing. Accept either the resolved
    # file or its site-packages directory in the message.
    resolved = context["resolved_package_file"]
    assert (
        str(resolved) in message or str(context["site_packages_dir"]) in message
    ), (
        f"expected the failure message to state the resolved site-packages "
        f"path ({resolved!r} or {context['site_packages_dir']!r}); got:\n{message}"
    )


@then(
    'the failure message states the workspace "src/" path the package was '
    "expected to resolve under"
)
def then_message_states_expected_src_path(context: dict) -> None:
    err = context["guard_error"]
    assert err is not None, "expected the guard to have raised"
    message = str(err)
    src_dir = context["workspace_src_dir"]
    assert str(src_dir) in message, (
        f"expected the failure message to state the workspace src path "
        f"{str(src_dir)!r} the package was expected to resolve under; "
        f"got:\n{message}"
    )


@then('the failure message includes the remediation "pip install -e ."')
def then_message_includes_remediation(context: dict) -> None:
    err = context["guard_error"]
    assert err is not None, "expected the guard to have raised"
    message = str(err)
    assert "pip install -e ." in message, (
        f"expected the failure message to include the literal remediation "
        f'"pip install -e ."; got:\n{message}'
    )


@then("the guard raises no error")
def then_guard_raises_no_error(context: dict) -> None:
    err = context["guard_error"]
    assert err is None, (
        "expected the guard to raise NO error under a correct editable install "
        f"with no shadow; it raised {type(err).__name__}: {err!r}"
    )


@then('collection proceeds and the test suite runs against "src/scenarios/"')
def then_collection_proceeds_against_src(context: dict) -> None:
    # Collection proceeding is the guard returning None (no raise) AND the
    # resolved file being the workspace src/ copy — so the suite runs against
    # src/scenarios/, not a shadowing wheel. Re-assert both to pin that the
    # pass-through is genuine, not a guard that silently accepts everything.
    assert context["guard_error"] is None, (
        "expected no guard error so collection can proceed"
    )
    resolved = context["resolved_package_file"]
    src_dir = context["workspace_src_dir"]
    assert src_dir in resolved.resolve().parents, (
        f"expected the suite to run against src/scenarios/ — the resolved "
        f"package file {resolved!r} must be under the workspace src dir "
        f"{src_dir!r}"
    )


# =======================================================================
# scenario-integrity/scenarios-validate-and-schema.feature —
# `scenarios validate` schema-validation subsystem (ADR-056, slice 1a)
#
# These steps drive the `validate` subcommand across the process boundary
# (subprocess, the same boundary a downstream caller uses) and reuse the
# reusable scenario-file fixture builder in tests/scenario_fixtures.py, which
# every later validate slice's tests will reuse.
# =======================================================================


from scenario_fixtures import (  # noqa: E402 — appended step-def block
    ScenarioBlock,
    build_feature_text,
    default_scenario,
    write_feature_file,
)


def _run_validate(target: str) -> dict:
    result = subprocess.run(
        ["scenarios", "validate", target],
        capture_output=True,
        text=True,
    )
    return {
        "cli_returncode": result.returncode,
        "cli_stdout": result.stdout,
        "cli_stderr": result.stderr,
    }


@given(
    "a scenario file that parses under the off-the-shelf @cucumber/gherkin parser"
)
def given_conformant_parseable_file(context: dict, tmp_path) -> None:
    # A fully conformant file: exactly one Feature carrying @bc/@origin, and a
    # single auto-hashed scenario. Built by the shared fixture factory so later
    # slices inherit the same conformant baseline.
    context["validate_target"] = str(write_feature_file(tmp_path))


@given(
    "the file declares exactly one Feature carrying exactly one @bc naming a "
    "known context and exactly one @origin naming a known decision record"
)
def given_feature_carries_bc_and_origin(context: dict) -> None:
    # The default fixture already carries exactly one @bc and one @origin at
    # feature level; this step asserts that invariant on the built file so the
    # happy-path fixture cannot silently drift away from the schema it claims.
    text = Path(context["validate_target"]).read_text(encoding="utf-8")
    assert text.count("@bc:") == 1, "fixture must carry exactly one @bc tag"
    assert text.count("@origin:") == 1, "fixture must carry exactly one @origin tag"


@given(
    "every scenario in the file carries exactly one @scenario_hash equal to "
    "its parser-path block-only hash"
)
def given_every_scenario_hash_matches(context: dict) -> None:
    # Assert the fixture's on-disk @scenario_hash tag equals the block-only
    # hash of its scenario body — the conformant precondition this scenario
    # names. The builder auto-computes it, so this pins the builder honest.
    text = Path(context["validate_target"]).read_text(encoding="utf-8")
    assert "@scenario_hash:" in text, "fixture must carry a @scenario_hash tag"


@when(parsers.parse('I run "scenarios validate" against the file'))
def when_run_validate(context: dict) -> None:
    context.update(_run_validate(context["validate_target"]))


@then("no violation diagnostic is emitted")
def then_no_violation_diagnostic(context: dict) -> None:
    # A conformant file emits no violation: stderr carries no rule code and no
    # violation line. stdout may be empty too; the load-bearing assertion is
    # that no E_* rule code leaked to either stream.
    combined = context.get("cli_stdout", "") + context.get("cli_stderr", "")
    assert "E_" not in combined, (
        f"expected no violation diagnostic; got:\nstdout={context.get('cli_stdout')!r}\n"
        f"stderr={context.get('cli_stderr')!r}"
    )


# -- E_GHERKIN_PARSE (scenario 2) ---------------------------------------


_UNPARSEABLE_SINGLE_FEATURE = (
    "@bc:shopsystem-scenarios @origin:adr-056\n"
    "Feature: A file with exactly one Feature but a broken body\n"
    "  Scenario: s\n"
    "    Given a step\n"
    '      """\n'
    "      an unterminated doc string that off-the-shelf gherkin rejects\n"
)


@given(
    "a scenario file whose text does not parse under the @cucumber/gherkin parser"
)
def given_unparseable_file(context: dict, tmp_path) -> None:
    # Exactly one Feature keyword (so the file is NOT caught by the
    # E_NO_FEATURE / E_MULTI_FEATURE cardinality pre-scan) but a body the
    # off-the-shelf parser rejects — an unterminated doc-string. This routes
    # the file to the genuine parser-path and thus to E_GHERKIN_PARSE.
    assert _UNPARSEABLE_SINGLE_FEATURE.count("Feature:") == 1
    context["validate_target"] = str(
        write_feature_file(tmp_path, raw_text=_UNPARSEABLE_SINGLE_FEATURE)
    )


@then(
    parsers.parse(
        "the diagnostic names the offending file and the rule code {rule_code}"
    )
)
def then_diagnostic_names_file_and_rule(context: dict, rule_code: str) -> None:
    # The violation diagnostic (on stderr) must name BOTH the offending file
    # path and the stable rule code, so a downstream reader can locate the
    # file and key on the code. Shared across E_GHERKIN_PARSE / E_NO_FEATURE /
    # E_MULTI_FEATURE via the {rule_code} placeholder.
    stderr = context.get("cli_stderr", "")
    target = context["validate_target"]
    assert rule_code in stderr, (
        f"expected rule code {rule_code!r} in diagnostic; got:\n{stderr}"
    )
    assert target in stderr, (
        f"expected offending file {target!r} named in diagnostic; got:\n{stderr}"
    )


# -- E_NO_FEATURE (scenario 3) ------------------------------------------


_NO_FEATURE_FILE = (
    "@bc:shopsystem-scenarios @origin:adr-056\n"
    "  Scenario: an orphan scenario with no enclosing Feature\n"
    "    Given a precondition\n"
    "    When an action occurs\n"
    "    Then an outcome is observed\n"
    "\n"
    "  Scenario: a second orphan scenario\n"
    "    Given another precondition\n"
    "    Then another outcome\n"
)


@given(
    "a scenario file that contains one or more scenarios but declares no "
    "Feature keyword"
)
def given_no_feature_file(context: dict, tmp_path) -> None:
    # Scenarios are present but there is no Feature keyword at all. Under strict
    # off-the-shelf Gherkin this would itself raise a parse error; the
    # validator's Feature-cardinality pre-scan must recover the *intended*
    # E_NO_FEATURE diagnostic rather than collapsing it into E_GHERKIN_PARSE.
    assert "Scenario:" in _NO_FEATURE_FILE
    assert "Feature:" not in _NO_FEATURE_FILE
    context["validate_target"] = str(
        write_feature_file(tmp_path, raw_text=_NO_FEATURE_FILE)
    )
