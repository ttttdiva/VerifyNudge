import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from verifynudge import already_mentioned_in_channel, load_state, plan, reminder_content, save_state

class NotifyTests(unittest.TestCase):
    def test_only_new_muted(self):
        self.assertEqual(plan({1,2,3}, {1}, 1), [2])

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
    async def test_prior_mentions_suppress_repeat(self):
        class FakeChannel:
            async def history(self, limit=None):
                assert limit is None
                yield SimpleNamespace(mentions=[SimpleNamespace(id=22)])
                yield SimpleNamespace(mentions=[SimpleNamespace(id=33)])
        found = await already_mentioned_in_channel(FakeChannel(), {22, 33, 44})
        self.assertEqual(found, {22, 33})
        self.assertEqual(plan({22, 33, 44}, found, 10), [44])

    async def test_stop_history_when_all_candidates_found(self):
        class FakeChannel:
            async def history(self, limit=None):
                yield SimpleNamespace(mentions=[SimpleNamespace(id=22)])
                raise AssertionError("Unnecessary history page read")
        self.assertEqual(await already_mentioned_in_channel(FakeChannel(), {22}), {22})

    async def test_empty_targets_does_not_read_history(self):
        class FakeChannel:
            def history(self, limit=None):
                raise AssertionError("History read should not happen")
        self.assertEqual(await already_mentioned_in_channel(FakeChannel(), set()), set())


if __name__ == "__main__":
    unittest.main()
