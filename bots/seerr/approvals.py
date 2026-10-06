"""The Approve and Decline buttons on a pending request post, gated on a slim-m permission."""

from __future__ import annotations

import asyncio
import contextlib
import re

from arrkit.guard import UNREACHABLE
from arrkit.service import AuthError
from slimbots import ApiError, Button, Permissions, rows

import seerr_core as core

ID_PREFIX = "seerrreq:"

_DECIDED = re.compile(r"\n(?:Approved|Declined) by [^\n]*\.$")


def buttons_for(request_id):
    return rows([
        Button("Approve", f"{ID_PREFIX}approve:{request_id}", style="primary"),
        Button("Decline", f"{ID_PREFIX}decline:{request_id}", style="danger"),
    ])


async def approver_refusal(bot, user_id, what):
    """None when the member may do `what` (approve, decline, manage links), else the sentence explaining why not."""
    needed = getattr(Permissions, core.SEERR_APPROVER_PERMISSION)
    if not bot.space.roles:
        return "the bot cannot read roles, so it cannot tell who may do that - an admin needs to give it MANAGE_ROLES."
    try:
        member = await bot.space.resolve_member(user_id)
    except ApiError:
        return "could not look you up - try again."
    if member.has_permission(needed):
        return None
    return f"only members with {core.SEERR_APPROVER_PERMISSION} can {what}."


async def on_decision_press(interaction):
    action, _, raw_id = interaction.custom_id[len(ID_PREFIX):].partition(":")
    if action not in ("approve", "decline") or not raw_id.isdigit():
        return
    refusal = await approver_refusal(interaction.bot, interaction.user_id, "approve or decline requests")
    if refusal:
        with contextlib.suppress(ApiError):
            await interaction.reply_ephemeral(refusal)
        return
    await interaction.ack()
    bot = interaction.bot
    verb = "approved" if action == "approve" else "declined"
    try:
        await asyncio.to_thread(core.set_request_state, raw_id, action)
    except (*UNREACHABLE, AuthError):
        with contextlib.suppress(ApiError):
            await interaction.reply_ephemeral(f"seerr did not accept that - request {raw_id} is unchanged.")
        return
    await bot.store.run(core.mark_announced, [f"r|{raw_id}|{verb}"])
    with contextlib.suppress(ApiError):
        await close_out(bot, interaction, f"{verb.capitalize()} by {interaction.user_display_name or 'a member'}.")


async def close_out(bot, interaction, decision):
    """Writes the decision under the post as it reads now, and drops the buttons; an unreadable post only loses them."""
    try:
        post = await bot.client.get_message(interaction.channel_id, interaction.message_id)
    except ApiError:
        post = None
    if post is not None and post.content and not _DECIDED.search(post.content):
        await bot.client.edit_message(interaction.channel_id, interaction.message_id, f"{post.content}\n{decision}")
    await bot.client.edit_components(interaction.channel_id, interaction.message_id, [])
