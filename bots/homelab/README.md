# homelab

A slim-m bot (directory `bots/homelab`, command `!homelab`) that keeps a few status tiles on a voice channel's Voice Canvas: Proxmox nodes and guests, Docker containers, and what Jellyfin is playing.
Each tile is one canvas note that is edited in place every interval, never placed again, so the canvas stays at three notes.

Needs `slim-m>=0.15.0`.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps the id of each note in its own sqlite file (`SLIMM_DB_PATH`, default `homelab.db`).
It uses only the standard library to talk to your services.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
HOMELAB_CHANNEL=<voice channel name or id> \
HOMELAB_PROXMOX_URL=https://pve.lan:8006 \
HOMELAB_PROXMOX_TOKEN='user@pam!tokenid=secret' \
python3 bot.py
```

The bot needs `VIEW_CHANNEL` and `USE_CANVAS` in that voice channel, and `SLIMM_CHANNELS` should include it so a removed tile is noticed at once.
A source with no settings is simply not drawn; with none set the bot says what to set when you type `!homelab`.

## Settings

| Variable | Default | What it does |
| --- | --- | --- |
| `HOMELAB_CHANNEL` | the one `SLIMM_CHANNELS` channel | the voice channel whose canvas shows the tiles |
| `HOMELAB_INTERVAL` | `30` | seconds between polls, at least 10 |
| `HOMELAB_X`, `HOMELAB_Y` | `400`, `0` | the top-left of the first tile in canvas units; tiles sit side by side from there |
| `HOMELAB_PROXMOX_URL`, `HOMELAB_PROXMOX_TOKEN` | empty | the Proxmox API root and an API token as `user@realm!tokenid=secret`; a read-only role is enough |
| `HOMELAB_PROXMOX_CA_FILE` | unset | path to the node's `pve-root-ca.pem`, for its self-signed certificate |
| `HOMELAB_DOCKER_SOCKET` | empty | the Docker socket path, for example `/var/run/docker.sock`, mounted read-only into the bot's container |
| `HOMELAB_JELLYFIN_URL`, `HOMELAB_JELLYFIN_KEY` | empty | the server address and an API key |

Tokens and keys are read from the environment and are never printed, logged or sent to chat.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `!homelab` | anyone | which tiles are drawn, where, and the last problem per source (only you see the answer) |
| `!homelab redraw` | Manage Server | remove the tiles and draw them again |

A source that cannot be reached shows `unreachable` on its tile, and the tile recovers on the next good poll; the bot keeps running.
A tile someone deletes is drawn again at the next poll.
