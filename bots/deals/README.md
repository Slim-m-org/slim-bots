# Steam Deals

A slim-m bot (directory `bots/deals`, command `!deals`) that posts free-to-keep Steam games into one channel, and pings a member in that channel when a Steam game they are watching falls to their target price.

Needs `slim-m>=0.15.0`.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps its state in its own sqlite file (`SLIMM_DB_PATH`, default `deals.db`).
No API key is needed anywhere, and it uses only the standard library for fetching (`urllib`) and parsing (`json`).

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
python3 bot.py
```

The bot needs `VIEW_CHANNEL` and `SEND_MESSAGES` in the deals channel, and the watcher must be able to see that channel for the mention to reach them.
It does not use `SLIMM_CHANNELS`: it posts where `!deals setup` was run.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `!deals setup` | Manage Server | make this channel the deals channel; the first check runs within a minute |
| `!deals digest on|off|<hour>` | Manage Server | a daily list of the best Steam discounts in the deals channel, off by default; the hour is UTC (18 unless set) |
| `!deals now` | Manage Server | check free games and watches right away (once a minute at most) |
| `!pricewatch <game name, Steam link or app id> <price>` | anyone | watch a game, for example `!pricewatch portal 2 4.99` or `!pricewatch store.steampowered.com/app/620/ 4.99` |
| `!pricewatch list` | anyone | your own watches with the last price seen |
| `!pricewatch remove <id>` | anyone | stop one of your watches |

A name is looked up with Steam's store search.
One clear hit (the only result, or the only one whose name equals what you typed) is used, and the reply names the game and its app id so a wrong match is easy to spot.
Otherwise the bot posts a short list of buttons that only you can press; a link or an app id skips the search.
If Steam finds nothing, the reply asks for the store link or the app id.

A command that fails unexpectedly answers with one short line only you can see, and the details go to the bot's log.
`!deals now` also says when the deals channel refused a post.

Replies to watch commands, refusals and `!deals now` are private ("only you can see this"); the free-game posts and price pings are public, in the deals channel.
Watching the same game again updates the target and re-arms the ping.
Each member can hold 20 watches.

## Where the data comes from

| What | Source | Notes |
| --- | --- | --- |
| Free games | [GamerPower](https://www.gamerpower.com/api-read) giveaways, `platform=steam&type=game` | Keyless. Includes publisher key giveaways as well as 100% off promotions, since both are free to keep. Epic and GOG are no longer checked. |
| Prices | `store.steampowered.com/api/appdetails` (`filters=basic,price_overview`) | Keyless. Steam allows roughly 200 requests per five minutes; the daily check asks once per distinct game and waits `DEALS_STEAM_DELAY` seconds between games. |
| Daily digest | `store.steampowered.com/api/featuredcategories` (the `specials` list) | Keyless. One request a day when the digest is on. |
| Game search | `store.steampowered.com/api/storesearch/?term=...&cc=...&l=en` | Keyless. Used only by `!pricewatch` with a name. |

GamerPower asks callers to stay under about four requests per second and to link back; the bot makes at most three requests every three hours.
Each offer is posted once, keyed by offer id in sqlite, and its message id is derived from that key, so a crash between the post and the bookkeeping cannot double post.
An offer whose post is refused is not marked seen and is retried on the next run.
A run posts at most 10 offers.

## Daily digest

`!deals digest on` makes the bot post one message a day after the chosen UTC hour: up to 10 of Steam's featured specials, biggest discount first, each as a link with the percentage off and the price now and before.
It mentions no one, and the text is capped well under the message limit.
A game is not featured again for seven days, so a quiet week posts nothing rather than the same list.
The post is keyed by the day, so a restart cannot double post, and a Steam outage or a refused post is retried on the next tick.

## Price watches

Prices are read in the store's minor unit as Steam reports them, which is hundredths of the currency even for yen (1,400 yen arrives as 140000), and the target is stored the same way.
Steam picks the currency from the country code (`DEALS_CC`); there is no separate currency setting.
If the country changes, a watch whose stored currency no longer matches is skipped rather than compared.

A watch mentions its owner in the deals channel when the price is at or below the target, then stays quiet until the price drops further, or rises above the target and falls again.
The mark is only set after the post succeeded, so a refused post retries at the next check.
A game Steam stops serving (removed, or not sold in the country) cannot be told apart from a region lock, so after three missed daily checks in a row the owner gets one notice and the watch stays until they remove it.
A Steam outage leaves watches untouched.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DEALS_CC` | `us` | Steam country code for prices and search |
| `DEALS_FREEBIES_INTERVAL` | `10800` | seconds between free-game checks, at least 900 |
| `DEALS_WATCH_INTERVAL` | `86400` | seconds between price checks, at least 3600 |
| `DEALS_STEAM_DELAY` | `1.5` | seconds between Steam lookups in one price check |
| `DEALS_TICK_SECONDS` | `60` | how often the loop looks at whether a check is due |

Compose entry, following the other bots (the `command` path is hardcoded per bot):

```yaml
  bot-deals:
    image: <the image the other bots use>
    command: ["python", "/bots/deals/bot.py"]
    environment:
      SLIMM_URL: https://your.space
      SLIMM_BOT_TOKEN: ${DEALS_BOT_TOKEN}
      SLIMM_DB_PATH: /data/deals.db
    volumes:
      - deals-data:/data
```

## What it does not do

- No store beyond Steam, and no DMs: pings are public mentions in the deals channel.
- No DLC, demos or in-game item giveaways; only games.
- No claiming: it links to the offer and the member claims it themselves.
- Steam region quirks (a game priced differently per country) are not compared; a watch uses the one configured country.
