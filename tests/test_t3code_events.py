import tempfile
import unittest
from pathlib import Path

from vcc_history.history import vcc
from vcc_history.history.events import Message, SystemEvent, Text
from vcc_history.history.frontends.t3code import parse_events


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class T3CodeEventsTests(unittest.TestCase):
    def test_parser_builds_semantic_events_and_preserves_details(self):
        conversation = parse_events(vcc.lex(str(FIXTURES / "t3code_projection.jsonl")))

        self.assertEqual(conversation.harness, "t3code")
        self.assertEqual([segment.reason for segment in conversation.segments], ["original"])
        self.assertIsInstance(conversation.segments[0].events[0], SystemEvent)
        self.assertEqual(conversation.segments[0].events[0].kind, "session_init")

        messages = [
            event
            for event in conversation.segments[0].events
            if isinstance(event, Message)
        ]
        self.assertEqual(
            [message.role for message in messages],
            ["user", "assistant", "custom:activity:tool.completed"],
        )
        self.assertEqual(messages[0].blocks, (Text("please inspect the T3 Code history"),))
        self.assertEqual(
            conversation.metadata["event_data"][3]["details"]["payload"]["detail"],
            "sqlite3 state.sqlite .tables",
        )

        with self.assertRaises(TypeError):
            conversation.metadata["event_data"][3]["details"]["tone"] = "changed"

    def test_compile_pass_accepts_prebuilt_t3code_conversation(self):
        conversation = parse_events(vcc.lex(str(FIXTURES / "t3code_projection.jsonl")))

        with tempfile.TemporaryDirectory() as tmp:
            vcc.compile_pass(
                str(Path(tmp, "prebuilt-t3.jsonl")),
                output_dir=tmp,
                conversation=conversation,
                quiet=True,
            )
            full = Path(tmp, "prebuilt-t3.txt").read_text(encoding="utf-8")

        self.assertIn("[session_init]", full)
        self.assertIn("[custom] activity:tool.completed", full)
        self.assertIn("sqlite3 state.sqlite .tables", full)


if __name__ == "__main__":
    unittest.main()
