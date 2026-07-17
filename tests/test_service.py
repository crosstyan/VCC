import json
import re
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from support import FIXTURES, HarnessStoreTestCase
from vcc_history.cli import main
from vcc_history.history import service, vcc
from vcc_history.history.refs import read_ref
from vcc_history.history.sources.base import file_alias_keys, finalize_record
from vcc_history.history.sources.codex import record_from_path as codex_record_from_path
class _StaticSource:
    def __init__(self, records):
        self.records = records

    def collect(self, config, include_delegated=False):
        return list(self.records)

class ServiceTests(HarnessStoreTestCase):
    def test_collect_sessions_reads_all_available_harnesses(self):
        records = service.collect_sessions("all")
        self.assertEqual(
            {record["harness"] for record in records},
            {"claude", "codex", "omp", "reasonix"},
        )

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

    def test_search_builds_structural_node_spans_and_redacts_display_lines(self):
        payload = "iVBORw0KGgo" + ("Aa0/" * 300)
        matched_text = f'AQA result image="data:image/png;base64,{payload}"'
        compiled_path = self.root / "structural-search.txt"
        full_lines = ["[assistant]", "", "alpha", matched_text, "omega"]
        compiled_path.write_text("\n".join(full_lines), encoding="utf-8")
        node = {
            "type": "assistant",
            "content": full_lines[2:5],
            "searchable": True,
            "start_line": 2,
            "end_line": 4,
        }
        record = {
            "harness": "claude",
            "path": str(self.claude_session),
        }

        with mock.patch.object(
            service,
            "_compiled_search_session",
            return_value=[(str(compiled_path), [node])],
        ) as compile_mock:
            hits = service.search_sessions([record], re.compile("AQA"))

        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit["ref"], f"{compiled_path}:3-5")
        self.assertEqual(hit["lines"][0].split(":", 1)[0], "  4")
        self.assertIn("data:image/png;base64,[omitted", hit["lines"][0])
        self.assertNotIn(payload, hit["lines"][0])
        self.assertEqual(compile_mock.call_args.args[0], record)

    def test_search_does_not_hide_compiler_failures(self):
        records = service.collect_sessions("claude")
        with mock.patch.object(
            service,
            "compile_session",
            side_effect=RuntimeError("compiler bug"),
        ):
            with self.assertRaisesRegex(RuntimeError, "compiler bug"):
                service.search_sessions(records, re.compile("fixture"))

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
        hits = json.loads(result.stdout)
        self.assertGreaterEqual(len(hits), 2)
        self.assertTrue(hits[0]["compiled_path"].endswith("claude_compacted_2.txt"))
        self.assertIn("Newest compaction-order-marker", read_ref(hits[0]["ref"]))


if __name__ == "__main__":
    unittest.main()
