"""One-shot Discord verification reminder. Never edits roles or reactions."""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from dataclasses import dataclass
from pathlib import Path
import discord
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("VerifyNudge")

@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int
    channel_id: int
    role_id: int | None
    role_name: str
    message_id: int | None
    rules_channel_id: int | None
    dry_run: bool
    batch_size: int
    state_path: Path

def optional_id(text: str | None) -> int | None:
    value = (text or "").strip()
    if not value:
        return None
    result = int(value)
    if result <= 0:
        raise ValueError("Discord IDs must be positive")
    return result

def config() -> Config:
    load_dotenv(ROOT / ".env")
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    guild_id = optional_id(os.getenv("DISCORD_GUILD_ID"))
    channel_id = optional_id(os.getenv("DISCORD_VERIFICATION_CHANNEL_ID"))
    if not token or not guild_id or not channel_id:
        raise ValueError("Specify DISCORD_BOT_TOKEN, DISCORD_GUILD_ID and DISCORD_VERIFICATION_CHANNEL_ID")
    batch_size = int(os.getenv("MAX_NOTIFICATIONS_PER_RUN", "9999"))
    if not 1 <= batch_size <= 9999:
        raise ValueError("MAX_NOTIFICATIONS_PER_RUN must be 1..9999")
    return Config(
        token=token, guild_id=guild_id, channel_id=channel_id,
        role_id=optional_id(os.getenv("DISCORD_MUTED_ROLE_ID")),
        role_name=os.getenv("DISCORD_MUTED_ROLE_NAME", "Muted"),
        message_id=optional_id(os.getenv("DISCORD_VERIFICATION_MESSAGE_ID")),
        rules_channel_id=optional_id(os.getenv("DISCORD_RULES_CHANNEL_ID")),
        dry_run=os.getenv("DRY_RUN", "true").lower() not in ("false", "0", "no"),
        batch_size=batch_size, state_path=ROOT / "data" / f"state-{guild_id}.json",
    )

def load_state(path: Path, guild_id: int) -> set[int]:
    if not path.exists():
        return set()
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj["guild_id"] != guild_id:
        raise ValueError("Saved state belongs to another guild; do not overwrite")
    return {int(x) for x in obj["notified"]}

def save_state(path: Path, guild_id: int, ids: set[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"guild_id": guild_id, "notified": sorted(ids)}, indent=2), encoding="utf-8")
    os.replace(temp, path)

def plan(
    muted: set[int], notified: set[int], limit: int, ordered_ids: list[int] | None = None
) -> list[int]:
    """Return new targets, prioritizing recently joined members when available."""
    if ordered_ids is None:
        return sorted(muted - notified)[:limit]
    return [uid for uid in ordered_ids if uid in muted and uid not in notified][:limit]

def reminder_content(user_id: int, rules_channel_id: int) -> str:
    """Build a clickable reminder to react to the rules post."""
    return (
        f"<@{user_id}> 認証がまだ完了していないようです。"
        f"ルールチャンネル <#{rules_channel_id}> にあるルール確認の投稿を読み、"
        "その投稿に 👍 リアクションを付けてください。"
    )

REMINDER_MARKERS = (
    "認証がまだ完了していないようです",
    "まだ認証が完了していないようです",
)


async def reconcile_channel_history(
    channel: discord.TextChannel,
    muted_ids: set[int],
    bot_user_id: int,
    dry_run: bool,
) -> tuple[set[int], int]:
    """Deduplicate mentions and remove this bot's obsolete reminder messages.

    Only one-user reminder messages authored by this bot are deleted.
    Human messages and unrelated bot messages are never modified.
    """
    mentions_for_muted: set[int] = set()
    removed = 0
    scanned = 0
    failures = 0
    async for message in channel.history(limit=None):
        scanned += 1
        mentioned_ids = {user.id for user in message.mentions}
        mentions_for_muted.update(mentioned_ids & muted_ids)
        is_our_reminder = (
            message.author.id == bot_user_id
            and any(marker in message.content for marker in REMINDER_MARKERS)
            and len(mentioned_ids) == 1
        )
        if is_our_reminder and not (mentioned_ids & muted_ids):
            if dry_run:
                LOG.info("Dry-run: would remove completed reminder message %d", message.id)
            else:
                try:
                    await message.delete()
                except discord.HTTPException:
                    failures += 1
                    LOG.exception("Could not delete completed reminder message %d", message.id)
                else:
                    LOG.info("Deleted completed reminder message %d", message.id)
            removed += 1
    LOG.info(
        "History scanned=%d, currently-muted mentions=%d, obsolete reminders=%d",
        scanned, len(mentions_for_muted), removed,
    )
    if failures:
        raise RuntimeError(f"Failed to delete {failures} obsolete reminder messages")
    return mentions_for_muted, removed


async def scan(client: discord.Client, cfg: Config) -> None:
    guild = client.get_guild(cfg.guild_id)
    if guild is None:
        raise RuntimeError("Bot was not invited to configured guild")
    if cfg.role_id:
        role = guild.get_role(cfg.role_id)
    else:
        roles = [r for r in guild.roles if r.name == cfg.role_name]
        if len(roles) > 1:
            raise RuntimeError("Multiple Muted roles; set DISCORD_MUTED_ROLE_ID")
        role = roles[0] if roles else None
    if role is None:
        raise RuntimeError("Muted role not found")

    channel = client.get_channel(cfg.channel_id) or await client.fetch_channel(cfg.channel_id)
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != cfg.guild_id:
        raise RuntimeError("Verification channel ID is not a text channel in this guild")

    me = guild.me
    if me is None:
        raise RuntimeError("Bot server member could not be found")
    permissions = channel.permissions_for(me)
    if not (permissions.view_channel and permissions.send_messages and permissions.read_message_history):
        raise RuntimeError(
            "VerifyNudge needs View Channel, Send Messages and Read Message History"
        )

    muted_members: list[discord.Member] = []
    checked = 0
    async for member in guild.fetch_members(limit=None):
        checked += 1
        if not member.bot and role in member.roles:
            muted_members.append(member)
    muted = {member.id for member in muted_members}

    # History is checked on every scan, even if local state is missing.
    # Users who have cleared Muted no longer need their reminder posted here.
    historical, removed = await reconcile_channel_history(
        channel, muted, client.user.id, cfg.dry_run
    )
    previous = load_state(cfg.state_path, cfg.guild_id)
    notified = (previous & muted) | historical
    recent_first = [
        member.id
        for member in sorted(
            muted_members,
            key=lambda member: (member.joined_at or member.created_at, member.id),
            reverse=True,
        )
    ]
    pending = plan(muted, notified, cfg.batch_size, ordered_ids=recent_first)
    LOG.info(
        "Members=%d Muted=%d history_skipped=%d obsolete=%d pending=%d dry_run=%s",
        checked, len(muted), len(historical), removed, len(pending), cfg.dry_run,
    )
    if cfg.dry_run:
        LOG.info("Dry-run targets: %s", pending)
        return

    if notified != previous:
        save_state(cfg.state_path, cfg.guild_id, notified)

    send_failures = 0
    for uid in pending:
        reference = (discord.MessageReference(
            message_id=cfg.message_id, channel_id=cfg.channel_id,
            guild_id=cfg.guild_id, fail_if_not_exists=False
        ) if cfg.message_id else None)
        try:
            await channel.send(
                reminder_content(uid, cfg.rules_channel_id or cfg.channel_id),
                reference=reference,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=True, replied_user=False
                )
            )
        except discord.HTTPException:
            send_failures += 1
            LOG.exception("Could not notify user %d; will retry next hour", uid)
            continue
        notified.add(uid)
        save_state(cfg.state_path, cfg.guild_id, notified)
        LOG.info("Notified user %d", uid)
    if send_failures:
        raise RuntimeError(f"Failed to notify {send_failures} members")

async def run(cfg: Config) -> None:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.members = True
    client = discord.Client(intents=intents, chunk_guilds_at_startup=False)
    failure: Exception | None = None
    started = False

    @client.event
    async def on_ready() -> None:
        nonlocal failure, started
        if started:
            return
        started = True
        try:
            await scan(client, cfg)
        except Exception as exc:
            failure = exc
            LOG.exception("Scan failed")
        finally:
            await client.close()

    try:
        await asyncio.wait_for(client.start(cfg.token, reconnect=False), timeout=180)
    finally:
        if not client.is_closed():
            await client.close()
    if failure:
        raise RuntimeError("Scan failed") from failure

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Preserve the exact output of Task Scheduler runs in a local rolling log.
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "verifynudge.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    try:
        cfg = config()
        if args.dry_run:
            cfg = Config(**{**vars(cfg), "dry_run": True})
        asyncio.run(run(cfg))
        return 0
    except Exception:
        LOG.exception("VerifyNudge failed")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
