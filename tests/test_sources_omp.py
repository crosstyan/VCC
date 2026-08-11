import json
import unittest

from click.testing import CliRunner

from support import HarnessStoreTestCase
from vcc_history.cli import main
from vcc_history.history import service
from vcc_history.history.refs import read_ref

class OmpSourceTests(HarnessStoreTestCase):
    def test_reads_omp_metadata(self):
        records = service.collect_sessions("omp")
        omp = next(record for record in records if record["harness"] == "omp")
        self.assertEqual(omp["cwd"], "/tmp/omp-project")
        self.assertEqual(omp["title"], "OMP fixture")
        self.assertEqual(omp["provider"], "anthropic")
        self.assertEqual(omp["model"], "claude-sonnet-4-5")
        self.assertEqual(omp["created_at"], "2026-06-23T00:00:00Z")
        self.assertEqual(omp["updated_at"], "2026-06-23T00:00:06Z")
        self.assertEqual(omp["timestamp"], omp["updated_at"])
        self.assertEqual(omp["kind"], "main")

    def test_search_cli_returns_readable_omp_refs(self):
        result = CliRunner().invoke(main, ["search", "OMP repo", "--harness", "omp", "--json"])

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.stdout)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0]["harness"], "omp")
        self.assertIn("omp_basic.txt:", hits[0]["ref"])
        self.assertIn("OMP repo history", read_ref(hits[0]["ref"]))

    def test_collects_title_first_session_file(self):
        # Modern omp files open with a {"type":"title",...} record that is
        # updated in place; the session record follows it. The loader must
        # scan to the first session record instead of requiring it first.
        session_dir = self.stores.omp_agent / "sessions" / "-tmp-title-first-project"
        session_dir.mkdir(parents=True)
        (session_dir / "title_first.jsonl").write_text(
            "\n".join(
                [
                    '{"type":"title","v":1,"title":"Title first project","source":"auto",'
                    '"updatedAt":"2026-08-11T11:39:00.526Z"}',
                    '{"type":"session","version":3,"id":"019ff08b-0000-7000-b71d-000000000001",'
                    '"timestamp":"2026-08-11T11:18:11Z","cwd":"/tmp/title-first-project",'
                    '"title":"Title first project","titleSource":"auto"}',
                    '{"type":"message","id":"e1","parentId":null,'
                    '"timestamp":"2026-08-11T11:18:40Z",'
                    '"message":{"role":"user","content":[{"type":"text","text":"hello"}]}}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        records = service.collect_sessions("omp")
        title_first = next(
            record
            for record in records
            if record["harness"] == "omp"
            and record["session_id"] == "019ff08b-0000-7000-b71d-000000000001"
        )
        self.assertEqual(title_first["cwd"], "/tmp/title-first-project")
        self.assertEqual(title_first["title"], "Title first project")
        self.assertEqual(title_first["created_at"], "2026-08-11T11:18:11Z")
        self.assertEqual(title_first["updated_at"], "2026-08-11T11:18:40Z")

    def test_skips_header_only_session_file(self):
        # A file whose records never include a session record stays skipped.
        session_dir = self.stores.omp_agent / "sessions" / "-tmp-header-only-project"
        session_dir.mkdir(parents=True)
        (session_dir / "header_only.jsonl").write_text(
            '{"type":"title","v":1,"title":"Never a session","source":"auto",'
            '"updatedAt":"2026-08-11T11:39:00.526Z"}\n',
            encoding="utf-8",
        )

        records = service.collect_sessions("omp")
        self.assertNotIn(
            "header_only.jsonl",
            [record["path"] for record in records],
        )


if __name__ == "__main__":
    unittest.main()
