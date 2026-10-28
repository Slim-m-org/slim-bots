# Smoke manifests

One `<bot>.json` per bot, read by `scripts/smoke_bot.py <bot>`.

```json
{
  "ready_seconds": 8,
  "env": {"SOME_BOT_SETTING": "value"},
  "steps": [
    {"say": "!help", "expect_contains": ["commands"], "timeout": 15}
  ]
}
```

- `say` is typed into the private channel by the tester account.
- `prefix` is the command prefix the smoke bot is created with, for a bot whose commands are not under `!`; `channel_kind` is `voice` for a bot that needs a voice channel.
- `pause` waits that many seconds before the step is typed, for a command with a cooldown.
- `expect_contains` lists substrings the replies must contain; leave it out to only require some reply.
- `env` is passed to the bot process on top of the caller's environment.
- List every documented command of the bot, including one bad-input case per command.

A bot PR adds its own manifest here (not under `bots/<name>/`).
Voice bots need a real LiveKit reachable from where the script runs.

Replies only the tester sees (a bot's private answers) are read off the tester's own websocket, so a manifest can assert on them like any other reply.
`exempt.json` maps a bot with no manifest to the reason it has none; `scripts/smoke_all.py` and `scripts/tests/test_smoke_coverage.py` fail when a bot is in neither place.
`scripts/smoke_all.py --base-url <fresh server>` claims a throwaway server, mints a bot and a channel per manifest and runs them all; the `smoke` workflow does that weekly against the published server image.
