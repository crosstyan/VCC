import json
import shutil
import unittest

from click.testing import CliRunner

from support import FIXTURES, HarnessStoreTestCase
from vcc_history.cli import main
from vcc_history.history import service
from vcc_history.history.refs import read_ref
from vcc_history.history.sources.reasonix import _workspace_slug

class ReasonixSourceTests(HarnessStoreTestCase):
    def test_reads_reasonix_metadata_and_skips_hidden_sessions(self):
        records = service.collect_sessions("reasonix")
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
        records = json.loads(result.stdout)
        paths = {record["path"] for record in records}
        self.assertIn(str(self.reasonix_session), paths)
        self.assertIn(str(self.reasonix_subagent_session), paths)
        self.assertNotIn(str(self.reasonix_cleanup_session), paths)

        result = CliRunner().invoke(
            main,
            ["search", "Reasonix VCC", "--harness", "reasonix", "--json"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.stdout)
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

    def test_bad_reasonix_sidecar_warns_and_keeps_session(self):
        sidecar = self.reasonix_session.with_name(self.reasonix_session.name + ".meta")
        sidecar.write_text("{not-json", encoding="utf-8")

        result = CliRunner().invoke(
            main,
            ["list", "--harness", "reasonix", "--limit", "0", "--json"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        records = json.loads(result.stdout)
        self.assertIn(str(self.reasonix_session), {record["path"] for record in records})
        self.assertNotIn("warning:", result.stdout)
        self.assertIn(f"warning: {sidecar}: could not read JSON:", result.stderr)


if __name__ == "__main__":
    unittest.main()
