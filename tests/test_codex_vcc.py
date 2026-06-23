import importlib.util
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VCC_PATH = ROOT / "skills" / "conversation-compiler" / "scripts" / "VCC.py"
FIXTURES = ROOT / "tests" / "fixtures"


def load_vcc_module():
    spec = importlib.util.spec_from_file_location("vcc_module", VCC_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CodexVccTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vcc = load_vcc_module()

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


if __name__ == "__main__":
    unittest.main()
