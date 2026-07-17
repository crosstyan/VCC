import unittest


from support import HarnessStoreTestCase
from vcc_history.history import service

class CodexSourceTests(HarnessStoreTestCase):
    def test_reads_codex_metadata(self):
        records = service.collect_sessions("codex")
        codex = next(record for record in records if record["harness"] == "codex")
        self.assertEqual(codex["created_at"], "2026-04-07T06:38:38.648000Z")
        self.assertEqual(codex["updated_at"], "2026-04-07T06:40:11Z")
        self.assertEqual(codex["timestamp"], codex["updated_at"])
        self.assertEqual(codex["kind"], "main")


if __name__ == "__main__":
    unittest.main()
