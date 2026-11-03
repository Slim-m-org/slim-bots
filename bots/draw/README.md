# draw

A slim-m bot (directory `bots/draw`, command `!draw`) that draws on the Voice Canvas of the call you are in.
`!draw <prompt>` has a model look at the canvas, pick free space and draw your prompt with real canvas objects (strokes, shapes, text, notes).
`!draw trace <word>` is the older picture tracer: it finds an openly licensed picture and redraws a simplified version as stroke objects, crediting the source.
Without a model configured, `!draw <word>` does the tracing.

Needs `slim-m>=0.15.0` and Pillow (to read the canvas and downloaded pictures); the model call uses the official `anthropic` package.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps the ids of the objects it placed in its own sqlite file (`SLIMM_DB_PATH`, default `draw.db`) so undo works after a restart.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
ANTHROPIC_API_KEY=sk-ant-... \
python3 bot.py
```

The bot acts where you are: it finds the voice call you are in and draws on that call's canvas, wherever you typed the command (a voice call's own chat works).
It needs `VIEW_CHANNEL` and `USE_CANVAS` there, and says exactly which one is missing when it lacks one.
If you are in no call it asks you to join one.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `!draw <prompt>` | anyone | the model draws your prompt in free space on your call's canvas; one drawing per member per two minutes, one at a time per call |
| `!draw photo [subject]` | anyone | a line drawing traced from a photo (the attached image, else the best freely licensed picture of the subject, credited); no model, no tokens; one per member per minute |
| `!draw dots [subject]` | anyone | a halftone dot portrait of the image attached to your message, else of the best openly licensed picture of the subject (credited); no model, no tokens, but hundreds of small objects; one per member per minute |
| `!draw trace <word>` | anyone | search, trace and draw a picture; one per member per minute |
| `!draw undo` | the member who drew it | remove your latest drawing in the call you are in, every object of it |
| `!draw clear` | Manage Canvas | remove every drawing the bot made on your call's canvas |

A prompt is at most 200 characters (60 for a trace), the prompt goes only into the user turn of the model call, and mentions in replies are defused.

## How a prompt becomes a drawing

1. The bot reads the live objects in a 1600 by 1000 area of the canvas and renders them as a picture: strokes in their colours, shapes as outlines, text and notes as labelled boxes, image tiles shrunk (the first bytes of each, at most 12).
   A coordinate ruler runs along the top and left, and the largest empty rectangle (up to 1000 by 800) is boxed in green.
   If nothing big enough is free it says so instead of drawing over people's work.
2. The bot also looks for a reference picture: the lead image of the best matching Wikipedia article when its file is freely licensed on Wikimedia Commons (CC or public domain; fair-use files are skipped), else the best Openverse result (openly licensed, mature results excluded). It sends the best thumbnail along as a reference photo for proportions and likeness; if nothing is found it carries on without one, and the reply credits the source (title, creator, licence) when one was used.
   The model gets the board picture, the free region and four tools: `draw_stroke`, `draw_shape`, `add_text` and `add_note`, which map to the canvas object kinds `stroke`, `shape`, `text` and `note`.
3. Every tool call is checked before it touches the canvas: inside the free region, up to 400 points per stroke (a long contour is split into pieces that share a joint), a colour from the pen palette, every piece under 4 KiB, text boxes sized from their content, at most `DRAW_MAX_STROKES` objects in all.
   A bad call is skipped and counted in the reply.
   `draw_stroke` and `draw_shape` take `guide: true` for construction and proportion lines: they show faintly in the model's own view but are never placed on the canvas and do not count against the object cap.
4. After each round the planned objects are drawn onto a picture of the free region (about 768 pixels on its long side, with a 100-unit grid) and sent back in the tool result, along with which calls were refused and why, so the model can see and fix its own drawing.
5. Objects are placed about 6 a second, below the canvas write limit of 10 a second, with a backoff when the server says slow down, and their ids are stored for `undo`.

The prompt asks for line art in one ink colour, medium contours and thin detail, sparse hatching instead of filled hair, and a check of each round's picture against the reference, fixing the largest proportion error first.

A model that declines, errors or draws nothing gets a plain sentence back; the API key is never logged or echoed.

## Cost

Each `!draw <prompt>` is a short conversation of up to 12 rounds: the model plans (extended thinking, `DRAW_AI_THINKING_BUDGET` tokens), draws, and after each round gets an image of its own drawing so far to correct proportions.
Every round carries the images, and `DRAW_AI_MAX_TOKENS` (default 16000) bounds each reply, so a drawing costs noticeably more than a single call; watch `DRAW_AI_DAILY_CAP`.
The default model is `claude-sonnet-4-6`, so a drawing is one image in and up to a few thousand tokens out; check current pricing before opening it to a large community.
`DRAW_AI_DAILY_CAP` (default 100) bounds the calls per day and `DRAW_AI_COOLDOWN` (default 120 seconds) bounds one member.

## A local model instead

Set `DRAW_LLM_URL` to an OpenAI-compatible base URL (for example `http://host:11434/v1`) and `DRAW_LLM_MODEL` to a vision model that supports tool calls.
No API key is needed; `DRAW_LLM_KEY` is sent as a bearer token if the endpoint wants one.
It is used instead of Anthropic whenever set.
Local models draw noticeably worse: expect rougher shapes and more skipped calls.

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
| `ANTHROPIC_API_KEY` | empty | turns on model drawing; without it `!draw <word>` traces |
| `DRAW_MODEL` | `claude-sonnet-4-6` | the Anthropic model id |
| `DRAW_AI_MAX_TOKENS` | `16000` | output tokens per model reply (thinking included), 1536 to 32000 |
| `DRAW_AI_THINKING_BUDGET` | `2048` | tokens the model may think before drawing, at least 1024; `0` turns thinking off |
| `DRAW_AI_DAILY_CAP` | `100` | model calls per day |
| `DRAW_AI_TIME_CAP` | `180` | seconds before an AI drawing stops asking the model and places what it has, at least 30 |
| `DRAW_AI_REQUEST_TIMEOUT` | `90` | seconds one model request may take, at least 10 |
| `DRAW_AI_COOLDOWN` | `120` | seconds between one member's drawings |
| `DRAW_LLM_URL`, `DRAW_LLM_MODEL`, `DRAW_LLM_KEY` | empty | an OpenAI-compatible endpoint used instead of Anthropic |
| `DRAW_VIEW_X`, `DRAW_VIEW_Y` | `0`, `0` | the middle of the canvas area the model looks at |
| `DRAW_X`, `DRAW_Y` | `900`, `600` | the top-left of the first traced drawing in canvas units |
| `DRAW_SIZE` | `480` | the side of the square a traced drawing fits in |
| `DRAW_MAX_STROKES` | `150` | the most objects one drawing may use, 10 to 300 |
| `DRAW_DOTS_MAX` | `900` | the most dots one `!draw dots` portrait may use, 50 to 1500 |
| `DRAW_PHOTO_MAX` | `220` | the most strokes one `!draw photo` drawing may use, 20 to 400 |
| `DRAW_PACE` | `0.15` | seconds between placements, at least 0.1 |
| `OPENVERSE_TOKEN` | empty | an optional Openverse API token |
| `SLIMM_CHANNELS` | unset | optional; a voice call's chat is always listened to |

`DRAW_CHANNEL` is no longer used: the bot draws in the call the member is in, never a fixed channel.
Without Pillow installed the bot starts and answers `!draw` with a sentence saying so.

## Line drawings from a photo

`!draw photo` needs no model.
It blurs the picture lightly at about 560 pixels, finds edges with a Sobel filter judged against each neighbourhood's own strength (so a faint brow in a quiet area still counts), thins them to one pixel, follows them into lines, smooths and simplifies each line (Douglas-Peucker), drops short fragments and keeps the longest, strongest and most central lines up to `DRAW_PHOTO_MAX` strokes.
A line is split into pieces of at most 150 points that share a joint, each is a stroke in the one ink colour at width 2, and the drawing is scaled into the largest free area of your call's canvas with the photo's aspect ratio.
A clean, close portrait or a landmark photo works best; a busy wide shot traces its background too.

## Dot portraits

`!draw dots` needs no model.
It reads the picture, averages it into a grid, treats cells close to the photo's border colour as background and skips them, and places one small closed stroke per remaining cell with its radius following how far the cell is from the background and its colour set to the cell's own `#rrggbb` hex.
The grid is narrowed until the dots fit under `DRAW_DOTS_MAX`, the picture is fitted into the largest free area of your call's canvas, and placement uses the usual pacing and backoff, so 600 dots take a couple of minutes.
A client without hex colour support paints the dots in the default ink, which still reads as a monochrome halftone.
`!draw undo` and `!draw clear` remove a dot portrait like any other drawing.

## Replies and the log

A drawing is acknowledged straight away in the channel ("drawing ..., this takes a minute or two") and the result is posted there too, so the call sees what happened; pure errors (no call, missing rights, cooldown, a refusal) stay private to whoever asked.
Every command writes one line to stdout when it ends, for example `draw command=draw channel=<call id> outcome="drew 41" source=wikipedia search=0.9s download=0.4s model=52.3s place=7.2s total=61.0s prompt="barack obama"`.
It has the outcome (drew N, refused with the reason, no call, cooldown, busy, no source, error and its class), the source used and the time per stage, and never a key or more than 60 characters of the prompt.
An unexpected exception is logged with its traceback on a `draw traceback` line, because the framework otherwise swallows a handler's exception.
