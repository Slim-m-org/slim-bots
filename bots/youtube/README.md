# youtube

A slim-m bot that runs a synced YouTube watch party in the voice call of whoever asks.

The bot joins the call and keeps the room's clock: which video, where it is, and whether it is playing.
It states that as the call's watch session (slim-m decision 0050), with `provider` set to `youtube`, and re-samples it every 5 seconds.
Each person's app plays the video in YouTube's own embed player and holds it on that clock.
The bot never streams, downloads or extracts a video, which YouTube's terms forbid, and it publishes no track: its tile in the call is a silent participant.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
python3 bot.py
```

Built on the `slimbots` `Bot` framework, see `../../docs/framework.md`.
It reads every voice channel's chat, so the commands also work typed into the call you are in.
The bot needs `VIEW_CHANNEL` and `CONNECT` in the voice channel, and `SEND_MESSAGES` there to post the link.

## Commands

- `!yt <link>` - starts a party in your current voice call, or adds the video to the queue when one is running there.
  A watch, `youtu.be`, shorts, embed or live link works, and a `t=` in it is where the first video starts.
- `!yt pause` / `!yt resume` - pauses or resumes the room.
- `!yt seek <h:mm:ss>` - moves the room to that time.
- `!yt skip` - plays the next queued video; with nothing queued the party ends.
- `!yt queue` - the current video and the next ten.
- `!yt np` - what is playing, where, and its link.
- `!yt stop` - ends the party and leaves the call.

The call dock shows Pause, Next video and Stop while the bot is on the call.
Steering is for whoever started the party, whoever queued the current video, or a member with Manage Channels; anyone can queue.
Everything acts on the call the invoker is in, not on the channel the command was typed in.
The party ends when the queue runs out, when someone stops it, or when the bot is the only one left on the call.

## What people see

A slim-m app with the watch session's `provider` field mounts the player in the call stage: the web app's iframe, the phone app's webview, or on desktop the video panel.
Anyone who pauses or seeks in the player watches on their own until they press "Rejoin the room".
An older app shows only the room's position bar, so every new video's link is also posted in the call's own chat, where it can be opened.
A video whose owner turned embedding off is refused up front, since nobody's app could play it.

## Settings

- `YOUTUBE_API_KEY` (optional) - reads title, channel, length and whether the video may be embedded from the YouTube Data API.
  Without it the bot uses YouTube's public oEmbed endpoint for the title and the watch page's own metadata for the length.
  A video whose length cannot be read plays until someone skips it.
- `SLIMM_DB_PATH` - the cursor file; set it to a writable volume when the bot runs in a container.
