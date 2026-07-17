import contextlib
import io
import json
import unittest
from unittest import mock

from click.testing import CliRunner

from support import HarnessStoreTestCase, build_t3code_store
from vcc_history.cli import main
from vcc_history.history import vcc
from vcc_history.history.errors import warn_malformed_json_record
from vcc_history.history.registry import DESCRIPTORS, HARNESS_CHOICES, HARNESS_IDS

class CliTests(HarnessStoreTestCase):
    def _append_malformed_claude_record(self, path=None):
        path = path or self.claude_session
        line_number = len(path.read_text(encoding="utf-8").splitlines()) + 1
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{malformed-json-record}\n")
        return f"warning: {path}:{line_number}: malformed JSON record:"

    def test_missing_read_ref_is_a_clean_click_error(self):
        ref = f"{self.root / 'missing.txt'}:1-2"
        result = CliRunner().invoke(main, ["read", ref])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Error: Could not read ref", result.output)
        self.assertNotIn("Traceback", result.output)

    def test_registry_drives_cli_choices_and_harness_labels(self):
        help_result = CliRunner().invoke(main, ["list", "--help"])
        self.assertEqual(help_result.exit_code, 0, help_result.output)
        self.assertEqual(HARNESS_CHOICES, ("all", *HARNESS_IDS))
        self.assertEqual(
            tuple(descriptor.id for descriptor in DESCRIPTORS),
            HARNESS_IDS,
        )
        self.assertIn("|".join(HARNESS_CHOICES), help_result.output)
        for descriptor in DESCRIPTORS:
            self.assertIn(descriptor.label, help_result.output)
        self.assertIn("--include-delegated", help_result.output)
        self.assertIn("Include delegated (subagent)", help_result.output)
        self.assertIn("transcripts.", help_result.output)
        self.assertNotIn("--include-subagents", help_result.output)

        list_result = CliRunner().invoke(main, ["list", "--harness", "all", "--limit", "10"])
        self.assertEqual(list_result.exit_code, 0, list_result.output)
        self.assertIn("claude\t", list_result.output)
        self.assertIn("codex\t", list_result.output)
        self.assertIn("omp\t", list_result.output)
        self.assertIn("reasonix\t", list_result.output)
        self.assertNotIn("(delegated)", list_result.output)

        json_result = CliRunner().invoke(
            main, ["list", "--harness", "all", "--limit", "10", "--json"]
        )
        self.assertEqual(json_result.exit_code, 0, json_result.output)
        public_records = json.loads(json_result.stdout)
        self.assertTrue(public_records)
        for record in public_records:
            self.assertIn("created_at", record)
            self.assertIn("updated_at", record)
            self.assertIn("timestamp", record)
            self.assertIn("kind", record)
            self.assertNotIn("alias_keys", record)

        delegated_result = CliRunner().invoke(
            main,
            ["list", "--harness", "claude", "--limit", "10", "--include-delegated"],
        )
        self.assertEqual(delegated_result.exit_code, 0, delegated_result.output)
        self.assertIn("claude (delegated)\t", delegated_result.output)

        reasonix_result = CliRunner().invoke(
            main,
            ["list", "--harness", "reasonix", "--limit", "10", "--include-delegated"],
        )
        self.assertEqual(reasonix_result.exit_code, 0, reasonix_result.output)
        self.assertIn("reasonix (delegated)\t", reasonix_result.output)

    def test_search_session_limit_can_expand_beyond_default_breadth(self):
        records = [
            {
                "harness": "claude",
                "path": f"/tmp/session-{index}.jsonl",
                "timestamp": f"2026-07-17T00:00:{index:02d}Z",
            }
            for index in range(21)
        ]
        with (
            mock.patch("vcc_history.cli.collect_sessions", return_value=records),
            mock.patch("vcc_history.cli.search_sessions", return_value=[]) as search_mock,
        ):
            default_result = CliRunner().invoke(main, ["search", "marker"])
            self.assertEqual(default_result.exit_code, 0, default_result.output)
            default_records = search_mock.call_args.args[0]
            self.assertEqual(len(default_records), 20)
            self.assertNotIn(records[0], default_records)

            unlimited_result = CliRunner().invoke(
                main, ["search", "marker", "--session-limit", "0"]
            )
            self.assertEqual(unlimited_result.exit_code, 0, unlimited_result.output)
            self.assertEqual(search_mock.call_args.args[0], records)

        search_help = CliRunner().invoke(main, ["search", "--help"])
        self.assertEqual(search_help.exit_code, 0, search_help.output)
        self.assertIn("|".join(HARNESS_CHOICES), search_help.output)
        self.assertIn("--session-limit", search_help.output)
        self.assertIn("Newest sessions to scan (0 = all).", search_help.output)
        self.assertNotIn("--limit", search_help.output)

        list_help = CliRunner().invoke(main, ["list", "--help"])
        self.assertEqual(list_help.exit_code, 0, list_help.output)
        self.assertIn("--limit", list_help.output)
        self.assertIn("Newest sessions to list (0 = all).", list_help.output)

    def test_compile_unknown_format_errors_with_harness_guidance(self):
        source = self.root / "unknown.jsonl"
        source.write_text('{"unexpected":"record"}\n', encoding="utf-8")

        result = CliRunner().invoke(main, ["compile", str(source)])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("could not detect session harness", result.output)
        self.assertIn("--harness", result.output)
        for harness in HARNESS_IDS:
            self.assertIn(harness, result.output)
        self.assertFalse(source.with_suffix(".txt").exists())

    def test_compile_harness_override_forces_claude_on_ambiguous_input(self):
        source = self.root / "ambiguous.jsonl"
        source.write_text(
            "\n".join(
                [
                    '{"role":"user","content":"reasonix poison"}',
                    '{"type":"user","sessionId":"ambiguous","message":'
                    '{"role":"user","content":"forced Claude marker"}}',
                    '{"type":"assistant","sessionId":"ambiguous","message":'
                    '{"role":"assistant","content":[{"type":"text",'
                    '"text":"forced Claude response"}]}}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.assertEqual(vcc.detect_format(vcc.lex(source)), "reasonix")

        result = CliRunner().invoke(
            main,
            ["compile", "--harness", "claude", "-o", str(self.root), str(source)],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        full = source.with_suffix(".txt").read_text(encoding="utf-8")
        self.assertIn("forced Claude marker", full)
        self.assertIn("forced Claude response", full)
        self.assertNotIn("reasonix poison", full)

    def test_explicit_claude_compile_still_writes_beside_source(self):
        result = CliRunner().invoke(main, ["compile", str(self.claude_session)])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(self.claude_session.with_suffix(".txt").is_file())
        self.assertTrue(self.claude_session.with_name("claude_basic.min.txt").is_file())
        self.assertFalse(self.claude_session.with_name("claude_basic.view.txt").exists())
        self.assertNotIn(".view.txt", result.output)
        self.assertFalse((self.root / "cache" / "compiled").exists())

    def test_malformed_warning_key_includes_parser_error_text(self):
        path = self.root / "shared-warning-key.jsonl"
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            warn_malformed_json_record(path, 7, "shared parser detail")
            warn_malformed_json_record(path, 7, "shared parser detail")
            warn_malformed_json_record(path, 7, "different parser detail")

        output = stderr.getvalue()
        self.assertEqual(output.count(f"warning: {path}:7: malformed JSON record:"), 2)
        self.assertEqual(output.count("shared parser detail"), 1)
        self.assertEqual(output.count("different parser detail"), 1)

    def test_claude_list_warns_for_each_malformed_line_and_file_and_keeps_json_clean(self):
        second_session = self.claude_project / "claude_second.jsonl"
        second_session.write_text(
            self.claude_session.read_text(encoding="utf-8").replace(
                "claude-session-basic", "claude-session-second"
            ),
            encoding="utf-8",
        )
        warning_prefixes = [
            self._append_malformed_claude_record(),
            self._append_malformed_claude_record(),
            self._append_malformed_claude_record(second_session),
        ]

        result = CliRunner().invoke(
            main, ["list", "--harness", "claude", "--limit", "0", "--json"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(json.loads(result.stdout))
        for warning_prefix in warning_prefixes:
            self.assertEqual(result.stderr.count(warning_prefix), 1)

    def test_claude_compile_warns_once_for_a_malformed_record(self):
        warning_prefix = self._append_malformed_claude_record()
        output_dir = self.root / "malformed-compile"

        result = CliRunner().invoke(
            main,
            [
                "compile",
                "--harness",
                "claude",
                "-o",
                str(output_dir),
                str(self.claude_session),
            ],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue((output_dir / "claude_basic.txt").is_file())
        self.assertEqual(result.stderr.count(warning_prefix), 1)

    def test_claude_search_deduplicates_each_discovery_and_compile_warning(self):
        warning_prefixes = [
            self._append_malformed_claude_record(),
            self._append_malformed_claude_record(),
        ]

        result = CliRunner().invoke(
            main,
            [
                "search",
                "sanitized Claude fixture marker",
                "--harness",
                "claude",
                "--session-limit",
                "0",
                "--json",
            ],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(json.loads(result.stdout))
        for warning_prefix in warning_prefixes:
            self.assertEqual(result.stderr.count(warning_prefix), 1)

    def test_t3code_compile_validates_thread_selection_and_output_flags(self):
        db_path = build_t3code_store(
            self.t3_home / "userdata" / "state.sqlite", self.codex_session
        )

        guarded = CliRunner().invoke(main, ["compile", str(db_path)])
        self.assertNotEqual(guarded.exit_code, 0)
        self.assertIn("state.sqlite contains many threads", guarded.output)
        self.assertIn("xcc list --harness t3code", guarded.output)
        self.assertIn("t3code:<db>#<thread>", guarded.output)
        self.assertIn("--all-threads", guarded.output)

        missing_output = CliRunner().invoke(
            main, ["compile", str(db_path), "--all-threads"]
        )
        self.assertNotEqual(missing_output.exit_code, 0)
        self.assertIn("--all-threads requires -o/--output-dir", missing_output.output)

        bulk_output = self.root / "t3-bulk"
        compiled = CliRunner().invoke(
            main,
            ["compile", str(db_path), "--all-threads", "-o", str(bulk_output)],
        )
        self.assertEqual(compiled.exit_code, 0, compiled.output)
        self.assertEqual(len(list(bulk_output.glob("*.min.txt"))), 2)
        full_outputs = [
            path
            for path in bulk_output.glob("*.txt")
            if not path.name.endswith(".min.txt")
        ]
        self.assertEqual(len(full_outputs), 2)


if __name__ == "__main__":
    unittest.main()
