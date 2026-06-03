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
    count [FILE]
        Reads a feature file (FILE, or stdin when omitted) and writes the
        number of scenarios it contains as a single line.
    titles [FILE]
        Reads a feature file (FILE, or stdin when omitted) and writes one
        line per scenario carrying just that scenario's title, in file
        order. Unlike ``list``, no @scenario_hash column is emitted.
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


def _read_feature(args: argparse.Namespace) -> str:
    # `list`, `count`, and `titles` share the same source contract: a
    # positional FILE, or stdin when omitted. Keep that resolution in one
    # place so the subcommands cannot drift apart.
    if args.file is None:
        return sys.stdin.read()
    with open(args.file, encoding="utf-8") as handle:
        return handle.read()


def _cmd_list(args: argparse.Namespace) -> int:
    for scenario_hash, title in iter_scenarios(_read_feature(args)):
        # A scenario without a preceding @scenario_hash tag prints an empty
        # hash column rather than being dropped, so the listing stays a
        # faithful one-line-per-scenario view of the file.
        print(f"{scenario_hash or ''}\t{title}")
    return 0


def _cmd_count(args: argparse.Namespace) -> int:
    count = sum(1 for _ in iter_scenarios(_read_feature(args)))
    print(count)
    return 0


def _cmd_titles(args: argparse.Namespace) -> int:
    # One title per line, in file order. `titles` is the hash-free sibling
    # of `list`: it discards the @scenario_hash column entirely, so the
    # output is a clean title listing usable directly by downstream callers.
    for _scenario_hash, title in iter_scenarios(_read_feature(args)):
        print(title)
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

    count_cmd = sub.add_parser(
        "count",
        help="print the number of scenarios in a feature file",
    )
    count_cmd.add_argument(
        "file",
        nargs="?",
        default=None,
        help="feature file to count; reads stdin when omitted",
    )
    count_cmd.set_defaults(func=_cmd_count)

    titles_cmd = sub.add_parser(
        "titles",
        help="print each scenario's title, one per line, in file order",
    )
    titles_cmd.add_argument(
        "file",
        nargs="?",
        default=None,
        help="feature file to read; reads stdin when omitted",
    )
    titles_cmd.set_defaults(func=_cmd_titles)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
