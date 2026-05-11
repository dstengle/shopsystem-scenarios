# shopsystem-scenarios

Scenarios bounded context of the shopsystem framework. Owns the Gherkin
canonicalization rule, the scenario hash function, and the `scenarios` CLI.

The canonicalization rule is part of the **scenario** contract, not the
messaging contract. Messages happen to carry scenarios; messages do not
define what a scenario is. Keeping this separate from the messaging
package lets the hash invariant live alongside the rule that produces it.

## Install

```bash
pip install "git+https://github.com/dstengle/shopsystem-scenarios@v0.1.0"
```

## Usage

The `scenarios` CLI is stdin-driven and composable in shell pipelines.

```bash
# Compute the canonical hash of a Gherkin scenario.
cat scenario.feature | scenarios hash
# => 3f123ba774758ff2

# Verify a hash matches. Exits 0 on match, non-zero with stderr on mismatch.
cat scenario.feature | scenarios verify --hash 3f123ba774758ff2
```

Library use:

```python
from scenarios.hash import compute_scenario_hash

compute_scenario_hash(gherkin_text)  # -> 16 hex chars
```

The canonicalization rule strips per-line whitespace, drops blank lines,
and ignores any line starting with `@scenario_hash:` so embedding the
hash as a Gherkin tag does not perturb subsequent recomputation.

## Context

This repo is one of four BC-aligned packages split out of the original
shopsystem framework prototype. See ADR-001 for the split rationale,
dependency direction, and migration sequencing:
<https://github.com/dstengle/ddd-product-system/blob/main/docs/shop-system/adr-001-framework-packaging.md>

(ADR-001 will migrate to `shopsystem-product` once docs extraction lands;
the link above is canonical until then.)
