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


if __name__ == "__main__":
    unittest.main()
