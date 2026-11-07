"""The rules.

Each rule is a pure function of one block, so it is testable in isolation and
cannot depend on what else happened to be in the file. The severities are
opinionated on purpose:

* **high** — a real defect: injection surface, or a credential in a prompt;
* **medium** — an instruction a model cannot follow reliably;
* **low** — hygiene.

A linter that reports everything as high gets muted, and then it protects
nothing.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Sequence

from .models import HIGH, LOW, MEDIUM, Block, Finding

Instruction = Callable[[Block], List[Finding]]

_CONCISE = re.compile(r"\b(concise|brief|short|succinct|terse|minimal)\b", re.I)
_THOROUGH = re.compile(r"\b(thorough|detailed|comprehensive|exhaustive|in depth|in-depth)\b", re.I)
_BOUND = re.compile(
    r"\b(\d+\s*(words?|sentences?|bullets?|characters?|chars?|lines?|tokens?|paragraphs?)|"
    r"at most|no more than|maximum of|max\.?)\b",
    re.I,
)
_JSON = re.compile(r"\bjson\b", re.I)
_SCHEMA_HINT = re.compile(r"(\bschema\b|\bkeys?\b|\bfields?\b|\{\s*\"|\bexactly these\b)", re.I)

_UNTRUSTED = re.compile(
    r"(user|input|query|message|feedback|request|email|comment|content|body|"
    r"document|doc|page|transcript|answer_from)",
    re.I,
)

_CREDENTIALS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

_SENTENCE = re.compile(r"[^.!?\n]{40,}[.!?]")


def _finding(block: Block, rule: str, severity: str, message: str) -> Finding:
    excerpt = block.text.strip().splitlines()[0][:100] if block.text.strip() else ""
    return Finding(rule, severity, message, block.path, block.line, excerpt)


def untrusted_interpolation(block: Block) -> List[Finding]:
    """User-controlled text spliced into an instruction, with no boundary.

    ``f"Answer this: {user_input}"`` is the shape of a prompt injection: the
    user's text is not data, it is the next sentence of the instructions.
    """
    if not block.interpolations or block.delimited:
        return []
    risky = [name for name in block.interpolations if _UNTRUSTED.search(name)]
    if not risky:
        return []
    return [
        _finding(
            block,
            "untrusted-interpolation",
            HIGH,
            f"{', '.join(sorted(set(risky)))} is spliced into instructions without a "
            "delimiter; wrap it in e.g. <data>…</data>",
        )
    ]


def credential_in_prompt(block: Block) -> List[Finding]:
    for pattern in _CREDENTIALS:
        match = pattern.search(block.text)
        if match:
            return [
                _finding(
                    block,
                    "credential-in-prompt",
                    HIGH,
                    f"a {match.group(0)[:8]}… credential is embedded in the prompt text",
                )
            ]
    return []


def json_without_schema(block: Block) -> List[Finding]:
    if not _JSON.search(block.text) or _SCHEMA_HINT.search(block.text):
        return []
    return [
        _finding(
            block,
            "json-without-schema",
            MEDIUM,
            "asks for JSON but never names the keys; weak models will invent them",
        )
    ]


def unbounded_length(block: Block) -> List[Finding]:
    if not _CONCISE.search(block.text) or _BOUND.search(block.text):
        return []
    return [
        _finding(
            block,
            "unbounded-length",
            MEDIUM,
            "asks for brevity without a bound (e.g. 'at most 3 bullets'); "
            "'concise' means something different to every run",
        )
    ]


def contradictory_length(block: Block) -> List[Finding]:
    if not (_CONCISE.search(block.text) and _THOROUGH.search(block.text)):
        return []
    return [
        _finding(
            block,
            "contradictory-length",
            MEDIUM,
            "asks for both brevity and thoroughness; the model will pick one at random",
        )
    ]


def duplicated_instruction(block: Block) -> List[Finding]:
    # Wrapped text is the norm, so sentences must be found across line breaks.
    flattened = " ".join(block.text.split())
    seen: Dict[str, int] = {}
    for sentence in _SENTENCE.findall(flattened):
        normalised = " ".join(sentence.lower().split())
        seen[normalised] = seen.get(normalised, 0) + 1
    repeats = [s for s, count in seen.items() if count > 1]
    if not repeats:
        return []
    return [
        _finding(
            block,
            "duplicated-instruction",
            LOW,
            f"the same sentence appears {seen[repeats[0]]} times; "
            "repetition usually means two rules drifted apart",
        )
    ]


RULES: Sequence[Instruction] = (
    untrusted_interpolation,
    credential_in_prompt,
    json_without_schema,
    unbounded_length,
    contradictory_length,
    duplicated_instruction,
)

RULE_IDS = tuple(rule.__name__.replace("_", "-") for rule in RULES)


def run(block: Block, enabled: Sequence[str] = RULE_IDS) -> List[Finding]:
    selected = set(enabled)
    findings: List[Finding] = []
    for rule in RULES:
        rule_id = rule.__name__.replace("_", "-")
        if rule_id not in selected:
            continue
        findings.extend(rule(block))
    return findings
