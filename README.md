# VerifyNudge

A minimal, self-hosted Discord bot to remind **unverified members** to react to an existing verification post. Intended for servers where the `Muted` role denotes an unverified user.

**VerifyNudge is not a verification bot.** It does not assign or remove roles or manage reactions; it only posts reminders.

## Behavior

- Start once per hour, scan the target server for members with a configured role (default: `Muted`).
- Mention newly found users in your verification channel; optionally reply to an existing verification post.
- Notify a member **once per unverified period**, not every hour. When the member is seen without the role, forget their notification record.
- Limit new mentions to 10 per run by default.
- Exit after each scan; use your own scheduler. Default is `DRY_RUN=true`, which never sends messages.

## Preparation

1. Create a Discord application and bot at https://discord.com/developers/applications .
2. Enable **Server Members Intent** in the application Bot settings.
3. Invite the bot to your server. Grant **View Channels** and **Send Messages** for the verification channel. No Administrator or Manage Roles permission is required.
4. Make sure the server has a Muted (unverified) role and a verification channel.

## Installation (Windows / PowerShell)

```powershell
git clone https://github.com/ttttdiva/VerifyNudge.git
cd VerifyNudge
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in `.env` with your **own** Discord bot token, server ID, and verification channel ID. Prefer setting `DISCORD_MUTED_ROLE_ID` (a stable role ID) instead of relying on the role name.

Set `DISCORD_RULES_CHANNEL_ID` to the channel containing the rules post that users must react to. The bot mentions that channel in the reminder while **posting the reminder to** `DISCORD_VERIFICATION_CHANNEL_ID`. If `DISCORD_RULES_CHANNEL_ID` is blank, the reminder points to the notification channel instead.

Optional: `DISCORD_VERIFICATION_MESSAGE_ID` makes the reminder reply to an existing message **in the notification channel**. This does not change the rules/reaction target.

Test the scan without sending:

```powershell
.\.venv\Scripts\python.exe verifynudge.py --dry-run
```

When the correct test user appears in the scan logs, change `DRY_RUN=false` in `.env` and run the same script without `--dry-run` to send one test reminder.

## Hourly Windows task

After manual verification, register the hourly task using the virtual environment in this checkout:

```powershell
.\scripts\install-hourly-task.ps1
```

To disable or enable the task:

```powershell
Disable-ScheduledTask -TaskName VerifyNudge
Enable-ScheduledTask -TaskName VerifyNudge
```

The scheduler task uses the name `VerifyNudge` and is intended for one installation per Windows host. The computer must be running for scheduled checks.

## Configuration reference

| Environment variable | Purpose |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Secret bot token (never commit) |
| `DISCORD_GUILD_ID` | Target server ID |
| `DISCORD_VERIFICATION_CHANNEL_ID` | Verification channel ID |
| `DISCORD_RULES_CHANNEL_ID` | Channel containing the rules post (defaults to notification channel if unset) |
| `DISCORD_MUTED_ROLE_ID` | Unverified role ID (preferred) |
| `DISCORD_MUTED_ROLE_NAME` | Fallback role name (default Muted) |
| `DISCORD_VERIFICATION_MESSAGE_ID` | Optional post ID for message replies |
| `MAX_NOTIFICATIONS_PER_RUN` | Message cap per scan (default 10) |
| `DRY_RUN` | true: log but never post (default) |

Notification history is stored in a Git-ignored `data/state-<guild-id>.json`. Local `.env`, virtual environments, and the state directory are ignored by Git. **Never commit private credentials**.

Built with [discord.py](https://discordpy.readthedocs.io/). MIT licensed.
