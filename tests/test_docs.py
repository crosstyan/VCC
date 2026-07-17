import unittest


from support import ROOT, HarnessStoreTestCase

class DocumentationTests(HarnessStoreTestCase):
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


if __name__ == "__main__":
    unittest.main()
