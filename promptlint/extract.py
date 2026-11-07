"""Find the prompts inside a repository.

A prompt is not a file type. It is a long string that reads like instructions,
and it hides in a Python module, a Markdown file, or a ``.prompt`` template. The
extraction heuristics are deliberately conservative: a false positive here is
noise in every subsequent report, so a string has to look like instructions in
at least two independent ways before it is treated as a prompt.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .models import Block, LintError

# Two of these, plus a minimum length, is what makes a string a prompt.
_INSTRUCTION_MARKERS = (
    "you are",
    "your task",
    "you must",
    "you should",
    "always",
    "never",
    "do not",
    "don't",
    "respond",
    "return",
    "output",
    "instructions",
    "answer",
    "summarise",
    "summarize",
)

# A variable with one of these names holding a string is a prompt even if the
# text alone is not conclusive.
_PROMPT_NAMES = ("prompt", "instruction", "system", "template", "persona")

_MIN_LENGTH = 40

# Characters that count as wrapping interpolated content in a boundary. Quotes
# are deliberately absent: wrapping untrusted text in quotes is not a delimiter
# and does not stop an instruction from escaping it.
_DELIMITERS_BEFORE = ("<", "[", "`", "###", "---")
_DELIMITERS_AFTER = ("]", "`", "###", "---")

_OPEN_TAG = re.compile(r"<([A-Za-z_][\w.\-]*)[^<>]*>\s*$")
_CLOSE_TAG = re.compile(r"^\s*</([A-Za-z_][\w.\-]*)\s*>")

_TEXT_SUFFIXES = (".md", ".txt", ".prompt", ".tmpl", ".j2")


def looks_like_prompt(text: str) -> bool:
    if len(text) < _MIN_LENGTH:
        return False
    lowered = text.lower()
    hits = sum(1 for marker in _INSTRUCTION_MARKERS if marker in lowered)
    return hits >= 2


def _is_prompt_name(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _PROMPT_NAMES)


def _placeholder_name(node: ast.FormattedValue) -> str:
    try:
        return ast.unparse(node.value)
    except Exception:  # pragma: no cover - unparse is total on parsed trees
        return "?"


def _boundary(previous: str, following: str) -> bool:
    """Is the interpolated value wrapped in something structural?

    ``<documents>{chunk}</documents>`` is the shape that survives injection:
    the model has an explicit signal that this is data. ``Answer: {question}``
    has none — the value simply becomes the next sentence.

    An opening tag only counts if the matching closing tag follows, otherwise
    ``<b>{x}`` would be treated as safely fenced when nothing fences it.
    """
    before = previous.rstrip()
    after = following.lstrip()
    if before.endswith(_DELIMITERS_BEFORE) or after.startswith(_DELIMITERS_AFTER):
        return True
    opening = _OPEN_TAG.search(before)
    closing = _CLOSE_TAG.match(after)
    return bool(opening and closing and opening.group(1) == closing.group(1))


def _from_joined_str(node: ast.JoinedStr, path: str, line: int) -> Block:
    parts: List[str] = []
    names: List[str] = []
    delimited = True
    values = node.values
    for index, value in enumerate(values):
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            names.append(_placeholder_name(value))
            previous = ""
            following = ""
            if index > 0 and isinstance(values[index - 1], ast.Constant):
                previous = str(values[index - 1].value)
            if index + 1 < len(values) and isinstance(values[index + 1], ast.Constant):
                following = str(values[index + 1].value)
            if not _boundary(previous, following):
                delimited = False
            parts.append("{" + names[-1] + "}")
    return Block("".join(parts), path, line, tuple(names), delimited)


def blocks_from_python(source: str, path: str) -> List[Block]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise LintError(f"{path}: cannot parse Python: {exc.msg} (line {exc.lineno})") from None

    found: List[Block] = []

    # Module-level string constants, so `PROMPT.format(...)` can be resolved.
    # Without this the most common way of building a prompt in Python — a
    # template constant spliced at the call site — is invisible.
    module_strings: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        module_strings[target.id] = node.value.value

    # Assignments whose *name* says prompt: these are prompts even when short.
    for node in ast.walk(tree):
        targets: Sequence[ast.expr] = ()
        value: Optional[ast.expr] = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None:
            continue
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not any(_is_prompt_name(name) for name in names):
            continue

        if isinstance(value, ast.JoinedStr):
            found.append(_from_joined_str(value, path, node.lineno))
        elif isinstance(value, ast.Constant) and isinstance(value.value, str):
            # Name-matched variables are prompts by declaration, so length is
            # not the test — the name is.
            if value.value.strip():
                found.append(Block(value.value, path, node.lineno))

    # `.format()` calls are separate from the assignment that defines the
    # template, so they need their own pass. Without it, a template with no
    # interpolations looks safe while the call site splices user text in.
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "format":
            continue
        receiver = node.func.value
        template: Optional[str] = None
        if isinstance(receiver, ast.Constant) and isinstance(receiver.value, str):
            template = receiver.value
        elif isinstance(receiver, ast.Name):
            template = module_strings.get(receiver.id)
        if template is None or not looks_like_prompt(template):
            continue
        names = tuple(
            keyword.arg or "?" for keyword in node.keywords
        ) + tuple(ast.unparse(arg) for arg in node.args)
        found.append(
            Block(
                template,
                path,
                node.lineno,
                names,
                delimited=_delimited_template(template),
            )
        )

    # And any inline f-string that reads like instructions.
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            block = _from_joined_str(node, path, node.lineno)
            if looks_like_prompt(block.text):
                found.append(block)

    return _dedupe(found)


def _delimited_template(template: str) -> bool:
    """For ``.format()`` we can only judge the template, not the call site."""
    if not template:
        return True
    for marker in _DELIMITERS_BEFORE:
        if marker in template:
            return True
    return False


def blocks_from_text(source: str, path: str) -> List[Block]:
    """Split a text file into paragraphs, keeping line numbers honest."""
    blocks: List[Block] = []
    lines = source.splitlines()
    start = 0
    buffer: List[str] = []

    def flush() -> None:
        if not buffer:
            return
        text = "\n".join(buffer)
        if looks_like_prompt(text):
            blocks.append(Block(text, path, start + 1))
        buffer.clear()

    for index, line in enumerate(lines):
        if line.strip():
            if not buffer:
                start = index
            buffer.append(line)
        else:
            flush()
    flush()
    return _dedupe(blocks)


def _dedupe(blocks: List[Block]) -> List[Block]:
    """Collapse blocks at the same position, keeping the most informative one.

    A template defined on line 3 and used with ``.format()`` on line 4 yields
    two views of the same text; the one that knows about interpolations is the
    one the rules need.
    """
    best = {}
    order: List[tuple] = []
    for block in blocks:
        key = (block.path, block.line, block.text)
        if key not in best:
            best[key] = block
            order.append(key)
        elif block.interpolations and not best[key].interpolations:
            best[key] = block
    return [best[key] for key in order]


def blocks_from_file(path: Path) -> List[Block]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise LintError(f"cannot read {path}: {exc}") from None

    if path.suffix == ".py":
        return blocks_from_python(source, str(path))
    if path.suffix in _TEXT_SUFFIXES:
        return blocks_from_text(source, str(path))
    return []


def iter_files(root: Path) -> List[Path]:
    skip = {"__pycache__", ".git", "node_modules", ".venv", "venv", "build", "dist"}
    files: List[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip or part.endswith(".egg-info") for part in path.parts):
            continue
        if path.suffix in (".py",) or path.suffix in _TEXT_SUFFIXES:
            files.append(path)
    return files
