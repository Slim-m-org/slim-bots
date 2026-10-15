# uptime-relay

A slim-m bot that turns monitoring webhooks into alerts in a channel, with Acknowledge and Silence buttons.
It speaks Uptime Kuma, Grafana alerting and Healthchecks.io out of the box, plus a small generic shape anything can post.

Needs `slim-m>=0.13.0`.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps its state in its own sqlite file (`SLIMM_DB_PATH`, default `uptime-relay.db`).
The webhook listener is a few dozen lines of `asyncio`, so there is no web framework to pin.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<alerts channel id> \
UPTIME_RELAY_SECRET=<a long random string> \
python3 bot.py
```

The bot needs `VIEW_CHANNEL` and `SEND_MESSAGES` in the one channel `SLIMM_CHANNELS` names.

## Settings

| Variable | Default | What it does |
| --- | --- | --- |
| `UPTIME_RELAY_SECRET` | required | the token every webhook must carry |
| `UPTIME_RELAY_HOST` | `0.0.0.0` | address the listener binds |
| `UPTIME_RELAY_PORT` | `8790` | port the listener binds |
| `UPTIME_RELAY_SILENCE_MINUTES` | `60` | how long Silence holds, at least 1 |

## Pointing a monitor at it

Each source has its own path, and the secret goes either in a `?token=` query or an `X-Relay-Token` header.
Keep the listener on your LAN or behind your reverse proxy; it is not meant to face the internet bare.

| Source | URL to configure |
| --- | --- |
| Uptime Kuma | Notification type "Webhook", `http://<bot host>:8790/hook/kuma?token=<secret>`, body "Preset - application/json" |
| Grafana | Contact point type "Webhook", `http://<bot host>:8790/hook/grafana?token=<secret>` |
| Healthchecks.io | Integration "Webhook", `http://<bot host>:8790/hook/healthchecks?token=<secret>`, POST with a JSON body like `{"name": "$NAME", "status": "$STATUS"}` for both down and up |
| Anything else | POST `http://<bot host>:8790/hook/generic?token=<secret>` with `{"name": "...", "status": "down" or "up", "message": "...", "url": "..."}` |

The listener answers 204 when it took the alert, 401 for a missing or wrong secret, 400 for a body it cannot read, 404 for an unknown path and 413 above 64 KB.

## What it posts

- A service going down posts one message: `DOWN - <service> (<source>)`, the monitor's message, when it started and the monitor's link, with Acknowledge and Silence buttons.
- More down alerts for the same service edit that message with a count instead of posting again.
- Recovery edits it to `RESOLVED - <service> was down for 25 min.` and clears the buttons.
- Acknowledge records the display name of whoever pressed it on the message and drops its own button.
- Silence holds new posts for that service for `UPTIME_RELAY_SILENCE_MINUTES`, and says who silenced it until when.
  An alert that is already open keeps updating while silenced, so the channel never shows a stale state.
- A service is identified by its source and name, so a Kuma monitor and a Grafana alert with the same name stay separate.
- Webhooks are handled one at a time, so two arriving together for one service count as a repeat rather than two posts.
