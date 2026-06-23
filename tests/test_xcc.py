import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vcc_history import xcc


FIXTURES = ROOT / "tests" / "fixtures"
VCC_PATH = ROOT / "skills" / "conversation-compiler" / "scripts" / "VCC.py"


class XccTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.codex_home = self.root / "codex"
        self.omp_agent = self.root / "omp-agent"
        self.codex_session = self.codex_home / "sessions" / "2026" / "06" / "codex_basic.jsonl"
        self.omp_session = self.omp_agent / "sessions" / "--tmp--omp-project" / "omp_basic.jsonl"
        self.codex_session.parent.mkdir(parents=True)
        self.omp_session.parent.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "codex_basic.jsonl", self.codex_session)
        shutil.copyfile(FIXTURES / "omp_basic.jsonl", self.omp_session)

        self.original_env = {
            "CODEX_HOME": os.environ.get("CODEX_HOME"),
            "PI_CODING_AGENT_DIR": os.environ.get("PI_CODING_AGENT_DIR"),
            "VCC_COMPILER_PATH": os.environ.get("VCC_COMPILER_PATH"),
            "VCC_ROOT": os.environ.get("VCC_ROOT"),
        }
        os.environ["CODEX_HOME"] = str(self.codex_home)
        os.environ["PI_CODING_AGENT_DIR"] = str(self.omp_agent)
        os.environ["VCC_COMPILER_PATH"] = str(VCC_PATH)
        os.environ["VCC_ROOT"] = str(ROOT)
        xcc._VCC_MODULE = None

    def tearDown(self):
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        xcc._VCC_MODULE = None
        self.tmp.cleanup()

    def test_collect_sessions_reads_codex_and_omp_metadata(self):
        records = xcc.collect_sessions("all")

        self.assertEqual({record["harness"] for record in records}, {"codex", "omp"})
        omp = next(record for record in records if record["harness"] == "omp")
        self.assertEqual(omp["cwd"], "/tmp/omp-project")
        self.assertEqual(omp["title"], "OMP fixture")
        self.assertEqual(omp["provider"], "anthropic")
        self.assertEqual(omp["model"], "claude-sonnet-4-5")

    def test_search_cli_returns_readable_omp_refs(self):
        result = CliRunner().invoke(xcc.main, ["search", "OMP repo", "--harness", "omp", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.output)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0]["harness"], "omp")
        self.assertIn("omp_basic.txt:", hits[0]["ref"])
        self.assertIn("OMP repo history", xcc.read_ref(hits[0]["ref"]))


if __name__ == "__main__":
    unittest.main()
