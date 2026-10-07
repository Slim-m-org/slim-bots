#!/usr/bin/env python3
"""bot-roles: self-service roles - `!role <name>`, `!role remove <name>`, `!role mine`, `!roles`, `!roles status`; see README.md."""

import uuid

from slimbots import Bot, Permissions
from slimbots.http import ApiError, is_forbidden, is_not_found

# Namespace for deriving a stable listing-message id from the channel id, so nothing needs to be persisted to disk.
LISTING_NAMESPACE = uuid.UUID("d1f6a9d0-0f0f-4b6a-9b0f-2f6b6f0f9a10")


bot = Bot(prefix="!", require_channels=True)
ROLES = bot.setting("SLIMM_ROLES", {}, type=dict)


def normalize(name):
    return " ".join(name.split()).casefold()


def find_role(name):
    """The configured (name, id) a typed name means, matched ignoring case and spacing, or None."""
    wanted = normalize(name)
    return next(((known, role_id) for known, role_id in ROLES.items() if normalize(known) == wanted), None)


def unusable_role_names(mapping):
    """Names nobody could type: a subcommand word, or a repeat of an earlier name once case is ignored."""
    seen, bad = set(), []
    for name in mapping:
        key = normalize(name)
        if key in ("mine", "remove") or key.startswith("remove ") or key in seen:
            bad.append(name)
        seen.add(key)
    return bad


def listing_message_id():
    return str(uuid.uuid5(LISTING_NAMESPACE, bot.channel))


def listing_text():
    lines = [
        "**Self-service roles**",
        f"`{bot.prefix}role <name>` to add one, `{bot.prefix}role remove <name>` to drop it, `{bot.prefix}role mine` to see what you hold.",
        "",
    ]
    lines.extend(f"- `{name}`" for name in ROLES)
    return "\n".join(lines)


async def post_listing():
    """Publishes the role listing at startup, editing the previous one in place when it already exists."""
    message_id = listing_message_id()
    try:
        await bot.client.edit_message(bot.channel, message_id, listing_text())
    except ApiError as err:
        if not is_not_found(err):
            raise
        await bot.client.send(bot.channel, listing_text(), message_id=message_id)


async def my_permissions():
    """The bot's own bits, read fresh: an admin grants them while it runs, and diagnostics must see that."""
    return (await bot.client.me()).get("permissions", 0)


async def fetch_role_permissions(role_id):
    """The configured role's own permission bits, or None if unreadable (needs MANAGE_ROLES, or the role is gone)."""
    try:
        roles = await bot.client.list_roles()
    except ApiError as err:
        if is_forbidden(err):
            return None
        raise
    return next((role["permissions"] for role in roles if role["id"] == role_id), None)


async def escalation_explanation(role_name, role_id):
    """Names the exact permission gap - a 403 alone cannot say which guard fired, so this asks `GET /roles` too."""
    held = await my_permissions()
    if not (held & Permissions.MANAGE_ROLES):
        return (
            "I can't grant or remove any role here, not even a zero-permission one - I don't hold MANAGE_ROLES "
            "myself. An admin needs to grant this bot's own account MANAGE_ROLES before self-service roles can work at all."
        )
    role_permissions = await fetch_role_permissions(role_id)
    if role_permissions is None:
        return (
            f"I hold MANAGE_ROLES but still can't grant `{role_name}` - either it was deleted, or something else "
            "is wrong. An admin should check it still exists."
        )
    missing = Permissions.names(role_permissions & ~held)
    if not missing:
        return (
            f"granting `{role_name}` was refused, but I hold everything it carries - an admin should check my "
            "role assignment did not just change."
        )
    named = ", ".join(missing)
    return (
        f"I hold MANAGE_ROLES, but `{role_name}` also carries {named}, which I don't hold myself. An admin needs "
        f"to grant this bot's own account {named} before it can hand out `{role_name}`."
    )


async def grant(ctx, typed):
    found = find_role(typed)
    if found is None:
        await ctx.reply(f"no role called `{typed}` is offered here - try `{bot.prefix}roles`.")
        return
    role_name, role_id = found
    try:
        await bot.space.grant_role(ctx.author, role_id)
    except ApiError as err:
        if is_forbidden(err):
            await ctx.reply(await escalation_explanation(role_name, role_id))
            return
        if is_not_found(err):
            await ctx.reply(f"`{role_name}` is misconfigured on my end - ask an admin to check it.")
            return
        raise
    await ctx.reply(f"done - you have `{role_name}` now.")


async def revoke(ctx, typed):
    found = find_role(typed)
    if found is None:
        await ctx.reply(f"no role called `{typed}` is offered here - try `{bot.prefix}roles`.")
        return
    role_name, role_id = found
    try:
        await bot.space.revoke_role(ctx.author, role_id)
    except ApiError as err:
        if is_forbidden(err):
            await ctx.reply(await escalation_explanation(role_name, role_id))
            return
        raise
    await ctx.reply(f"removed `{role_name}`.")


async def show_mine(ctx):
    held = set(ctx.author.role_ids)
    mine = [name for name, role_id in ROLES.items() if role_id in held]
    if mine:
        await ctx.reply(f"you hold: {', '.join(mine)}")
    else:
        await ctx.reply("you hold none of the roles offered here.")


async def show_status(ctx):
    """The same diagnosis a failed grant gives, but on demand and for every configured role at once."""
    held = await my_permissions()
    lines = [f"I hold: {', '.join(Permissions.names(held)) or 'nothing'}"]
    if not (held & Permissions.MANAGE_ROLES):
        lines.append("MANAGE_ROLES is missing, so no role here is grantable yet.")
        await ctx.reply("\n".join(lines))
        return
    for name, role_id in ROLES.items():
        role_permissions = await fetch_role_permissions(role_id)
        if role_permissions is None:
            lines.append(f"`{name}`: cannot verify (role missing or unreadable)")
            continue
        missing = Permissions.names(role_permissions & ~held)
        lines.append(f"`{name}`: grantable" if not missing else f"`{name}`: missing {', '.join(missing)}")
    await ctx.reply("\n".join(lines))


@bot.command(name="roles", help="List self-service roles, or `status` to diagnose what's grantable", usage="[status]")
async def roles_cmd(ctx, sub: str = None):
    if sub and sub.lower() == "status":
        await show_status(ctx)
        return
    await ctx.reply(listing_text())


@bot.command(name="role", help="Add a role, `remove <name>` to drop it, `mine` to see what you hold", usage="<name> | remove <name> | mine")
async def role_cmd(ctx, name: str):
    action, _, rest = name.strip().partition(" ")
    if normalize(name) == "mine":
        await show_mine(ctx)
        return
    if action.lower() == "remove" and rest.strip():
        await revoke(ctx, rest.strip())
        return
    await grant(ctx, name.strip())


@bot.event
async def on_connect():
    await post_listing()


def main():
    if not ROLES:
        raise SystemExit("set SLIMM_ROLES")
    if unusable := unusable_role_names(ROLES):
        raise SystemExit(f"SLIMM_ROLES has names that cannot be requested (a subcommand word or a case-insensitive repeat): {', '.join(unusable)}")
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
