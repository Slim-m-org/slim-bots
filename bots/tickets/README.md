# tickets

A slim-m bot for support tickets: a button opens a private channel for one member and your staff, staff claim and close it, and a plain-text transcript is kept in the bot's own sqlite.

Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It needs `slim-m>=0.13.0`.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
TICKETS_STAFF_ROLE=Support \
python3 bots/tickets/bot.py
```

Then run `!ticket panel` in the channel where members should find the button.

## How a ticket stays private

slim-m threads are not private: a thread inherits who can see its parent channel, so it cannot hide a conversation from the rest of the parent's members.
A channel can be private, so each ticket is a channel, not a thread:

1. `POST /channels` with `restricted: true`, which denies `@everyone` the right to view it from the first instant.
2. `PUT /channels/{id}/overwrites` grants view, send, attach and react to the opener and to the staff role only.

The bot needs Manage Channels and Manage Roles, and it can only grant bits it holds itself.
Anyone with Administrator can still see every ticket, because Administrator bypasses overwrites.
If either call fails the bot deletes the half-made channel, frees the member's slot, and says so.

## Settings

- `TICKETS_STAFF_ROLE` (required): the staff role, by name or id. Members with this role, or Manage Server, can see tickets, claim, close and read transcripts. With it unset the Open button answers that tickets are not set up.
- `TICKETS_CATEGORY`: a category name or id to file tickets under. Empty, or a name that matches nothing, files them uncategorised.
- `TICKETS_MAX_TRANSCRIPT`: characters kept per transcript (default 100000). A longer ticket is cut and the transcript says so.
- `TICKETS_MAX_REASON`: longest reason text (default 200).

## Commands and buttons

- `!ticket panel` posts the **Open a ticket** button. Manage Server only; anyone else gets a private refusal.
- **Open a ticket** makes `ticket-0001`, `ticket-0002`, ... and answers the presser privately. One open ticket per member: a second press says which one they already have.
- `!ticket reason <text>`, inside a ticket, from its opener or staff. It is stored and set as the channel topic.
- **Claim**, staff only. It takes the ticket for the presser, greys the button out, and a second claim is refused.
- **Close**, staff only. It reads the whole channel history, saves the transcript, then deletes the channel. If the history cannot be read, the ticket stays open.
- `!ticket transcript <number> [page]`, staff only, answered privately in pages of about 1800 characters.

A transcript is one line per message, oldest first: `[2026-01-02 03:04:05 UTC] Display Name: text`.

## What this deliberately does not do

- **Use threads.** They cannot be made private here, see above.
- **Keep closed channels.** Close deletes the channel (a soft delete on the server); the transcript in sqlite is the record. The ephemeral confirmation to the person who pressed Close may not be shown once its channel is gone.
- **Save attachments.** A transcript has message text only; an attachment is not copied.
- **Let the opener close their own ticket.** Close is staff only, as asked.
- **Resume a half-made ticket after a crash.** A restart drops unfinished reservations, and puts a ticket that was mid-close back to open.
- **Post a transcript to a log channel.** It stays in sqlite and is read with the command.
