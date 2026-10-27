import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from verifynudge import reconcile_channel_history, load_state, plan, reminder_content, save_state

class NotifyTests(unittest.TestCase):
    def test_only_new_muted(self):
        self.assertEqual(plan({1,2,3}, {1}, 1), [2])

    def test_newest_joined_has_priority(self):
        self.assertEqual(plan({10, 20, 30}, {20}, 1, ordered_ids=[30, 20, 10]), [30])

    def test_reentry(self):
        old = {42}
        cleared = old & set()
        self.assertEqual(plan({42}, cleared, 10), [42])

    def test_reminder_links_to_rules_channel(self):
        message = reminder_content(123, 456)
        self.assertIn('<@123>', message)
        self.assertIn('<#456>', message)
        self.assertIn('\U0001F44D', message)

    def test_state(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state.json"
            save_state(state, 123, {3,1})
            self.assertEqual(load_state(state, 123), {1,3})
            with self.assertRaises(ValueError):
                load_state(state, 456)

class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_mentions_suppress_repeat_and_delete_obsolete(self):
        class FakeMessage:
            def __init__(self, message_id, author_id, content, mentioned):
                self.id = message_id
                self.author = SimpleNamespace(id=author_id)
                self.content = content
                self.mentions = [SimpleNamespace(id=uid) for uid in mentioned]
                self.deleted = False

            async def delete(self):
                self.deleted = True

        reminder = "認証がまだ完了していないようです"
        keep = FakeMessage(1, 999, reminder, [22])
        delete = FakeMessage(2, 999, reminder, [33])
        unrelated = FakeMessage(3, 999, "not a reminder", [44])
        human = FakeMessage(4, 12, reminder, [55])
        mixed = FakeMessage(5, 999, reminder, [33, 22])
        msgs = [keep, delete, unrelated, human, mixed]

        class FakeChannel:
            async def history(self, limit=None):
                assert limit is None
                for msg in msgs:
                    yield msg

        found, removed = await reconcile_channel_history(FakeChannel(), {22, 55}, 999, False)
        self.assertEqual(found, {22, 55})
        self.assertEqual(removed, 1)
        self.assertTrue(delete.deleted)
        self.assertFalse(any(m.deleted for m in [keep, unrelated, human, mixed]))
        self.assertEqual(plan({22, 55, 66}, found, 10), [66])

    async def test_dry_run_does_not_delete(self):
        class FakeMessage:
            id = 1
            author = SimpleNamespace(id=999)
            content = "認証がまだ完了していないようです"
            mentions = [SimpleNamespace(id=33)]

            async def delete(self):
                raise AssertionError("Dry-run must never delete")

        class FakeChannel:
            async def history(self, limit=None):
                yield FakeMessage()

        found, removed = await reconcile_channel_history(FakeChannel(), {22}, 999, True)
        self.assertEqual(found, set())
        self.assertEqual(removed, 1)

    async def test_empty_history(self):
        class FakeChannel:
            async def history(self, limit=None):
                if False:
                    yield

        self.assertEqual(await reconcile_channel_history(FakeChannel(), set(), 999, False), (set(), 0))


if __name__ == "__main__":
    unittest.main()
