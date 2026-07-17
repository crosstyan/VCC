import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vcc_history.cli import main
from vcc_history.history import service, vcc
from vcc_history.history.errors import HistoryError
from vcc_history.history.refs import read_ref
from vcc_history.history.registry import DESCRIPTORS, HARNESS_CHOICES, HARNESS_IDS
from vcc_history.history.sources.base import file_alias_keys, finalize_record
from vcc_history.history.sources.codex import record_from_path as codex_record_from_path
from vcc_history.history.sources.reasonix import _workspace_slug
from vcc_history.history.sources.t3code import _export_path


FIXTURES = ROOT / "tests" / "fixtures"


class _StaticSource:
    def __init__(self, records):
        self.records = records

    def collect(self, config, include_delegated=False):
        return list(self.records)


class XccTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.claude_config = self.root / "claude"
        self.codex_home = self.root / "codex"
        self.omp_agent = self.root / "omp-agent"
        self.t3_home = self.root / "t3"
        self.reasonix_home = self.root / "reasonix"
        self.workspace = self.root / "workspace" / "example-project"
        self.workspace.mkdir(parents=True)
        self.claude_project = self.claude_config / "projects" / "-workspace-example-project"
        self.claude_session = self.claude_project / "claude_basic.jsonl"
        self.claude_subagent_session = (
            self.claude_project / "claude-session-basic" / "subagents" / "agent-history.jsonl"
        )
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
        self.claude_session.parent.mkdir(parents=True)
        self.claude_subagent_session.parent.mkdir(parents=True)
        self.codex_session.parent.mkdir(parents=True)
        self.omp_session.parent.mkdir(parents=True)
        self.reasonix_session.parent.mkdir(parents=True)
        self.reasonix_subagent_session.parent.mkdir(parents=True)
        self.reasonix_cleanup_session.parent.mkdir(parents=True)
        claude_fixture = (FIXTURES / "claude_basic.jsonl").read_text(encoding="utf-8")
        claude_fixture = claude_fixture.replace(
            "/workspace/example-project", str(self.workspace)
        )
        self.claude_session.write_text(claude_fixture, encoding="utf-8")
        self.claude_subagent_session.write_text(
            claude_fixture.replace(
                "sanitized Claude fixture marker", "nested Claude subagent marker"
            ),
            encoding="utf-8",
        )
        (self.claude_project / "journal.jsonl").write_text(
            '{"type":"progress","timestamp":"2026-06-24T09:00:00Z","data":{"journal":true}}\n',
            encoding="utf-8",
        )
        (self.claude_project / "metadata-only.jsonl").write_text(
            "\n".join(
                [
                    '{"type":"assistant","timestamp":"2026-06-24T09:00:01Z","message":{"model":"claude-sonnet-4-6"}}',
                    '{"type":"ai-title","timestamp":"2026-06-24T09:00:02Z","aiTitle":"Metadata only"}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        nested_nonagent = self.claude_project / "other" / "nested.jsonl"
        nested_nonagent.parent.mkdir(parents=True)
        nested_nonagent.write_text(claude_fixture, encoding="utf-8")
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
            "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR"),
            "CODEX_HOME": os.environ.get("CODEX_HOME"),
            "PI_CODING_AGENT_DIR": os.environ.get("PI_CODING_AGENT_DIR"),
            "T3CODE_HOME": os.environ.get("T3CODE_HOME"),
            "REASONIX_HOME": os.environ.get("REASONIX_HOME"),
            "VCC_T3CODE_DB": os.environ.get("VCC_T3CODE_DB"),
            "VCC_CACHE_DIR": os.environ.get("VCC_CACHE_DIR"),
        }
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude_config)
        os.environ["CODEX_HOME"] = str(self.codex_home)
        os.environ["PI_CODING_AGENT_DIR"] = str(self.omp_agent)
        os.environ["T3CODE_HOME"] = str(self.t3_home)
        os.environ["REASONIX_HOME"] = str(self.reasonix_home)
        os.environ.pop("VCC_T3CODE_DB", None)
        os.environ["VCC_CACHE_DIR"] = str(self.root / "cache")

    def tearDown(self):
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_collect_sessions_reads_all_harness_metadata(self):
        records = service.collect_sessions("all")

        self.assertEqual(
            {record["harness"] for record in records},
            {"claude", "codex", "omp", "reasonix"},
        )
        claude = next(record for record in records if record["harness"] == "claude")
        self.assertEqual(claude["path"], str(self.claude_session))
        self.assertEqual(claude["session_id"], "claude-session-basic")
        self.assertEqual(claude["cwd"], str(self.workspace))
        self.assertEqual(claude["title"], "Sanitized Claude fixture")
        self.assertEqual(claude["model"], "claude-opus-4-1")
        self.assertEqual(claude["created_at"], "2026-06-24T10:00:00Z")
        self.assertEqual(claude["updated_at"], "2026-06-24T10:00:07Z")
        self.assertEqual(claude["timestamp"], claude["updated_at"])
        self.assertEqual(claude["kind"], "main")
        self.assertIsNone(claude["source"])
        claude_records = [record for record in records if record["harness"] == "claude"]
        self.assertEqual(claude_records, [claude])
        codex = next(record for record in records if record["harness"] == "codex")
        self.assertEqual(codex["created_at"], "2026-04-07T06:38:38.648000Z")
        self.assertEqual(codex["updated_at"], "2026-04-07T06:40:11Z")
        self.assertEqual(codex["timestamp"], codex["updated_at"])
        self.assertEqual(codex["kind"], "main")
        omp = next(record for record in records if record["harness"] == "omp")
        self.assertEqual(omp["cwd"], "/tmp/omp-project")
        self.assertEqual(omp["title"], "OMP fixture")
        self.assertEqual(omp["provider"], "anthropic")
        self.assertEqual(omp["model"], "claude-sonnet-4-5")
        self.assertEqual(omp["created_at"], "2026-06-23T00:00:00Z")
        self.assertEqual(omp["updated_at"], "2026-06-23T00:00:06Z")
        self.assertEqual(omp["timestamp"], omp["updated_at"])
        self.assertEqual(omp["kind"], "main")
        reasonix = next(record for record in records if record.get("session_id") == "reasonix-session")
        self.assertEqual(reasonix["session_id"], "reasonix-session")
        self.assertEqual(reasonix["cwd"], "/tmp/reasonix-project")
        self.assertEqual(reasonix["title"], "Reasonix fixture")
        self.assertEqual(reasonix["provider"], "deepseek-flash")
        self.assertEqual(reasonix["model"], "deepseek-v4-flash")
        self.assertEqual(reasonix["created_at"], "2026-06-23T01:02:03Z")
        self.assertEqual(reasonix["updated_at"], "2026-06-23T01:02:04Z")
        self.assertEqual(reasonix["timestamp"], reasonix["updated_at"])
        self.assertEqual(reasonix["kind"], "main")
        paths = {record["path"] for record in records}
        self.assertNotIn(str(self.reasonix_subagent_session), paths)
        self.assertNotIn(str(self.reasonix_cleanup_session), paths)

    def test_claude_missing_store_is_skipped(self):
        original = os.environ["CLAUDE_CONFIG_DIR"]
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.root / "missing-claude-config")
        try:
            self.assertEqual(service.collect_sessions("claude"), [])
        finally:
            os.environ["CLAUDE_CONFIG_DIR"] = original

    def test_claude_live_partial_utf8_does_not_break_discovery(self):
        with self.claude_session.open("ab") as fh:
            fh.write(b'{"type":"user","message":{"content":"\xe2')

        records = service.collect_sessions("claude")

        self.assertEqual([record["path"] for record in records], [str(self.claude_session)])

    def test_claude_discovery_skips_completed_invalid_utf8_records(self):
        invalid = self.claude_project / "invalid-utf8.jsonl"
        invalid.write_bytes(
            b'{"type":"user","sessionId":"bad","cwd":"/tmp/demo",'
            b'"timestamp":"2026-07-17T00:00:00Z","message":{"role":"user",'
            b'"content":"bad-utf8-\xff"}}\n'
        )

        records = service.collect_sessions("claude")

        self.assertNotIn(str(invalid), {record["path"] for record in records})

    def test_claude_custom_title_wins_and_synthetic_model_is_ignored(self):
        metadata = self.claude_project / "metadata.jsonl"
        metadata.write_text(
            "\n".join(
                [
                    '{"type":"user","sessionId":"metadata","cwd":"/tmp/demo",'
                    '"timestamp":"2026-07-17T00:00:00Z","message":{"role":"user",'
                    '"content":"hello"}}',
                    '{"type":"assistant","sessionId":"metadata","cwd":"/tmp/demo",'
                    '"timestamp":"2026-07-17T00:00:01Z","message":{"role":"assistant",'
                    '"model":"claude-opus-4-8","content":[{"type":"text","text":"answer"}]}}',
                    '{"type":"ai-title","sessionId":"metadata","aiTitle":"Generated title"}',
                    '{"type":"custom-title","sessionId":"metadata",'
                    '"customTitle":"User chosen title"}',
                    '{"type":"assistant","sessionId":"metadata","cwd":"/tmp/demo",'
                    '"timestamp":"2026-07-17T00:00:02Z","message":{"role":"assistant",'
                    '"model":"<synthetic>","content":[{"type":"text","text":"summary"}]}}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        record = next(
            record
            for record in service.collect_sessions("claude")
            if record["path"] == str(metadata)
        )

        self.assertEqual(record["title"], "User chosen title")
        self.assertEqual(record["model"], "claude-opus-4-8")

    def test_claude_delegated_sessions_are_optional_and_only_agent_jsonl_is_nested(self):
        main_records = service.collect_sessions("claude")
        all_records = service.collect_sessions("claude", include_delegated=True)

        self.assertEqual([record["path"] for record in main_records], [str(self.claude_session)])
        self.assertEqual(
            {record["path"] for record in all_records},
            {str(self.claude_session), str(self.claude_subagent_session)},
        )
        subagent = next(record for record in all_records if record["kind"] == "delegated")
        self.assertEqual(subagent["session_id"], "history")
        self.assertEqual(subagent["source"], "subagent")
        self.assertNotIn(
            str(self.claude_project / "other" / "nested.jsonl"),
            {record["path"] for record in all_records},
        )

    def test_reasonix_delegated_sessions_are_optional(self):
        main_records = service.collect_sessions("reasonix")
        all_records = service.collect_sessions("reasonix", include_delegated=True)

        self.assertEqual(
            {record["path"] for record in main_records},
            {str(self.reasonix_session)},
        )
        self.assertEqual(
            {record["path"] for record in all_records},
            {str(self.reasonix_session), str(self.reasonix_subagent_session)},
        )
        delegated = next(record for record in all_records if record["kind"] == "delegated")
        self.assertEqual(delegated["session_id"], "sa_reasonix_visible")
        self.assertEqual(delegated["cwd"], "/tmp/reasonix-project")
        self.assertEqual(delegated["created_at"], "2026-06-23T01:02:05Z")
        self.assertEqual(delegated["updated_at"], "2026-06-23T01:02:06Z")
        self.assertEqual(delegated["source"], "subagent")

    def test_claude_symlink_alias_is_deduplicated_in_favor_of_real_path(self):
        alias = self.claude_project / "claude_alias.jsonl"
        alias.symlink_to(self.claude_session)

        records = service.collect_sessions("claude")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["path"], str(self.claude_session))

    def test_claude_inode_alias_does_not_reclassify_main_as_subagent(self):
        alias = self.claude_project / "claude-a" / "subagents" / "agent-alias.jsonl"
        alias.parent.mkdir(parents=True)
        os.link(self.claude_session, alias)

        records = service.collect_sessions("claude", include_delegated=True)

        main = next(record for record in records if record["session_id"] == "claude-session-basic")
        self.assertEqual(main["path"], str(self.claude_session))
        self.assertEqual(main["kind"], "main")
        self.assertIsNone(main["source"])
        self.assertNotIn(str(alias), {record["path"] for record in records})

    def test_claude_cwd_filter_uses_canonical_paths(self):
        workspace_alias = self.root / "workspace-alias"
        workspace_alias.symlink_to(self.workspace, target_is_directory=True)

        records = service.filter_sessions(
            service.collect_sessions("claude"),
            str(workspace_alias),
            latest=False,
            limit=0,
        )

        self.assertEqual([record["path"] for record in records], [str(self.claude_session)])

    def test_collect_sessions_sorts_mixed_timestamp_formats_by_instant(self):
        records = [
            {"harness": "claude", "path": "/tmp/z-late", "timestamp": "2026-07-17T01:00:00Z"},
            {"harness": "claude", "path": "/tmp/a-same", "timestamp": "2026-07-17T02:00:00+01:00"},
            {"harness": "claude", "path": "/tmp/unknown", "timestamp": None},
            {"harness": "claude", "path": "/tmp/early", "timestamp": "2026-07-17T01:00:00+02:00"},
            {"harness": "claude", "path": "/tmp/middle", "timestamp": "2026-07-17 00:30:00"},
        ]
        with mock.patch(
            "vcc_history.history.service.selected_sources",
            return_value=[_StaticSource(records)],
        ):
            sorted_records = service.collect_sessions("claude")

        self.assertEqual(
            [record["path"] for record in sorted_records],
            [
                "/tmp/unknown",
                "/tmp/early",
                "/tmp/middle",
                "/tmp/a-same",
                "/tmp/z-late",
            ],
        )

    def test_all_harness_dedup_connects_symlink_aliases_and_prefers_t3code(self):
        alias = self.root / "codex-alias.jsonl"
        alias.symlink_to(self.codex_session)
        codex_record = codex_record_from_path(str(alias))
        self.assertIsNotNone(codex_record)

        t3_record = finalize_record(
            {
                "harness": "t3code",
                "path": str(self.codex_session.resolve()),
                "session_id": "thread-alias",
                "created_at": "2026-04-07T06:38:38.648Z",
                "updated_at": "2026-04-07T06:40:11Z",
                "kind": "main",
                "cwd": "/repo",
                "title": "T3 alias",
                "source": str(self.t3_home / "state.sqlite"),
                "provider": "codex",
                "model": "gpt-5.5",
                "alias_keys": file_alias_keys(
                    "t3code", self.codex_session.resolve(), "thread-alias"
                ),
            }
        )
        with mock.patch(
            "vcc_history.history.service.selected_sources",
            return_value=[_StaticSource([codex_record]), _StaticSource([t3_record])],
        ):
            records = service.collect_sessions("all")

        self.assertEqual(records, [t3_record])
        self.assertEqual(records[0]["harness"], "t3code")
        self.assertIn(
            f"path:{self.codex_session.resolve()}",
            records[0]["alias_keys"],
        )

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
        public_records = json.loads(json_result.output)
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

    def test_claude_search_uses_cache_and_returns_readable_refs(self):
        result = CliRunner().invoke(
            main,
            ["search", "sanitized Claude fixture marker", "--harness", "claude", "--json"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.output)
        self.assertGreaterEqual(len(hits), 1)
        self.assertTrue(all(hit["harness"] == "claude" for hit in hits))
        compiled_path = Path(hits[0]["compiled_path"])
        self.assertTrue(
            compiled_path.is_relative_to(self.root.resolve() / "cache" / "compiled" / "claude")
        )
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(compiled_path.parent.stat().st_mode), 0o700)
        self.assertIn("sanitized Claude fixture marker", read_ref(hits[0]["ref"]))
        self.assertEqual(list(self.claude_project.glob("claude_basic*.txt")), [])

    def test_search_refuses_symlinked_cache_directories(self):
        cache_root = self.root / "cache"
        outside = self.root / "outside-cache"
        cache_root.mkdir()
        outside.mkdir()
        (cache_root / "compiled").symlink_to(outside, target_is_directory=True)

        result = CliRunner().invoke(
            main,
            ["search", "sanitized Claude fixture marker", "--harness", "claude", "--json"],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Refusing symlinked VCC cache directory", result.output)
        self.assertEqual(list(outside.iterdir()), [])

    def test_search_does_not_hide_compiler_failures(self):
        records = service.collect_sessions("claude")
        with mock.patch.object(
            service,
            "compile_session",
            side_effect=RuntimeError("compiler bug"),
        ):
            with self.assertRaisesRegex(RuntimeError, "compiler bug"):
                service.search_sessions(records, re.compile("fixture"))

    def test_claude_search_isolates_same_basename_sessions(self):
        second_project = self.claude_config / "projects" / "-workspace-second-project"
        second_project.mkdir(parents=True)
        second_session = second_project / self.claude_session.name
        second_fixture = self.claude_session.read_text(encoding="utf-8").replace(
            "claude-session-basic", "claude-session-second"
        )
        second_session.write_text(second_fixture, encoding="utf-8")

        result = CliRunner().invoke(
            main,
            ["search", "sanitized Claude fixture marker", "--harness", "claude", "--json"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.output)
        by_session = {hit["session_path"]: hit for hit in hits}
        self.assertIn(str(self.claude_session), by_session)
        self.assertIn(str(second_session), by_session)
        first_compiled = Path(by_session[str(self.claude_session)]["compiled_path"])
        second_compiled = Path(by_session[str(second_session)]["compiled_path"])
        self.assertEqual(first_compiled.name, second_compiled.name)
        self.assertNotEqual(first_compiled.parent, second_compiled.parent)
        self.assertIn("sanitized Claude fixture marker", read_ref(by_session[str(second_session)]["ref"]))

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

    def test_known_session_harness_bypasses_detection_for_compile_and_search(self):
        source = self.claude_project / "poisoned.jsonl"
        source.write_text(
            "\n".join(
                [
                    '{"role":"user","content":"reasonix poison"}',
                    '{"type":"user","sessionId":"poisoned","cwd":"/tmp/poisoned",'
                    '"timestamp":"2026-07-17T00:00:00Z","message":{"role":"user",'
                    '"content":"threaded harness marker"}}',
                    '{"type":"assistant","sessionId":"poisoned","cwd":"/tmp/poisoned",'
                    '"timestamp":"2026-07-17T00:00:01Z","message":{"role":"assistant",'
                    '"content":[{"type":"text","text":"threaded harness response"}]}}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        record = next(
            record
            for record in service.collect_sessions("claude")
            if record["path"] == str(source)
        )
        self.assertEqual(vcc.detect_format(vcc.lex(source)), "reasonix")

        with mock.patch.object(
            vcc,
            "detect_format",
            side_effect=AssertionError("known harness must bypass detection"),
        ):
            compiled = service.compile_session(
                record["path"],
                output_dir=str(self.root / "known-compile"),
                harness=record["harness"],
            )
            hits = service.search_sessions([record], re.compile("threaded harness marker"))

        full = Path(compiled[0][0]).read_text(encoding="utf-8")
        self.assertIn("threaded harness marker", full)
        self.assertTrue(hits)
        self.assertTrue(any("threaded harness marker" in line for line in hits[0]["lines"]))

    def test_explicit_claude_compile_still_writes_beside_source(self):
        result = CliRunner().invoke(main, ["compile", str(self.claude_session)])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(self.claude_session.with_suffix(".txt").is_file())
        self.assertTrue(self.claude_session.with_name("claude_basic.min.txt").is_file())
        self.assertFalse((self.root / "cache" / "compiled").exists())

    def test_claude_search_returns_newest_compaction_chunk_first(self):
        compacted = self.claude_project / "claude_compacted.jsonl"
        compacted.write_text(
            (FIXTURES / "claude_compacted.jsonl")
            .read_text(encoding="utf-8")
            .replace("/workspace/example-project", str(self.workspace)),
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            ["search", "compaction-order-marker", "--harness", "claude", "--json"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.output)
        self.assertGreaterEqual(len(hits), 2)
        self.assertTrue(hits[0]["compiled_path"].endswith("claude_compacted_2.txt"))
        self.assertIn("Newest compaction-order-marker", read_ref(hits[0]["ref"]))

    def test_docs_and_skills_have_no_machine_specific_xcc_dependency(self):
        text_files = [ROOT / "README.md", ROOT / "INSTALL.md"]
        text_files.extend(
            path
            for path in sorted((ROOT / "skills").rglob("*"))
            if path.is_file() and path.suffix in {".md", ".py"}
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in text_files)

        self.assertNotIn("/Users/crosstyan", combined)
        self.assertNotIn("~/.codex/skills/conversation-compiler", combined)
        self.assertNotIn("uv --project", combined)

    def test_search_cli_returns_readable_omp_refs(self):
        result = CliRunner().invoke(main, ["search", "OMP repo", "--harness", "omp", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.output)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0]["harness"], "omp")
        self.assertIn("omp_basic.txt:", hits[0]["ref"])
        self.assertIn("OMP repo history", read_ref(hits[0]["ref"]))

    def test_reasonix_cwd_filter_and_search_refs_are_readable(self):
        result = CliRunner().invoke(
            main,
            [
                "list",
                "--harness",
                "reasonix",
                "--cwd",
                "/tmp/reasonix-project",
                "--include-delegated",
                "--json",
            ],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        records = json.loads(result.output)
        paths = {record["path"] for record in records}
        self.assertIn(str(self.reasonix_session), paths)
        self.assertIn(str(self.reasonix_subagent_session), paths)
        self.assertNotIn(str(self.reasonix_cleanup_session), paths)

        result = CliRunner().invoke(
            main,
            ["search", "Reasonix VCC", "--harness", "reasonix", "--json"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.output)
        self.assertGreaterEqual(len(hits), 1)
        self.assertTrue(all(hit["harness"] == "reasonix" for hit in hits))
        self.assertTrue(any("Reasonix VCC history" in read_ref(hit["ref"]) for hit in hits))

    def test_reasonix_slug_fallback_uses_canonical_cwd(self):
        real_workspace = self.root / "reasonix-workspace"
        real_workspace.mkdir()
        workspace_alias = self.root / "reasonix-workspace-alias"
        workspace_alias.symlink_to(real_workspace, target_is_directory=True)
        session = (
            self.reasonix_home
            / "projects"
            / _workspace_slug(str(real_workspace.resolve()))
            / "sessions"
            / "20260623-010205-model.jsonl"
        )
        session.parent.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "reasonix_basic.jsonl", session)

        records = service.filter_sessions(
            service.collect_sessions("reasonix"),
            str(workspace_alias),
            latest=False,
            limit=0,
        )

        self.assertIn(str(session), {record["path"] for record in records})

    def test_t3code_export_paths_do_not_collapse_distinct_thread_ids(self):
        cache_root = (self.root / "cache").resolve()
        db_path = self.t3_home / "userdata" / "state.sqlite"

        first = _export_path(db_path, "a/b", cache_root)
        second = _export_path(db_path, "a-b", cache_root)

        self.assertNotEqual(first, second)

    def test_t3code_thread_ref_rejects_a_mismatched_harness(self):
        with self.assertRaisesRegex(HistoryError, "require harness 't3code'"):
            service.compile_session(
                "t3code:/tmp/state.sqlite#thread-1",
                harness="codex",
            )

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
            conn.execute(
                """
                INSERT INTO projection_threads (
                  thread_id, project_id, title, created_at, updated_at, deleted_at, model_selection_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
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
                  message_id, thread_id, turn_id, role, text, is_streaming, created_at, updated_at, attachments_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    "payload": {"thread": {"path": str(self.codex_session)}},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        guarded = CliRunner().invoke(main, ["compile", str(db_path)])
        self.assertNotEqual(guarded.exit_code, 0)
        self.assertIn("state.sqlite contains many threads", guarded.output)
        self.assertIn("xcc list --harness t3code", guarded.output)
        self.assertIn("t3code:<db>#<thread>", guarded.output)
        self.assertIn("--all-threads", guarded.output)

        missing_output = CliRunner().invoke(main, ["compile", str(db_path), "--all-threads"])
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
            path for path in bulk_output.glob("*.txt") if not path.name.endswith(".min.txt")
        ]
        self.assertEqual(len(full_outputs), 2)

        records = service.collect_sessions("t3code")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["harness"], "t3code")
        self.assertTrue(records[0]["path"].startswith("t3code:"))
        self.assertIn(
            f"path:{self.codex_session.resolve()}",
            records[0]["alias_keys"],
        )
        self.assertEqual(records[0]["cwd"], "/tmp/t3-project")
        self.assertEqual(records[0]["created_at"], "2026-06-23T01:00:00Z")
        self.assertEqual(records[0]["updated_at"], "2026-06-23T01:00:04Z")
        self.assertEqual(records[0]["timestamp"], records[0]["updated_at"])
        self.assertEqual(records[0]["kind"], "main")

        result = CliRunner().invoke(main, ["search", "T3 Code history", "--harness", "t3code", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.output)
        self.assertEqual(hits[0]["harness"], "t3code")
        self.assertIn("please inspect the T3 Code history", read_ref(hits[0]["ref"]))

        t3_cache = self.root / "cache" / "t3code"
        exported = list(t3_cache.rglob("*.jsonl"))
        self.assertEqual(len(exported), 2)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(exported[0].parent.stat().st_mode), 0o700)

        shutil.rmtree(t3_cache)
        outside = self.root / "outside-t3-cache"
        outside.mkdir()
        t3_cache.symlink_to(outside, target_is_directory=True)

        refused = CliRunner().invoke(
            main,
            ["search", "T3 Code history", "--harness", "t3code", "--json"],
        )

        self.assertNotEqual(refused.exit_code, 0)
        self.assertIn("Refusing symlinked VCC cache directory", refused.output)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
