import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from vcc_history.history import vcc


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class CodexVccTests(unittest.TestCase):
    vcc = vcc

    def test_claude_basic_preserves_thinking_tools_results_and_chunk_merging(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.vcc.compile_pass(str(FIXTURES / "claude_basic.jsonl"), output_dir=tmp)
            full = Path(tmp, "claude_basic.txt").read_text(encoding="utf-8")
            brief = Path(tmp, "claude_basic.min.txt").read_text(encoding="utf-8")

        self.assertIn("[system]", full)
        self.assertIn("Sanitized Claude system context.", full)
        self.assertIn("[user]", full)
        self.assertIn("Please inspect the sanitized Claude fixture marker.", full)
        self.assertEqual(full.count("[assistant]"), 1)
        self.assertIn(">>>thinking", full)
        self.assertIn("Private fixture thinking", full)
        self.assertIn("I will inspect the fixture in two chunks.", full)
        self.assertIn("The second chunk is merged with the first.", full)
        self.assertIn(">>>tool_call Read:read01", full)
        self.assertIn(">>>tool_call Bash:bash02", full)
        self.assertIn("[tool] Read:read01", full)
        self.assertIn("def example():", full)
        self.assertIn("[tool_error] Bash:bash02", full)
        self.assertIn("No module named tests.test_example", full)
        self.assertNotIn("Private fixture thinking", brief)
        self.assertNotIn("def example():", brief)
        self.assertIn('* Read "src/example.py"', brief)
        self.assertIn('* Bash "Run sanitized fixture tests"', brief)

    def test_claude_brief_links_tool_results_by_full_id(self):
        records = [
            {
                "type": "assistant",
                "message": {
                    "id": "msg_tool_id_collision",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_first_ABCDEF",
                            "name": "Read",
                            "input": {"file_path": "first.txt"},
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_second_ABCDEF",
                            "name": "Read",
                            "input": {"file_path": "second.txt"},
                        },
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_first_ABCDEF",
                            "content": "FIRST_RESULT_MARKER",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_second_ABCDEF",
                            "content": "SECOND_RESULT_MARKER",
                        },
                    ],
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "claude_tid_collision.jsonl")
            source.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            self.vcc.compile_pass(str(source), output_dir=tmp)
            full = Path(tmp, "claude_tid_collision.txt").read_text(encoding="utf-8")
            brief = Path(tmp, "claude_tid_collision.min.txt").read_text(encoding="utf-8")

        def result_range(summary):
            match = re.search(r"\([^:]+:\d+-\d+,(\d+)-(\d+)\)", summary)
            self.assertIsNotNone(match)
            return int(match.group(1)), int(match.group(2))

        brief_lines = brief.splitlines()
        first_summary = next(line for line in brief_lines if '* Read "first.txt"' in line)
        second_summary = next(line for line in brief_lines if '* Read "second.txt"' in line)
        first_range = result_range(first_summary)
        second_range = result_range(second_summary)

        self.assertNotEqual(first_range, second_range)
        full_lines = full.splitlines()
        first_result = "\n".join(full_lines[first_range[0] - 1:first_range[1]])
        second_result = "\n".join(full_lines[second_range[0] - 1:second_range[1]])
        self.assertIn("FIRST_RESULT_MARKER", first_result)
        self.assertNotIn("SECOND_RESULT_MARKER", first_result)
        self.assertIn("SECOND_RESULT_MARKER", second_result)
        self.assertNotIn("FIRST_RESULT_MARKER", second_result)

    def test_claude_compaction_splits_into_oldest_then_newest_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.vcc.compile_pass(str(FIXTURES / "claude_compacted.jsonl"), output_dir=tmp)
            first = Path(tmp, "claude_compacted_1.txt").read_text(encoding="utf-8")
            second = Path(tmp, "claude_compacted_2.txt").read_text(encoding="utf-8")

        self.assertIn("Old compaction-order-marker question.", first)
        self.assertNotIn("Newest compaction-order-marker question.", first)
        self.assertIn("Newest compaction-order-marker question.", second)
        self.assertNotIn("Old compaction-order-marker question.", second)
        self.assertIn("[compact summary", second)
        self.assertNotIn("Hidden compact summary content", second)

    def test_claude_live_partial_final_json_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.vcc.compile_pass(
                    str(FIXTURES / "claude_live_partial.jsonl"), output_dir=tmp
                )
            full = Path(tmp, "claude_live_partial.txt").read_text(encoding="utf-8")

        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("Valid content before a live partial record.", full)
        self.assertIn("Valid response before the live partial record.", full)
        self.assertNotIn("unfinished", full)

    def test_claude_malformed_completed_line_warns_and_parsing_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.vcc.compile_pass(str(FIXTURES / "claude_malformed.jsonl"), output_dir=tmp)
            full = Path(tmp, "claude_malformed.txt").read_text(encoding="utf-8")

        self.assertIn("claude_malformed.jsonl:2: malformed JSON record", stderr.getvalue())
        self.assertIn("Valid content before a malformed completed line.", full)
        self.assertIn("Valid response after the malformed completed line.", full)

    def test_codex_basic_compiles_messages_and_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.vcc.compile_pass(str(FIXTURES / "codex_basic.jsonl"), output_dir=tmp)
            full = Path(tmp, "codex_basic.txt").read_text(encoding="utf-8")
            brief = Path(tmp, "codex_basic.min.txt").read_text(encoding="utf-8")

        self.assertIn("[system]", full)
        self.assertIn("Developer instructions", full)
        self.assertIn("[assistant]", full)
        self.assertIn(">>>tool_call exec_command:abc123", full)
        self.assertIn("[tool] exec_command:abc123", full)
        self.assertIn("[image]", full)
        self.assertNotIn("encrypted_content", full)
        self.assertIn('* exec_command "pwd && ls -la"', brief)
        self.assertNotIn("Developer instructions", brief)

    def test_codex_compaction_splits_without_replacement_history_duplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.vcc.compile_pass(str(FIXTURES / "codex_compacted.jsonl"), output_dir=tmp)
            first = Path(tmp, "codex_compacted_1.txt").read_text(encoding="utf-8")
            second = Path(tmp, "codex_compacted_2.txt").read_text(encoding="utf-8")

        self.assertIn("first prompt", first)
        self.assertIn("first answer", first)
        self.assertIn("second prompt", second)
        self.assertIn(">>>tool_call exec_command:def456", second)
        self.assertNotIn("old hidden summary", second)
        self.assertNotIn("first prompt", second)

    def test_codex_grep_uses_matching_blocks_and_hides_reasoning(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.vcc.compile_pass(
                str(FIXTURES / "codex_basic.jsonl"),
                output_dir=tmp,
                grep_pattern=re.compile("repo"),
            )
            view = Path(tmp, "codex_basic.view.txt").read_text(encoding="utf-8")

        self.assertIn("[user]", view)
        self.assertIn("(codex_basic.txt:", view)
        self.assertIn("please inspect the repo", view)
        self.assertNotIn("encrypted_content", view)

    def test_codex_grep_redacts_embedded_base64_blobs(self):
        payload = "iVBORw0KGgo" + ("Aa0/" * 300)
        line = f'AQA result image="data:image/png;base64,{payload}"'

        rendered = self.vcc.match_lines(
            [line],
            re.compile("AQA"),
            "codex_blob.txt",
            42,
        )

        self.assertIn("data:image/png;base64,[omitted", rendered[1])
        self.assertNotIn(payload, rendered[1])

    def test_omp_basic_compiles_messages_tools_and_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.vcc.compile_pass(str(FIXTURES / "omp_basic.jsonl"), output_dir=tmp)
            full = Path(tmp, "omp_basic.txt").read_text(encoding="utf-8")
            brief = Path(tmp, "omp_basic.min.txt").read_text(encoding="utf-8")

        self.assertIn("[session_init]", full)
        self.assertIn("inspect the OMP repo history", full)
        self.assertIn("[model_change]", full)
        self.assertIn("[user]", full)
        self.assertIn("please inspect the OMP repo history", full)
        self.assertIn(">>>tool_call read:123456", full)
        self.assertIn(">>>tool_call bash:mpbash", full)
        self.assertIn("[tool] read:123456", full)
        self.assertIn("[tool_error] bash:mpbash", full)
        self.assertIn("[custom] irc:incoming", full)
        self.assertIn("[branch]", full)
        self.assertIn('* read "packages/coding-agent/src/session/session-listing.ts"', brief)
        self.assertIn('* bash "git status --short"', brief)

    def test_reasonix_basic_compiles_messages_reasoning_and_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.vcc.compile_pass(str(FIXTURES / "reasonix_basic.jsonl"), output_dir=tmp)
            full = Path(tmp, "reasonix_basic.txt").read_text(encoding="utf-8")
            brief = Path(tmp, "reasonix_basic.min.txt").read_text(encoding="utf-8")

        self.assertIn("[system]", full)
        self.assertIn("[user]", full)
        self.assertIn("please inspect Reasonix VCC history", full)
        self.assertIn("[image]", full)
        self.assertIn(">>>thinking", full)
        self.assertIn("private Reasonix reasoning text", full)
        self.assertIn(">>>tool_call read_file:123456", full)
        self.assertIn(">>>tool_call bash:987654", full)
        self.assertIn("[tool] read_file:123456", full)
        self.assertIn("[tool_error] bash:987654", full)
        self.assertIn("Search uses BM25.", full)
        self.assertNotIn("Reasonix system prompt should stay out of the brief view.", brief)
        self.assertNotIn("private Reasonix reasoning text", brief)
        self.assertIn('* read_file "internal/history/search.go"', brief)
        self.assertIn('* bash "go test ./internal/history"', brief)

    def test_t3code_projection_compiles_messages_and_activities(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.vcc.compile_pass(str(FIXTURES / "t3code_projection.jsonl"), output_dir=tmp)
            full = Path(tmp, "t3code_projection.txt").read_text(encoding="utf-8")
            brief = Path(tmp, "t3code_projection.min.txt").read_text(encoding="utf-8")

        self.assertIn("[session_init]", full)
        self.assertIn("please inspect the T3 Code history", full)
        self.assertIn("[assistant]", full)
        self.assertIn("[custom] activity:tool.completed", full)
        self.assertIn("sqlite3 state.sqlite .tables", full)
        self.assertNotIn("[session_init]", brief)
        self.assertIn("I will inspect the T3 Code projection tables.", brief)


if __name__ == "__main__":
    unittest.main()
