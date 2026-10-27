import tempfile
import unittest
from pathlib import Path
from verifynudge import load_state, plan, save_state

class NotifyTests(unittest.TestCase):
    def test_only_new_muted(self):
        self.assertEqual(plan({1,2,3}, {1}, 1), [2])

    def test_reentry(self):
        old = {42}
        cleared = old & set()
        self.assertEqual(plan({42}, cleared, 10), [42])

    def test_state(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state.json"
            save_state(state, 123, {3,1})
            self.assertEqual(load_state(state, 123), {1,3})
            with self.assertRaises(ValueError):
                load_state(state, 456)

if __name__ == "__main__":
    unittest.main()
