"""Running the rules over a repository."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from . import rules
from .extract import blocks_from_file, iter_files
from .models import Block, LintError, Report


def lint_blocks(blocks: Iterable[Block], enabled: Sequence[str] = rules.RULE_IDS) -> Report:
    report = Report()
    for block in blocks:
        report.blocks_scanned += 1
        report.findings.extend(rules.run(block, enabled))
    return _dedupe(report)


def lint_path(
    path: Path,
    enabled: Sequence[str] = rules.RULE_IDS,
    *,
    files: Optional[Sequence[Path]] = None,
) -> Report:
    targets: List[Path] = list(files) if files is not None else (
        [path] if path.is_file() else iter_files(path)
    )
    blocks: List[Block] = []
    for target in targets:
        blocks.extend(blocks_from_file(target))
    return lint_blocks(blocks, enabled)


def _dedupe(report: Report) -> Report:
    seen = set()
    unique = []
    for finding in report.findings:
        if finding.key in seen:
            continue
        seen.add(finding.key)
        unique.append(finding)
    report.findings = unique
    return report
