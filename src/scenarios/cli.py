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
    tags [FILE]
        Reads a feature file (FILE, or stdin when omitted) and writes the
        distinct @-tags carried by its scenarios, one tag per line, in
        first-seen file order. A tag carried by more than one scenario is
        emitted exactly once.
    journal query JOURNAL-FILE BLOCK-ONLY-HASH
        Reads an on-disk journal file (one block-only canonical hash per
        line) and writes a definite ``yes``/``no`` line to stdout reporting
        whether BLOCK-ONLY-HASH is recorded. Exits 0 in both cases —
        success means "answered", not "found".
    journal rebuild FEATURES-TREE JOURNAL-FILE
        Walks FEATURES-TREE recursively, harvests the as-committed
        ``@scenario_hash`` tag values alone (no recomputation), and writes
        them to JOURNAL-FILE in the journal format (one block-only canonical
        hash per line). Idempotent: re-running over the same tree leaves an
        identical entry set hash-for-hash.
"""
from __future__ import annotations

import argparse
import sys

from scenarios.feature import iter_scenarios, iter_tags
from scenarios.hash import compute_scenario_hash
from scenarios.journal import (
    harvest_features_tree,
    is_recorded,
    write_journal_entries,
)


def _read_scenario_stdin(command: str) -> str | None:
    # `hash` and `verify` both canonicalize a scenario body piped on stdin.
    # Empty (or whitespace-only) stdin is a caller error — not a scenario
    # whose hash is the SHA-256-of-empty-string (e3b0c44...). Hashing it
    # silently would emit a value indistinguishable from a real scenario
    # hash and let `verify` report a confident false "hash mismatch", a
    # silent false negative that can mislead a reviewer into a false gate
    # block (bead uh7). Return None to signal the caller should error out.
    gherkin_text = sys.stdin.read()
    if not gherkin_text.strip():
        print(
            f"scenarios {command}: no scenario text on stdin "
            "(pipe a Gherkin scenario body in)",
            file=sys.stderr,
        )
        return None
    return gherkin_text


def _cmd_hash(args: argparse.Namespace) -> int:
    gherkin_text = _read_scenario_stdin("hash")
    if gherkin_text is None:
        return 2
    print(compute_scenario_hash(gherkin_text))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    gherkin_text = _read_scenario_stdin("verify")
    if gherkin_text is None:
        return 2
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


def _cmd_tags(args: argparse.Namespace) -> int:
    # Distinct @-tags, one per line, in first-seen file order. A dict over
    # the tag iterator de-duplicates while preserving insertion order, so
    # the output is deterministic (not subject to set-iteration order) and
    # a tag repeated across scenarios collapses to a single line.
    seen: dict[str, None] = {}
    for tag in iter_tags(_read_feature(args)):
        seen.setdefault(tag, None)
    for tag in seen:
        print(tag)
    return 0


def _cmd_journal_query(args: argparse.Namespace) -> int:
    # A definite yes/no, keyed solely on block-only-hash membership in the
    # journal file. Both answers are success (exit 0): the command answers a
    # question, it does not signal "found" via exit status. The journal
    # stores only hashes, so there is no bead id, title, dispatch record, or
    # message-bus row to key on.
    print("yes" if is_recorded(args.journal_file, args.block_only_hash) else "no")
    return 0


def _cmd_journal_rebuild(args: argparse.Namespace) -> int:
    # Harvest the as-committed @scenario_hash tag values across the features
    # tree (no recomputation, no work_done or message-bus event) and write
    # them as the journal file. write_journal_entries de-duplicates and emits
    # a stable sorted order, so re-running over the same tree leaves an entry
    # set identical hash-for-hash — the idempotency the behavior requires.
    write_journal_entries(
        args.journal_file, harvest_features_tree(args.features_tree)
    )
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

    tags_cmd = sub.add_parser(
        "tags",
        help="print the distinct @-tags across a feature file, one per line",
    )
    tags_cmd.add_argument(
        "file",
        nargs="?",
        default=None,
        help="feature file to read; reads stdin when omitted",
    )
    tags_cmd.set_defaults(func=_cmd_tags)

    # `journal` is a two-level command group: its own action subparser hangs
    # off it so future journal actions (e.g. rebuild) share the namespace.
    journal_cmd = sub.add_parser(
        "journal",
        help="operate on the on-disk scenario journal of serviced block-only hashes",
    )
    journal_sub = journal_cmd.add_subparsers(dest="journal_action", required=True)

    journal_query_cmd = journal_sub.add_parser(
        "query",
        help="report yes/no whether a block-only hash is recorded in a journal file",
    )
    journal_query_cmd.add_argument(
        "journal_file",
        help="path to the journal file (one block-only canonical hash per line)",
    )
    journal_query_cmd.add_argument(
        "block_only_hash",
        help="block-only canonical hash to test for membership in the journal file",
    )
    journal_query_cmd.set_defaults(func=_cmd_journal_query)

    journal_rebuild_cmd = journal_sub.add_parser(
        "rebuild",
        help="rebuild a journal file from a features tree's @scenario_hash tags",
    )
    journal_rebuild_cmd.add_argument(
        "features_tree",
        help="root of the features tree to harvest @scenario_hash tags from",
    )
    journal_rebuild_cmd.add_argument(
        "journal_file",
        help="path to the journal file to write (one block-only hash per line)",
    )
    journal_rebuild_cmd.set_defaults(func=_cmd_journal_rebuild)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
