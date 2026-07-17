import tempfile
import unittest
from pathlib import Path

from vcc_history.history.errors import HistoryError
from vcc_history.history.refs import read_ref, split_ref


class RefTests(unittest.TestCase):
    def test_split_ref_and_read_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            compiled_path = Path(tmp) / "structural-search.txt"
            full_lines = ["[assistant]", "", "alpha", "matched", "omega"]
            compiled_path.write_text("\n".join(full_lines), encoding="utf-8")
            node_content = full_lines[2:5]
            path_text, ranges = split_ref(f"{compiled_path}:3-5")

            self.assertEqual(path_text, str(compiled_path))
            self.assertEqual(ranges, [(3, 5)])
            self.assertEqual(
                Path(path_text).read_text(encoding="utf-8").splitlines()[2:5],
                node_content,
            )

    def test_missing_read_ref_raises_history_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = f"{Path(tmp) / 'missing.txt'}:1-2"
            with self.assertRaisesRegex(HistoryError, "Could not read ref"):
                read_ref(ref)


if __name__ == "__main__":
    unittest.main()
