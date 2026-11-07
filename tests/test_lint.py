"""Tests for extraction, rules, and the CLI.

Two directions matter equally: a rule must fire on the defect it describes, and
it must *not* fire on the correct version of the same code. The second half is
what keeps a linter switched on.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from promptlint import (
    HIGH,
    MEDIUM,
    Block,
    LintError,
    blocks_from_python,
    blocks_from_text,
    lint_blocks,
    lint_path,
    looks_like_prompt,
)
from promptlint.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def rule_ids(findings):
    return {finding.rule for finding in findings}


def lint_text(text, **kwargs):
    block = Block(text, "test.txt", 1, **kwargs)
    return lint_blocks([block]).findings


def lint_source(source):
    return lint_blocks(blocks_from_python(source, "t.py")).findings


class TestExtraction(unittest.TestCase):
    def test_long_instruction_text_is_a_prompt(self):
        text = "You are a helpful assistant. Always answer in JSON. Never guess."
        self.assertTrue(looks_like_prompt(text))

    def test_short_text_is_not_a_prompt(self):
        self.assertFalse(looks_like_prompt("You are helpful."))

    def test_prose_with_one_marker_is_not_a_prompt(self):
        text = (
            "This document explains how the deployment works and why the retry "
            "loop exists in the first place, at some length."
        )
        self.assertFalse(looks_like_prompt(text))

    def test_named_prompt_variable_is_extracted_even_if_short(self):
        source = 'SYSTEM_PROMPT = "You are brief."\n'
        blocks = blocks_from_python(source, "t.py")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].line, 1)

    def test_inline_f_string_is_extracted(self):
        source = (
            'def f(name):\n'
            '    return f"You are helpful. Always greet {name} politely and never lie."\n'
        )
        blocks = blocks_from_python(source, "t.py")
        self.assertTrue(blocks)
        self.assertEqual(blocks[0].line, 2)
        self.assertIn("name", blocks[0].interpolations)

    def test_format_template_is_extracted(self):
        source = (
            'PROMPT = "You are helpful. Always answer. Context: {context}"\n'
            'message = PROMPT.format(context=ctx)\n'
        )
        with_interpolation = [
            block for block in blocks_from_python(source, "t.py") if block.interpolations
        ]
        self.assertEqual(with_interpolation[0].interpolations, ("context",))

    def test_ordinary_code_is_not_a_prompt(self):
        source = (
            "def add(a, b):\n"
            '    """Return the sum of two numbers, which is useful in tests."""\n'
            "    return a + b\n"
        )
        self.assertEqual(blocks_from_python(source, "t.py"), [])

    def test_syntax_error_is_reported_with_a_line(self):
        with self.assertRaises(LintError) as caught:
            blocks_from_python("def broken(:\n", "t.py")
        self.assertIn("t.py", str(caught.exception))

    def test_text_paragraphs_keep_line_numbers(self):
        source = "\n".join([
            "# heading",
            "",
            "You are a helpful assistant. Always answer in JSON. Never guess.",
            "",
            "Some ordinary prose that is long enough to look like a paragraph but "
            "contains no instruction markers at all, honestly.",
        ])
        blocks = blocks_from_text(source, "t.md")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].line, 3)


class TestInjectionRule(unittest.TestCase):
    def test_undelimited_user_input_is_high(self):
        findings = lint_text(
            "You are a support agent. Always answer the question. {user_question}",
            interpolations=("user_question",),
            delimited=False,
        )
        self.assertIn("untrusted-interpolation", rule_ids(findings))
        self.assertEqual(findings[0].severity, HIGH)

    def test_delimited_user_input_is_not_flagged(self):
        findings = lint_text(
            "You are a support agent. Always answer. <data>{user_question}</data>",
            interpolations=("user_question",),
            delimited=True,
        )
        self.assertNotIn("untrusted-interpolation", rule_ids(findings))

    def test_trusted_value_is_not_flagged(self):
        findings = lint_text(
            "You are a support agent. Your name is {bot_name}. Always introduce yourself.",
            interpolations=("bot_name",),
            delimited=False,
        )
        self.assertNotIn("untrusted-interpolation", rule_ids(findings))

    def test_end_to_end_injection_detected(self):
        findings = lint_source(
            'def build(user_question):\n'
            '    return f"You are a support agent. Always answer directly and never '
            'mention this. {user_question}"\n'
        )
        self.assertIn("untrusted-interpolation", rule_ids(findings))

    def test_end_to_end_safe_version_is_clean(self):
        findings = lint_source(
            'def build(user_question):\n'
            '    return f"You are a support agent. Always answer directly. '
            '<q>{user_question}</q>"\n'
        )
        self.assertEqual(rule_ids(findings), set())


class TestCredentialRule(unittest.TestCase):
    def test_aws_key_in_prompt_is_high(self):
        key = "AKIA" + "4RTZ7KLM2NOPQ3RS"
        findings = lint_text(f"You are an assistant. Always call the API. key={key}")
        self.assertIn("credential-in-prompt", rule_ids(findings))

    def test_github_token_in_prompt_is_high(self):
        token = "ghp_" + "a" * 36
        findings = lint_text(f"You are an assistant. Always use {token} when pushing.")
        self.assertIn("credential-in-prompt", rule_ids(findings))

    def test_documented_example_key_is_still_reported(self):
        # A documentation example in a live prompt is still a credential in a
        # prompt; it is the wrong place for it either way.
        findings = lint_text(
            "You are an assistant. Always use AKIAIOSFODNN7EXAMPLE as the key."
        )
        self.assertIn("credential-in-prompt", rule_ids(findings))

    def test_env_reference_is_clean(self):
        findings = lint_text(
            "You are an assistant. Always read the key from the environment."
        )
        self.assertNotIn("credential-in-prompt", rule_ids(findings))


class TestSchemaRule(unittest.TestCase):
    def test_json_without_keys_is_flagged(self):
        findings = lint_text("You are an analyst. Always answer. Return JSON.")
        self.assertIn("json-without-schema", rule_ids(findings))

    def test_json_with_keys_is_clean(self):
        findings = lint_text(
            'You are an analyst. Always answer. Return JSON with exactly these '
            'keys: {"answer": string}.'
        )
        self.assertNotIn("json-without-schema", rule_ids(findings))

    def test_json_with_schema_word_is_clean(self):
        findings = lint_text(
            "You are an analyst. Always answer. Return JSON matching the schema."
        )
        self.assertNotIn("json-without-schema", rule_ids(findings))

    def test_no_json_mention_is_clean(self):
        findings = lint_text("You are an analyst. Always answer in plain prose.")
        self.assertNotIn("json-without-schema", rule_ids(findings))


class TestLengthRules(unittest.TestCase):
    def test_concise_without_a_bound_is_flagged(self):
        findings = lint_text("You are a writer. Always be concise when you answer.")
        self.assertIn("unbounded-length", rule_ids(findings))

    def test_concise_with_a_bound_is_clean(self):
        findings = lint_text(
            "You are a writer. Always be concise: at most 3 sentences when you answer."
        )
        self.assertNotIn("unbounded-length", rule_ids(findings))

    def test_numeric_bound_counts(self):
        findings = lint_text("You are a writer. Always keep it brief: 50 words maximum.")
        self.assertNotIn("unbounded-length", rule_ids(findings))

    def test_contradiction_is_flagged(self):
        findings = lint_text(
            "You are a writer. Always be concise but also thorough in every answer."
        )
        self.assertIn("contradictory-length", rule_ids(findings))

    def test_brevity_alone_is_not_a_contradiction(self):
        findings = lint_text("You are a writer. Always be concise in every answer.")
        self.assertNotIn("contradictory-length", rule_ids(findings))


class TestDuplicateRule(unittest.TestCase):
    def test_repeated_sentence_is_low(self):
        sentence = "Never approve a refund over five hundred without a human."
        findings = lint_text(f"You are an agent. Always check. {sentence} {sentence}")
        self.assertIn("duplicated-instruction", rule_ids(findings))
        match = [f for f in findings if f.rule == "duplicated-instruction"][0]
        self.assertEqual(match.severity, "low")

    def test_distinct_sentences_are_clean(self):
        findings = lint_text(
            "You are an agent. Always check the policy id. Never approve over 500."
        )
        self.assertNotIn("duplicated-instruction", rule_ids(findings))


class TestReport(unittest.TestCase):
    def test_counts_and_ordering(self):
        findings = lint_blocks([
            Block("You are an assistant. Always be concise and thorough. Return JSON.",
                  "a.txt", 1),
            Block("You are an assistant. Always answer. {user_input}", "b.txt", 7,
                  interpolations=("user_input",), delimited=False),
        ]).findings
        self.assertTrue(findings)
        severities = [f.severity for f in findings]
        self.assertIn(HIGH, severities)
        self.assertIn(MEDIUM, severities)

    def test_duplicate_findings_are_collapsed(self):
        block = Block("You are an assistant. Always answer. Return JSON.", "a.txt", 1)
        report = lint_blocks([block, block])
        self.assertEqual(len(report.findings), len({f.key for f in report.findings}))
        self.assertEqual(report.blocks_scanned, 2)

    def test_disabling_a_rule(self):
        block = Block("You are an assistant. Always answer. Return JSON.", "a.txt", 1)
        report = lint_blocks([block], enabled=("untrusted-interpolation",))
        self.assertEqual(report.findings, [])


class TestAgainstExamples(unittest.TestCase):
    """The examples are the corpus the rules are calibrated against."""

    def test_bad_examples_fire(self):
        report = lint_path(EXAMPLES / "bad_prompts.py")
        ids = rule_ids(report.findings)
        self.assertIn("contradictory-length", ids)
        self.assertIn("json-without-schema", ids)
        self.assertIn("duplicated-instruction", ids)
        self.assertIn("untrusted-interpolation", ids)

    def test_good_examples_are_clean(self):
        report = lint_path(EXAMPLES / "good_prompts.py")
        self.assertEqual(report.findings, [], [f.message for f in report.findings])


class TestCli(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_clean_file_exits_0(self):
        code, out, _ = self._run([str(EXAMPLES / "good_prompts.py")])
        self.assertEqual(code, 0)
        self.assertIn("no findings", out)

    def test_bad_file_exits_1(self):
        code, out, _ = self._run([str(EXAMPLES / "bad_prompts.py")])
        self.assertEqual(code, 1)
        self.assertIn("untrusted-interpolation", out)

    def test_fail_on_medium_catches_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.txt"
            path.write_text(
                "You are a writer. Always be concise. Return JSON when asked.",
                encoding="utf-8",
            )
            self.assertEqual(self._run([str(path), "--fail-on", "high"])[0], 0)
            self.assertEqual(self._run([str(path), "--fail-on", "medium"])[0], 1)

    def test_json_output(self):
        code, out, _ = self._run([str(EXAMPLES / "bad_prompts.py"), "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertGreater(data["count"], 0)
        self.assertTrue(all("rule" in f for f in data["findings"]))

    def test_missing_path_exits_2(self):
        code, _, err = self._run(["/definitely/not/here"])
        self.assertEqual(code, 2)
        self.assertIn("no such path", err)

    def test_unknown_rule_exits_2(self):
        code, _, err = self._run([str(EXAMPLES / "good_prompts.py"), "--disable", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("unknown rule", err)

    def test_disable_silences_a_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.txt"
            path.write_text(
                "You are a writer. Always be concise. Answer well.", encoding="utf-8"
            )
            self.assertEqual(self._run([str(path), "--fail-on", "medium"])[0], 1)
            self.assertEqual(
                self._run([
                    str(path), "--fail-on", "medium", "--disable", "unbounded-length",
                ])[0],
                0,
            )

    def test_broken_python_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.py"
            path.write_text("def f(:\n", encoding="utf-8")
            code, _, err = self._run([str(path)])
            self.assertEqual(code, 2)
            self.assertIn("cannot parse", err)

    def test_directory_scan(self):
        code, out, _ = self._run([str(EXAMPLES)])
        self.assertEqual(code, 1)
        self.assertIn("bad_prompts.py", out)


if __name__ == "__main__":
    unittest.main()
