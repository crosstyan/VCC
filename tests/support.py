import contextlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXTURES = ROOT / "tests" / "fixtures"
_ENV_KEYS = (
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "PI_CODING_AGENT_DIR",
    "T3CODE_HOME",
    "REASONIX_HOME",
    "VCC_T3CODE_DB",
    "VCC_CACHE_DIR",
)


class HarnessStores:
    pass


def build_harness_stores(root):
    stores = HarnessStores()
    stores.root = Path(root)
    stores.claude_config = stores.root / "claude"
    stores.codex_home = stores.root / "codex"
    stores.omp_agent = stores.root / "omp-agent"
    stores.t3_home = stores.root / "t3"
    stores.reasonix_home = stores.root / "reasonix"
    stores.workspace = stores.root / "workspace" / "example-project"
    stores.workspace.mkdir(parents=True)
    stores.claude_project = (
        stores.claude_config / "projects" / "-workspace-example-project"
    )
    stores.claude_session = stores.claude_project / "claude_basic.jsonl"
    stores.claude_subagent_session = (
        stores.claude_project
        / "claude-session-basic"
        / "subagents"
        / "agent-history.jsonl"
    )
    stores.codex_session = (
        stores.codex_home / "sessions" / "2026" / "06" / "codex_basic.jsonl"
    )
    stores.omp_session = (
        stores.omp_agent / "sessions" / "--tmp--omp-project" / "omp_basic.jsonl"
    )
    stores.reasonix_session = (
        stores.reasonix_home
        / "projects"
        / "-tmp-reasonix-project"
        / "sessions"
        / "20260623-010203.123456789-deepseek-v4-flash.jsonl"
    )
    stores.reasonix_subagent_session = (
        stores.reasonix_home
        / "projects"
        / "-tmp-reasonix-project"
        / "sessions"
        / "subagents"
        / "sa_reasonix_visible.jsonl"
    )
    stores.reasonix_cleanup_session = (
        stores.reasonix_home
        / "sessions"
        / "20260623-010204.123456789-hidden-model.jsonl"
    )

    stores.claude_session.parent.mkdir(parents=True)
    stores.claude_subagent_session.parent.mkdir(parents=True)
    stores.codex_session.parent.mkdir(parents=True)
    stores.omp_session.parent.mkdir(parents=True)
    stores.reasonix_session.parent.mkdir(parents=True)
    stores.reasonix_subagent_session.parent.mkdir(parents=True)
    stores.reasonix_cleanup_session.parent.mkdir(parents=True)

    claude_fixture = (FIXTURES / "claude_basic.jsonl").read_text(encoding="utf-8")
    claude_fixture = claude_fixture.replace(
        "/workspace/example-project", str(stores.workspace)
    )
    stores.claude_session.write_text(claude_fixture, encoding="utf-8")
    stores.claude_subagent_session.write_text(
        claude_fixture.replace(
            "sanitized Claude fixture marker", "nested Claude subagent marker"
        ),
        encoding="utf-8",
    )
    (stores.claude_project / "journal.jsonl").write_text(
        '{"type":"progress","timestamp":"2026-06-24T09:00:00Z",'
        '"data":{"journal":true}}\n',
        encoding="utf-8",
    )
    (stores.claude_project / "metadata-only.jsonl").write_text(
        "\n".join(
            [
                '{"type":"assistant","timestamp":"2026-06-24T09:00:01Z",'
                '"message":{"model":"claude-sonnet-4-6"}}',
                '{"type":"ai-title","timestamp":"2026-06-24T09:00:02Z",'
                '"aiTitle":"Metadata only"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    nested_nonagent = stores.claude_project / "other" / "nested.jsonl"
    nested_nonagent.parent.mkdir(parents=True)
    nested_nonagent.write_text(claude_fixture, encoding="utf-8")

    shutil.copyfile(FIXTURES / "codex_basic.jsonl", stores.codex_session)
    shutil.copyfile(FIXTURES / "omp_basic.jsonl", stores.omp_session)
    shutil.copyfile(FIXTURES / "reasonix_basic.jsonl", stores.reasonix_session)
    shutil.copyfile(FIXTURES / "reasonix_basic.jsonl", stores.reasonix_subagent_session)
    shutil.copyfile(FIXTURES / "reasonix_basic.jsonl", stores.reasonix_cleanup_session)
    stores.reasonix_session.with_name(stores.reasonix_session.name + ".meta").write_text(
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
    stores.reasonix_subagent_session.with_suffix(".meta.json").write_text(
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
    stores.reasonix_cleanup_session.with_suffix(".cleanup-pending.json").write_text(
        json.dumps({"operation": "delete", "createdAt": 1782147723000}),
        encoding="utf-8",
    )
    return stores


@contextlib.contextmanager
def patched_harness_env(stores):
    original = {key: os.environ.get(key) for key in _ENV_KEYS}
    os.environ.update(
        {
            "CLAUDE_CONFIG_DIR": str(stores.claude_config),
            "CODEX_HOME": str(stores.codex_home),
            "PI_CODING_AGENT_DIR": str(stores.omp_agent),
            "T3CODE_HOME": str(stores.t3_home),
            "REASONIX_HOME": str(stores.reasonix_home),
            "VCC_CACHE_DIR": str(stores.root / "cache"),
        }
    )
    os.environ.pop("VCC_T3CODE_DB", None)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def build_t3code_store(db_path, codex_session):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
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
              thread_id, project_id, title, created_at, updated_at, deleted_at,
              model_selection_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
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
              message_id, thread_id, turn_id, role, text, is_streaming,
              created_at, updated_at, attachments_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
              activity_id, thread_id, turn_id, tone, kind, summary, payload_json,
              created_at, sequence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        conn.execute(
            """
            INSERT INTO projection_threads (
              thread_id, project_id, title, created_at, updated_at, deleted_at,
              model_selection_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "thread-2",
                "project-1",
                "Second T3 fixture",
                "2026-06-23T01:01:00Z",
                "2026-06-23T01:01:02Z",
                None,
                json.dumps({"provider": "codex", "model": "gpt-5.5"}),
            ),
        )
        conn.execute(
            """
            INSERT INTO projection_thread_messages (
              message_id, thread_id, turn_id, role, text, is_streaming,
              created_at, updated_at, attachments_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "msg-2",
                "thread-2",
                "turn-2",
                "user",
                "second T3 Code thread",
                0,
                "2026-06-23T01:01:01Z",
                "2026-06-23T01:01:01Z",
                None,
            ),
        )
        conn.execute(
            "INSERT INTO projection_thread_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "thread-2",
                "ready",
                "codex",
                None,
                None,
                None,
                None,
                "2026-06-23T01:01:02Z",
                "full-access",
                "codex",
            ),
        )

    provider_log = db_path.parent / "logs" / "provider" / "thread-1.log"
    provider_log.parent.mkdir(parents=True)
    provider_log.write_text(
        "[fixture] NTIVE: "
        + json.dumps(
            {
                "provider": "codex",
                "payload": {"thread": {"path": str(Path(codex_session))}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return db_path


class HarnessStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        stores = build_harness_stores(self.tmp.name)
        self.stores = stores
        self.__dict__.update(stores.__dict__)
        env_context = patched_harness_env(stores)
        env_context.__enter__()
        self.addCleanup(env_context.__exit__, None, None, None)
