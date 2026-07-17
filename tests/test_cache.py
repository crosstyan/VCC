import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from support import SRC, HarnessStoreTestCase, build_t3code_store
from vcc_history.cli import main
from vcc_history.history import service
from vcc_history.history.refs import read_ref
from vcc_history.history.sources.base import exclusive_cache_lock
from vcc_history.history.sources.t3code import _export_path

class CacheTests(HarnessStoreTestCase):
    def test_claude_search_uses_cache_and_returns_readable_refs(self):
        result = CliRunner().invoke(
            main,
            ["search", "sanitized Claude fixture marker", "--harness", "claude", "--json"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        hits = json.loads(result.stdout)
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
        self.assertEqual(list((self.root / "cache").rglob("*.view.txt")), [])

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

    def test_search_reuses_fingerprinted_node_index_without_rewriting_outputs(self):
        record = next(
            record
            for record in service.collect_sessions("claude")
            if record["path"] == str(self.claude_session)
        )
        first_hits = service.search_sessions(
            [record], re.compile("sanitized Claude fixture marker")
        )
        compiled_path = Path(first_hits[0]["compiled_path"])
        nodes_path = compiled_path.parent / ".nodes.json"
        fingerprint_path = compiled_path.parent / ".fingerprint"
        initial_mtimes = {
            path: path.stat().st_mtime_ns
            for path in (compiled_path, nodes_path, fingerprint_path)
        }

        with mock.patch.object(
            service,
            "compile_session",
            side_effect=AssertionError("unchanged search should reuse the node index"),
        ):
            second_hits = service.search_sessions(
                [record], re.compile("sanitized Claude fixture marker")
            )

        self.assertEqual(second_hits, first_hits)
        self.assertEqual(
            {path: path.stat().st_mtime_ns for path in initial_mtimes},
            initial_mtimes,
        )

    def test_search_rebuilds_a_corrupt_node_index(self):
        record = next(
            record
            for record in service.collect_sessions("claude")
            if record["path"] == str(self.claude_session)
        )
        first_hits = service.search_sessions(
            [record], re.compile("sanitized Claude fixture marker")
        )
        nodes_path = Path(first_hits[0]["compiled_path"]).parent / ".nodes.json"
        nodes_path.write_text("{not-json", encoding="utf-8")

        original_compile = service.compile_session
        with mock.patch.object(
            service, "compile_session", wraps=original_compile
        ) as compile_mock:
            rebuilt_hits = service.search_sessions(
                [record], re.compile("sanitized Claude fixture marker")
            )

        self.assertEqual(rebuilt_hits, first_hits)
        compile_mock.assert_called_once()
        self.assertIsInstance(json.loads(nodes_path.read_text(encoding="utf-8")), dict)

    def test_search_invalidates_cache_when_compiler_version_changes(self):
        record = next(
            record
            for record in service.collect_sessions("claude")
            if record["path"] == str(self.claude_session)
        )
        service.search_sessions([record], re.compile("sanitized Claude fixture marker"))

        original_compile = service.compile_session
        with (
            mock.patch.object(service.vcc, "COMPILER_VERSION", "next-test-version"),
            mock.patch.object(
                service, "compile_session", wraps=original_compile
            ) as compile_mock,
        ):
            hits = service.search_sessions(
                [record], re.compile("sanitized Claude fixture marker")
            )

        self.assertTrue(hits)
        compile_mock.assert_called_once()

    def test_search_invalidates_cache_when_source_metadata_changes(self):
        record = next(
            record
            for record in service.collect_sessions("claude")
            if record["path"] == str(self.claude_session)
        )
        service.search_sessions([record], re.compile("sanitized Claude fixture marker"))
        source_status = self.claude_session.stat()
        os.utime(
            self.claude_session,
            ns=(source_status.st_atime_ns, source_status.st_mtime_ns + 1_000_000_000),
        )

        original_compile = service.compile_session
        with mock.patch.object(
            service, "compile_session", wraps=original_compile
        ) as compile_mock:
            hits = service.search_sessions(
                [record], re.compile("sanitized Claude fixture marker")
            )

        self.assertTrue(hits)
        compile_mock.assert_called_once()

    def test_cache_prune_removes_orphans_and_keeps_live_entries(self):
        record = next(
            record
            for record in service.collect_sessions("claude")
            if record["path"] == str(self.claude_session)
        )
        hits = service.search_sessions([record], re.compile("sanitized Claude fixture marker"))
        live_dir = Path(hits[0]["compiled_path"]).parent
        orphan_dir = live_dir.parent / ("f" * 64)
        orphan_dir.mkdir(mode=0o700)
        (orphan_dir / ".fingerprint").write_text("orphan", encoding="utf-8")

        result = CliRunner().invoke(main, ["cache", "prune"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(live_dir.is_dir())
        self.assertFalse(orphan_dir.exists())
        self.assertIn(f"removed {orphan_dir}", result.output)
        self.assertIn("Removed 1 cache entries.", result.output)

    def test_cache_clear_removes_compiled_and_t3code_trees(self):
        record = next(
            record
            for record in service.collect_sessions("claude")
            if record["path"] == str(self.claude_session)
        )
        service.search_sessions([record], re.compile("sanitized Claude fixture marker"))
        t3_tree = self.root / "cache" / "t3code" / "fixture-db"
        t3_tree.mkdir(parents=True)
        (t3_tree / "thread.jsonl").write_text("{}\n", encoding="utf-8")

        result = CliRunner().invoke(main, ["cache", "clear"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse((self.root / "cache" / "compiled").exists())
        self.assertFalse((self.root / "cache" / "t3code").exists())
        self.assertIn("Removed 2 cache entries.", result.output)

    def test_exclusive_cache_lock_serializes_processes(self):
        lock_dir = self.root / "lock-test"
        lock_dir.mkdir()
        lock_path = lock_dir / ".lock"
        script = "\n".join(
            [
                "from pathlib import Path",
                "from vcc_history.history.sources.base import exclusive_cache_lock",
                f"with exclusive_cache_lock(Path({str(lock_path)!r}), timeout=2) as acquired:",
                "    print('acquired' if acquired else 'timed-out', flush=True)",
            ]
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(SRC), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)

        with exclusive_cache_lock(lock_path):
            process = subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            time.sleep(0.2)
            self.assertIsNone(process.poll())
        stdout, stderr = process.communicate(timeout=3)

        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout.strip(), "acquired")

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
        hits = json.loads(result.stdout)
        by_session = {hit["session_path"]: hit for hit in hits}
        self.assertIn(str(self.claude_session), by_session)
        self.assertIn(str(second_session), by_session)
        first_compiled = Path(by_session[str(self.claude_session)]["compiled_path"])
        second_compiled = Path(by_session[str(second_session)]["compiled_path"])
        self.assertEqual(first_compiled.name, second_compiled.name)
        self.assertNotEqual(first_compiled.parent, second_compiled.parent)
        self.assertIn("sanitized Claude fixture marker", read_ref(by_session[str(second_session)]["ref"]))

    def test_t3code_export_cache_permissions_and_symlink_refusal(self):
        db_path = build_t3code_store(
            self.t3_home / "userdata" / "state.sqlite", self.codex_session
        )
        records = service.collect_sessions("t3code")
        service.search_sessions(records, re.compile("T3 Code history"))

        t3_cache = self.root / "cache" / "t3code"
        exported = list(t3_cache.rglob("*.jsonl"))
        self.assertEqual(len(exported), 2)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(exported[0].parent.stat().st_mode), 0o700)

        shutil.rmtree(t3_cache)
        outside = self.root / "outside-t3-cache"
        outside.mkdir()
        t3_cache.symlink_to(outside, target_is_directory=True)
        db_status = db_path.stat()
        os.utime(
            db_path,
            ns=(db_status.st_atime_ns, db_status.st_mtime_ns + 1_000_000_000),
        )

        refused = CliRunner().invoke(
            main,
            ["search", "T3 Code history", "--harness", "t3code", "--json"],
        )

        self.assertNotEqual(refused.exit_code, 0)
        self.assertIn("Refusing symlinked VCC cache directory", refused.output)
        self.assertEqual(list(outside.iterdir()), [])

    def test_t3code_export_paths_do_not_collapse_distinct_thread_ids(self):
        cache_root = (self.root / "cache").resolve()
        db_path = self.t3_home / "userdata" / "state.sqlite"

        first = _export_path(db_path, "a/b", cache_root)
        second = _export_path(db_path, "a-b", cache_root)

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
