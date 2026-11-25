# homelab

A slim-m bot (directory `bots/homelab`, command `!homelab`) that keeps a few status tiles on a voice or canvas channel's canvas: Proxmox nodes and guests, Docker containers, and what Jellyfin is playing.
Each tile is one canvas note that is edited in place every interval, never placed again, so the canvas stays at three notes.

Needs `slim-m>=0.21.0`.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps the id of each note in its own sqlite file (`SLIMM_DB_PATH`, default `homelab.db`).
It reads Proxmox and Jellyfin through the library's `JsonService`, which follows a redirect only within the same host, so a token is never sent anywhere else.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
HOMELAB_CHANNEL=<voice channel name or id> \
HOMELAB_PROXMOX_URL=https://pve.lan:8006 \
HOMELAB_PROXMOX_TOKEN='user@pam!tokenid=secret' \
python3 bot.py
```

It also works on a canvas channel; a voice channel whose canvas is off is refused at start with a message naming the setting.
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
| `HOMELAB_ALLOW_HTTP` | off | `1` allows a plaintext `http` Proxmox or Jellyfin URL on a trusted LAN; loopback and a bare Docker service name never need it |
| `HOMELAB_DOCKER_SOCKET` | empty | a filtering socket proxy, as its `http://` URL (`http://homelab-docker-proxy:2375`) or the path of the unix socket it serves; a raw Docker socket path also works but see Docker access below |
| `HOMELAB_JELLYFIN_URL`, `HOMELAB_JELLYFIN_KEY` | empty | the server address and an API key |

Tokens and keys are read from the environment and are never printed, logged or sent to chat.

## Docker access

The bot sends one request to Docker: `GET /containers/json?all=1`.
Mounting `/var/run/docker.sock` into its container, even with `:ro`, gives it the whole Docker API: read-only applies to the file, not to the requests sent over the socket.
Anything that runs in the bot's container, such as a compromised dependency, could then create a privileged container and take over the host.
Put a filtering socket proxy in front of Docker that allows only `GET /containers/json`, and point `HOMELAB_DOCKER_SOCKET` at the proxy instead of the socket.
[wollomatic/socket-proxy](https://github.com/wollomatic/socket-proxy) can express exactly that, with `-allowGET=(/v[0-9.]+)?/containers/json`, `-listenip=0.0.0.0` and `-allowfrom=` the bot's service name.
Run it on a network only the proxy and the bot join, and pin its image by digest.
Tecnativa's docker-socket-proxy with `CONTAINERS=1` is not enough here: it also lets through `/containers/{id}/json`, which returns every container's environment, and `/containers/{id}/archive`, which returns its files.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `!homelab` | anyone | which tiles are drawn, where (named only when you can see that channel), and the last problem per source (only you see the answer) |
| `!homelab redraw` | Manage Server | remove the tiles and draw them again |

A source that cannot be reached shows `unreachable` on its tile, and the tile recovers on the next good poll; the bot keeps running.
A tile someone deletes is drawn again at the next poll.
