import json
import unittest

from click.testing import CliRunner

from support import HarnessStoreTestCase, build_t3code_store
from vcc_history.cli import main
from vcc_history.history import service
from vcc_history.history.errors import HistoryError
from vcc_history.history.refs import read_ref

class T3CodeSourceTests(HarnessStoreTestCase):
    def test_t3code_thread_ref_rejects_a_mismatched_harness(self):
        with self.assertRaisesRegex(HistoryError, "require harness 't3code'"):
            service.compile_session(
                "t3code:/tmp/state.sqlite#thread-1",
                harness="codex",
            )

    def test_t3code_sqlite_threads_are_discovered_and_searchable(self):
        build_t3code_store(
            self.t3_home / "userdata" / "state.sqlite", self.codex_session
        )

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

        result = CliRunner().invoke(
            main,
            ["search", "T3 Code history", "--harness", "t3code", "--json"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.stdout)
        self.assertEqual(hits[0]["harness"], "t3code")
        self.assertIn("please inspect the T3 Code history", read_ref(hits[0]["ref"]))

    def test_corrupt_t3code_db_errors_when_selected_and_warns_under_all(self):
        db_path = self.t3_home / "userdata" / "state.sqlite"
        db_path.parent.mkdir(parents=True)
        db_path.write_bytes(b"not a sqlite database")

        selected = CliRunner().invoke(main, ["list", "--harness", "t3code"])

        self.assertNotEqual(selected.exit_code, 0)
        self.assertIn("Could not read T3 Code history store", selected.output)
        self.assertIn(str(db_path), selected.output)
        self.assertNotIn("Traceback", selected.output)

        combined = CliRunner().invoke(main, ["list", "--harness", "all", "--limit", "0"])

        self.assertEqual(combined.exit_code, 0, combined.output)
        self.assertIn(str(self.claude_session), combined.stdout)
        self.assertIn(f"warning: {db_path.resolve()}: T3 Code:", combined.stderr)


if __name__ == "__main__":
    unittest.main()
