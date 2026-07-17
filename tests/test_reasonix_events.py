import tempfile
import unittest
from pathlib import Path

from vcc_history.history import vcc
from vcc_history.history.events import Attachment, Message, Thinking, ToolCall, ToolResult
from vcc_history.history.frontends.reasonix import parse_events
from vcc_history.history.render import render


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class ReasonixEventsTests(unittest.TestCase):
    def test_parser_builds_semantic_events_with_full_tool_ids(self):
        conversation = parse_events(vcc.lex(str(FIXTURES / "reasonix_basic.jsonl")))

        self.assertEqual(conversation.harness, "reasonix")
        self.assertEqual([segment.reason for segment in conversation.segments], ["original"])

        user = next(
            event
            for event in conversation.segments[0].events
            if isinstance(event, Message) and event.role == "user"
        )
        attachment = next(
            block for block in user.blocks if isinstance(block, Attachment)
        )
        self.assertEqual(attachment.path, "data:image/png;base64,AAAA")

        assistant = next(
            event
            for event in conversation.segments[0].events
            if isinstance(event, Message) and event.role == "assistant"
        )
        self.assertTrue(any(isinstance(block, Thinking) for block in assistant.blocks))
        calls = [block for block in assistant.blocks if isinstance(block, ToolCall)]
        self.assertEqual(
            [call.call_id for call in calls],
            ["call_01_abcdef123456", "call_02_bash987654"],
        )
        self.assertEqual(calls[0].input["path"], "internal/history/search.go")

        results = [
            block
            for event in conversation.segments[0].events
            if isinstance(event, Message) and event.role == "tool"
            for block in event.blocks
            if isinstance(block, ToolResult)
        ]
        self.assertEqual(
            [(result.call_id, result.name, result.is_error) for result in results],
            [
                ("call_01_abcdef123456", "read_file", False),
                ("call_02_bash987654", "bash", True),
            ],
        )

    def test_compile_pass_accepts_a_prebuilt_reasonix_conversation(self):
        conversation = parse_events(vcc.lex(str(FIXTURES / "reasonix_basic.jsonl")))

        with tempfile.TemporaryDirectory() as tmp:
            source_name = Path(tmp, "prebuilt.jsonl")
            vcc.compile_pass(
                str(source_name),
                output_dir=tmp,
                conversation=conversation,
                quiet=True,
            )
            full = Path(tmp, "prebuilt.txt").read_text(encoding="utf-8")

        self.assertIn(">>>tool_call read_file:123456", full)
        self.assertIn("[tool_error] bash:987654", full)

    def test_renderer_sets_structural_roles_and_preserves_reasonix_labels(self):
        conversation = parse_events(vcc.lex(str(FIXTURES / "reasonix_basic.jsonl")))

        with tempfile.TemporaryDirectory() as tmp:
            nodes = render(conversation, tmp, "reasonix")
            self.assertEqual(list(Path(tmp).iterdir()), [])

        headers = [node for node in nodes if node["type"] == "meta_header"]
        self.assertEqual(
            [header["_role"] for header in headers],
            ["system", "user", "assistant", "tool", "tool_error", "assistant"],
        )
        tool_calls = [node for node in nodes if node.get("_tool_name")]
        self.assertEqual(
            [node["_tool_name"] for node in tool_calls],
            ["read_file", "bash"],
        )
        image = next(node for node in nodes if node["type"] == "user_image")
        self.assertEqual(image["content"], ["[image]"])

    def test_empty_tool_results_do_not_create_empty_sections(self):
        conversation = parse_events(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-empty",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-empty", "content": ""},
            ]
        )

        tool_messages = [
            event
            for event in conversation.segments[0].events
            if isinstance(event, Message) and event.role == "tool"
        ]
        self.assertEqual(tool_messages, [])


if __name__ == "__main__":
    unittest.main()
