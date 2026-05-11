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

import re
import subprocess

import pytest
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
