import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from vcc_history.history import vcc
from vcc_history.history.events import Attachment, Message, ToolCall, ToolResult
from vcc_history.history.frontends.codex import parse_events


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class CodexEventsTests(unittest.TestCase):
    def test_parser_builds_semantic_segments_with_full_tool_ids(self):
        basic = parse_events(vcc.lex(str(FIXTURES / "codex_basic.jsonl")))
        compacted = parse_events(vcc.lex(str(FIXTURES / "codex_compacted.jsonl")))

        self.assertEqual(basic.harness, "codex")
        self.assertEqual(
            [segment.reason for segment in compacted.segments],
            ["original", "compaction"],
        )

        calls = [
            block
            for event in basic.segments[0].events
            if isinstance(event, Message)
            for block in event.blocks
            if isinstance(block, ToolCall)
        ]
        self.assertEqual(
            [(call.call_id, call.name) for call in calls],
            [("call_abc123", "exec_command")],
        )

        results = [
            block
            for event in basic.segments[0].events
            if isinstance(event, Message)
            for block in event.blocks
            if isinstance(block, ToolResult)
        ]
        self.assertEqual(
            [(result.call_id, result.name) for result in results],
            [("call_abc123", "exec_command")],
        )

        image = next(
            block
            for event in basic.segments[0].events
            if isinstance(event, Message)
            for block in event.blocks
            if isinstance(block, Attachment)
        )
        self.assertEqual((image.kind, image.path), ("image", None))

        with self.assertRaises(FrozenInstanceError):
            basic.harness = "other"
        with self.assertRaises(TypeError):
            calls[0].input["cmd"] = "changed"

    def test_compile_pass_accepts_a_prebuilt_conversation(self):
        conversation = parse_events(vcc.lex(str(FIXTURES / "codex_basic.jsonl")))

        with tempfile.TemporaryDirectory() as tmp:
            source_name = Path(tmp, "prebuilt.jsonl")
            vcc.compile_pass(
                str(source_name),
                output_dir=tmp,
                conversation=conversation,
                quiet=True,
            )
            full = Path(tmp, "prebuilt.txt").read_text(encoding="utf-8")

        self.assertIn("Developer instructions", full)
        self.assertIn(">>>tool_call exec_command:abc123", full)


if __name__ == "__main__":
    unittest.main()
