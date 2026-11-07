"""Shared types for the linter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

HIGH = "high"
MEDIUM = "medium"
LOW = "low"

SEVERITY_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}


class LintError(Exception):
    """A file could not be read or parsed."""


@dataclass(frozen=True)
class Block:
    """One candidate prompt, as static text.

    ``interpolations`` holds the expressions spliced in by an f-string or
    ``.format()``. They are not evaluated — they cannot be, statically — so the
    text carries a ``{name}`` placeholder where each one lands.
    """

    text: str
    path: str
    line: int
    interpolations: Tuple[str, ...] = ()
    delimited: bool = True


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    message: str
    path: str
    line: int
    excerpt: str = ""

    @property
    def key(self) -> str:
        return f"{self.rule}|{self.path}|{self.line}"


@dataclass
class Report:
    findings: list = field(default_factory=list)
    blocks_scanned: int = 0

    def sorted(self) -> list:
        return sorted(
            self.findings,
            key=lambda f: (SEVERITY_ORDER[f.severity], f.path, f.line, f.rule),
        )

    def count_at_or_above(self, severity: str) -> int:
        threshold = SEVERITY_ORDER[severity]
        return sum(1 for f in self.findings if SEVERITY_ORDER[f.severity] <= threshold)
