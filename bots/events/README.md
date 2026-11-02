# events

A slim-m bot (directory `bots/events`, command `!event`) for small plans like a movie night: it posts the plan with Going and Not going buttons, and reminds the people who said they are going.
It is deliberately not a calendar: no recurring events, no invitations, no per-person timezones.

Needs `slim-m>=0.15.0`.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps its state in its own sqlite file (`SLIMM_DB_PATH`, default `events.db`).

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
EVENTS_TZ=Europe/Berlin \
python3 bot.py
```

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `!event "movie night" fri 8pm` | anyone | post the plan with buttons; the day is a weekday, `today`, `tomorrow` or `YYYY-MM-DD`, the time `8pm`, `8:30pm` or `20:00` |
| `!event list` | anyone | the open events with their numbers |
| `!event cancel <n>` | the planner, or Manage Messages | cancel an event and take the buttons off its post |

A quoted title is optional: `!event game night sat 19:30` works too.
At most 10 events are open at once, and a start can be at most 60 days away.
A weekday that has already passed today means next week.
The press of a button is private to the member and can be changed any time before the start.

The bot posts one reminder 15 minutes before and one at the start, in the channel the event was planned in, mentioning only the people who are going.
A reminder is skipped when nobody is going.

## Settings

| Variable | Default | What it does |
| --- | --- | --- |
| `EVENTS_TZ` | `UTC` | the IANA timezone the day and time are read in and shown in |
| `EVENTS_REMIND_MINUTES` | `15` | how long before the start the reminder goes out; `0` skips it |

## Why buttons and not a native poll

A poll's tally reaches a bot without saying who voted, so a bot cannot remind the people who answered yes.
Buttons arrive with the member who pressed them.
