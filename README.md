# VSFC Live Match Tracker

The "1 volunteer + big buttons" architecture, working end to end.

## Run it

```bash
pip install flask
python app.py
```

- `/admin` — list/create matches
- `/admin/<id>` — volunteer's button panel (goal / card / half-time / full-time, 1-2 taps per event)
- `/live/<id>` — public scoreboard, auto-refreshes every 4s
- `/api/matches/<id>` — full JSON state of one match
- `/api/matches` — JSON state of all matches
- `/api/standings/<group>` — 3-1-0 points table computed from full-time matches in that group

## How the score stays correct

Score is never typed in directly — it's *derived* by counting `goal` events per team
(`recompute_score()` in app.py). That means it can't drift out of sync with the event log,
and "who scored, in what minute" is always reconstructable from history.

## Wiring this to your LLM / chatbot layer

When a spectator asks "what's the score" or "who's leading Group A", your chatbot
should call the JSON endpoints above and hand the result to Claude alongside your
tournament PDF (fixtures, rules, referees) as context, e.g.:

```python
import requests

def build_context(match_id):
    match = requests.get(f"http://localhost:5000/api/matches/{match_id}").json()
    return f"""
    Live match data: {match}
    """
# then feed `build_context(...)` + the fixtures/rules PDF text into your
# Claude API call as system/context, and let it answer naturally.
```

## Extending toward the "no volunteer" architecture

Everything here is intentionally decoupled: `admin_add_event()` is the *only* place
that writes to the `events` table. If you later build the computer-vision-on-livestream
pipeline, it just needs to call the same `/admin/<id>/event` endpoint (or the same
Python function directly) with detected events — the admin buttons, the public
live page, and the LLM API all keep working unmodified. That's the natural migration
path from "human enters it" to "computer vision detects it" without a rewrite.
