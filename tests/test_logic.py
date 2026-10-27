import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from verifynudge import Config, load_state, plan, reminder_content, save_state, scan

class LogicTests(unittest.TestCase):
    def test_priority(self):
        self.assertEqual(plan({1,2,3}, {2}, 10, ordered_ids=[3,2,1]), [3,1])

    def test_dm_link(self):
        msg=reminder_content("Test Server",123,456,789)
        self.assertIn("https://discord.com/channels/123/456/789",msg)
        self.assertIn("👍",msg)
        self.assertNotIn("<@123>",msg)

    def test_channel_link_fallback(self):
        self.assertIn("123/456",reminder_content("Test",123,456,None))

    def test_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"dm-state.json"
            save_state(path,123,{1,2})
            self.assertEqual(load_state(path,123),{1,2})
            with self.assertRaises(ValueError):
                load_state(path,456)

class DMTests(unittest.IsolatedAsyncioTestCase):
    async def test_dm_only_and_not_duplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            role=SimpleNamespace(id=2,name="Muted")
            guild=SimpleNamespace(id=123,name="Test Server")
            rules=SimpleNamespace(id=456,guild=guild)
            guild.get_role=lambda rid: role if rid==2 else None
            guild.get_channel=lambda cid: rules if cid==456 else None
            class Member:
                id=999
                bot=False
                roles=[role]
                joined_at=datetime(2026,1,1,tzinfo=timezone.utc)
                created_at=joined_at
                def __init__(self): self.sent=[]
                async def send(self,content,**kwargs): self.sent.append(content)
            user=Member()
            async def members(limit=None): yield user
            guild.fetch_members=members
            client=SimpleNamespace(get_guild=lambda gid:guild)
            cfg=Config(
                token="fake",guild_id=123,channel_id=111,role_id=2,role_name="Muted",
                message_id=None,rules_channel_id=456,rules_message_id=789,dry_run=False,
                batch_size=9999,state_path=Path(folder)/"dm.json"
            )
            with patch("verifynudge.discord.TextChannel",type(rules)):
                await scan(client,cfg)
                await scan(client,cfg)
            self.assertEqual(len(user.sent),1)
            self.assertIn("/456/789",user.sent[0])
            self.assertEqual(load_state(cfg.state_path,123),{999})

if __name__=="__main__":
    unittest.main()
