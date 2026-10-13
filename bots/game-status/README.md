# game-status

A slim-m bot that shows whether game servers are up, with player counts, in one status message per channel that it edits in place.
It generalizes the `minecraft` and `pelican` bots: it asks each game server directly, so it needs no panel and no log access.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
GAMESTATUS_SERVERS='Valheim=a2s:valheim.lan:2457,Survival=minecraft:mc.lan:25565,Factorio=tcp:factorio.lan:34197' \
GAMESTATUS_CHANNELS=<channel-uuid> \
python3 bot.py
```

## What it queries

| Kind | Protocol | Gives |
| --- | --- | --- |
| `a2s` | Steam A2S_INFO over UDP, including the challenge round trip. Covers Valheim, CS2 and most Source and Steam games. | up/down, players/max, map, game name |
| `minecraft` | Java Server List Ping over TCP (VarInt framed handshake and status request). | up/down, players/max, version and MOTD |
| `tcp` | A plain TCP connect. | up/down only |

Factorio has no simple public query protocol (its RCON needs a password and gives no player list in a form worth parsing), so it uses `tcp`: up means the game port accepts a connection.
Note that Valheim's A2S port is the game port plus one (2457 for the default 2456), and that some games leave A2S off by default.
Bedrock Minecraft uses a different UDP protocol and is not covered; use `tcp` only if the port is TCP.

Every query runs under `GAMESTATUS_TIMEOUT` and all servers are asked at once, so a dead host shows as down without delaying the others.
Only the standard library is used (asyncio sockets); no dependency was added.

## Commands

- `!gamestatus` - anyone: a live check, answered privately (public if the server cannot do private replies).
- `!gameserver add <a2s|minecraft|tcp> <host> <port> <name>` - add or replace a server. Manage Server only.
- `!gameserver remove <name>`, `!gameserver list` - Manage Server only.
- `!gameserver board` - Manage Server only: post the live status message in this channel.
  `!gameserver unboard` stops updating it.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GAMESTATUS_SERVERS` | none | Comma-separated `name=kind:host:port` entries, added to the stored list at start. |
| `GAMESTATUS_CHANNELS` | none | Channel ids that get a live status message. `!gameserver board` adds more. |
| `GAMESTATUS_INTERVAL` | `60` | Seconds between polls. |
| `GAMESTATUS_MIN_INTERVAL` | `15` | The floor `GAMESTATUS_INTERVAL` is clamped to, never below 5. |
| `GAMESTATUS_TIMEOUT` | `5` | Seconds each query may take before the server counts as down. |
| `SLIMM_DB_PATH` | `game-status.db` | Where the server list and status message ids are kept. |

At most 25 servers can be added (the cap the on-demand embed needs).
A config entry that already exists by name is overwritten at start; one removed with `!gameserver remove` comes back on restart while it is still in `GAMESTATUS_SERVERS`.

## The status message

The board has one line per server (`[up]` or `[down]`, then players, map, version or the reason it did not answer) and a last-checked line.
Its message id is stored per channel, so a restart keeps editing the same message, and a deleted message is posted again.
The board is plain markdown text, because the server's edit route changes only `content`; the embed is used only for the one-off `!gamestatus` reply.
A channel the bot cannot write to is logged and skipped, and an exception in one poll cycle never stops the loop.
Only a revoked bot token ends the bot.

## What it deliberately does not do

- **Announce transitions.** The message shows state; it never posts when a server goes down or comes back.
- **Player names.** Only counts. A2S player lists and Minecraft sample names are not read.
- **Query unsafe hosts for members.** Only Manage Server can add a host, but the bot does not block private addresses, since a LAN game server is the normal case.
- **Debounce.** One missed poll shows as down.
