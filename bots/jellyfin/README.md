# jellyfin

A slim-m bot that watches a Jellyfin server and posts to a channel when
something new is added (a movie, a batch of episodes, an album), answers
`!jellyfin search <query>` / `!jellyfin recent [days]` / `!jellyfin help`,
and can join a voice channel to run a watch party with `!watch <title>`.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
JELLYFIN_URL=https://your.jellyfin \
JELLYFIN_API_KEY=... \
python3 bot.py
```

Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
`Bot` owns `SLIMM_URL`, `SLIMM_BOT_TOKEN`, `SLIMM_CHANNELS` and `SLIMM_DB_PATH` (where this bot's own tables live); every `JELLYFIN_*` variable is read through `bot.setting()`.
A missing `JELLYFIN_URL` or `JELLYFIN_API_KEY` is reported in the same start-up error as a missing `SLIMM_URL`.
The Jellyfin side is plain HTTP/JSON via `urllib`, run off the event loop with `asyncio.to_thread`.
It is called at most once per `JELLYFIN_POLL_SECONDS` and cooldown-guarded from a command, so it does not need `httpx`.

The bot always holds a websocket connection.
`SLIMM_CHANNELS` is both where new-item posts land by default and where `!jellyfin` is answered.
A deployment that only wants the new-item posts can simply not grant the bot `SEND_MESSAGES` anywhere commands would be answered.

`SLIMM_CHANNELS` should name exactly one channel here - `bot.channel` is
that channel. It needs `SEND_MESSAGES` and `ATTACH_FILES` there; see "What
your bot may do" in `docs/bots/building-bots.md` for granting a bot a
channel overwrite.

The bot also reads every voice channel's chat, so `!watch` works typed into the call you are in; it still needs `VIEW_CHANNEL` and `SEND_MESSAGES` in that voice channel.
To keep it apart from other bots in a shared channel, set `SLIMM_PREFIX` (for example `jf!`) or address it as `@<its username> watch ...`.

`JELLYFIN_API_KEY` comes from Jellyfin's own Dashboard -> API Keys, as a
Jellyfin server administrator. It is a credential exactly like a slim-m bot
token: keep it in the environment, never in source, and this bot never logs
it or puts it in a URL. It goes in an `Authorization: MediaBrowser
Token="..."` header - confirmed against a real Jellyfin 12.0.0 server that
this is the only one of four commonly documented forms that works there:
`X-Emby-Token`, `X-MediaBrowser-Token` and an `api_key` query parameter all
answered `401` on this version, the `Authorization` header alone answered
`200`. Whether an older Jellyfin version accepts a different header is not
tested here - there was only the one live server to test against.

## Commands

- `!jellyfin search <query>` - hits Jellyfin's own search directly, so it
  can answer for something added before this bot ever ran, not just what
  it happened to post.
- `!jellyfin recent [days]` (default 7, max 30) - reuses the same
  `DateCreated` paging the poll loop uses, against a wall-clock cutoff
  instead of the stored cursor.
- `!jellyfin link <jellyfin username>` - maps you to that jellyfin account (the admin key lists users; disabled ones are refused).
  Resume position, "continue watching" (`!watch` with no title) and the season and episode lists then use your account, and
  progress from a stream you started is written to it. Everyone else keeps using the shared account. The answer is private.
- `!jellyfin unlink` - removes the mapping. `!jellyfin account` - shows which account you are on, privately.
- `!jellyfin help` (or any unrecognised `!jellyfin ...`) - the list.

The mapping lives in the bot's own sqlite (`user_links`, added with `CREATE TABLE IF NOT EXISTS`, so an existing database picks it up on start).
One jellyfin user can be linked to one member at a time. There is no password check, so a link is a claim: anyone can link to an
account nobody else has claimed yet. That is fine for a home server and worth knowing before pointing this at a larger one.
Progress goes to the account of whoever started the stream, not to everyone watching it.

Both real commands share one `cooldown=` on the command itself, and their
input is bounded (`require_len`/`require_int`) before it ever reaches a
Jellyfin request.

## Watch party

`!watch <title>` can be typed in any text channel this bot listens on - it
searches Jellyfin the same way `!jellyfin search` does, finds *the
invoker's own* voice channel with `bot.voice.find_member()` (`slimbots`;
see `docs/framework.md`), and joins that one through `bot.voice.join()`
to stream the result in as a screen share. The bot needs `CONNECT` and
`SPEAK` in the invoker's channel, the same grants a human sharing their
screen needs, and says which one is missing if either is absent. Several
matches post up to five buttons, and a series leads to season buttons and then
episode buttons (ten to a page, with Back, Previous and Next page). Only the
person who typed `!watch` can choose; anyone else gets a private reply saying so.
The chooser closes itself after five minutes. One watch party runs per voice channel, so the one bot can
stream a different title in each call it is in (a second `!watch` in a call
that already has one is refused, since two shares from one participant would
read as a single share). The control commands
(`!pause`/`!resume`/`!seek`/`!np`/`!subs`/`!quality`/`!stop`) work from any
channel this bot listens on and act on the call the person who typed them
is in. Someone in no call reaches the party if only one is running, and is
asked to join the call they mean if several are.

Their answers ("paused.", "subtitles off.", a refusal) go only to the person
who typed the command, so a run of commands leaves nothing for the room to
scroll past; the room sees the change on the panel and in the call. Once a
control command works the bot also deletes it, which needs `MANAGE_MESSAGES`
in that channel and is skipped without it. A refused command stays, so its
author can see what they typed. `!watch`'s own errors are private too, but
the `!watch` line stays, since the panel and the chooser reply to it. A
private answer the server will not deliver (past its 15-minute window) is
posted publicly instead, so an error is never lost.

- `!watch <title>` - find the invoker's voice channel, join it, and start
  playing; confirms with "streaming **title** into #channel-name". If
  jellyfin already has a saved position for it (past 30 seconds, not in the
  last minute) the bot asks first with Resume and Start over buttons. With no title, `!watch` offers the account's most
  recent unfinished item the same way (`/UserItems/Resume`).
- The now-playing panel: `!watch` answers with one message that carries buttons and edits itself as the stream changes - The status is one short line, and stopping edits it to "ended" rather than posting another.
  Pause/Play, -30s, +30s, Stop, then a second row of 480p, 720p, 1080p (the current one is greyed), Subtitles (highlighted when on)
  and, for an episode, Next episode. Every button is disabled once the stream ends. Anyone who is in the call can press them;
  someone outside it gets a private "join #call to use these controls" and nothing changes. The text commands still work and
  redraw the panel too. The panel shows the position only while paused (a stopped clock in a message would be wrong within a second); `!np` has the live one.
- Call dock: while the bot is in the call the dock shows Play or pause, Back 30s, Forward 30s and Stop (`@bot.call_control`, slimbots 0.7.0). They run the same code as the panel's buttons, for the party in the call the member used them in.
- Watch position: while a party runs the bot states it to the server (`PUT /channels/{id}/watch-session`) on start, pause, resume, seek and a new title, and ticks it every 5 seconds, paused or not, so a member who joins mid-film sees where the room is. A seek sets `seeked`, stopping ends the session, and the bot needs `slim-m>=0.9.4` and a server that has the watch-session routes (0.77.0). A conflict (another bot holds the call) is logged and the party carries on unsynced. The panel is unaffected. Call controls reach the bot whatever `SLIMM_CHANNELS` says.
- When an episode ends and there is a next one, the bot stays in the call and the panel says
  "finished - next up: ..." with only Next episode and Stop enabled; it leaves after
  `JELLYFIN_NEXT_WAIT_SECONDS` if nobody presses. A movie or a finale ends the party as before.
- `!pause` / `!resume` - stops or resumes reading the decoded stream;
  ffmpeg blocks on its own full pipe buffer while paused, so it costs no
  CPU and resumes exactly where it left off.
- A title ends only when the video `ffmpeg` exits cleanly within a few seconds of its runtime.
  Any other end of the stream (Jellyfin refusing it, a cut connection, a crash) stops the party with "the stream dropped",
  reports the position it reached, and leaves the title unwatched; `!watch` resumes it.
  A title with no audio stream keeps playing its video in silence.
- `!seek <h:mm:ss>` - restarts the transcode at a new position (`mm:ss`
  and a bare second count also work).
- `!np` - an embed with title, position, duration, and subtitle state.
- `!stop` - ends the stream and leaves the call.
- Progress: while a stream plays the bot writes its position back to
  jellyfin every 30 seconds and on `!stop` (`POST /UserItems/{id}/UserData`
  with `PlaybackPositionTicks` and `LastPlayedDate`), and marks the item
  played and clears the position when it finishes, so jellyfin's own
  Continue Watching stays correct. It does not use the `/Sessions/Playing`
  endpoints, which need a user token rather than the API key.
- `!subs <lang|off>` - matches a subtitle track by language code or
  display title and restarts the transcode with `SubtitleMethod=Encode`
  burning it in, or clears it with `off`.
- `!quality [low|medium|high]` - alone, reports the current quality.
  With a preset (480p at 1.5 Mbps, 720p at 4 Mbps, 1080p at 8 Mbps) it
  restarts the transcode and republishes the share at that size from the
  current position. A stream starts at whatever the `JELLYFIN_STREAM_*`
  settings below say, reported as `default`. `high` warns about the
  encode cost measured under "Stream quality".

The text commands `!pause`/`!resume`/`!seek`/`!stop`/`!subs`/`!quality <preset>` are refused unless the caller
either started the stream or holds `MANAGE_CHANNELS`. `!watch` itself
refuses with "join a voice channel first, then run `!watch` again" if the
invoker is not in any call this bot can see - never "join this channel's
voice call", since the channel `!watch` was typed in may not have one.

The stream stops itself when the movie ends, or when the voice channel
empties - checked on `on_voice_activity` when the LiveKit webhook (decision
0032) is configured, and on a 20-second roster poll regardless, so this
works even on a deployment that has not wired up the webhook.

Video is Jellyfin's own server-side transcode
(`/Videos/{id}/stream?VideoCodec=h264&AudioCodec=aac...`), decoded by a
local `ffmpeg` (a system binary - not pip-installed, and not in the
hash-locked CI requirements since the test suite never spawns it) into
raw I420 frames letterboxed to `JELLYFIN_STREAM_WIDTH`x`JELLYFIN_STREAM_HEIGHT`
and PCM audio (one ffmpeg, video on stdout and audio on a second pipe), published through `bot.voice`'s `SOURCE_SCREENSHARE`/
`SOURCE_SCREENSHARE_AUDIO` tracks - see `stream_session.py`.
Picture and sound start together: each pump holds its first chunk until
the other has one (`pump_sync.StartLine`), since Jellyfin takes a second
or more to start a transcode and a picture paced from launch raced
through that second and stayed ahead of the sound. The audio queue is
kept at 100 ms and cleared on every restart, so a seek or a pause does
not leave old sound playing over the new picture.

Those raw frames are then re-encoded a second time by LiveKit's own
WebRTC publish, which used to get no explicit bitrate/framerate ceiling
at all - only the SDK's own default for an unset `VideoEncoding`/
`AudioEncoding`, well below what Jellyfin had already been asked to
transcode at. `JELLYFIN_STREAM_WEBRTC_MAX_BITRATE` and
`JELLYFIN_STREAM_AUDIO_MAX_BITRATE` close that gap; see
`../../docs/framework.md`'s voice section and `bot.voice`'s own
`publish_screen_share` for what they set. Resolution and frame rate are
left at their existing defaults on purpose - CPU cost, not bitrate, is
what actually limits going past 720p; see below.

| Variable | Default | What it does |
| --- | --- | --- |
| `JELLYFIN_USER_ID` | first enabled user | The shared jellyfin account `!watch` reads and writes for a member who has not run `!jellyfin link`. |
| `JELLYFIN_AUTOPLAY_NEXT` | off | When an episode ends, start the next one on its own instead of waiting for Next episode. |
| `JELLYFIN_NEXT_WAIT_SECONDS` | `180` | How long the bot stays in the call after an episode ends, offering Next episode, before it leaves. |
| `JELLYFIN_STREAM_WIDTH` | `1280` | The published video width; Jellyfin's own aspect ratio is letterboxed into this. |
| `JELLYFIN_STREAM_HEIGHT` | `720` | The published video height. |
| `JELLYFIN_STREAM_FPS` | `30` | The highest published frame rate. A title plays at its own rate, or the largest whole fraction of it under this (a 23.976 fps film at 23.976, 50 fps at 25), so no frame is shown twice. |
| `JELLYFIN_STREAM_MAX_BITRATE` | `8000000` | The `VideoBitrate` Jellyfin is asked to transcode at, in bits/second. |
| `JELLYFIN_STREAM_WEBRTC_MAX_BITRATE` | `JELLYFIN_STREAM_MAX_BITRATE` | The ceiling on LiveKit's own re-encode of the decoded frames, in bits/second. Defaults to whatever `JELLYFIN_STREAM_MAX_BITRATE` resolves to, so raising one without the other no longer throws away the extra quality. |
| `JELLYFIN_STREAM_VIDEO_CODEC` | unset (VP8) | `h264` publishes H.264 instead of VP8. Only worth it with a hardware encoder: livekit's software H.264 costs more than VP8. Every viewer must decode H.264 (Chrome, Safari, the desktop and mobile apps do; Firefox needs its OpenH264 plugin). |
| `JELLYFIN_STREAM_VIDEO_ENCODER` | unset (livekit picks) | `nvenc`, `vaapi`, `hardware` or `software`. `nvenc` needs the GPU passed into the bot's container (`runtime: nvidia`, `NVIDIA_DRIVER_CAPABILITIES=video,compute,utility`). |
| `JELLYFIN_STREAM_AUDIO_MAX_BITRATE` | `128000` | The ceiling on LiveKit's Opus re-encode of the decoded PCM audio, in bits/second. The unset default is speech-call-tuned and noticeably worse for movie audio. |

## Other settings

| Variable | Default | What it does |
| --- | --- | --- |
| `JELLYFIN_ITEM_TYPES` | `Movie,Episode` | Comma-separated Jellyfin item types to watch. `Audio` is deliberately not in the default - see Batching. |
| `JELLYFIN_LIBRARY_IDS` | unset (whole server) | Comma-separated library (`parentId`) UUIDs to scope to. |
| `JELLYFIN_POLL_SECONDS` | `300` | How often to check Jellyfin for anything new. |
| `JELLYFIN_BATCH_THRESHOLD` | `3` | More than this many ungrouped items of the same type in one poll collapse into one summary message. |
| `JELLYFIN_LIBRARY_ROUTES` | unset | `libraryId:channelId,...` - send one library's posts to its own channel instead of `bot.channel`. A library not listed still falls back to `bot.channel`. |
| `JELLYFIN_EXCLUDE_GENRES` | unset | Comma-separated, case-insensitive. An item carrying any of these genres is dropped before it is ever grouped into a post - still recorded in `posted_items` and still advances the cursor, so it is never re-considered on a later poll. |
| `JELLYFIN_DEDUPE_DAYS` | `7` | A movie or episode is not announced again within this many days of its last announcement, even under a new item id. Episodes match on series, season and episode number. Movies match on tmdb or imdb id, else name and year. `0` turns it off. |
| `JELLYFIN_REANNOUNCE_REPLACED` | off | Set to `true` to announce a replaced file as new again, the old behaviour. |
| `SLIMM_DB_PATH` | `jellyfin_watch.db` | Where the Jellyfin cursor and dedupe table live. |

## Cold start

The first run posts nothing. It looks up whatever is currently the newest
item in the watched library or libraries, silently records everything that
shares that exact instant as already-seen, and starts watching forward from
there - replaying years of an existing library into a channel the moment
this bot is first pointed at it is exactly the spam problem it exists to
solve, not a feature to preserve.

## Batching

A Jellyfin library scan can add forty episodes, or a whole album, in one
pass. The rule:

- **Episodes group by series.** One new episode posts as that one episode;
  more than one posts as a single message naming the season(s) and episode
  count, with the series' own poster - never a season-specific one.
- **Audio tracks group by album** the same way, which is also why `Audio`
  sits outside `JELLYFIN_ITEM_TYPES`'s default.
- **Everything else posts one message per item** - each with its own
  poster - until more than `JELLYFIN_BATCH_THRESHOLD` land in a single
  poll, at which point they collapse into one summary line with no
  attachment.

## The cursor is `DateCreated`, found by paging, not by filtering

`DateLastSaved` is not a real field - requesting it in `fields` returns
nothing - and `sortBy=DateLastSaved` does not sort by anything date-related.
`minDateLastSaved` is honoured as a filter, but bumps on any metadata
resave, not just on add - tested live against a 16,506-item Jellyfin 12
library, a window meant to mean "added in the last 3 days" returned 1,754
items, of which only 219 actually had a `DateCreated` in that window.
`minDateCreated` is silently ignored entirely.

So this bot pages backward from the newest item
(`sortBy=DateCreated&sortOrder=Descending`, stepping `startIndex` by
`PAGE_SIZE`) until an item's own `DateCreated` falls below the cursor,
capped by `MAX_ITEMS_PER_POLL`. The boundary is inclusive, because ties
happen (a bulk import can give several items the exact same `DateCreated`);
`posted_items` and a cursor that only ever moves forward close that gap -
a crash between sending a batch and recording it just repeats that one
fetch, rebuilds the identical group, and sends under the same deterministic
message id.

The cursor also never moves past the oldest item that has not been sent yet.
Posts go out in grouped order, not date order, so without that a failed older
post would be left behind a newer one that had already landed and never retried.

## Output

A post with a poster is the poster as a slim-m attachment plus a small
`Embed` (title and description, no image - an embed's image is a URL the
server fetches itself, not an already-uploaded attachment), with an empty
body so the title shows once. A post without a poster is plain text only.
See `../../docs/framework.md`'s embeds section for the fallback an older
server gets instead.

A message carries exactly one poster. Checked against the live channel on 2026-09-29: every recent post has one
`poster.jpg` attachment and an embed with no image, and the web client draws the image once. The two sizes that once read as
a double upload are one file in two units - the api reports bytes (481585) and the client shows KiB under the image (470.3 KB).

When Sonarr replaces a file, Jellyfin makes a new item with a new id and a new `DateCreated`, so the dedupe by item id alone
treats it as new. `posted_media` records each announced episode (series, season, episode) and movie (provider ids, else name
and year) with a timestamp, and a replacement inside `JELLYFIN_DEDUPE_DAYS` is recorded as seen and not posted.
The table is created with `CREATE TABLE IF NOT EXISTS`, so an existing database picks it up on the next start.
A series that arrives episode by episode over an hour also gets one grouped post per poll.

## What was and was not verified in this port

The auth header, `DateCreated`-vs-`DateLastSaved`, and paging findings above
came from testing an earlier version of this bot live against a real
16,506-item Jellyfin 12 server - that testing is not repeated here, and
this port did not have a working Jellyfin credential available to redo it
against. This port's own testing is `test_bot.py`: grouping, cursor
advance, genre exclusion, library routing, and the command layer (search,
recent, cooldown, bot-ignore) against `FakeAsyncClient` with Jellyfin's own
functions monkeypatched, not a live server. The polling/grouping/auth logic
itself is unchanged from the version that was tested live; only its
integration onto `Bot` (config, the async command loop, the embed) is new
and only test-covered.

## What was verified for the watch party

Run for real against `slim-m`'s `scripts/e2e.sh` stack (real LiveKit, real
server, two real headless-Chrome web clients) and a real local Jellyfin
12.1.0 server with a two-track (H.264 + AAC) test file:

- `!watch`, `!pause`, `!resume`, `!seek`, `!np`, `!subs off`, and `!stop`
  all worked end to end against the real REST API and were checked at the
  LiveKit SFU (`ListParticipants`), not just by trusting the bot's own
  replies: the bot's identity showed `ACTIVE` with unmuted `SCREEN_SHARE`
  and `SCREEN_SHARE_AUDIO` tracks for the whole session.
- Both web clients rendered the actual decoded video on their call stage
  under "Jellyfin's screen" (screenshotted), and the SFU-level track
  state confirms the paired audio track was live and unmuted throughout.
- Auto-stop fired correctly both ways: naturally at end of video, and via
  `!stop` with the starter/`MANAGE_CHANNELS` gate (a third member without
  either was refused with the expected reply).
- A live Jellyfin 12.1.0 server's progressive `stream` endpoint silently
  drops the audio track when `Container=ts` is requested even though the
  source has an audio stream (`mp4`/`mkv` do not have this problem) -
  `build_stream_url` uses `mkv` because of this; see its own comment.
- One client (of two already in the call) did not pick up the bot's very
  first join - it saw the bot on a second `!watch` a few minutes later
  with no code change. This reads as a LiveKit-room-event timing edge
  case (a client's own participant-connected callback, not anything this
  bot controls) rather than a bug in `stream_session.py`/`watch_cog.py`,
  but it was not root-caused - see the PR description.
- CPU on the box this ran on (see the PR description for the exact
  hardware): `JELLYFIN_STREAM_WIDTH`/`HEIGHT` at the 1280x720 default drew
  roughly 20% of one core in the bot process (LiveKit's own encode) plus
  11% in the decoding `ffmpeg`; at 1920x1080 that was roughly 87% plus 25%
  - the LiveKit-side software encode, not the Jellyfin transcode or the
  decoding `ffmpeg`, is what 1080p actually costs.
- Not exercised live in that run: `!subs <lang>` actually burning in a subtitle track (the test
  file carried no subtitle stream), and a deployment where the invoker is
  in a *different* voice channel than the one named (refused by code
  inspection and the unit tests, not by a live attempt).

## Stream quality: what changed and what is only reasoned

`JELLYFIN_STREAM_WEBRTC_MAX_BITRATE`/`JELLYFIN_STREAM_AUDIO_MAX_BITRATE`
were added by reading `slimbots.voice`'s `publish_screen_share` and the
`livekit` client library's own docstrings and protobuf field comments,
not by measuring a stream before and after: nothing here re-ran the live
e2e stack above with a bandwidth or quality probe attached.

- Confirmed by source reading, not measurement: `TrackPublishOptions`
  never got a `video_encoding`/`audio_encoding` before this change, so
  LiveKit's own encoder picked whatever an unset ceiling means for a
  `SOURCE_SCREENSHARE` track - not the same 8 Mbps already spent getting
  the source out of Jellyfin. Matching the WebRTC ceiling to
  `JELLYFIN_STREAM_MAX_BITRATE` by default stops that gap from being the
  bottleneck for the same reason a low-bitrate transcode would be one:
  a later encoder cannot invent detail an earlier one already threw away,
  and until now it did not even get the chance to keep what was there.
- Resolution and frame rate are unchanged on purpose. The CPU numbers a
  few lines above are this repo's own prior measurement, not a new one -
  LiveKit's software encode alone went from ~20% of a core at 720p to
  ~87% at 1080p on that hardware. Raising the ceiling costs nothing
  extra there; raising the resolution risked exactly the kind of
  stutter this change is trying to avoid, on hardware nobody re-profiled
  for this PR.
- `degradation_preference` is set explicitly to `MAINTAIN_RESOLUTION`,
  matching what the protobuf field's own comment says is already the
  unset default, and what `is_screencast=True` already biases the
  encoder toward. A movie is mostly static shots and dialogue - losing
  a few frames under congestion reads far better than the whole frame
  going blurry or blocky.
- Simulcast is off as of the slimbots release that added `publish_screen_share(simulcast=)`.
  Before it, this section said simulcast "stays off", but nothing set it, and livekit's default is on: every `!watch` published two VP8 layers.
  The second layer of a screen share is encoded at about 3 fps (960x540 under a 1080p share, 640x360 under 720p), and a client with adaptive stream is sent the smallest layer that covers its tile.
  So a viewer whose tile was at most about 600 physical pixels tall saw a 3 fps slideshow at 1080p, where the same tile got the full 30 fps layer at 720p.

## 1080p cost, measured (2026-10-06)

Measured on the prod host (Ryzen 5 7600, 12 threads), livekit 1.1.20, with this bot's exact ffmpeg and publish settings replayed in a throwaway container, a second participant subscribing, and per-thread CPU read from `/proc`.
Percentages are of one core.

| Setup | Bot process (encode) | Decoding ffmpeg | Delivered fps |
| --- | --- | --- | --- |
| 1280x720, simulcast on (old default) | 39% | 15% | 30 |
| 1920x1080, simulcast on (old `high`) | 184% | 26% | 30 |
| 1920x1080, simulcast off | 92% | 24% | 30 |
| 1920x1072, simulcast off (new `high`) | 66% | 25% | 30 |
| 1920x1080, simulcast on, 6 of 12 threads busy elsewhere | 382% | 25% | 7 to 10 |
| 1920x1072, simulcast off, 6 of 12 threads busy elsewhere | 130% | 27% | 29 |
| 1280x720, simulcast on, 6 of 12 threads busy elsewhere | 70% | 18% | 30 |

- The jump at 1080p is libwebrtc's own VP8 thread heuristic: at 1920x1080 or more on a machine with more than 8 cores it encodes with 8 threads, below that with 3.
  libvpx's threads spin while they wait on each other, so 8 of them cost more than the encode itself, and on a busy host they starve each other: the encoder drops to 7-10 fps and reports `LIMITATION_CPU`.
  That is the 1080p lag, and why 720p never showed it.
- `Quality.frame_size` therefore publishes a 1080p-class preset 8 rows short (1920x1072), which keeps libwebrtc on 3 threads.
  A 1.85:1 film is 1920x1040 out of Jellyfin, so it is only letterboxed by a few rows less.
- Jellyfin itself already transcodes on the GPU (`h264_nvenc` from a CUDA decode), so its share is a short burst while it runs ahead of playback, not a steady cost.
- Publishing through NVENC instead (`video_codec=H264`, encoder backend NVENC, the GPU passed into the container) measured 15% in the bot process at 1080p, idle or loaded, but needs a compose change and every client to decode H.264.

## What this deliberately does not do

- **Push instead of poll.** Jellyfin has no first-class webhook in core -
  that exists only as a separately-installed plugin, which would mean
  giving Jellyfin a URL back into wherever this bot runs.
- **A cursor per library.** All watched libraries share one cursor;
  `posted_items` is the real backstop regardless.
- **Recovering from a dead Jellyfin API key.** Treated the same as slim-m's
  own `401`: this exits rather than polling a revoked key forever.
- **Season-specific posters**, or any image past a series', movie's, or
  album's own primary one.
- **Retrying a failed poster separately from its message.** A poster that
  fails to fetch or upload just means that message posts without an
  attachment.
- **Catching up a very long outage in one poll.** `MAX_ITEMS_PER_POLL`
  caps how far back a single poll pages; a bigger backlog just takes more
  cycles, never a silently dropped difference.
- **Two streams in the same call.** One participant publishing two screen
  shares reads as one share to the client, so `!watch` refuses a second
  stream in a call that already has one.
- **Changing the frame rate mid-stream.** `!quality` changes the size and
  bitrate; `JELLYFIN_STREAM_FPS` is read once at bot startup.
