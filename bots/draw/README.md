# draw

A slim-m bot (directory `bots/draw`, command `!draw`) that draws on the Voice Canvas of the call you are in.
`!draw <prompt>` has a model look at the canvas, pick free space and draw your prompt with real canvas objects (strokes, shapes, text, notes).
`!draw trace <word>` is the older picture tracer: it finds an openly licensed picture and redraws a simplified version as stroke objects, crediting the source.
Without a model configured, `!draw <word>` does the tracing.

Needs `slim-m>=0.21.0` and Pillow (to read the canvas and downloaded pictures); the model call uses the official `anthropic` package.
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
| `!draw color [subject]` | anyone | the whole picture (attached, else the first picture found, credited) as coloured brushwork: rows of touching strokes, each a run of one colour from a 48-colour palette; no crop, no model, no tokens; one per member per minute |
| `!draw paste [subject]` | anyone | the picture itself as one image object on the canvas (uploaded as an attachment, sized into the free area), credited; no drawing |
| `!draw shaded [subject]` (alias `dots`) | anyone | a tonal squiggle portrait of the image attached to your message, else of the first picture found for the subject (credited), drawn whole with its background, no face crop; no model, no tokens; one per member per minute |
| `!draw trace <word>` | anyone | search, trace and draw a picture; one per member per minute |
| `!draw undo` | the member who drew it | remove your latest drawing in the call you are in, every object of it |
| `!draw clear` | Manage Canvas | remove every drawing the bot made on your call's canvas |

Typing `!draw ` opens a pick list of the modes (`photo`, `shaded`, `trace`, `undo`, `clear`) while anything else you type is still just the idea to draw; the list needs a slimbots with `suggest` and a slim-m with command options, and without them the bot works the same minus the list.
A prompt is at most 200 characters (60 for a trace), the prompt goes only into the user turn of the model call, and mentions in replies are defused.

## How a prompt becomes a drawing

1. The bot reads the live objects in a 1600 by 1000 area of the canvas and renders them as a picture: strokes in their colours, shapes as outlines, text and notes as labelled boxes, image tiles shrunk (the first bytes of each, at most 12).
   A coordinate ruler runs along the top and left, and the largest empty rectangle (up to 1000 by 800) is boxed in green.
   If nothing big enough is free it says so instead of drawing over people's work.
2. The bot also looks for a reference picture (see Picture sources) and sends the best one along as a reference photo for proportions and likeness; if nothing is found it carries on without one, and the reply credits the page it came from when one was used.
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

1. The search finds a picture of the word (see Picture sources).
2. The picture's thumbnail is downloaded (at most 3 MB) and shrunk to fit 96 pixels.
3. The tracer blurs it, cuts it at three brightness levels, finds the outline of each dark area, simplifies the outlines and keeps the longest ones within the stroke budget.
   An outline longer than 120 points is split into pieces that share a joint, so each stroke object stays well under the server's 4 KiB limit.
4. Each outline is one stroke object. The bot places them about 6 a second, below the canvas write limit of 10 a second, and waits and retries if the server says slow down.

The reply names the picture and the page it came from.
There is no content scanning, as everywhere in slim-m: the bot is rate limited, and anything on the canvas can be reported and removed like any other mark.

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
| `DRAW_COLOR_MAX` | `2000` | the most strokes one `!draw color` painting may use, 200 to 4000 |
| `DRAW_SHADED_MAX` | `1500` | the most strokes one `!draw shaded` picture may use, 200 to 4000 (`DRAW_DOTS_MAX` is still read if this is unset) |
| `DRAW_BOARD` | `dark` | `dark` or `light`: which board `photo` and `shaded` draw for (see below) |
| `DRAW_PHOTO_SHADING` | off | `1` adds pen hatching inside the face to `!draw photo` |
| `SERPAPI_KEY` | empty | when set, SerpAPI's Google Images results are tried first |
| `GOOGLE_CSE_KEY`, `GOOGLE_CSE_ID` | empty | when both are set, Google's Programmable Search image results are tried first |
| `DRAW_PHOTO_MAX` | `300` | the most strokes one `!draw photo` drawing may use, 20 to 1500 |
| `DRAW_PACE` | `0.15` | seconds between placements, at least 0.1 |
| `SLIMM_CHANNELS` | unset | optional; a voice call's chat is always listened to |

`DRAW_CHANNEL` is no longer used: the bot draws in the call the member is in, never a fixed channel.
Without Pillow installed the bot starts and answers `!draw` with a sentence saying so.

## Picture sources

`photo`, `shaded` and the AI reference photo find pictures, in this order, reading only as far as needed and trying the next candidate when one fails to download or decode (up to eight tries):

1. SerpAPI's Google Images results, only when `SERPAPI_KEY` is set (below). The first result's original picture is tried first, then its thumbnail if the original does not download.
2. Google Programmable Search image results, only when `GOOGLE_CSE_KEY` and `GOOGLE_CSE_ID` are set.
3. Bing image search, the first result first (its async results page, read for each result's full-size picture and the page it was on). It is asked with the bot's own honest User-Agent; no attempt is made to look like a browser.
4. The lead image of the best Wikipedia article.
5. Wikimedia Commons picture files (a file search in namespace 6).

With no keys the order is Bing, Wikipedia, Commons.

A picture is downloaded only from a public address: a result that points at a private or loopback host, or redirects to one, is skipped.
A picture, or a canvas image the bot looks at, that declares more than 25 megapixels is not decoded.

### SerpAPI

[SerpAPI](https://serpapi.com) answers Google Images searches over a plain API, and its free plan allows about 250 searches a month.
Sign up there, copy the key from the dashboard (the "API key" page), and add `SERPAPI_KEY=<the key>` to the bots' `.env` file, the one the compose file passes to the draw bot, then restart the bot.
Each picture lookup costs one search; the log line carries `serpapi_month=<n>`, the searches this bot has made this calendar month, so you can watch the quota.
The key is sent only to serpapi.com and is masked in anything the bot logs or says; when SerpAPI refuses it or the quota is spent, the lookup falls through to Bing and the log notes `serpapi: ...`.

There is no licence filter; the reply credits the page the picture came from (SerpAPI's `link`, Bing's `purl`, the Wikipedia article or the Commons file page).
Every request has a timeout, an honest User-Agent naming the bot, and a 3 MB size cap, and a source that fails is skipped and noted in the command's log line.
`!draw trace` uses the same sources.

## Whole pictures, sized by detail

No model is involved, so `photo`, `shaded` and `color` work with no API key and spend no tokens (only `!draw <prompt>` uses the key).
None of them zooms on a face or masks a subject: the whole picture is drawn, background included, and the only crop is flat white or black borders of at least 12 percent of a side (`crop.trim_box`).
`photo` still looks for a face (OpenCV's YuNet detector, `face_detection_yunet_2023mar.onnx`, MIT, shipped here with its licence, else the bundled Haar cascade) but only to lower its edge thresholds there; the picture is not cut to it.
The log line carries `crop="trim x,y,w,h"` or `crop="whole ..."`.

`sizing.py` measures detail as the share of pixels on a clear edge in a 256 pixel copy, from 0 (plain) to 1 (busy).
Detail sets how much of the free region the drawing uses (70 to 100 percent of what fits, at most 1600 units wide) and, for `shaded` and `color`, how many rows to start from (40 to 200 for shaded, 48 to 220 for color); rows are then reduced until the strokes fit the budget.

## Line drawings from a photo

`!draw photo` needs no model, and it favours a few clean contours over many rough ones.
It analyses the picture at about 560 pixels with a fairly strong blur, so structure shows and texture (hair strands, skin, fabric) does not, and finds edges with a Sobel filter judged against each neighbourhood's own strength.
Edges are kept by hysteresis, so only weak pixels touching a strong edge survive; inside the face ellipse the thresholds are lower, so eyes, nose and lips come through.
Edges are thinned, linked into long lines, smoothed with a moving average, simplified (Douglas-Peucker) and rounded (one Chaikin pass).
A line must pass a quality bar: at least 4 percent of the image diagonal long (2 percent on the face), not jagged (a short line that doubles back on itself is dropped), not along the crop's border, not a long straight near-vertical or near-horizontal line outside the middle 60 percent (curtain, flag and frame edges), and on the subject ellipse.
Lines are ranked by length times strength times a soft subject weight, so long clean contours win, up to `DRAW_PHOTO_MAX` strokes (default 300).
A line is split into pieces of at most 150 points sharing a joint, each a stroke in the one ink colour at width 2, scaled into the largest free area of your call's canvas with the photo's aspect ratio.
`DRAW_PHOTO_SHADING=1` also hatches the dark features inside the face (`photo_shade.py`); it is off by default because the cross-hatching blacks out the eyes.

## Dark and light boards

The canvas has no background colour a bot can read, so `photo` and `shaded` draw for one board, `DRAW_BOARD` (default `dark`, since most of the app's themes are dark).
On a dark board the ink is light (`#f2f2f2`) and `shaded` puts it where the picture is light, so the photo reads as a positive (the darkest quarter of the picture stays bare and an S-curve and gamma separate the mid tones); on a light board the ink is dark (`#1f1f1f`) and goes where the picture is dark.
A viewer on the opposite theme sees the drawing inverted, a light-ink drawing on a light board for example, and a client that does not render `#rrggbb` ink paints the theme's default ink, which still reads as a drawing.

## Colour paintings

`!draw color` paints the whole picture, with no subject mask and no face crop.
The picture is shrunk to a grid, its colours are reduced to 48 by median cut (after a light median filter so speckle does not split runs), and neighbouring cells of one colour along a row are merged into one stroke, so a flat sky is a few long strokes and a face is many short ones.
Each stroke is a horizontal line with the row's pitch as its width (a little wider, 1.3 times, so rows overlap and the round caps leave no board showing), coloured with its palette colour as `#rrggbb`.
The grid is made as fine as fits under `DRAW_COLOR_MAX` strokes (up to 220 rows), and the strokes go down in batches of 200, so 2000 strokes take seconds when the server has the batch route; a client without hex colour support paints the default ink.

## Pasting a picture

`!draw paste` shrinks the picture to at most 1600 pixels, uploads it as an attachment and places one canvas `image` object (`props.attachment`) sized into the free area with its aspect ratio kept.
It is undone like any drawing, though the uploaded file stays on the server.

## Shaded portraits

`!draw shaded` (and its old name `dots`) draws the picture as wavy scanlines: each row's wave gets taller and tighter where the picture is darker, and the pen lifts where it is light, so tone reads as density.
The whole picture is drawn and the board decides which tones get ink: on a dark board the light tones (the darkest quarter stays bare), on a light board the dark ones, so the background is mapped like everything else.
Tone is stretched over the picture's own range (one with almost no tone range draws nothing), rows start from the detail-based count and drop until they fit the stroke budget, short gaps are bridged so a row is a few strokes, and each stroke is at most 150 points.

### How these were chosen

Several ways of turning a photo into a stroke budget were rendered side by side for an official portrait of Barack Obama, a Taylor Swift photo, the Eiffel Tower and a golden retriever: edge contours, scanline squiggles, stippling joined by a nearest-neighbour path, tone hatching and marching-squares tone contours.
Tone contours read as a mask and stippling fills the frame, so `photo` stays on edges and `shaded` on scanlines.
The algorithms of the plotter tools (linedraw, DrawingBotV3's sketch and squiggle modes, potrace) were studied from their published descriptions and behaviour and reimplemented here; `THIRD_PARTY.md` lists them, their licences and what was taken.

## Replies and the log

A drawing is acknowledged straight away in the channel ("drawing ..., this takes a minute or two") and the result is posted there too, so the call sees what happened; pure errors (no call, missing rights, cooldown, a refusal) stay private to whoever asked.
Every command writes one line to stdout when it ends, for example `draw command=draw channel=<call id> outcome="drew 41" source=wikipedia search=0.9s download=0.4s model=52.3s place=7.2s total=61.0s prompt="barack obama"`.
It has the outcome (drew N, refused with the reason, no call, cooldown, busy, no source, error and its class), the source used and the time per stage, and never a key or more than 60 characters of the prompt.
An unexpected exception is logged with its traceback on a `draw traceback` line, because the framework otherwise swallows a handler's exception.

## Placement speed

When the server has `POST /channels/{id}/canvas/objects/batch`, a drawing goes down in batches of up to 200 objects (one request and one transaction each) instead of one paced request per object, so 600 strokes take seconds instead of a minute.
A 429 backs off and retries; a 404 or 405 on the first batch means an older server, which is remembered for the process and every object is then placed alone at the usual pace.
All objects of one drawing carry the same `props.group`, so the client treats them as one undo step.
`!draw undo` and `clear` remove in ops of 64 ids, three at a time, backing off on 429.
Batch placement needs `slimbots` with `Canvas.place_many`, which is not in a release yet.
