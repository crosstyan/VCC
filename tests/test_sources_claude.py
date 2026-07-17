import os
import unittest

from click.testing import CliRunner

from support import HarnessStoreTestCase
from vcc_history.cli import main
from vcc_history.history import service

class ClaudeSourceTests(HarnessStoreTestCase):
    def test_reads_claude_metadata(self):
        records = service.collect_sessions("claude")
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
        self.assertEqual(records, [claude])

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

    @unittest.skipUnless(
        hasattr(os, "geteuid") and os.geteuid() != 0,
        "permission checks require a non-root POSIX user",
    )
    def test_unreadable_claude_project_warns_and_other_sessions_continue(self):
        unreadable = self.claude_config / "projects" / "unreadable-project"
        unreadable.mkdir()
        (unreadable / "hidden.jsonl").write_text("{}\n", encoding="utf-8")
        unreadable.chmod(0)
        try:
            result = CliRunner().invoke(main, ["list", "--harness", "all", "--limit", "0"])
        finally:
            unreadable.chmod(0o700)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(str(self.claude_session), result.stdout)
        self.assertIn(f"warning: {unreadable}:", result.stderr)


if __name__ == "__main__":
    unittest.main()
