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
        self.reasonix_home = self.root / "reasonix"
        self.codex_session = self.codex_home / "sessions" / "2026" / "06" / "codex_basic.jsonl"
        self.omp_session = self.omp_agent / "sessions" / "--tmp--omp-project" / "omp_basic.jsonl"
        self.reasonix_session = (
            self.reasonix_home
            / "projects"
            / "-tmp-reasonix-project"
            / "sessions"
            / "20260623-010203.123456789-deepseek-v4-flash.jsonl"
        )
        self.reasonix_subagent_session = (
            self.reasonix_home
            / "projects"
            / "-tmp-reasonix-project"
            / "sessions"
            / "subagents"
            / "sa_reasonix_visible.jsonl"
        )
        self.reasonix_cleanup_session = (
            self.reasonix_home
            / "sessions"
            / "20260623-010204.123456789-hidden-model.jsonl"
        )
        self.codex_session.parent.mkdir(parents=True)
        self.omp_session.parent.mkdir(parents=True)
        self.reasonix_session.parent.mkdir(parents=True)
        self.reasonix_subagent_session.parent.mkdir(parents=True)
        self.reasonix_cleanup_session.parent.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "codex_basic.jsonl", self.codex_session)
        shutil.copyfile(FIXTURES / "omp_basic.jsonl", self.omp_session)
        shutil.copyfile(FIXTURES / "reasonix_basic.jsonl", self.reasonix_session)
        shutil.copyfile(FIXTURES / "reasonix_basic.jsonl", self.reasonix_subagent_session)
        shutil.copyfile(FIXTURES / "reasonix_basic.jsonl", self.reasonix_cleanup_session)
        self.reasonix_session.with_name(self.reasonix_session.name + ".meta").write_text(
            json.dumps(
                {
                    "id": "reasonix-session",
                    "created_at": "2026-06-23T01:02:03Z",
                    "updated_at": "2026-06-23T01:02:04Z",
                    "workspace_root": "/tmp/reasonix-project",
                    "topic_title": "Reasonix fixture",
                    "model": "deepseek-flash/deepseek-v4-flash",
                    "schema_version": 1,
                    "turns": 1,
                    "preview": "please inspect Reasonix VCC history",
                }
            ),
            encoding="utf-8",
        )
        self.reasonix_subagent_session.with_suffix(".meta.json").write_text(
            json.dumps(
                {
                    "ref": "sa_reasonix_visible",
                    "createdAt": "2026-06-23T01:02:05Z",
                    "updatedAt": "2026-06-23T01:02:06Z",
                    "status": "completed",
                    "kind": "task",
                    "name": "history lookup",
                    "workspaceRoot": "/tmp/reasonix-project",
                    "parentSession": "20260623-010203.123456789-deepseek-v4-flash",
                    "model": "deepseek-flash/deepseek-v4-flash",
                }
            ),
            encoding="utf-8",
        )
        self.reasonix_cleanup_session.with_suffix(".cleanup-pending.json").write_text(
            json.dumps({"operation": "delete", "createdAt": 1782147723000}),
            encoding="utf-8",
        )

        self.original_env = {
            "CODEX_HOME": os.environ.get("CODEX_HOME"),
            "PI_CODING_AGENT_DIR": os.environ.get("PI_CODING_AGENT_DIR"),
            "REASONIX_HOME": os.environ.get("REASONIX_HOME"),
            "VCC_COMPILER_PATH": os.environ.get("VCC_COMPILER_PATH"),
            "VCC_ROOT": os.environ.get("VCC_ROOT"),
        }
        os.environ["CODEX_HOME"] = str(self.codex_home)
        os.environ["PI_CODING_AGENT_DIR"] = str(self.omp_agent)
        os.environ["REASONIX_HOME"] = str(self.reasonix_home)
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

        self.assertEqual({record["harness"] for record in records}, {"codex", "omp", "reasonix"})
        omp = next(record for record in records if record["harness"] == "omp")
        self.assertEqual(omp["cwd"], "/tmp/omp-project")
        self.assertEqual(omp["title"], "OMP fixture")
        self.assertEqual(omp["provider"], "anthropic")
        self.assertEqual(omp["model"], "claude-sonnet-4-5")
        reasonix = next(record for record in records if record.get("session_id") == "reasonix-session")
        self.assertEqual(reasonix["session_id"], "reasonix-session")
        self.assertEqual(reasonix["cwd"], "/tmp/reasonix-project")
        self.assertEqual(reasonix["title"], "Reasonix fixture")
        self.assertEqual(reasonix["provider"], "deepseek-flash")
        self.assertEqual(reasonix["model"], "deepseek-v4-flash")
        subagent = next(record for record in records if record.get("session_id") == "sa_reasonix_visible")
        self.assertEqual(subagent["source"], "subagent")
        self.assertEqual(subagent["cwd"], "/tmp/reasonix-project")
        self.assertNotIn(str(self.reasonix_cleanup_session), {record["path"] for record in records})

    def test_search_cli_returns_readable_omp_refs(self):
        result = CliRunner().invoke(xcc.main, ["search", "OMP repo", "--harness", "omp", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.output)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0]["harness"], "omp")
        self.assertIn("omp_basic.txt:", hits[0]["ref"])
        self.assertIn("OMP repo history", xcc.read_ref(hits[0]["ref"]))

    def test_reasonix_cwd_filter_and_search_refs_are_readable(self):
        result = CliRunner().invoke(
            xcc.main,
            ["list", "--harness", "reasonix", "--cwd", "/tmp/reasonix-project", "--json"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        records = json.loads(result.output)
        paths = {record["path"] for record in records}
        self.assertIn(str(self.reasonix_session), paths)
        self.assertIn(str(self.reasonix_subagent_session), paths)
        self.assertNotIn(str(self.reasonix_cleanup_session), paths)

        result = CliRunner().invoke(
            xcc.main,
            ["search", "Reasonix VCC", "--harness", "reasonix", "--json"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.output)
        self.assertGreaterEqual(len(hits), 1)
        self.assertTrue(all(hit["harness"] == "reasonix" for hit in hits))
        self.assertTrue(any("Reasonix VCC history" in xcc.read_ref(hit["ref"]) for hit in hits))


if __name__ == "__main__":
    unittest.main()
