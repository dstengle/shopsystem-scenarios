---
name: work-done-gate
description: Pre-emission gate that must pass before any work_done --status complete is sent; converts to --status blocked with named evidence on any failure
---

# Work-Done Gate

## Overview

Before any `work_done --status complete` emission, run this gate. Three checks must all pass. Any single failure converts the emission to `work_done --status blocked` with named evidence — no exceptions, no partial passes.

This gate is the enforcement point for ADR-010 and the integration contract. It is not optional.

## The Three Pre-Emit Checks

### Check 1: Clean Working Tree

```bash
git status --porcelain
```

**Pass:** output is empty (no modified, staged, untracked, or deleted files).

**Fail:** any output from `git status --porcelain`.

Evidence on failure: show the full `git status --porcelain` output. Name the dirty paths. Block with message:
```
blocked: dirty working tree at emission time. Paths: <list from git status --porcelain>
```

A dirty tree at `work_done` time means uncommitted work may be present. The BC must commit or discard all changes before emitting.

### Check 2: work_id Commit Reachable from `origin/main`

```bash
git fetch origin
git log origin/main -E --grep="\b<work_id>\b" --oneline
```

**Pass:** the search finds the work_id as a WHOLE TOKEN in at least one commit on `origin/main`.

**Fail:** no match.

Evidence on failure: show the current `origin/main` HEAD SHA and the work_id searched for. Block with message:
```
blocked: work_id <work_id> not reachable from origin/main (HEAD: <sha>). Run integrating-to-main.
```

**Why `git fetch origin` first:** the local ref `origin/main` may be stale. Fetching before the check ensures the gate sees the actual remote state. Skipping the fetch is a false pass.

The attribution mechanism: the work_id must appear as a WHOLE TOKEN (exact / word-boundary match — bounded by start/end-of-line or non-identifier characters) in the commit subject or body, NOT as a loose substring. A work_id that is a strict PREFIX of another commit's work_id (e.g. `lead-8v` as a prefix of `lead-8vwf`) therefore does NOT match — loose substring matching false-positive-attributes the wrong commit's lineage. Tags and `git notes` naming exactly the work_id are also acceptable, but the word-boundary `git log -E --grep="\b<work_id>\b"` form is the canonical check (it is the same canonical attribution form the executable `bc-emit work-done` wrapper applies in both its commit-mode and tag-mode reachability checks).

### Check 3: Scenario Hash Integrity (ADR-010)

ADR-010 rule: `work_done.scenario_hashes` must be a **subset** of the `@scenario_hash:` tags actually pinned in the BC's `features/`.

Three sub-steps:

**3a. Recompute hashes.**
```bash
scenarios hash features/<scenario_file>.feature
```
Recompute the canonical hash for each scenario you intend to include in `work_done`. Do not trust hashes from memory or prior conversation turns.

**3b. Confirm presence via git grep.**
```bash
git grep "@scenario_hash:<hash>" features/
```
Each hash you intend to report must appear in the committed `features/` tree. If a hash is not present in `features/`, it was never pinned — you cannot claim it.

**3c. Enforce subset rule.**
The set of hashes you pass (one repeatable `--scenario-hash <hash>` flag per hash) must be a strict subset of the hashes found in `features/`. You may report fewer than all pinned hashes (partial delivery is valid); you must not report a hash not in `features/`.

**Pass:** all three sub-steps pass for every hash in the intended `work_done` payload.

**Fail:** any hash not found via `git grep`, or any hash whose recomputed value does not match.

Evidence on failure: name the mismatched or missing hash, the path searched, and the `origin/main` short SHA. Block with message:
```
blocked: scenario_hash <hash> not found in features/ (searched: features/; origin/main: <sha>). Scenario may not be pinned.
```

### Check 4: BD Plan Sub-Issues Present and Closed

Verify that bd sub-issues exist for the work_id and that they are all closed.
Specifically, at least one sub-issue must be an explicit failing-test (RED)
sub-issue (title contains "write the failing test for" or similar RED
nomenclature).

```bash
bd show <work_id>   # inspect sub-issues: status and titles
```

**Pass:** at least one RED sub-issue exists and all sub-issues are closed.

**Fail:** no sub-issues exist for the work_id, or sub-issues are not all
closed, or no RED (failing-test) sub-issue is present.

Evidence on failure: list the open sub-issues and note the missing RED
sub-issue. Block with message:
```
blocked: no bd plan sub-issues for <work_id>
```
or:
```
blocked: bd sub-issue(s) not closed for <work_id>: <list>
```

### Check 5: Test-First Artifact in Work-Branch History

For each behavior, verify that a `test(red): <behavior>` commit precedes its
`feat(green): <behavior>` commit in the work-branch history:

```bash
git log --oneline bc/<work_id>   # inspect commit sequence
```

For each behavior, locate the `test(red)` commit and the `feat(green)` commit.
The `test(red)` commit must appear **earlier** in the log (i.e., was authored
before the `feat(green)` commit).

**Pass:** for every behavior with a `feat(green)` commit, a `test(red)` commit
for the same behavior appears before it in the branch history.

**Fail:** any `feat(green)` commit has no corresponding `test(red)` commit, or
the `test(red)` commit appears after `feat(green)`.

Evidence on failure: name the behavior, the `feat(green)` commit SHA, and
the missing or mis-ordered `test(red)` commit. Block with message:
```
blocked: no test-first commit sequence for <behavior>
```

## Gate Summary Table

| Check | Command | Pass condition | Fail → blocked evidence |
|---|---|---|---|
| Clean working tree | `git status --porcelain` | empty output | dirty paths list |
| work_id reachable | `git fetch origin && git log origin/main -E --grep="\b<work_id>\b" --oneline` | whole-token match | work_id + origin/main HEAD SHA |
| Scenario hash subset | `scenarios hash` + `git grep` | all hashes in features/ | mismatched hash + path + SHA |
| BD plan sub-issues | `bd show <work_id>` | sub-issue(s) exist, all closed, ≥1 RED | "no bd plan sub-issues for <work_id>" |
| Test-first artifact | `git log --oneline bc/<work_id>` | test(red) precedes feat(green) per behavior | "no test-first commit sequence for <behavior>" |

## Failure Converts Complete to Blocked

When any check fails, do not emit `work_done --status complete`. Emit:

```bash
shop-msg respond work_done \
  --bc <name> \
  --work-id <work_id> \
  --status blocked \
  --summary "<named evidence from the failing check>"
```

Named evidence means: specific paths, the work_id value, the `origin/main` short SHA, and the specific check that failed. Vague evidence ("something went wrong") is not acceptable.

## After Resolving a Block

Fix the underlying cause (clean tree → commit or discard; unreachable commit → run `integrating-to-main`; hash mismatch → pin or correct the scenario), then re-run the full gate from Check 1. Do not skip checks that previously passed — run all three again.
