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
# release_workflow.feature — the BC's own release pipeline fans out a
# repository_dispatch to bc-launcher on a version-tag release. Unlike the
# CLI/canonicalization scenarios this pins a property of a shipped
# artifact (the workflow YAML), so the steps read .github/workflows/ from
# disk and assert structurally rather than exercising runtime behaviour.
# =======================================================================


_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"
_DISPATCH_TARGET = "dstengle/shopsystem-bc-launcher"
_DISPATCH_TOKEN_SECRET = "BC_LAUNCHER_DISPATCH_TOKEN"


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


@given("the shopsystem-scenarios framework-utility source repository")
def given_source_repository(context: dict) -> None:
    # The repository under test is this checkout; the scenario inspects
    # the artifacts it ships rather than any installed package.
    assert _REPO_ROOT.is_dir(), f"repo root not found: {_REPO_ROOT}"
    context["repo_root"] = _REPO_ROOT


@when(parsers.parse('its release workflow file under "{workflows_dir}" is inspected'))
def when_inspect_workflow_files(workflows_dir: str, context: dict) -> None:
    # Load every workflow definition under .github/workflows/ so the Then
    # steps can select the release workflow by its trigger rather than by
    # a hard-coded filename. The path comes from the Gherkin so the step
    # and the prose stay in sync.
    target_dir = context["repo_root"] / workflows_dir
    assert target_dir.is_dir(), (
        f"expected a workflows directory at {target_dir}; none found"
    )
    workflows = []
    for path in sorted(target_dir.glob("*.y*ml")):
        raw = path.read_text(encoding="utf-8")
        workflows.append((path, raw, yaml.safe_load(raw)))
    assert workflows, f"no workflow files found under {target_dir}"
    context["workflows"] = workflows


@then(parsers.parse('the workflow declares a trigger on push of tags matching "{pattern}"'))
def then_workflow_triggers_on_tag_push(pattern: str, context: dict) -> None:
    # The release workflow is the one whose push.tags would match a
    # v-prefixed version tag (e.g. v1.2.3) via the declared glob. Select
    # it here so the remaining Then steps inspect that same workflow.
    matches = []
    for path, raw, parsed in context["workflows"]:
        for tag_glob in _push_tag_patterns(parsed):
            if tag_glob == pattern and fnmatch.fnmatch("v1.2.3", tag_glob):
                matches.append((path, raw, parsed))
                break
    assert matches, (
        f"no workflow under .github/workflows/ declares a push trigger on "
        f"tags matching {pattern!r}; "
        f"inspected: {[p.name for p, _, _ in context['workflows']]}"
    )
    assert len(matches) == 1, (
        f"expected exactly one release workflow with a {pattern!r} tag "
        f"trigger; found {[p.name for p, _, _ in matches]}"
    )
    context["release_workflow"] = matches[0]


@then(
    parsers.parse(
        'the workflow contains a step that performs a "{dispatch_type}" '
        'targeting the "{target}" repository, satisfied by either a REST '
        "call to the GitHub repository-dispatches API or a use of the "
        '"{action}" action'
    )
)
def then_workflow_dispatches_to_target(
    dispatch_type: str, target: str, action: str, context: dict
) -> None:
    assert dispatch_type == "repository_dispatch", dispatch_type
    assert target == _DISPATCH_TARGET, target
    _, _, parsed = context["release_workflow"]
    dispatch_steps = [s for s in _iter_steps(parsed) if _step_targets_dispatch(s)]
    assert dispatch_steps, (
        f"release workflow declares no step performing a {dispatch_type} to "
        f"{target!r} (via the {action!r} action or a REST call to the "
        "repository-dispatches API)"
    )
    assert len(dispatch_steps) == 1, (
        f"expected exactly one dispatch step targeting {target!r}; "
        f"found {len(dispatch_steps)}"
    )
    context["dispatch_step"] = dispatch_steps[0]


@then(parsers.parse('that step references the secret "{secret}" as the dispatch token'))
def then_dispatch_step_references_secret(secret: str, context: dict) -> None:
    assert secret == _DISPATCH_TOKEN_SECRET, secret
    step = context["dispatch_step"]
    # Serialise the matched step back to text so the assertion covers the
    # secret reference wherever it lives — `with.token` for the action
    # form, or an Authorization header / env var for the REST form.
    step_text = yaml.safe_dump(step)
    needle = f"secrets.{secret}"
    assert needle in step_text, (
        f"dispatch step does not reference the secret {secret!r} "
        f"(expected a ${{{{ {needle} }}}} reference); step was:\n{step_text}"
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
