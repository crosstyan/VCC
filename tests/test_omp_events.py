import base64
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from vcc_history.history import vcc
from vcc_history.history.events import Attachment, Message, SystemEvent, ToolCall, ToolResult
from vcc_history.history.frontends.omp import parse_events
from vcc_history.history.render import render


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class OmpEventsTests(unittest.TestCase):
    def test_parser_builds_semantic_segments_with_full_tool_ids(self):
        basic = parse_events(vcc.lex(str(FIXTURES / "omp_basic.jsonl")))
        compacted = parse_events(
            [
                {"type": "session", "id": "session"},
                {
                    "type": "message",
                    "message": {"role": "user", "content": "before"},
                },
                {"type": "compaction", "summary": "summary"},
                {
                    "type": "message",
                    "message": {"role": "assistant", "content": "after"},
                },
            ]
        )

        self.assertEqual(basic.harness, "omp")
        self.assertEqual(
            [segment.reason for segment in compacted.segments],
            ["original", "compaction"],
        )
        self.assertIsInstance(compacted.segments[1].events[0], SystemEvent)
        self.assertEqual(compacted.segments[1].events[0].kind, "compaction")

        calls = [
            block
            for event in basic.segments[0].events
            if isinstance(event, Message)
            for block in event.blocks
            if isinstance(block, ToolCall)
        ]
        self.assertEqual(
            [(call.call_id, call.name) for call in calls],
            [
                ("toolu_omp123456", "read"),
                ("toolu_ompbash", "bash"),
            ],
        )

        results = [
            block
            for event in basic.segments[0].events
            if isinstance(event, Message)
            for block in event.blocks
            if isinstance(block, ToolResult)
        ]
        self.assertEqual(
            [(result.call_id, result.name, result.is_error) for result in results],
            [
                ("toolu_omp123456", "read", False),
                ("toolu_ompbash", "bash", True),
            ],
        )
        custom = next(
            event
            for event in basic.segments[0].events
            if isinstance(event, Message) and event.role.startswith("custom:")
        )
        self.assertEqual(custom.role, "custom:irc:incoming")

        with self.assertRaises(FrozenInstanceError):
            basic.harness = "other"
        with self.assertRaises(TypeError):
            calls[0].input["path"] = "changed"

    def test_compile_pass_accepts_a_prebuilt_conversation(self):
        conversation = parse_events(vcc.lex(str(FIXTURES / "omp_basic.jsonl")))

        with tempfile.TemporaryDirectory() as tmp:
            source_name = Path(tmp, "prebuilt.jsonl")
            vcc.compile_pass(
                str(source_name),
                output_dir=tmp,
                conversation=conversation,
                quiet=True,
            )
            full = Path(tmp, "prebuilt.txt").read_text(encoding="utf-8")

        self.assertIn("You are an OMP coding agent.", full)
        self.assertIn(">>>tool_call read:123456", full)

    def test_renderer_handles_omp_specific_entries(self):
        records = [
            {"type": "thinking_level_change", "thinkingLevel": "high", "configured": True},
            {
                "type": "message",
                "message": {"role": "custom", "customType": "empty-ping"},
            },
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "toolu_details123456",
                    "toolName": "read",
                    "details": {"status": "empty"},
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "branchSummary",
                    "fromId": "entry-1",
                    "summary": "branch text",
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "fileMention",
                    "files": [{"path": "src/demo.py", "content": "print('ok')"}],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "bashExecution",
                    "command": "pwd",
                    "exitCode": 0,
                    "output": "/tmp/demo",
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "pythonExecution",
                    "code": "print(1)",
                    "cancelled": True,
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            nodes = render(parse_events(records), tmp, "omp_entries")
        vcc.assign_lines(nodes)
        full = "\n".join(vcc.emit(nodes))

        self.assertIn("[thinking_level_change]", full)
        self.assertIn("thinkingLevel: high", full)
        self.assertIn("[custom] empty-ping\n\n[empty-ping]", full)
        self.assertIn("[tool] read:123456\n\nstatus: empty", full)
        self.assertIn("[branch]\n\nfrom: entry-1\n\nbranch text", full)
        self.assertIn('<file path="src/demo.py">', full)
        self.assertIn("command: |\n  pwd\nexit_code: 0", full)
        self.assertIn("code: |\n  print(1)\ncancelled: true", full)

    def test_attachment_extraction_is_deferred_until_render(self):
        image = b"omp image bytes"
        records = [
            {
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(image).decode(),
                            },
                        }
                    ],
                },
            }
        ]

        conversation = parse_events(records)
        message = conversation.segments[0].events[0]
        self.assertIsInstance(message, Message)
        self.assertIsInstance(message.blocks[0], Attachment)

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list(Path(tmp).iterdir()), [])
            render(conversation, tmp, "omp_attachment")
            extracted = Path(tmp, "omp_attachment_img_0.png")
            self.assertEqual(extracted.read_bytes(), image)


if __name__ == "__main__":
    unittest.main()
