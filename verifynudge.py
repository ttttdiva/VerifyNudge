"""One-shot Discord verification reminder. Never edits roles or reactions."""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
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
    batch_size = int(os.getenv("MAX_NOTIFICATIONS_PER_RUN", "10"))
    if not 1 <= batch_size <= 100:
        raise ValueError("MAX_NOTIFICATIONS_PER_RUN must be 1..100")
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

def plan(muted: set[int], notified: set[int], limit: int) -> list[int]:
    return sorted(muted - notified)[:limit]

def reminder_content(user_id: int, rules_channel_id: int) -> str:
    """Build a clickable reminder to react to the rules post."""
    return (
        f"<@{user_id}> 認証がまだ完了していないようです。"
        f"ルールチャンネル <#{rules_channel_id}> にあるルール確認の投稿を読み、"
        "その投稿に 👍 リアクションを付けてください。"
    )

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

    muted: set[int] = set()
    checked = 0
    async for member in guild.fetch_members(limit=None):
        checked += 1
        if not member.bot and role in member.roles:
            muted.add(member.id)
    previous = load_state(cfg.state_path, cfg.guild_id)
    notified = previous & muted
    pending = plan(muted, notified, cfg.batch_size)
    LOG.info("Members=%d Muted=%d new=%d dry_run=%s", checked, len(muted), len(pending), cfg.dry_run)
    if cfg.dry_run:
        LOG.info("Dry-run targets: %s", pending)
        return

    if notified != previous:
        save_state(cfg.state_path, cfg.guild_id, notified)

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
            LOG.exception("Could not notify user %d; will retry next hour", uid)
            continue
        notified.add(uid)
        save_state(cfg.state_path, cfg.guild_id, notified)
        LOG.info("Notified user %d", uid)

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
