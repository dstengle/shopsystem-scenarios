"""scenarios CLI entry point.

Subcommands:
    hash
        Reads Gherkin text from stdin and writes the canonical hash
        (16 hex chars, no trailing newline beyond what `print` adds)
        to stdout. Mirrors the unix idiom: composable, language-
        agnostic from the caller's perspective.
    verify --hash HASH
        Reads Gherkin text from stdin and exits 0 if the canonical hash
        of the body equals HASH; exits non-zero with a stderr message
        otherwise. Mirrors the unix idiom: silent on success, message
        on stderr on failure. Pins the canonicalization-stability
        contract from a third party's perspective.
    list [FILE]
        Reads a feature file (FILE, or stdin when omitted) and writes one
        ``<scenario_hash>\\t<title>`` line per scenario, pairing each
        scenario's title with the @scenario_hash tag value preceding it.
"""
from __future__ import annotations

import argparse
import sys

from scenarios.feature import iter_scenarios
from scenarios.hash import compute_scenario_hash


def _cmd_hash(args: argparse.Namespace) -> int:
    gherkin_text = sys.stdin.read()
    print(compute_scenario_hash(gherkin_text))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    gherkin_text = sys.stdin.read()
    actual = compute_scenario_hash(gherkin_text)
    if actual == args.hash:
        return 0
    print(
        f"scenarios verify: hash mismatch (expected {args.hash}, computed {actual})",
        file=sys.stderr,
    )
    return 1


def _cmd_list(args: argparse.Namespace) -> int:
    if args.file is None:
        feature_text = sys.stdin.read()
    else:
        with open(args.file, encoding="utf-8") as handle:
            feature_text = handle.read()
    for scenario_hash, title in iter_scenarios(feature_text):
        # A scenario without a preceding @scenario_hash tag prints an empty
        # hash column rather than being dropped, so the listing stays a
        # faithful one-line-per-scenario view of the file.
        print(f"{scenario_hash or ''}\t{title}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scenarios")
    sub = parser.add_subparsers(dest="command", required=True)

    hash_cmd = sub.add_parser(
        "hash",
        help="canonicalize Gherkin from stdin and emit the scenario hash",
    )
    hash_cmd.set_defaults(func=_cmd_hash)

    verify_cmd = sub.add_parser(
        "verify",
        help="check that a hash matches the canonical hash of Gherkin from stdin",
    )
    verify_cmd.add_argument(
        "--hash",
        required=True,
        help="expected scenario hash to verify against the canonicalized stdin body",
    )
    verify_cmd.set_defaults(func=_cmd_verify)

    list_cmd = sub.add_parser(
        "list",
        help="list each scenario's title paired with its @scenario_hash value",
    )
    list_cmd.add_argument(
        "file",
        nargs="?",
        default=None,
        help="feature file to list; reads stdin when omitted",
    )
    list_cmd.set_defaults(func=_cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
