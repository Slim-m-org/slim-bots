# cs2

A slim-m bot (directory `bots/cs2`, command `!cs2`) for a group that plays Counter-Strike 2 together.
Members opt in with their Steam id; the bot then says when one of them starts CS2, answers `!cs2 lfg` and shows the group's own game server.
This is the sanctioned place for game status: slim-m core deliberately has no rich presence, and this bot is opt-in and easy to leave.

Needs `slim-m>=0.15.0`.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps its state in its own sqlite file (`SLIMM_DB_PATH`, default `cs2.db`): a member id, the Steam id they gave, and two flags.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
STEAM_API_KEY=... \
CS2_CHANNEL=<channel id for announcements> \
python3 bot.py
```

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `!cs2 link <Steam id, profile link or name>` | anyone | opt in; a profile name needs the API key to resolve |
| `!cs2 forget` | anyone | remove your link and everything kept about you |
| `!cs2 quiet` | anyone | stop (or resume) the "just started CS2" announcement and stay linked for lfg |
| `!cs2 who` | anyone | linked members and whether they are online, in a game, in CS2 or offline (only you see it) |
| `!cs2 lfg [note]` | anyone | if at least `CS2_LFG_MIN` linked members are online and not in a game, ping the role (or them) |
| `!cs2 server` | anyone | players and map of the server in `CS2_SERVER`, read with Steam's A2S query over UDP |

Steam only shows a member's current game when their profile's game details are public.
Nothing is announced for a member who is not linked or who ran `quiet`, and `forget` deletes the row.
Every Steam command says plainly when `STEAM_API_KEY` is missing instead of failing, and the key is never printed, logged or sent to chat.

## Settings

| Variable | Default | What it does |
| --- | --- | --- |
| `STEAM_API_KEY` | empty | a [Steam Web API key](https://steamcommunity.com/dev/apikey); without it the presence features are off |
| `CS2_CHANNEL` | empty | the channel id announcements go to; unset turns announcements off |
| `CS2_ROLE` | empty | a role name to ping for `lfg`, written as `@name`; unset pings the free members themselves |
| `CS2_LFG_MIN` | `3` | how many free linked members `lfg` needs before it pings |
| `CS2_SERVER` | empty | `host:port` of the group's CS2 server |
| `CS2_POLL` | `90` | seconds between presence checks, at least 60 |

Up to 50 members can link.
The match-prediction idea on the casino bot's chips is not here; it belongs in that bot as a command.
