# draw

A slim-m bot (directory `bots/draw`, command `!draw`) that finds an openly licensed picture and redraws a simplified version of it on a voice channel's Voice Canvas, as a few dozen stroke objects.
It credits the source in its reply and can take its drawing back in one command.

Needs `slim-m>=0.15.0` and Pillow for reading the downloaded picture; everything else, including the tracer, is plain Python and the standard library.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps the ids of the strokes it placed in its own sqlite file (`SLIMM_DB_PATH`, default `draw.db`) so undo works after a restart.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
DRAW_CHANNEL=<voice channel name or id> \
python3 bot.py
```

The bot needs `VIEW_CHANNEL` and `USE_CANVAS` in that voice channel.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `!draw <query>` | anyone | search, trace and draw; one drawing per member per minute, one at a time overall |
| `!draw undo` | the member who drew it | remove your latest drawing, every stroke of it |
| `!draw clear` | Manage Canvas | remove every drawing the bot made |

The canvas area holds six drawings in a 3 by 2 grid starting at `DRAW_X`, `DRAW_Y`; undo or clear makes room.
A query is at most 60 characters, and an at-sign in it is defused in the reply.

## How a picture becomes strokes

1. The search asks [Openverse](https://openverse.org/) for commercially usable pictures and excludes results it marks mature.
   It needs no key; set `OPENVERSE_TOKEN` to an API token for a higher rate limit.
2. The picture's thumbnail is downloaded (at most 3 MB) and shrunk to fit 96 pixels.
3. The tracer blurs it, cuts it at three brightness levels, finds the outline of each dark area, simplifies the outlines and keeps the longest ones within the stroke budget.
   An outline longer than 120 points is split into pieces that share a joint, so each stroke object stays well under the server's 4 KiB limit.
4. Each outline is one stroke object. The bot places them about 6 a second, below the canvas write limit of 10 a second, and waits and retries if the server says slow down.

The reply names the picture, its creator, its licence and the page it came from.
There is no content scanning, as everywhere in slim-m: the search excludes mature results, the bot is rate limited, and anything on the canvas can be reported and removed like any other mark.

## Settings

| Variable | Default | What it does |
| --- | --- | --- |
| `DRAW_CHANNEL` | the one `SLIMM_CHANNELS` channel | the voice channel whose canvas gets the drawings |
| `DRAW_X`, `DRAW_Y` | `900`, `600` | the top-left of the first drawing in canvas units |
| `DRAW_SIZE` | `480` | the side of the square a drawing fits in |
| `DRAW_MAX_STROKES` | `150` | the most stroke objects one drawing may use, 10 to 300 |
| `DRAW_PACE` | `0.15` | seconds between placements, at least 0.1 |
| `OPENVERSE_TOKEN` | empty | an optional Openverse API token |

Without Pillow installed the bot starts and answers `!draw` with a sentence saying so.
