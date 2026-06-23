import json
import os
import shutil
import sqlite3
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
        self.t3_home = self.root / "t3"
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
            "T3CODE_HOME": os.environ.get("T3CODE_HOME"),
            "REASONIX_HOME": os.environ.get("REASONIX_HOME"),
            "VCC_T3CODE_DB": os.environ.get("VCC_T3CODE_DB"),
            "VCC_CACHE_DIR": os.environ.get("VCC_CACHE_DIR"),
            "VCC_COMPILER_PATH": os.environ.get("VCC_COMPILER_PATH"),
            "VCC_ROOT": os.environ.get("VCC_ROOT"),
        }
        os.environ["CODEX_HOME"] = str(self.codex_home)
        os.environ["PI_CODING_AGENT_DIR"] = str(self.omp_agent)
        os.environ["T3CODE_HOME"] = str(self.t3_home)
        os.environ["REASONIX_HOME"] = str(self.reasonix_home)
        os.environ.pop("VCC_T3CODE_DB", None)
        os.environ["VCC_CACHE_DIR"] = str(self.root / "cache")
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

    def test_t3code_sqlite_threads_are_discovered_and_searchable(self):
        db_path = self.t3_home / "userdata" / "state.sqlite"
        db_path.parent.mkdir(parents=True)
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE projection_projects (
                  project_id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  workspace_root TEXT NOT NULL,
                  scripts_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  deleted_at TEXT,
                  default_model_selection_json TEXT
                );
                CREATE TABLE projection_threads (
                  thread_id TEXT PRIMARY KEY,
                  project_id TEXT NOT NULL,
                  title TEXT NOT NULL,
                  branch TEXT,
                  worktree_path TEXT,
                  latest_turn_id TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  deleted_at TEXT,
                  runtime_mode TEXT NOT NULL DEFAULT 'full-access',
                  interaction_mode TEXT NOT NULL DEFAULT 'default',
                  model_selection_json TEXT,
                  archived_at TEXT,
                  latest_user_message_at TEXT,
                  pending_approval_count INTEGER NOT NULL DEFAULT 0,
                  pending_user_input_count INTEGER NOT NULL DEFAULT 0,
                  has_actionable_proposed_plan INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE projection_thread_messages (
                  message_id TEXT PRIMARY KEY,
                  thread_id TEXT NOT NULL,
                  turn_id TEXT,
                  role TEXT NOT NULL,
                  text TEXT NOT NULL,
                  is_streaming INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  attachments_json TEXT
                );
                CREATE TABLE projection_thread_activities (
                  activity_id TEXT PRIMARY KEY,
                  thread_id TEXT NOT NULL,
                  turn_id TEXT,
                  tone TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  summary TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  sequence INTEGER
                );
                CREATE TABLE projection_thread_sessions (
                  thread_id TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  provider_name TEXT,
                  provider_session_id TEXT,
                  provider_thread_id TEXT,
                  active_turn_id TEXT,
                  last_error TEXT,
                  updated_at TEXT NOT NULL,
                  runtime_mode TEXT NOT NULL DEFAULT 'full-access',
                  provider_instance_id TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO projection_projects VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "project-1",
                    "T3 project",
                    "/tmp/t3-project",
                    "[]",
                    "2026-06-23T01:00:00Z",
                    "2026-06-23T01:00:00Z",
                    None,
                    None,
                ),
            )
            conn.execute(
                """
                INSERT INTO projection_threads (
                  thread_id, project_id, title, created_at, updated_at, deleted_at, model_selection_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "thread-1",
                    "project-1",
                    "T3 fixture",
                    "2026-06-23T01:00:00Z",
                    "2026-06-23T01:00:04Z",
                    None,
                    json.dumps({"provider": "codex", "model": "gpt-5.5"}),
                ),
            )
            conn.execute(
                """
                INSERT INTO projection_thread_messages (
                  message_id, thread_id, turn_id, role, text, is_streaming, created_at, updated_at, attachments_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "msg-1",
                    "thread-1",
                    "turn-1",
                    "user",
                    "please inspect the T3 Code history",
                    0,
                    "2026-06-23T01:00:01Z",
                    "2026-06-23T01:00:01Z",
                    None,
                ),
            )
            conn.execute(
                """
                INSERT INTO projection_thread_activities (
                  activity_id, thread_id, turn_id, tone, kind, summary, payload_json, created_at, sequence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "activity-1",
                    "thread-1",
                    "turn-1",
                    "tool",
                    "tool.completed",
                    "Ran command",
                    json.dumps({"detail": "sqlite3 state.sqlite .tables"}),
                    "2026-06-23T01:00:02Z",
                    1,
                ),
            )
            conn.execute(
                "INSERT INTO projection_thread_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "thread-1",
                    "ready",
                    "codex",
                    None,
                    None,
                    None,
                    None,
                    "2026-06-23T01:00:03Z",
                    "full-access",
                    "codex",
                ),
            )

        records = xcc.collect_sessions("t3code")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["harness"], "t3code")
        self.assertEqual(records[0]["cwd"], "/tmp/t3-project")

        result = CliRunner().invoke(xcc.main, ["search", "T3 Code history", "--harness", "t3code", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.output)
        self.assertEqual(hits[0]["harness"], "t3code")
        self.assertIn("please inspect the T3 Code history", xcc.read_ref(hits[0]["ref"]))


if __name__ == "__main__":
    unittest.main()
