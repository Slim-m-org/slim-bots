#!/usr/bin/env python3
"""Tickets: a button opens a private channel for one member and staff, who claim and close it; see README.md."""

import logging
import uuid

from slimbots import ApiError, Bot, Button, Permissions, rows
from slimbots.hidden_chars import has_hidden_char

import ticketdb

log = logging.getLogger("tickets")

bot = Bot(prefix="!", default_data_path="tickets.db", store_migrate=ticketdb.init_db)

STAFF_ROLE = bot.setting("TICKETS_STAFF_ROLE", "")
CATEGORY = bot.setting("TICKETS_CATEGORY", "")
MAX_TRANSCRIPT = bot.setting("TICKETS_MAX_TRANSCRIPT", 100000, type=int)
MAX_REASON = bot.setting("TICKETS_MAX_REASON", 200, type=int)

PAGE_SIZE = 1800
HISTORY_PAGE = 100
MAX_HISTORY_PAGES = 100
TICKET_PERMISSIONS = Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES | Permissions.ATTACH_FILES | Permissions.ADD_REACTIONS


def staff_role_id():
    role = bot.space.get_role(STAFF_ROLE) if STAFF_ROLE else None
    return role.id if role else None


async def is_staff(user_id):
    member = await bot.space.find_member(user_id)
    if member is None:
        return False
    role_id = staff_role_id()
    return member.has_permission(Permissions.MANAGE_SERVER) or (role_id is not None and role_id in member.role_ids)


async def category_id():
    if not CATEGORY:
        return None
    for category in await bot.client.call("GET", "/categories"):
        if CATEGORY in (category["id"], category["name"]) or category["name"].lower() == CATEGORY.lower():
            return category["id"]
    log.warning("TICKETS_CATEGORY %r matches no category; tickets go uncategorised", CATEGORY)
    return None


def ticket_buttons(*, claimed):
    return rows([Button("Claimed" if claimed else "Claim", "ticket:claim", style="primary", disabled=claimed), Button("Close", "ticket:close", style="danger")])


@bot.group(name="ticket", help="`panel` posts the open-a-ticket button, `transcript <n>` reads a closed one, `reason <text>` sets this ticket's reason")
async def ticket(ctx, rest: str = ""):
    p = bot.prefix
    await ctx.reply_ephemeral(f"try `{p}ticket panel`, `{p}ticket transcript <number>` or `{p}ticket reason <text>`", public_fallback=True)


@ticket.command(
    name="panel", help="Post the Open a ticket button here",
    requires=Permissions.MANAGE_SERVER, requires_message="only someone with Manage Server can post the ticket panel.",
)
async def ticket_panel(ctx):
    await bot.client.send(
        ctx.channel_id, "Need help? Press the button to open a private ticket with the staff.",
        components=rows([Button("Open a ticket", "ticket:open", style="primary")]),
    )


@ticket.command(name="transcript", help="Read a closed ticket's transcript privately", usage="<number> [page]")
async def ticket_transcript(ctx, number: int, page: int = 1):
    if not await is_staff(ctx.author.id):
        await ctx.reply_ephemeral("only ticket staff can read a transcript.", public_fallback=True)
        return
    found = await bot.store.run(ticketdb.get_transcript, number)
    if found is None:
        await ctx.reply_ephemeral(f"no saved transcript for ticket {number}.", public_fallback=True)
        return
    opener, body, truncated = found
    pages = max(1, -(-len(body) // PAGE_SIZE))
    page = min(max(page, 1), pages)
    start = (page - 1) * PAGE_SIZE
    note = " (cut at the size limit when saved)" if truncated else ""
    head = f"ticket {number} from {opener}, page {page} of {pages}{note}"
    await ctx.reply_ephemeral(f"{head}\n{body[start:start + PAGE_SIZE] or '(empty)'}", public_fallback=True)


@ticket.command(name="reason", help="Say what this ticket is about", usage="<text>")
async def ticket_reason(ctx, text: str):
    found = await bot.store.run(ticketdb.by_channel, ctx.channel_id)
    if found is None:
        await ctx.reply_ephemeral("run this inside your ticket.", public_fallback=True)
        return
    number, opener_id = found[0], found[1]
    if ctx.author.id != opener_id and not await is_staff(ctx.author.id):
        await ctx.reply_ephemeral("only the ticket's opener or staff can set the reason.", public_fallback=True)
        return
    text = " ".join(text.split())
    if not text or len(text) > MAX_REASON or has_hidden_char(text):
        await ctx.reply_ephemeral(f"the reason is 1 to {MAX_REASON} plain characters.", public_fallback=True)
        return
    await bot.store.run(ticketdb.set_reason, number, text)
    await bot.client.call("PATCH", f"/channels/{ctx.channel_id}", {"topic": text})
    await ctx.reply_ephemeral("reason saved.", public_fallback=True)


@bot.button(prefix="ticket:")
async def on_ticket_button(interaction):
    action = interaction.custom_id.removeprefix("ticket:")
    handler = {"open": open_ticket, "claim": claim_ticket, "close": close_ticket}.get(action)
    if handler is None:
        await interaction.ack()
        return
    await handler(interaction)


async def open_ticket(interaction):
    role_id = staff_role_id()
    if role_id is None:
        await interaction.reply_ephemeral("tickets are not set up yet: an admin needs to set TICKETS_STAFF_ROLE.")
        return
    name = interaction.user_display_name or "member"
    number = await bot.store.run(ticketdb.reserve, interaction.user_id, name)
    if number is None:
        _, channel_id = await bot.store.run(ticketdb.live_ticket, interaction.user_id)
        channel = bot.space.get_channel(channel_id) if channel_id else None
        where = f"<#{channel_id}>" if channel else "one that is still being set up"
        await interaction.reply_ephemeral(f"you already have an open ticket: {where}. close it before opening another.")
        return
    try:
        channel = await create_private_channel(number, interaction.user_id, role_id)
    except ApiError as err:
        await bot.store.run(ticketdb.release, number)
        log.warning("could not open ticket %s: %s", number, err)
        await interaction.reply_ephemeral("could not open a ticket: the bot needs Manage Channels and Manage Roles. tell an admin.")
        return
    await bot.store.run(ticketdb.activate, number, channel["id"])
    await bot.client.send(channel["id"], await opening_message(number, interaction.user_id, role_id), components=ticket_buttons(claimed=False))
    await interaction.reply_ephemeral(f"Your ticket is open: <#{channel['id']}>")


async def opening_message(number, opener_id, role_id):
    opener = await bot.space.find_member(opener_id)
    role = bot.space.get_role(role_id)
    pings = " ".join(p for p in (f"@{opener.username}" if opener else "", f"@[{role.name}]" if role else "") if p)
    return f"Ticket {number:04d} for {pings}. Staff will be with you soon. Say what it is about with `{bot.prefix}ticket reason <text>`."


async def create_private_channel(number, opener_id, role_id):
    body = {"id": str(uuid.uuid4()), "name": f"ticket-{number:04d}", "kind": "text", "restricted": True}
    parent = await category_id()
    if parent:
        body["category_id"] = parent
    channel = await bot.client.call("POST", "/channels", body)
    grants = [{"kind": "member", "id": opener_id, "allow": TICKET_PERMISSIONS}, {"kind": "role", "id": role_id, "allow": TICKET_PERMISSIONS}]
    try:
        await bot.client.call("PUT", f"/channels/{channel['id']}/overwrites", {"overwrites": grants})
    except ApiError:
        await bot.client.call("DELETE", f"/channels/{channel['id']}")
        raise
    return channel


async def staff_only(interaction):
    if await is_staff(interaction.user_id):
        return True
    await interaction.reply_ephemeral("only ticket staff can do that.")
    return False


async def claim_ticket(interaction):
    if not await staff_only(interaction):
        return
    found = await bot.store.run(ticketdb.by_channel, interaction.channel_id)
    if found is None:
        await interaction.ack()
        return
    name = interaction.user_display_name or "staff"
    if not await bot.store.run(ticketdb.claim, found[0], interaction.user_id, name):
        await interaction.reply_ephemeral(f"already claimed by {found[5] or 'someone else'}.")
        return
    await interaction.edit_components(ticket_buttons(claimed=True))
    await bot.client.send(interaction.channel_id, f"{name} claimed this ticket.")


async def close_ticket(interaction):
    if not await staff_only(interaction):
        return
    found = await bot.store.run(ticketdb.by_channel, interaction.channel_id)
    if found is None or not await bot.store.run(ticketdb.begin_close, found[0]):
        await interaction.ack()
        return
    number = found[0]
    try:
        messages = await fetch_history(interaction.channel_id)
        body, truncated = ticketdb.build_transcript(messages, MAX_TRANSCRIPT)
        await bot.store.run(ticketdb.finish_close, number, interaction.user_id, body, truncated)
    except ApiError as err:
        await bot.store.run(ticketdb.abort_close, number)
        log.warning("could not close ticket %s: %s", number, err)
        await interaction.reply_ephemeral("could not read the ticket's messages, so it stays open. try again.")
        return
    await interaction.reply_ephemeral(f"ticket {number} closed. read it later with `{bot.prefix}ticket transcript {number}`.")
    try:
        await bot.client.call("DELETE", f"/channels/{interaction.channel_id}")
    except ApiError as err:
        log.warning("ticket %s is saved but its channel was not deleted: %s", number, err)


async def fetch_history(channel_id):
    collected = []
    before = None
    for _ in range(MAX_HISTORY_PAGES):
        params = {"limit": HISTORY_PAGE, **({"before": before} if before else {})}
        page = await bot.client.call("GET", f"/channels/{channel_id}/messages", params=params)
        collected.extend(page)
        if len(page) < HISTORY_PAGE:
            break
        before = min(m["seq"] for m in page)
    return collected


if __name__ == "__main__":
    bot.run_cli()
