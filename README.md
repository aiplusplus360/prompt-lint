# prompt-lint

**Find the injectable and unfollowable parts of a prompt — without calling a model.**

[![CI](https://github.com/aiplusplus360/prompt-lint/actions/workflows/ci.yml/badge.svg)](https://github.com/aiplusplus360/prompt-lint/actions/workflows/ci.yml)

```
$ promptlint examples/bad_prompts.py

[  high] untrusted-interpolation  examples/bad_prompts.py:22
         user_question is spliced into instructions without a delimiter; wrap it in e.g. <data>…</data>

[medium] contradictory-length     examples/bad_prompts.py:8
         asks for both brevity and thoroughness; the model will pick one at random

[medium] json-without-schema      examples/bad_prompts.py:8
         asks for JSON but never names the keys; weak models will invent them

[medium] unbounded-length         examples/bad_prompts.py:8
         asks for brevity without a bound (e.g. 'at most 3 bullets')

[   low] duplicated-instruction   examples/bad_prompts.py:13
         the same sentence appears 2 times; repetition usually means two rules drifted apart

5 finding(s) across 3 prompt block(s): 1 high, 4 at or above medium, 5 total
```

```
$ promptlint examples/good_prompts.py
no findings across 2 prompt block(s)
```

Zero dependencies. Python 3.9+. No API key, no model, no network.

---

## The problem

Prompts are the only production artefact in an AI application that nobody
reviews. They live in string literals, they are edited by anyone, and they fail
in ways that look like model flakiness:

* a f-string splices user text straight into the instructions, and the user
  supplies new instructions;
* "be concise" and "be thorough" survive side by side, and the output length is
  a coin flip per run;
* "return JSON" never names the keys, so every run invents its own schema;
* a rule gets added twice while somebody was refactoring, and they now differ.

All four are visible in the source. None of them need an LLM to detect.

---

## Install

```bash
pip install prompt-lint        # or: pip install -e .
```

## Use it

```bash
promptlint .                     # report, exit 1 on a high finding
promptlint prompts/ --json       # machine readable
promptlint prompt.py --excerpt   # show the offending line
promptlint . --fail-on medium    # stricter gate
promptlint . --disable unbounded-length
```

Exit codes: `0` clean, `1` findings at or above `--fail-on`, `2` bad input
(unreadable file, unparseable Python, unknown rule).

### As a pre-commit or CI step

```yaml
- name: Lint prompts
  run: promptlint . --fail-on high
```

---

## The rules

| Rule | Severity | Catches |
|---|---|---|
| `untrusted-interpolation` | **high** | user-controlled text spliced into instructions with no delimiter |
| `credential-in-prompt` | **high** | an AWS/GitHub/Stripe key or private key in the prompt text |
| `json-without-schema` | medium | asks for JSON, never names the keys |
| `unbounded-length` | medium | "concise" with no number attached |
| `contradictory-length` | medium | wants both brevity and thoroughness |
| `duplicated-instruction` | low | the same sentence twice |

Severities are opinionated. A linter that calls everything high gets muted, and
a muted linter protects nothing.

### The one that matters

```
return f"You are a support agent. Always answer directly. {user_question}"
```

The user's text is not data in that prompt — it is the next sentence of the
instructions. Compare:

```
return f"You are a support agent. <data>{user_question}</data>"
```

The linter distinguishes the two by looking at what is immediately around the
interpolation in the parsed source: an opening tag only counts if its matching
closing tag follows, so `<b>{x}` is *not* treated as fenced. Quotes do not
count either — wrapping untrusted text in quotes is not a boundary.

**The check keys on the expression's name** — `user_question`, `input`,
`document`, `feedback` and friends. A value spliced in as `{q}` or `{v}` is not
recognised as untrusted, because it cannot be: static analysis cannot prove
where a value came from. Name the variable after where the data came from and
the rule will see it.

---

## How prompts are found

A prompt is not a file type, so extraction is heuristic — and deliberately
conservative, because a false positive here is noise in every later report.

**Python:**
- any string assigned to a name containing `prompt`, `instruction`, `system`,
  `template`, or `persona` (length does not matter — the name is the signal);
- any f-string that reads like instructions: at least two of `you are`, `always`,
  `never`, `do not`, `return`, `output`, … and at least 40 characters;
- `.format()` calls, including the common `PROMPT.format(...)` shape, resolved
  through module-level string constants.

**Text files** (`.md`, `.txt`, `.prompt`, `.tmpl`, `.j2`): each paragraph, with
real line numbers preserved.

Nothing is executed. The AST is parsed and walked; no imports, no calls, no
side effects.

---

## Design notes

- **Rules are pure functions of one block.** A rule cannot depend on what else
  was in the file, so each one is testable in isolation and results do not
  change when scan order changes.
- **Both directions are tested.** Every rule has a "fires on the defect" test
  *and* a "stays quiet on the correct version" test. The second half is what
  keeps the tool switched on.
- **`examples/` is the calibration corpus.** The CI step asserts the bad file
  fires and the good file is silent; if a rule stops firing, the rule broke.
- **Findings are deduplicated by `(rule, path, line)`**, so a prompt matched by
  two extraction paths is reported once.
- **`--excerpt` is opt-in.** Default reports stay one screen per finding.

### What this deliberately does not do

- **No judgement of prompt quality.** It cannot tell you the prompt is *good*,
  only that it contains shapes that reliably cause trouble.
- **No rendering.** Interpolated expressions are not evaluated — they become
  `{name}` in the static text. That is enough for every rule here and keeps the
  tool safe to run on untrusted code.
- **No fix suggestions applied.** It reports; you decide.

---

## Development

```bash
python -m unittest discover -s tests -v
```

43 tests, no third-party runtime dependencies.

```
promptlint/
  models.py    Block, Finding, Report
  extract.py   AST + paragraph extraction
  rules.py     the six rules, each a pure function
  lint.py      run rules over a path
  cli.py       report, JSON, exit codes
```

## License

MIT
