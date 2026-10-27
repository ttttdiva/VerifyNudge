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
    channel_id: int | None
    role_id: int | None
    role_name: str
    message_id: int | None
    rules_channel_id: int | None
    rules_message_id: int | None
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
    if not token or not guild_id:
        raise ValueError("Specify DISCORD_BOT_TOKEN and DISCORD_GUILD_ID")
    rules_channel_id = optional_id(os.getenv("DISCORD_RULES_CHANNEL_ID"))
    if not rules_channel_id:
        raise ValueError("DISCORD_RULES_CHANNEL_ID is required for private DM reminders")
    batch_size = int(os.getenv("MAX_NOTIFICATIONS_PER_RUN", "9999"))
    if not 1 <= batch_size <= 9999:
        raise ValueError("MAX_NOTIFICATIONS_PER_RUN must be 1..9999")
    return Config(
        token=token, guild_id=guild_id, channel_id=channel_id,
        role_id=optional_id(os.getenv("DISCORD_MUTED_ROLE_ID")),
        role_name=os.getenv("DISCORD_MUTED_ROLE_NAME", "Muted"),
        message_id=optional_id(os.getenv("DISCORD_VERIFICATION_MESSAGE_ID")),
        rules_channel_id=rules_channel_id,
        rules_message_id=optional_id(os.getenv("DISCORD_RULES_MESSAGE_ID")),
        dry_run=os.getenv("DRY_RUN", "true").lower() not in ("false", "0", "no"),
        batch_size=batch_size, state_path=ROOT / "data" / f"dm-state-{guild_id}.json",
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

def reminder_content(
    guild_name: str, guild_id: int, rules_channel_id: int, rules_message_id: int | None
) -> str:
    """Private DM containing a clickable link to the rules/reaction post."""
    url = f"https://discord.com/channels/{guild_id}/{rules_channel_id}"
    if rules_message_id:
        url += f"/{rules_message_id}"
    return (
        f"【{guild_name}｜認証のお願い】\n"
        "まだサーバーの認証が完了していないようです。\n"
        "以下のルール投稿を確認して、その投稿に 👍 リアクションを付けてください。\n"
        f"{url}"
    )


async def scan(client: discord.Client, cfg: Config, only_user_id: int | None = None) -> None:
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

    rules_channel = guild.get_channel(cfg.rules_channel_id) or await client.fetch_channel(cfg.rules_channel_id)
    if not isinstance(rules_channel, discord.TextChannel) or rules_channel.guild.id != cfg.guild_id:
        raise RuntimeError("Rules channel ID is not a text channel in the configured server")

    muted_members: list[discord.Member] = []
    count = 0
    async for member in guild.fetch_members(limit=None):
        count += 1
        if not member.bot and role in member.roles:
            muted_members.append(member)

    muted_ids = {member.id for member in muted_members}
    previous = load_state(cfg.state_path, cfg.guild_id)
    # A separate DM-only state prevents old public mentions from blocking first DMs.
    notified = previous & muted_ids
    candidates = sorted(
        muted_members,
        key=lambda m: (m.joined_at or m.created_at, m.id),
        reverse=True,
    )
    if only_user_id is not None:
        candidates = [m for m in candidates if m.id == only_user_id]
        if not candidates:
            raise RuntimeError(f"Target {only_user_id} was not found with the Muted role")

    pending = [m for m in candidates if m.id not in notified][:cfg.batch_size]
    LOG.info(
        "Delivery=private-DM, rules_channel=%d, members=%d, muted=%d, "
        "already_dmed=%d, new=%d, dry_run=%s",
        cfg.rules_channel_id, count, len(muted_ids), len(notified), len(pending), cfg.dry_run,
    )
    if cfg.dry_run:
        LOG.info("Dry-run DM targets: %s", [m.id for m in pending])
        return

    if notified != previous:
        save_state(cfg.state_path, cfg.guild_id, notified)

    content = reminder_content(
        guild.name, cfg.guild_id, cfg.rules_channel_id, cfg.rules_message_id
    )
    failures = 0
    for member in pending:
        try:
            # Direct messages are visible to the recipient only. Never publish
            # a fallback message into the shared server channel on failure.
            await member.send(content, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            failures += 1
            LOG.warning("Could not DM user %d (DMs blocked or unavailable)", member.id)
            continue
        except discord.HTTPException:
            failures += 1
            LOG.exception("Discord failed to deliver DM to user %d", member.id)
            continue
        notified.add(member.id)
        save_state(cfg.state_path, cfg.guild_id, notified)
        LOG.info("DM sent to user %d", member.id)
    if failures:
        raise RuntimeError(f"Failed to deliver private DMs to {failures} member(s)")


async def run(cfg: Config, only_user_id: int | None = None) -> None:
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
            await scan(client, cfg, only_user_id)
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
    parser.add_argument("--only-user", type=int, help="Test one Muted member only")
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
        asyncio.run(run(cfg, args.only_user))
        return 0
    except Exception:
        LOG.exception("VerifyNudge failed")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
