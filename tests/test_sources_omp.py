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


if __name__ == "__main__":
    unittest.main()
