# memories

A slim-m bot (directory `bots/memories`) with two small, friendly commands that look back at the public channels: a quote from this date months or years ago, and a monthly "wrapped".
Neither ranks people: the recap has channel, day and emoji totals and one message, never a per-member count.

Needs `slim-m>=0.15.0`.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps only its own "already posted" markers in a sqlite file (`SLIMM_DB_PATH`, default `memories.db`); it reads history from the server when asked and stores none.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
python3 bot.py
```

The bot needs `VIEW_CHANNEL` in the channels it should read.
Only text channels the server reports as readable by everyone are used; a private channel is never read.
If your server does not say which channels are public, list them in `MEMORIES_CHANNELS`.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `!onthisday` | anyone | quote one message from this date 1 year ago, else 6, 3 or 1 months ago (the ages are a setting) |
| `!recap [YYYY-MM]` | anyone | last month, or the month you name: message count, busiest channel and day, most used reactions, the most reacted message |

A message is quoted only if it is 20 to 280 characters, has no link, is not from a bot and is not a command.
The pick is the same all day, so asking twice shows the same message.
An at-sign in a quote is defused so it cannot ping anyone.
Both commands read history page by page with a limit, so a very busy server may see a partial recap; `!onthisday` and `!recap` each have a cooldown for the whole deployment.

## Settings

| Variable | Default | What it does |
| --- | --- | --- |
| `MEMORIES_CHANNEL` | empty | a channel id to post a daily memory in; unset turns the daily post off |
| `MEMORIES_HOUR` | `15` | the UTC hour after which the daily post goes out |
| `MEMORIES_MONTHLY` | `false` | also post last month's recap on the 1st |
| `MEMORIES_AGES` | `1y,6m,3m,1m` | how far back to look, in order |
| `MEMORIES_CHANNELS` | empty | channel ids or names to treat as public |
| `MEMORIES_QUOTE_PAGES`, `MEMORIES_RECAP_PAGES` | `40`, `150` | the most pages of 100 messages read per channel |
