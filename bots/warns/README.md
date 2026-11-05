# warns

A slim-m bot (directory `bots/warns`) that keeps a strike history per member and acts when the count reaches a number you pick: a timeout, or removal from the space.

Needs `slim-m>=0.15.0`.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps its state in its own sqlite file (`SLIMM_DB_PATH`, default `warns.db`).

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
python3 bot.py
```

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `!warn @member [reason]` | Kick Members | add a warn; if a rule matches the new count, it runs |
| `!warns [@member]` | anyone for themselves, Kick Members for others | the active warns, newest last, only you see the answer |
| `!unwarn <number>` | Kick Members | remove one warn by its number |
| `!unwarn clear @member` | Kick Members | remove every warn a member has |
| `!warnrule add <count> timeout <duration>` | Manage Server | for example `!warnrule add 3 timeout 1h` |
| `!warnrule add <count> kick` | Manage Server | remove the member from the space at that count |
| `!warnrule remove <count>` / `!warnrule list` | Manage Server / anyone | drop or show the rules |

A rule runs once, when a new warn brings the member's active count to exactly its number.
A timeout needs Kick Members for the bot and a removal needs Ban Members; without it the reply says which one is missing.
There can be 10 rules, and a duration is `30m`, `2h` or `1d` (the server caps a timeout at 28 days).
A reason is cut at 300 characters, and an at-sign in it is defused so it cannot ping anyone.

## Settings

| Variable | Default | What it does |
| --- | --- | --- |
| `WARNS_LOG_CHANNEL` | empty | a channel id; every warn, removal and clear is also posted there |
| `WARNS_EXPIRE_DAYS` | `0` | warns older than this stop counting; `0` keeps them forever |

The bot does not scan messages: it only records what a moderator types.
It pairs with `bots/automod`, which times members out on its own rules but keeps no history.
