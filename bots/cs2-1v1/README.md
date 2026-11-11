# cs2-1v1

A slim-m bot (directory `bots/cs2-1v1`, command `!1v1`) that starts a throwaway Counter-Strike 2 1v1 arena server and removes it afterwards.
The server runs the [alerena](https://github.com/ale-devl/alerena) arena plugin on Metamod:Source and CounterStrikeSharp: anyone can join (up to `CS2_MAX_PLAYERS`), the plugin pairs people into 1v1 arenas on a multi-arena workshop map, and the rest wait in the queue.
It is separate from `bots/cs2`, which only reads Steam presence.

Needs `slim-m>=0.13.0` and no library change.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
State lives in Docker itself: every server the bot starts carries labels (owner, channel, map, port), so a restart picks its servers up again.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `!1v1 [map]` | anyone | starts an arena server on that map (a random configured one when omitted; `am_` is optional) and posts `connect HOST:PORT; password PW` once it answers |
| `!1v1 status` | anyone | shows the running server with its map and players/max, and the connect line once it is up (unless `CS2_PRIVATE`) |
| `!1v1 stop` | the starter, or an administrator | stops it now |
| `!1v1 help` | anyone | the short version of this table |

One server runs at a time by default (`CS2_MAX_SERVERS`); a second `!1v1` says who has the running one.
Each server is a casual-mode arena server with a random 10-character password, no cheats and no CS2 bots from the game mode.
Nobody is turned away: the server takes up to `CS2_MAX_PLAYERS` (10 by default) and the plugin handles odd counts, with `alerena-bots` giving whoever has no opponent a bot for that round.

## Arena maps

`CS2_MAPS` defaults to ten workshop multi-arena maps (checked against the Steam Workshop API on 2026-11-10); no competitive map is in the list.

| Name | Workshop id |
| --- | --- |
| `am_redline` | 3139172262 |
| `am_must2` | 3175903838 |
| `am_minecraft` | 3197575080 |
| `am_mirage_mid` | 3272916833 |
| `am_anubis` | 3242420753 |
| `am_forgotten_yard` | 3356301765 |
| `am_dust2` | 3337701091 |
| `am_texture2` | 3132214859 |
| `am_plain2` | 3131339633 |
| `am_blackline` | 3172439876 |

The image downloads a workshop map when the server starts, so the first start of each map takes longer.

## Arena plugins

The image has no Metamod support, so the bot starts the game container with a short bootstrap before the image's own entry script.
The bootstrap writes `installer.py` (passed in the `SLIMM_INSTALLER` env var) into the volume and runs `installer.py ensure`, which downloads Metamod:Source 2.0.0-git1490, CounterStrikeSharp 1.0.379 (with runtime) and alerena 3.0.0-rc.2, checks each against a pinned sha256, and unpacks it under `game/csgo/` in the shared volume.
A package already installed at its pinned hash is not downloaded again, so later starts cost nothing.
A hash mismatch or an archive path that escapes its folder stops the container, so a server never starts with half-installed or tampered plugins.

Metamod loads through a `Game csgo/addons/metamod` line in `game/csgo/gameinfo.gi`, and every CS2 update overwrites that file.
`installer.py ensure` therefore also writes the volume's `pre.sh`, which the image sources after its steamcmd update and before it starts the server; it runs `installer.py patch` to re-add the line.
The patch is idempotent and fails the start when `gameinfo.gi` has no `Game csgo` line, which would mean CS2 changed the file's layout.

To change a plugin version, edit `PACKAGES` in `installer.py` (url and sha256 of the new file); the next start installs it.
CS2 updates can break Metamod or CounterStrikeSharp until they ship a matching release; when arenas stop loading after an update, bump those two pins.
Alerena is a release candidate; [K4-Arenas](https://github.com/KitsuneLab-Development/K4-Arenas) 2.0.8, the project it forked from, is archived.

## When a server stops

- `!1v1 stop` by the starter or an administrator.
- Everyone left: someone connected, and then no human has been on for `CS2_EMPTY_SECONDS` (60 by default).
- Nobody connected within `CS2_IDLE_MINUTES` (10) of the server coming up.
- The server ran `CS2_MAX_MATCH_MINUTES` (90).
- It never came up within `CS2_START_TIMEOUT_MINUTES` (240), or it stopped answering for two minutes.

The bot reads the player count with the A2S query over UDP; bots on the server do not count as players.
It does not read match results, so a server ends when everyone has left, not when a game does.

## First start

The CS2 install is about 75 GB and lives on one shared Docker volume (`cs2-server`), which also holds the arena plugins.
The first server downloads it, which takes a long time; the bot says so when it starts, and posts the download percentage every five minutes.
Later starts only start the container.
Keep `CS2_MAX_SERVERS` at 1 unless you accept two servers sharing one install directory.

## Docker access

The bot never mounts `/var/run/docker.sock`.
It talks to a [docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy) over http (`CS2_DOCKER_HOST`) that allows `CONTAINERS`, `IMAGES` and `POST` only.
Inside that, the bot acts only on containers carrying its own label: it lists with a label filter and re-checks the label, and it inspects a container's labels before every start, stop or log read.
The proxy cannot filter by label, and a client that may create containers could ask for a privileged one, so the proxy is a guard against a bug in the bot, not against someone who controls the bot's token.
The bot always creates the same shape: the pinned image, one UDP port, one named volume, no privileges, memory and CPU limits, `AutoRemove`.

## Settings

| Variable | Default | |
| --- | --- | --- |
| `SLIMM_URL`, `SLIMM_BOT_TOKEN` | | as every bot |
| `CS2_HOST` | required | the public host or IP in the connect line |
| `CS2_DOCKER_NETWORK` | required | the Docker network both the bot and the game servers join; the bot queries servers by container name on it |
| `CS2_DOCKER_HOST` | `http://dockerproxy:2375` | the socket proxy |
| `CS2_PORT` | `27015` | first UDP port; the next server takes the next free one |
| `CS2_GSLT` | none | a Steam game server login token, passed to the image as `SRCDS_TOKEN` |
| `CS2_MAX_PLAYERS` | `10` | player slots, 2 to 64; the arena plugin pairs everyone it can |
| `CS2_MAX_SERVERS` | `1` | |
| `CS2_IDLE_MINUTES` | `10` | |
| `CS2_EMPTY_SECONDS` | `60` | |
| `CS2_MAX_MATCH_MINUTES` | `90` | |
| `CS2_START_TIMEOUT_MINUTES` | `240` | covers the first download |
| `CS2_MAPS` | the ten arena maps above | comma-separated: an arena map name from the table, `name=workshop:<id>`, or `workshop:<id>`; each must be a multi-arena map the plugin can pair on |
| `CS2_CHANNEL` | any | the one channel `!1v1` works in |
| `CS2_PRIVATE` | `false` | keep the password out of the channel: the ready post only pings the starter, and only the starter sees the connect line, privately, from `!1v1 status` |
| `CS2_IMAGE` | pinned `joedwards32/cs2@sha256:...` | the server image |
| `CS2_VOLUME` | `cs2-server` | the shared install volume |
| `CS2_CPUS`, `CS2_MEMORY_MB` | `2`, `6144` | per-server limits |
| `CS2_RULES` | none | extra server arguments (cvars) for the image; trusted operator input. The plugin sets its own round rules, and its in-game `!arenaconfig` covers the rest |

`CS2_MAPS`, `CS2_GSLT` and the generated values are checked at start because the image passes them through a shell.

## Before it works for players outside the LAN

- Forward UDP `CS2_PORT` on the router to the host (and `CS2_PORT+1`... when `CS2_MAX_SERVERS` is above 1).
- Create a game server login token for CS2 (app 730) at steamcommunity.com/dev/managegameservers and set `CS2_GSLT`; a server without one is generally only joinable on the LAN.
- Set `CS2_HOST` to the address players type.

## Tests

```bash
pip install -r requirements.txt
PYTHONPATH=../../slimbots python3 test_bot.py
python3 test_dockerapi.py
python3 test_a2s.py
PYTHONPATH=../../slimbots python3 test_installer.py
```

`compose.snippet.yml` holds the service, socket proxy, network and volume for the fleet compose, which lives outside this repo.
