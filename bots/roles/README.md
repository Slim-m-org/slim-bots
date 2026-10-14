# roles

A slim-m bot: `!role <name>` to grab a self-service role, `!role remove
<name>` to drop it, `!role mine` to see what you hold, `!roles` to see
what is on offer, `!roles status` to diagnose what this bot can currently
grant. At startup it also posts (or updates) that same listing in its
channel.

Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
`@bot.command` replaces the old regex trigger wall, role
grants/revokes go through `bot.space.grant_role`/`revoke_role` rather
than a raw HTTP call, and `Bot` itself reads `SLIMM_URL`/`SLIMM_BOT_TOKEN`/
`SLIMM_CHANNELS` and persists the seq cursor - this script never imports
`os`, reading `SLIMM_ROLES` through `bot.setting()` instead. `bot-ping`
stays free of the library on purpose; see its own README.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
SLIMM_ROLES=member:<role-uuid>,helper:<role-uuid> \
python3 bot.py
```

`SLIMM_ROLES` is a comma-separated `name:role-id` map. Only the roles listed
there are offered; a request for anything else is refused with a message
that says so, never ignored silently. Names may hold spaces and are matched
ignoring case; the bot refuses to start if one is `mine`, `remove`, starts
with `remove `, or repeats another once case is ignored. `SLIMM_CHANNELS` should name exactly
one channel here - `bot.channel` is that one channel - the one this bot
watches and posts in. See "Getting a token" and "What your bot may do" in
`docs/bots/building-bots.md` for how to find channel and role ids, and how
to grant the bot `SEND_MESSAGES`/`VIEW_CHANNEL` there.

## Role menus

A role menu is one message with a button per role; pressing a button toggles that role for the presser.
Someone with Manage Roles runs these in the channel where the menu should live:

- `!rolemenu create [--one] <title> | <role>, <role>` posts the menu. `--one` makes it pick-one: choosing a role removes the presser's other roles from that menu.
- `!rolemenu add <menu id> <role>` and `!rolemenu remove <menu id> <role>` change the buttons in place.
- `!rolemenu delete <menu id>` deletes the message and forgets the menu.
- `!rolemenu list` shows the menus in this channel with their ids.

Only roles from `SLIMM_ROLES` may go in a menu; anything else is refused privately with the reason.
A menu holds at most 25 roles (the server allows 5 rows of 5 buttons, `components.rs`), and a title is at most 200 characters.
Every reply to these commands, and to a button press, is private ("added `x`" / "removed `x`").

Menus are stored in a sqlite file (`SLIMM_DB_PATH`, default `roles.db`), so buttons keep working after a restart.
A press is checked against the stored menu: the role comes from the menu row for that message, never from the custom id alone, so a forged id naming a role outside the menu is refused.
If the role has since left `SLIMM_ROLES`, was deleted from the space, or is above what the bot may grant, the presser gets a private explanation instead of a silent failure.

## The no-escalation rule

`crates/slimm-server/src/http/roles.rs` refuses to grant a role that carries
a permission the actor does not already hold themselves. That check applies
to this bot exactly the way it applies to a person: holding `MANAGE_ROLES`
is not enough on its own, the bot must also already hold at least whatever
permissions the roles it hands out carry.

Concretely, this means:

- If a role in `SLIMM_ROLES` carries no extra permissions (a plain "member"
  or "notified" tag with `permissions: 0`), the bot only needs `MANAGE_ROLES`
  itself, since granting `0` extra permissions can never escalate anyone.
- If a role carries real permissions (say a "helper" role with
  `MANAGE_MESSAGES`), the bot needs `MANAGE_MESSAGES` too, or every grant of
  that role comes back `403` and the member sees a plain refusal explaining
  why rather than the bot quietly doing nothing.

This is deliberate on the platform's side, not a bug to route around. A
deployment that wants this bot to hand out a role with real permissions has
to give the bot at least that much itself. If a grant is coming back
forbidden, check the bot's own roles before assuming the code is broken.

## Diagnosing the gap instead of a bare refusal

The server gives every `403` the same body, so a failed grant and a missing
`MANAGE_ROLES` are not distinguishable from the wire alone. This bot tells
them apart using what it already has: its own `permissions` bitmask from
`GET /me`, and - once `MANAGE_ROLES` is confirmed - the target role's own
bits from `GET /roles`, turned into names with `slimbots.Permissions.names`.
`!role <name>` and `!role remove <name>` show this automatically on a
refusal; `!roles status` runs the same check for every configured role at
once, on demand, so an admin can see the whole picture without provoking a
403 first.

## Do not answer yourself

Like every slim-m bot, this one checks the message author against its own
id from `GET /me` before acting - see "Answering yourself" in
`docs/bots/building-bots.md`. It matters more here than in `bot-ping`: this
bot posts its own role listing in the very channel it listens to, so
skipping that check would have it react to its own listing message forever.

## What this deliberately does not do

- **Reaction roles.** React to an emoji, get a role, is the obvious shape
  for this and is impossible against slim-m today, on purpose. The wire
  event for a reaction change (`ReactionsChanged`) carries public counts
  only - `ReactionCountDto` in `crates/slimm-server/src/http/ws/frames.rs`
  says in its own doc comment that what a user reacted with is never
  broadcast. Decision 0009 explains why: a reactor someone has blocked must
  not be visible to that someone, so reactor identity is stripped for
  everyone, not just the blocking relationship. There is no reactor id on
  the wire to build reaction roles from, so this bot is command-driven
  instead. This was checked twice already, once statically against the
  frame types and once live against a running deployment - it is a platform
  property, not a missing feature waiting on this example.
- **A store for role grants.** (Role menus do keep a small sqlite file, as above.) A reminder is a promise to act later and
  must survive a restart, or it silently never fires, so `bot-reminders`
  needs its own sqlite file. A role grant needs nothing of its own: it is
  acted on immediately, and if the bot is offline when it arrives, the
  member's cost is typing it again once `Bot`'s own cursor catches the
  command up on reconnect - see `../../docs/framework.md`. The listing
  message's id is derived deterministically from the channel id (a
  UUIDv5), so even that needs nothing bot-specific persisted.
- **Re-granting a role a moderator took away by hand.** This bot only ever
  touches a member's roles in direct response to a `!role`/`!role remove`
  command. There is no background pass that walks members and reconciles
  their roles against some expected state, so a moderator revoking a role
  by hand stays revoked until the member asks for it again themselves.
- **Answering outside `SLIMM_CHANNELS`.** Only one channel is configured on
  purpose; every other channel the bot's role can see is left alone.
- **Editing a pending request, role hierarchies, or approval flows.** This
  is a flat, self-service list. A role that should require approval before
  it is handed out is a different, larger bot.
