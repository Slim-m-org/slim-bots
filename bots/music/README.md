# music

A slim-m bot that plays audio into the voice call of whoever asks.
It joins the invoker's current call, publishes a microphone-source track, so it shows up as a participant speaking rather than a share tile, and keeps one queue per voice channel.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
JELLYFIN_URL=https://your.jellyfin \
JELLYFIN_API_KEY=... \
python3 bot.py
```

`ffmpeg` must be on `PATH`: it decodes whatever the source serves to raw PCM, and the bot pushes that into LiveKit at the room's own pace.
`JELLYFIN_URL` and `JELLYFIN_API_KEY` are optional, and must be set together.
Without them the bot only plays direct stream URLs.

Built on the `slimbots` `Bot` framework, see `../../docs/framework.md`.
It reads every voice channel's chat, so the commands work typed into the call you are in.
The bot needs `VIEW_CHANNEL` and `SEND_MESSAGES` in that voice channel, plus `CONNECT` and `SPEAK` to join and publish.

## Commands

The default prefix is `~`, not `!`, because the jellyfin bot answers `!stop`, `!pause` and `!np` too and two bots replying to one line is worse than a different prefix.
Set `SLIMM_PREFIX=!` if it runs alone, or address it as `@<its username> play ...`.

- `~play <song or url>` - a name searches the Jellyfin music library and plays the first match, an `http(s)` URL is played directly.
  If a call already has music playing, the track is queued.
- `~queue` (`~q`) - the current track and the next ten.
- `~skip` - ends the current track and drops its queued audio, so the next one starts at once; the rest of the queue carries on.
- `~pause` / `~resume` - the track keeps about a tenth of a second queued in the call, so a pause is heard almost at once.
- `~stop` - clears the queue and leaves.
- `~np` - the current track, position, duration and how many are queued.

Everything acts on the call the invoker is in right now, not on the channel the message was typed in.
Anyone in that call can control it; someone outside it gets "nothing is playing in your call".
The bot leaves when the queue runs out, when someone runs `~stop`, or when it is the only participant left.

## Sources, and what this deliberately does not do

- **Jellyfin music library and direct URLs only.**
  Direct URLs cover internet radio and files on a server you control.
  There is no YouTube or Spotify playback: that means scraping a third party against its terms, so it is not built here.
- **Direct URLs to private addresses are refused.**
  ffmpeg fetches on the bot host, so `~play http://192.168.x.x/...` would otherwise let a chat member probe the host's network.
  The configured Jellyfin host is exempt.
  Before ffmpeg sees a URL the bot opens it itself, follows every redirect and checks each hop against the same rule, connecting to the address it checked.
  ffmpeg is then given the address that answered, and a playlist or manifest (`.m3u8`, `.pls`, `.mpd`) is refused because its entries would be fetched without that check.
  An ffmpeg that lists the http `max_redirects` option is also told not to follow a redirect from that address; one that does not cannot be, so a server that answers ffmpeg differently from the bot's own request is not caught there.
  A server that answers in the old `ICY 200 OK` form instead of HTTP is refused too, since the bot cannot check what it cannot parse.
  ffmpeg is also limited to `http`, `https`, `tcp`, `tls` and `crypto`, so a URL cannot reach `file:` or a raw socket.
- **The Jellyfin key only goes to Jellyfin.**
  It is sent as an `Authorization` header on Jellyfin tracks and never on a direct URL.
  It is also an argument of the ffmpeg process (`-headers`), so anyone who can list processes on the bot host can read it.
  The Jellyfin watch bot no longer has this exposure, since its transcode reaches ffmpeg on stdin; this bot still does, so run it in its own container.
- **No volume command.**
  It would mean scaling PCM or restarting ffmpeg mid-track, and every listener already has a per-participant volume slider.
- **No persistence.**
  A queue lives in memory and is gone when the bot restarts or leaves the call.
- **Playlists and albums are not expanded.**
  `~play` takes one track at a time.
- **Audio is published at 128 kbps.**
  LiveKit's default microphone bitrate is tuned for speech and sounds thin on music.

## Layout

- `bot.py` - the entry point.
- `music_core.py` - settings, the Jellyfin audio search, track model and URL vetting.
- `player.py` - `MusicSession`: the queue, the ffmpeg decode and the LiveKit publish.
- `music_cog.py` - the commands and the per-voice-channel session registry.
- `test_bot.py` - run with `python3 test_bot.py`.
  ffmpeg is replaced by a scripted decoder and LiveKit by a fake room, so CI needs neither.
