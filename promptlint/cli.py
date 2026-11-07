"""Static analysis for prompts.

    promptlint .                       # report
    promptlint . --fail-on high        # CI gate
    promptlint prompts/ --json         # machine readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import rules
from .lint import lint_path
from .models import HIGH, LOW, MEDIUM, SEVERITY_ORDER, LintError

EXIT_CLEAN = 0
EXIT_FOUND = 1
EXIT_ERROR = 2


def _print_report(report, *, show_excerpt: bool) -> None:
    for finding in report.sorted():
        print(f"[{finding.severity:>6}] {finding.rule:<24} {finding.path}:{finding.line}")
        print(f"         {finding.message}")
        if show_excerpt and finding.excerpt:
            print(f"         > {finding.excerpt}")
        print()

    counts = {level: report.count_at_or_above(level) for level in (HIGH, MEDIUM, LOW)}
    total = len(report.findings)
    if not total:
        print(f"no findings across {report.blocks_scanned} prompt block(s)")
        return
    print(
        f"{total} finding(s) across {report.blocks_scanned} prompt block(s): "
        f"{counts[HIGH]} high, {counts[MEDIUM]} at or above medium, "
        f"{counts[LOW]} total"
    )


def cmd_lint(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()
    if not path.exists():
        print(f"error: no such path: {path}", file=sys.stderr)
        return EXIT_ERROR

    enabled = list(rules.RULE_IDS)
    disabled = [rule for group in (args.disable or []) for rule in group]
    if disabled:
        unknown = [rule for rule in disabled if rule not in rules.RULE_IDS]
        if unknown:
            print(
                f"error: unknown rule(s) {unknown}; known: {list(rules.RULE_IDS)}",
                file=sys.stderr,
            )
            return EXIT_ERROR
        enabled = [rule for rule in enabled if rule not in disabled]

    try:
        report = lint_path(path, enabled)
    except LintError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(
            {
                "blocks_scanned": report.blocks_scanned,
                "count": len(report.findings),
                "findings": [
                    {
                        "rule": f.rule,
                        "severity": f.severity,
                        "message": f.message,
                        "path": f.path,
                        "line": f.line,
                        "excerpt": f.excerpt,
                    }
                    for f in report.sorted()
                ],
            },
            indent=2,
        ))
    elif not args.quiet:
        _print_report(report, show_excerpt=args.excerpt)

    threshold = SEVERITY_ORDER[args.fail_on]
    if any(SEVERITY_ORDER[f.severity] <= threshold for f in report.findings):
        return EXIT_FOUND
    return EXIT_CLEAN


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promptlint",
        description="Static analysis for prompts: injection surface and unfollowable instructions.",
    )
    parser.add_argument("path", nargs="?", default=".", help="file or directory")
    parser.add_argument("--fail-on", choices=(HIGH, MEDIUM, LOW), default=HIGH,
                        help="exit 1 at this severity or above")
    parser.add_argument(
        "--disable",
        action="append",
        nargs="+",
        metavar="RULE",
        default=None,
        help="rule id(s) to skip; accepts both '--disable a b' and repeated flags",
    )
    parser.add_argument("--json", action="store_true", help="machine readable")
    parser.add_argument("--excerpt", action="store_true", help="show the matching line")
    parser.add_argument("--quiet", action="store_true", help="no report, exit code only")
    parser.set_defaults(func=cmd_lint)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
