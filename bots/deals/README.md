# deals

A slim-m bot that posts free-to-keep games from the Epic Games Store, Steam and GOG into one channel, and pings a member in that channel when a Steam game they are watching falls to their target price.

Needs `slim-m>=0.9.3`.
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
| `!deals sources <list>` | Manage Server | choose among `epic`, `steam`, `gog`, comma separated |
| `!deals now` | Manage Server | check free games and watches right away (once a minute at most) |
| `!pricewatch <steam link or app id> <price>` | anyone | watch a game, for example `!pricewatch store.steampowered.com/app/620/ 4.99` |
| `!pricewatch list` | anyone | your own watches with the last price seen |
| `!pricewatch remove <id>` | anyone | stop one of your watches |

Replies to watch commands, refusals and `!deals now` are private ("only you can see this"); the free-game posts and price pings are public, in the deals channel.
Watching the same game again updates the target and re-arms the ping.
Each member can hold 20 watches.

## Where the data comes from

| Store | Source | Notes |
| --- | --- | --- |
| Epic | Epic's own `freeGamesPromotions` endpoint, used by the store's front page | A game counts only while its price is 0 and a promotion is live; upcoming weeks are ignored. Epic writes a free game as `discountPercentage: 0`. The country follows `DEALS_CC`. |
| Steam | [GamerPower](https://www.gamerpower.com/api-read) giveaways, `platform=steam&type=game` | Keyless. Includes publisher key giveaways as well as 100% off promotions, since both are free to keep. |
| GOG | GamerPower, `platform=gog&type=game` | Keyless. GOG has no public giveaway API of its own, so this depends on GamerPower noticing the giveaway. It answered "none active" when the fixtures were recorded, so `fixtures/gamerpower_gog.json` is the recorded Epic answer reshaped for GOG, not a real GOG capture. |
| Steam prices | `store.steampowered.com/api/appdetails` (`filters=basic,price_overview`) | Keyless. Steam allows roughly 200 requests per five minutes; the daily check asks once per distinct game and waits `DEALS_STEAM_DELAY` seconds between games. |

GamerPower asks callers to stay under about four requests per second and to link back; the bot makes at most three requests every three hours.
Each offer is posted once, keyed by store and offer id in sqlite, and its message id is derived from that key, so a crash between the post and the bookkeeping cannot double post.
An offer whose post is refused is not marked seen and is retried on the next run.
A run posts at most 10 offers.

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
| `DEALS_SOURCES` | `epic,steam,gog` | sources used until `!deals sources` changes them |
| `DEALS_CC` | `us` | Steam country code for prices, and Epic's country |
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

- No stores beyond Steam for price watches, and no DMs: pings are public mentions in the deals channel.
- No DLC, demos or in-game item giveaways; only games.
- No claiming: it links to the offer and the member claims it themselves.
- Steam region quirks (a game priced differently per country) are not compared; a watch uses the one configured country.
