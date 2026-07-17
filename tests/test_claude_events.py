import base64
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from vcc_history.history import vcc
from vcc_history.history.events import (
    Attachment,
    Conversation,
    Message,
    Segment,
    ToolCall,
    ToolResult,
)
from vcc_history.history.frontends.claude import parse_events
from vcc_history.history.render import render


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class ClaudeEventsTests(unittest.TestCase):
    def test_parser_builds_semantic_segments_with_full_tool_ids(self):
        basic = parse_events(vcc.lex(str(FIXTURES / "claude_basic.jsonl")))
        compacted = parse_events(vcc.lex(str(FIXTURES / "claude_compacted.jsonl")))

        self.assertEqual(basic.harness, "claude")
        self.assertEqual([segment.reason for segment in compacted.segments], [
            "original",
            "compaction",
        ])

        assistant = next(
            event
            for event in basic.segments[0].events
            if isinstance(event, Message) and event.role == "assistant"
        )
        calls = [block for block in assistant.blocks if isinstance(block, ToolCall)]
        self.assertEqual(
            [call.call_id for call in calls],
            ["toolu_read01", "toolu_bash02"],
        )

        result_message = next(
            event
            for event in basic.segments[0].events
            if isinstance(event, Message)
            and any(isinstance(block, ToolResult) for block in event.blocks)
        )
        results = [
            block for block in result_message.blocks if isinstance(block, ToolResult)
        ]
        self.assertEqual(
            [(result.call_id, result.name) for result in results],
            [("toolu_read01", "Read"), ("toolu_bash02", "Bash")],
        )

        with self.assertRaises(FrozenInstanceError):
            basic.harness = "other"
        with self.assertRaises(TypeError):
            calls[0].input["file_path"] = "changed.py"

    def test_compile_pass_accepts_a_prebuilt_conversation(self):
        conversation = parse_events(vcc.lex(str(FIXTURES / "claude_basic.jsonl")))

        with tempfile.TemporaryDirectory() as tmp:
            source_name = Path(tmp, "prebuilt.jsonl")
            vcc.compile_pass(
                str(source_name),
                output_dir=tmp,
                conversation=conversation,
                quiet=True,
            )
            full = Path(tmp, "prebuilt.txt").read_text(encoding="utf-8")

        self.assertIn("Sanitized Claude system context.", full)
        self.assertIn(">>>tool_call Read:read01", full)

    def test_brief_tool_policy_uses_structural_tool_names(self):
        conversation = Conversation(
            harness="claude",
            segments=(
                Segment(
                    reason="original",
                    events=(
                        Message(
                            role="assistant",
                            blocks=(
                                ToolCall(
                                    call_id="toolu_todo",
                                    name="TodoWrite",
                                    input={"todos": ["one"]},
                                    raw_text=None,
                                ),
                            ),
                            timestamp=None,
                        ),
                    ),
                ),
            ),
            metadata={},
        )

        with tempfile.TemporaryDirectory() as tmp:
            nodes = render(conversation, tmp, "structural-tool")
        tool_header = next(node for node in nodes if node.get("_tool_name"))
        tool_header["content"][0] = ">>>tool_call Visible:todo"
        vcc.assign_lines(nodes)
        vcc.lower_brief(nodes, truncate=128, filename="structural-tool.txt")
        brief = "\n".join(vcc.emit(nodes, "content_brief"))

        self.assertNotIn("* TodoWrite", brief)

    def test_attachment_extraction_is_deferred_until_render(self):
        image = b"sanitized image bytes"
        document = b"sanitized document bytes"
        records = [
            {
                "type": "user",
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
                        },
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.b64encode(document).decode(),
                            },
                        },
                    ],
                },
            }
        ]

        conversation = parse_events(records)
        message = conversation.segments[0].events[0]
        self.assertIsInstance(message, Message)
        self.assertTrue(all(isinstance(block, Attachment) for block in message.blocks))

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list(Path(tmp).iterdir()), [])
            nodes = render(conversation, tmp, "attachments")
            self.assertEqual(
                sorted(path.name for path in Path(tmp).iterdir()),
                ["attachments_doc_1.pdf", "attachments_img_0.png"],
            )
            self.assertEqual(Path(tmp, "attachments_img_0.png").read_bytes(), image)
            self.assertEqual(Path(tmp, "attachments_doc_1.pdf").read_bytes(), document)

        headers = [node for node in nodes if node["type"] == "meta_header"]
        self.assertEqual(headers[0]["_role"], "user")


if __name__ == "__main__":
    unittest.main()
