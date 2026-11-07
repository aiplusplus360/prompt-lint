"""Find the injectable and unfollowable parts of a prompt, without a model."""

from .extract import blocks_from_file, blocks_from_python, blocks_from_text, looks_like_prompt
from .lint import lint_blocks, lint_path
from .models import (
    HIGH,
    LOW,
    MEDIUM,
    SEVERITY_ORDER,
    Block,
    Finding,
    LintError,
    Report,
)
from .rules import RULE_IDS, RULES

__version__ = "0.1.0"

__all__ = [
    "Block",
    "Finding",
    "Report",
    "LintError",
    "HIGH",
    "MEDIUM",
    "LOW",
    "SEVERITY_ORDER",
    "RULES",
    "RULE_IDS",
    "looks_like_prompt",
    "blocks_from_python",
    "blocks_from_text",
    "blocks_from_file",
    "lint_blocks",
    "lint_path",
    "__version__",
]
