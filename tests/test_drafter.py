import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import drafter  # noqa: E402

DRAFT = """---
id: 2026-11-02-test
lang: en
type: ai-news
status: draft
---
Body of the post.
"""


class TempRepo(unittest.TestCase):
    """Point every path in drafter at a throwaway folder and silence Telegram."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        base = self.tmp / "content"
        (base / "drafts").mkdir(parents=True)
        patches = {
            "REPO": self.tmp, "ENV_FILE": self.tmp / ".env", "BASE": base,
            "DRAFTS": base / "drafts", "PUBLISHED": base / "published",
            "IDEAS": base / "ideas.md", "STATE": base / ".state.json", "LOCK": base / ".state.lock",
        }
        for name, value in patches.items():
            p = mock.patch.object(drafter, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.sent = []
        p = mock.patch.object(drafter, "say", lambda token, chat, text, **kw: self.sent.append(text) or {"message_id": 1})
        p.start()
        self.addCleanup(p.stop)

    def draft(self, name="2026-11-02-test.md", text=DRAFT):
        path = drafter.DRAFTS / name
        path.write_text(text)
        return path


class DraftFiles(TempRepo):
    def test_parse_draft_splits_frontmatter_and_body(self):
        meta, body = drafter.parse_draft(self.draft())
        self.assertEqual(meta["lang"], "en")
        self.assertEqual(meta["status"], "draft")
        self.assertEqual(body, "Body of the post.")

    def test_parse_draft_without_frontmatter(self):
        meta, body = drafter.parse_draft(self.draft(text="Just text\n"))
        self.assertEqual(meta, {})
        self.assertEqual(body, "Just text")

    def test_set_status_rewrites_only_status(self):
        path = self.draft()
        drafter.set_status(path, "approved")
        meta, body = drafter.parse_draft(path)
        self.assertEqual(meta["status"], "approved")
        self.assertEqual(body, "Body of the post.")


class Schedule(unittest.TestCase):
    def test_defaults(self):
        days, at, langs = drafter.schedule({})
        self.assertEqual(days, {0, 3})
        self.assertEqual(at, dt.time(8, 30))
        self.assertEqual(langs, ["en"])

    def test_custom_values(self):
        days, at, langs = drafter.schedule({"DRAFT_DAYS": "Tuesday, fri", "DRAFT_TIME": "19:05", "DRAFT_LANGS": "es,en"})
        self.assertEqual(days, {1, 4})
        self.assertEqual(at, dt.time(19, 5))
        self.assertEqual(langs, ["es", "en"])

    def test_language_rotation(self):
        self.assertEqual(drafter.next_lang(["es", "en"], None), "es")
        self.assertEqual(drafter.next_lang(["es", "en"], "es"), "en")
        self.assertEqual(drafter.next_lang(["es", "en"], "en"), "es")
        self.assertEqual(drafter.next_lang(["en"], "en"), "en")


class Messages(TempRepo):
    def test_new_command_with_topic(self):
        self.assertEqual(drafter.handle_message("t", 1, {"text": "/new local LLMs"}), ("generate", "local LLMs"))

    def test_new_command_without_topic(self):
        self.assertEqual(drafter.handle_message("t", 1, {"text": "/new"}), ("generate", None))

    def test_reply_to_a_draft_is_feedback(self):
        with drafter.state() as s:
            s["messages"]["42"] = "content/drafts/2026-11-02-test.md"
        job = drafter.handle_message("t", 1, {"text": "shorter", "reply_to_message": {"message_id": 42}})
        self.assertEqual(job, ("revise", "content/drafts/2026-11-02-test.md", "shorter"))

    def test_text_after_edit_button_is_feedback(self):
        with drafter.state() as s:
            s["awaiting_feedback"] = "content/drafts/x.md"
        self.assertEqual(drafter.handle_message("t", 1, {"text": "less formal"}), ("revise", "content/drafts/x.md", "less formal"))
        with drafter.state() as s:
            self.assertNotIn("awaiting_feedback", s)

    def test_long_text_after_publishing_is_learned(self):
        with drafter.state() as s:
            s["awaiting_final"] = "content/drafts/x.md"
        final = "word " * 50
        self.assertEqual(drafter.handle_message("t", 1, {"text": final}), ("learn", "content/drafts/x.md", final.strip()))

    def test_other_text_is_saved_as_idea(self):
        self.assertIsNone(drafter.handle_message("t", 1, {"text": "post about eval harnesses"}))
        self.assertIn("post about eval harnesses", drafter.IDEAS.read_text())
        self.assertIn("Idea saved", self.sent[-1])

    def test_pending_lists_open_drafts(self):
        self.draft()
        self.draft("2026-11-03-done.md", DRAFT.replace("status: draft", "status: published"))
        drafter.handle_message("t", 1, {"text": "/pending"})
        self.assertIn("2026-11-02-test", self.sent[-1])
        self.assertNotIn("2026-11-03-done", self.sent[-1])


if __name__ == "__main__":
    unittest.main()
