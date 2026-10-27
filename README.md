# VerifyNudge

A lightweight Discord bot that privately reminds unverified members to react to a rules post **via direct messages (DMs)**. It posts **nothing** into shared server channels.

Discord ephemeral replies require an interaction initiated by the user; a scheduled proactive notification must use DMs instead.

## Setup (Windows)

1. Create a Discord Bot and enable **Server Members Intent** in Discord Developer Portal.
2. Invite it to the target server. It does not need Manage Roles or Administrator.
3. Install Python 3.10 or later.

```powershell
git clone https://github.com/ttttdiva/VerifyNudge.git
cd VerifyNudge
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`: set the Bot token, server ID, Muted role ID, rules channel ID, and ideally the exact rules message ID. NEVER commit your token.

```powershell
# See targets without sending
.\.venv\Scripts\python.exe verifynudge.py --dry-run

# Send a test DM ONLY to one Muted test member (when DRY_RUN=false)
.\.venv\Scripts\python.exe verifynudge.py --only-user 123456789012345678

# After testing, optionally register an hourly task
.\scripts\install-hourly-task.ps1
```

- DISCORD_BOT_TOKEN: local secret.
- DISCORD_GUILD_ID: target server.
- DISCORD_MUTED_ROLE_ID: unverified role (or name fallback).
- DISCORD_RULES_CHANNEL_ID: required rules channel.
- DISCORD_RULES_MESSAGE_ID: optional exact post to link to.
- MAX_NOTIFICATIONS_PER_RUN: default 9999.
- DRY_RUN: true (default) disables sending.

Sends once per user while they remain Muted. Local DM notification history: `data/dm-state-<guild-id>.json`. Failed DMs are logged, with no public fallback; some users block DMs from Bots. Logs/DM state/`.env` are ignored by Git.

MIT License.
