# feeds

A slim-m bot that follows RSS, Atom, YouTube, Reddit, Mastodon and Forgejo/Gitea release feeds and posts each new item into a channel as an embed.
It generalizes the poll loop in `bots/github-releases` to any number of feeds chosen from chat.

Needs `slim-m>=0.9.3`.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps its state in its own sqlite file (`SLIMM_DB_PATH`, default `feeds.db`).
It uses only the standard library for fetching (`urllib`) and parsing (`xml.etree`), so there is no feed-parser dependency to pin.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
python3 bot.py
```

The bot needs `VIEW_CHANNEL` and `SEND_MESSAGES` in each channel it posts to.
It does not use `SLIMM_CHANNELS`: a feed posts to the channel where it was added.

## Commands

Changing feeds needs Manage Server; `list` is open to everyone.
A refusal is a private reply.

| Command | What it does |
| --- | --- |
| `!feed add <url> [keywords]` | follow a feed in this channel, optionally with a keyword filter |
| `!feed list` | every feed with its channel, interval, filter and, if it keeps failing, the last error |
| `!feed remove <id>` | stop following it and forget what was posted |
| `!feed filter <id> <keywords\|off>` | set or clear the filter |
| `!feed interval <id> <duration>` | how often to check, such as `30m`, `2h` or `1d` |

### What `add` accepts

- RSS 2.0, RSS 1.0 (RDF) and Atom URLs, used as given.
- YouTube: `youtube.com/channel/UC...` becomes the public `feeds/videos.xml?channel_id=` feed.
  A `@handle` URL does not carry the channel id, so it is refused with a hint rather than scraped.
- Reddit: `reddit.com/r/<name>` or `/user/<name>` gets `/.rss`.
- Mastodon: `https://host/@name` gets `.rss`.
- Forgejo, Gitea and GitHub: `https://host/<owner>/<repo>/releases` becomes `releases.rss` (`releases.atom` on github.com).
- Anything else is fetched as is, so any URL that already serves a feed works.
  Autodiscovery from an HTML page is not done.

### Keyword filters

Terms are comma separated and matched case-insensitively against the title and summary.
An item posts when it matches any plain term; a term starting with `-` excludes, so `rust, -sponsored` means "rust, but not sponsored".
Items skipped by a filter are still recorded as seen, so clearing the filter later does not backfill them.

## When it posts

- First add posts only the latest item (if it passes the filter) and records the rest of the feed as seen, so adding a feed confirms it works without flooding the channel.
- After that, new items post oldest first, at most `FEEDS_MAX_POSTS_PER_POLL` (5) per check.
  Anything beyond that cap is marked seen and dropped rather than trickled out later.
- Dedupe is by the entry's guid or id, falling back to its link, then its title, kept in `SLIMM_DB_PATH` (the newest 1000 per feed).
  A restart never reposts, and a send keeps a stable message id as well.
- Each post is an embed with the title, link, a summary of at most 300 characters, the feed's own title as the footer and the entry date.
- A refused post (for example the bot lost `SEND_MESSAGES`) is retried on the next check and the feed shows the error in `list`.

## Talking to feeds

- Conditional GET: the stored `ETag` and `Last-Modified` are sent back, and a 304 counts as a healthy check.
- Each feed has its own interval, default `FEEDS_DEFAULT_INTERVAL` (30m) and never under `FEEDS_MIN_INTERVAL` (10m).
- 20 second timeout, 2 MB body cap, and a body with a DOCTYPE or entity declaration is refused (no entity expansion).
- A bad or empty feed, a timeout, an HTTP error or a body that is not XML is recorded against that feed and never stops the loop.
  After 3 failed checks in a row `list` marks the feed FAILING with the last error, and checks back off up to 6 times the interval.
  It recovers on its own at the first good check; nothing is removed automatically.
- Hosts that do not resolve to a public address are refused, redirects included, so a member cannot point the bot at your LAN.
  Set `FEEDS_ALLOW_PRIVATE=1` to follow a feed on a private network, such as a Forgejo on the same host.
  The check resolves the name once before connecting, so it is a guard against mistakes, not a defence against a hostile DNS server.
- `list` shows only the host of each URL, since a private feed's URL often carries a secret.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `FEEDS_MIN_INTERVAL` | `600` | shortest per-feed interval, seconds (floor 60) |
| `FEEDS_DEFAULT_INTERVAL` | `1800` | interval given to a new feed, seconds |
| `FEEDS_MAX` | `50` | feeds the bot will follow in total |
| `FEEDS_MAX_POSTS_PER_POLL` | `5` | most items posted from one feed per check |
| `FEEDS_TICK_SECONDS` | `60` | how often the loop looks for feeds that are due |
| `FEEDS_ALLOW_PRIVATE` | off | allow feeds on private addresses |

## What it deliberately does not do

- No `@handle` resolution for YouTube, no HTML autodiscovery, no per-feed channel moves (remove and add again).
- No images or media embeds; only text and a link.
- No automatic removal of dead feeds.
- No catch-up of items older than the cap after a long outage.

## Tests

```bash
cd bots/feeds
PYTHONPATH=../../slimbots python3 test_bot.py
PYTHONPATH=../../slimbots python3 test_parse.py
```
