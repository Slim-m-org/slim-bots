"""Which Jellyfin account a slim-m member watches as: `!jellyfin link|unlink|account`; see README.md.
With no link a member uses the shared account (`JELLYFIN_USER_ID`, else the first enabled user)."""

from __future__ import annotations

import asyncio
import http.client
import sys
import time
import urllib.error
from dataclasses import dataclass

from slimbots import ApiError

import jellyfin_core
import playback_progress
import quick_connect

POLL_SECONDS = 5.0
CODE_LIFETIME_SECONDS = 300.0
CONFIRM_SECONDS = 600.0
JELLYFIN_DOWN = (OSError, http.client.HTTPException)


@dataclass
class _Flow:
    """One member's link in progress: waiting on a code, then holding the approval until they confirm it."""

    code: str
    approved: quick_connect.Approved | None = None
    expires_at: float = 0.0


_flows: dict[str, _Flow] = {}


def get_link(conn, slimm_user_id):
    """`(jellyfin_user_id, jellyfin_name)` for a member, or None."""
    row = conn.execute("SELECT jellyfin_user_id, jellyfin_name FROM user_links WHERE slimm_user_id = ?", (slimm_user_id,)).fetchone()
    return (row[0], row[1]) if row else None


def verified_link(conn, slimm_user_id):
    """The link only when the member proved it with Quick Connect; a link made by typing a username does not count."""
    row = conn.execute(
        "SELECT jellyfin_user_id, jellyfin_name FROM user_links WHERE slimm_user_id = ? AND verified = 1", (slimm_user_id,)
    ).fetchone()
    return (row[0], row[1]) if row else None


def owner_of(conn, jellyfin_user_id):
    row = conn.execute("SELECT slimm_user_id FROM user_links WHERE jellyfin_user_id = ?", (jellyfin_user_id,)).fetchone()
    return row[0] if row else None


def set_link(conn, slimm_user_id, jellyfin_user_id, jellyfin_name, verified=False):
    conn.execute(
        "INSERT INTO user_links (slimm_user_id, jellyfin_user_id, jellyfin_name, linked_at, verified) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(slimm_user_id) DO UPDATE SET jellyfin_user_id = excluded.jellyfin_user_id, "
        "jellyfin_name = excluded.jellyfin_name, linked_at = excluded.linked_at, verified = excluded.verified",
        (slimm_user_id, jellyfin_user_id, jellyfin_name, int(time.time()), int(verified)),
    )
    conn.commit()


def claim_link(conn, slimm_user_id, jellyfin_user_id, jellyfin_name, verified=False):
    """One store call, so the owner check and the write cannot be split by another member's link; False if someone else holds it."""
    taken_by = owner_of(conn, jellyfin_user_id)
    if taken_by not in (None, slimm_user_id):
        return False
    set_link(conn, slimm_user_id, jellyfin_user_id, jellyfin_name, verified)
    return True


def rename_link(conn, slimm_user_id, jellyfin_name):
    conn.execute("UPDATE user_links SET jellyfin_name = ? WHERE slimm_user_id = ?", (jellyfin_name, slimm_user_id))
    conn.commit()


def remove_link(conn, slimm_user_id):
    removed = conn.execute("DELETE FROM user_links WHERE slimm_user_id = ?", (slimm_user_id,)).rowcount
    conn.commit()
    return removed > 0


async def user_for(bot, slimm_user_id):
    """The Jellyfin user id to read and write for this member: their link, else the shared account."""
    link = await bot.store.run(get_link, slimm_user_id) if slimm_user_id else None
    if link is not None:
        return link[0]
    return await asyncio.to_thread(playback_progress.resolve_user_id)


async def _tell(ctx, text, fallback):
    """Answers privately; a server without private replies gets the name-free `fallback` in the channel."""
    try:
        await ctx.reply_ephemeral(text)
    except ApiError as err:
        if err.status not in (404, 405):
            raise
        await ctx.reply(fallback)


async def run_link(ctx, rest):
    word = (rest or "").strip().lower()
    if word == "confirm":
        await run_confirm(ctx)
    elif word == "cancel":
        removed = _flows.pop(ctx.author.id, None)
        await _tell(ctx, "cancelled." if removed else "you had no link in progress.", "done.")
    else:
        await run_start(ctx)


async def run_start(ctx):
    flow = _flows.get(ctx.author.id)
    if flow is not None and flow.approved is None:
        await _tell(ctx, f"you already have a code waiting: {flow.code}.", "you already have a code waiting.")
        return
    wait_message = jellyfin_core._command_cooldown.check(ctx.author.id)
    if wait_message:
        await ctx.reply(wait_message)
        return
    try:
        pending = await asyncio.to_thread(quick_connect.initiate, ctx.author.display_name)
    except quick_connect.QuickConnectDisabled:
        await ctx.reply("linking needs Quick Connect, which is turned off on this jellyfin server. A jellyfin admin can turn it on in the dashboard.")
        return
    except JELLYFIN_DOWN:
        await ctx.reply("jellyfin is unavailable right now.")
        return
    minutes = int(CODE_LIFETIME_SECONDS // 60)
    try:
        await ctx.reply_ephemeral(
            f"your code is {pending.code}. In jellyfin, open your profile, choose Quick Connect and enter it within {minutes} minutes. "
            "Only enter a code you asked for yourself."
        )
    except ApiError as err:
        if err.status not in (404, 405):
            raise
        await ctx.reply("this server cannot send private replies, and the code has to stay private, so linking is not available here.")
        return
    flow = _Flow(code=pending.code)
    _flows[ctx.author.id] = flow
    ctx.bot.background(_await_approval(ctx, flow, pending), name=f"jellyfin link {ctx.author.id}")


async def _await_approval(ctx, flow, pending):
    """Polls until the code is approved or expires, then asks the member to confirm who approved it; never raises."""
    try:
        approved = await _poll(pending)
        if _flows.get(ctx.author.id) is not flow:
            return
        if approved is None:
            del _flows[ctx.author.id]
            await ctx.reply_ephemeral(f"the code expired without being approved. `{ctx.bot.prefix}jellyfin link` gets a new one.")
            return
        flow.approved, flow.expires_at = approved, time.monotonic() + CONFIRM_SECONDS
        await ctx.reply_ephemeral(
            f'the code was approved by the jellyfin user "{approved.user_name}". If that is your account, '
            f"`{ctx.bot.prefix}jellyfin link confirm` links you to it. If not, ignore this."
        )
    except (ApiError, *JELLYFIN_DOWN, KeyError, TypeError, ValueError) as err:
        if _flows.get(ctx.author.id) is flow:
            del _flows[ctx.author.id]
        print(f"jellyfin link for {ctx.author.id} stopped: {type(err).__name__}: {err}", file=sys.stderr)


async def _poll(pending):
    """The approving user once the code is approved, or None when it expires or Jellyfin forgets it."""
    deadline = time.monotonic() + CODE_LIFETIME_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        try:
            if await asyncio.to_thread(quick_connect.is_approved, pending):
                return await asyncio.to_thread(quick_connect.redeem, pending)
        except urllib.error.HTTPError as err:
            if err.code in (400, 401, 404):
                return None
        except JELLYFIN_DOWN:
            continue
    return None


def _take_approval(member_id):
    """Removes and returns the member's approval while it is still fresh; a stale one is dropped as well."""
    flow = _flows.get(member_id)
    if flow is None or flow.approved is None:
        return None
    del _flows[member_id]
    return flow.approved if time.monotonic() <= flow.expires_at else None


async def run_confirm(ctx):
    approved = _take_approval(ctx.author.id)
    if approved is None:
        await _tell(ctx, f"there is no approved code to confirm. `{ctx.bot.prefix}jellyfin link` starts one.", "nothing to confirm.")
        return
    if not await ctx.bot.store.run(claim_link, ctx.author.id, approved.user_id, approved.user_name, True):
        await _tell(ctx, f'"{approved.user_name}" is already linked to someone else.', "that jellyfin user is already linked.")
        return
    p = ctx.bot.prefix
    await _tell(
        ctx, f'linked as "{approved.user_name}". `{p}watch` now uses their resume position and continue watching, and `{p}jellyfin unlink` undoes it.',
        "linked.",
    )


async def run_unlink(ctx):
    removed = await ctx.bot.store.run(remove_link, ctx.author.id)
    text = f"unlinked - `{ctx.bot.prefix}watch` uses the shared jellyfin account again." if removed else "you were not linked to a jellyfin user."
    await _tell(ctx, text, "done.")


async def _current_name(jellyfin_user_id):
    """The jellyfin user's name now, or None once that user is gone; a stored name can go stale."""
    try:
        user = await asyncio.to_thread(jellyfin_core.jf_get, f"/Users/{jellyfin_user_id}")
    except urllib.error.HTTPError as err:
        if err.code in (400, 404):
            return None
        raise
    return user.get("Name")


async def run_account(ctx):
    p = ctx.bot.prefix
    link = await ctx.bot.store.run(get_link, ctx.author.id)
    if link is None:
        text = f"you are not linked, so `{p}watch` uses the shared jellyfin account. `{p}jellyfin link` links you with a Quick Connect code."
    elif await ctx.bot.store.run(verified_link, ctx.author.id) is None:
        text = f'you are linked to "{link[1]}" by name only, which was never confirmed. `{p}jellyfin link` confirms it with a Quick Connect code.'
    else:
        text = await _verified_account_text(ctx, link)
    await _tell(ctx, text, f"see `{p}jellyfin help`.")


async def _verified_account_text(ctx, link):
    try:
        name = await _current_name(link[0])
    except (jellyfin_core.JellyfinAuthError, *JELLYFIN_DOWN):
        return f'you are linked to the jellyfin user "{link[1]}".'
    if name is None:
        await ctx.bot.store.run(remove_link, ctx.author.id)
        return f"the jellyfin user you were linked to no longer exists, so you are unlinked. `{ctx.bot.prefix}jellyfin link` links another."
    if name != link[1]:
        await ctx.bot.store.run(rename_link, ctx.author.id, name)
    return f'you are linked to the jellyfin user "{name}".'
