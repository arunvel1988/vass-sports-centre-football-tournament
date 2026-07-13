"""
VSFC Live Match Tracker
=======================

A minimal Flask app that lets ONE volunteer update a match in 1-2 seconds
per event (goal / card / half-time / full-time) using big tap buttons,
and exposes a JSON API that an LLM layer (or any chatbot) can query to
answer questions like "what's the score" or "who is leading Group A".

Run:
    pip install flask
    python app.py

Then open:
    http://localhost:5000/admin            -> pick/create a match
    http://localhost:5000/admin/<match_id> -> volunteer's button panel
    http://localhost:5000/live/<match_id>  -> public live scoreboard
    http://localhost:5000/api/matches/<id> -> JSON state (for the LLM)
    http://localhost:5000/api/standings/<group> -> JSON standings
"""

import sqlite3
from datetime import datetime
from flask import Flask, g, render_template, request, jsonify, redirect, url_for

DATABASE = "vsfc.db"

app = Flask(__name__)

# Knockout stages in play order. compute_next_stage() uses this to name a
# new round based on how many teams are going into it.
STAGE_ORDER = ["group", "round_of_16", "quarterfinal", "semifinal", "third_place", "final"]
STAGE_LABELS = {
    "group": "Group Stage",
    "round_of_16": "Round of 16",
    "quarterfinal": "Quarter-Final",
    "semifinal": "Semi-Final",
    "third_place": "3rd Place Play-off",
    "final": "Final",
}


def stage_for_team_count(n):
    """How many teams go INTO a round decides what that round is called."""
    return {2: "final", 4: "semifinal", 8: "quarterfinal", 16: "round_of_16"}.get(n)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with app.app_context():
        db = get_db()
        with open("schema.sql", "r") as f:
            db.executescript(f.read())
        db.commit()


# ---------------------------------------------------------------------------
# Small internal helpers
# ---------------------------------------------------------------------------

def get_match_or_404(match_id):
    db = get_db()
    match = db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    if match is None:
        return None
    return match


def compute_current_minute(match):
    """
    Works out 'what minute is it' from kickoff timestamps, so the volunteer
    doesn't have to do mental math — but they can still override it manually
    per event (stoppage time, etc).
    """
    now = datetime.utcnow()

    if match["status"] == "live" and match["half"] == 1 and match["kickoff_time"]:
        kickoff = datetime.fromisoformat(match["kickoff_time"])
        elapsed = (now - kickoff).total_seconds() / 60
        return min(int(elapsed) + 1, match["half_length_minutes"])

    if match["status"] == "live" and match["half"] == 2 and match["second_half_start_time"]:
        start = datetime.fromisoformat(match["second_half_start_time"])
        elapsed = (now - start).total_seconds() / 60
        return min(match["half_length_minutes"] + int(elapsed) + 1, match["duration_minutes"])

    if match["status"] == "half_time":
        return match["half_length_minutes"]

    if match["status"] == "full_time":
        return match["duration_minutes"]

    return 0


def get_winner(match):
    """
    Winner of a completed match. For knockout matches that end level, this
    returns None until the volunteer manually declares a winner (penalties) —
    see /admin/<id>/set-winner.
    """
    if match["status"] != "full_time":
        return None
    if match["winner_team"]:
        return match["winner_team"]
    if match["full_time_score_a"] > match["full_time_score_b"]:
        return match["team_a"]
    if match["full_time_score_b"] > match["full_time_score_a"]:
        return match["team_b"]
    return None  # drawn, unresolved


def compute_standings(group_name):
    """
    Simple 3-1-0 standings table from completed GROUP-STAGE matches only
    (knockout matches never affect the table).
    """
    db = get_db()
    matches = db.execute(
        "SELECT * FROM matches WHERE group_name=? AND stage='group' AND status='full_time'",
        (group_name,),
    ).fetchall()

    table = {}

    def team_row(name):
        return table.setdefault(name, {
            "team": name, "played": 0, "won": 0, "drawn": 0, "lost": 0,
            "gf": 0, "ga": 0, "points": 0,
        })

    for m in matches:
        a, b = team_row(m["team_a"]), team_row(m["team_b"])
        a["played"] += 1
        b["played"] += 1
        a["gf"] += m["score_a"]
        a["ga"] += m["score_b"]
        b["gf"] += m["score_b"]
        b["ga"] += m["score_a"]

        if m["score_a"] > m["score_b"]:
            a["won"] += 1
            a["points"] += 3
            b["lost"] += 1
        elif m["score_a"] < m["score_b"]:
            b["won"] += 1
            b["points"] += 3
            a["lost"] += 1
        else:
            a["drawn"] += 1
            b["drawn"] += 1
            a["points"] += 1
            b["points"] += 1

    return sorted(
        table.values(),
        key=lambda r: (r["points"], r["gf"] - r["ga"], r["gf"]),
        reverse=True,
    )


def get_config():
    db = get_db()
    row = db.execute("SELECT * FROM tournament_config WHERE id=1").fetchone()
    if row is None:
        db.execute(
            "INSERT INTO tournament_config (id, tournament_name, format, qualifiers_per_group) "
            "VALUES (1, 'VSFC Tournament', 'group_knockout', 2)"
        )
        db.commit()
        row = db.execute("SELECT * FROM tournament_config WHERE id=1").fetchone()
    return row


def recompute_score(match_id):
    """Score is derived from goal events, so it can never drift."""
    db = get_db()
    match = db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    goals_a = db.execute(
        "SELECT COUNT(*) c FROM events WHERE match_id=? AND type='goal' AND team=?",
        (match_id, match["team_a"]),
    ).fetchone()["c"]
    goals_b = db.execute(
        "SELECT COUNT(*) c FROM events WHERE match_id=? AND type='goal' AND team=?",
        (match_id, match["team_b"]),
    ).fetchone()["c"]
    db.execute(
        "UPDATE matches SET score_a=?, score_b=? WHERE id=?",
        (goals_a, goals_b, match_id),
    )
    db.commit()


def match_to_dict(match_id):
    db = get_db()
    match = db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if match is None:
        return None
    events = db.execute(
        "SELECT * FROM events WHERE match_id=? ORDER BY id ASC", (match_id,)
    ).fetchall()

    def cards_for(team, card_type):
        return [e["player"] or "unknown" for e in events
                if e["team"] == team and e["type"] == card_type]

    return {
        "id": match["id"],
        "group": match["group_name"],
        "stage": match["stage"],
        "stage_label": STAGE_LABELS.get(match["stage"], match["stage"]),
        "team_a": match["team_a"],
        "team_b": match["team_b"],
        "score_a": match["score_a"],
        "score_b": match["score_b"],
        "winner": get_winner(match),
        "status": match["status"],          # scheduled / live / half_time / full_time
        "half": match["half"],
        "duration_minutes": match["duration_minutes"],
        "half_length_minutes": match["half_length_minutes"],
        "current_minute": compute_current_minute(match),
        "half_time_score": (
            f'{match["half_time_score_a"]}-{match["half_time_score_b"]}'
            if match["half_time_score_a"] is not None else None
        ),
        "full_time_score": (
            f'{match["full_time_score_a"]}-{match["full_time_score_b"]}'
            if match["full_time_score_a"] is not None else None
        ),
        "scorers": [
            {"team": e["team"], "player": e["player"], "minute": e["minute"]}
            for e in events if e["type"] == "goal"
        ],
        "yellow_cards": {
            match["team_a"]: cards_for(match["team_a"], "yellow"),
            match["team_b"]: cards_for(match["team_b"], "yellow"),
        },
        "red_cards": {
            match["team_a"]: cards_for(match["team_a"], "red"),
            match["team_b"]: cards_for(match["team_b"], "red"),
        },
        "events": [
            {
                "type": e["type"], "team": e["team"], "player": e["player"],
                "minute": e["minute"], "timestamp": e["timestamp"],
            }
            for e in events
        ],
    }


# ---------------------------------------------------------------------------
# HOME — landing page linking to everything
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    db = get_db()
    matches = db.execute("SELECT * FROM matches ORDER BY id DESC").fetchall()
    groups = sorted({m["group_name"] for m in matches})
    return render_template("home.html", matches=matches, groups=groups)


# ---------------------------------------------------------------------------
# ADMIN — the volunteer's screen
# ---------------------------------------------------------------------------

@app.route("/admin")
def admin_list():
    db = get_db()
    matches = db.execute("SELECT * FROM matches ORDER BY id DESC").fetchall()
    return render_template("admin_list.html", matches=matches)


@app.route("/admin/new", methods=["POST"])
def admin_new_match():
    db = get_db()
    duration = int(request.form.get("duration_minutes") or 90)
    # half length defaults to exactly half the duration (16 -> 8, 90 -> 45)
    half_length = int(request.form.get("half_length_minutes") or (duration // 2))
    stage = request.form.get("stage") or "group"
    group_name = request.form.get("group_name") or ""

    db.execute(
        "INSERT INTO matches "
        "(group_name, team_a, team_b, status, half, score_a, score_b, "
        " duration_minutes, half_length_minutes, stage) "
        "VALUES (?, ?, ?, 'scheduled', 0, 0, 0, ?, ?, ?)",
        (group_name, request.form["team_a"], request.form["team_b"],
         duration, half_length, stage),
    )
    db.commit()
    return redirect(url_for("admin_list"))


@app.route("/admin/<int:match_id>")
def admin_match(match_id):
    match = get_match_or_404(match_id)
    if match is None:
        return "Match not found", 404
    return render_template("admin_match.html", match=match)


@app.route("/admin/<int:match_id>/event", methods=["POST"])
def admin_add_event(match_id):
    """
    One tap = one event. Called by the big buttons via fetch().
    Expected JSON body: {"type": "goal"|"yellow"|"red"|"half_time"|"full_time",
                          "team": "<team name or ''>",
                          "player": "<optional>",
                          "minute": <int, optional>}
    """
    match = get_match_or_404(match_id)
    if match is None:
        return jsonify({"error": "match not found"}), 404

    data = request.get_json(force=True)
    ev_type = data.get("type")
    team = data.get("team", "")
    player = data.get("player", "")
    minute = data.get("minute")  # volunteer can type an exact minute; else we compute it

    db = get_db()
    now_iso = datetime.utcnow().isoformat()

    # First-ever action on the match (usually kickoff) starts the clock.
    if match["status"] == "scheduled":
        db.execute(
            "UPDATE matches SET status='live', half=1, kickoff_time=? WHERE id=?",
            (now_iso, match_id),
        )
        db.commit()
        match = get_match_or_404(match_id)  # refresh for minute calc below

    if ev_type in ("goal", "yellow", "red"):
        if minute in (None, ""):
            minute = compute_current_minute(match)
        db.execute(
            "INSERT INTO events (match_id, type, team, player, minute, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (match_id, ev_type, team, player, minute, now_iso),
        )
        db.commit()
        if ev_type == "goal":
            recompute_score(match_id)

    elif ev_type == "half_time":
        # Lock in the score at the half-time whistle, separate from live score.
        db.execute(
            "UPDATE matches SET status='half_time', half=1, "
            "half_time_score_a=score_a, half_time_score_b=score_b WHERE id=?",
            (match_id,),
        )
        db.commit()

    elif ev_type == "second_half":
        db.execute(
            "UPDATE matches SET status='live', half=2, second_half_start_time=? WHERE id=?",
            (now_iso, match_id),
        )
        db.commit()

    elif ev_type == "full_time":
        # Lock in the final score at the full-time whistle.
        db.execute(
            "UPDATE matches SET status='full_time', "
            "full_time_score_a=score_a, full_time_score_b=score_b WHERE id=?",
            (match_id,),
        )
        db.commit()
        # Auto-resolve the winner if it wasn't a draw (knockout matches only
        # need this; group matches just ignore the 'winner' field).
        match = get_match_or_404(match_id)
        winner = get_winner(match)
        if winner:
            db.execute("UPDATE matches SET winner_team=? WHERE id=?", (winner, match_id))
            db.commit()

    else:
        return jsonify({"error": "unknown event type"}), 400

    return jsonify(match_to_dict(match_id))


@app.route("/admin/<int:match_id>/set-winner", methods=["POST"])
def admin_set_winner(match_id):
    """
    For knockout matches that finish level — the volunteer picks the winner
    (e.g. after a penalty shootout) so the bracket can advance.
    """
    match = get_match_or_404(match_id)
    if match is None:
        return jsonify({"error": "match not found"}), 404
    data = request.get_json(force=True)
    team = data.get("team")
    if team not in (match["team_a"], match["team_b"]):
        return jsonify({"error": "team must be team_a or team_b of this match"}), 400
    db = get_db()
    db.execute("UPDATE matches SET winner_team=? WHERE id=?", (team, match_id))
    db.commit()
    return jsonify(match_to_dict(match_id))


# ---------------------------------------------------------------------------
# TOURNAMENT SETUP — format, groups, qualifiers
# ---------------------------------------------------------------------------

@app.route("/admin/setup", methods=["GET", "POST"])
def admin_setup():
    db = get_db()
    if request.method == "POST":
        db.execute(
            "UPDATE tournament_config SET tournament_name=?, format=?, "
            "total_teams=?, total_groups=?, qualifiers_per_group=? WHERE id=1",
            (
                request.form.get("tournament_name") or "VSFC Tournament",
                request.form.get("format") or "group_knockout",
                request.form.get("total_teams") or None,
                request.form.get("total_groups") or None,
                int(request.form.get("qualifiers_per_group") or 2),
            ),
        )
        db.commit()
        return redirect(url_for("admin_knockouts"))

    config = get_config()
    return render_template("admin_setup.html", config=config)


# ---------------------------------------------------------------------------
# KNOCKOUTS — seed a round from group standings, then advance winners
# ---------------------------------------------------------------------------

@app.route("/admin/knockouts")
def admin_knockouts():
    db = get_db()
    config = get_config()

    groups = sorted({
        r["group_name"] for r in
        db.execute("SELECT DISTINCT group_name FROM matches WHERE stage='group' AND group_name != ''")
    })
    standings_by_group = {g: compute_standings(g) for g in groups}

    # Existing knockout matches, grouped by stage, in play order.
    knockout_matches = db.execute(
        "SELECT * FROM matches WHERE stage != 'group' ORDER BY id ASC"
    ).fetchall()
    stages_present = [s for s in STAGE_ORDER if s != "group"
                       and any(m["stage"] == s for m in knockout_matches)]
    rounds = []
    for stage in stages_present:
        stage_matches = [m for m in knockout_matches if m["stage"] == stage]
        rounds.append({
            "stage": stage,
            "label": STAGE_LABELS[stage],
            "matches": stage_matches,
            "all_complete": all(m["status"] == "full_time" for m in stage_matches),
            "all_resolved": all(get_winner(m) for m in stage_matches),
        })

    return render_template(
        "admin_knockouts.html",
        config=config, groups=groups, standings_by_group=standings_by_group,
        rounds=rounds,
    )


@app.route("/admin/knockouts/generate-from-groups", methods=["POST"])
def generate_from_groups():
    db = get_db()
    qualifiers = int(request.form.get("qualifiers_per_group") or 2)
    selected_groups = request.form.getlist("groups")
    duration = int(request.form.get("duration_minutes") or 90)
    half_length = int(request.form.get("half_length_minutes") or (duration // 2))

    if not selected_groups:
        return "Pick at least one group.", 400

    # Seeds ordered rank-major (all 1st-place teams, then all 2nd-place, ...)
    # so the bracket pairing below naturally avoids same-group first-round
    # clashes in the common cases (2 or 4 groups).
    seeds = []
    for rank in range(qualifiers):
        for grp in selected_groups:
            table = compute_standings(grp)
            if rank < len(table):
                seeds.append(table[rank]["team"])

    n = len(seeds)
    stage = stage_for_team_count(n)
    if stage is None:
        return (f"{n} qualifying teams doesn't make a clean bracket "
                f"(need 2, 4, 8, or 16). Adjust groups or qualifiers per group."), 400

    # Classic bracket pairing: seed 1 vs seed N, seed 2 vs seed N-1, etc.
    for i in range(n // 2):
        team_a, team_b = seeds[i], seeds[n - 1 - i]
        db.execute(
            "INSERT INTO matches "
            "(group_name, team_a, team_b, status, half, score_a, score_b, "
            " duration_minutes, half_length_minutes, stage) "
            "VALUES ('', ?, ?, 'scheduled', 0, 0, 0, ?, ?, ?)",
            (team_a, team_b, duration, half_length, stage),
        )
    db.commit()
    return redirect(url_for("admin_knockouts"))


@app.route("/admin/knockouts/generate-next", methods=["POST"])
def generate_next_round():
    db = get_db()
    current_stage = request.form.get("stage")
    duration = int(request.form.get("duration_minutes") or 90)
    half_length = int(request.form.get("half_length_minutes") or (duration // 2))

    matches = db.execute(
        "SELECT * FROM matches WHERE stage=? ORDER BY id ASC", (current_stage,)
    ).fetchall()

    winners = []
    losers = []
    for m in matches:
        w = get_winner(m)
        if not w:
            return (f"Match {m['team_a']} vs {m['team_b']} isn't resolved yet "
                    f"(finish it, or declare a penalty winner)."), 400
        winners.append(w)
        losers.append(m["team_b"] if w == m["team_a"] else m["team_a"])

    n = len(winners)
    next_stage = stage_for_team_count(n)
    if next_stage is None:
        return f"{n} winners doesn't make a clean next round.", 400

    for i in range(0, n, 2):
        db.execute(
            "INSERT INTO matches "
            "(group_name, team_a, team_b, status, half, score_a, score_b, "
            " duration_minutes, half_length_minutes, stage) "
            "VALUES ('', ?, ?, 'scheduled', 0, 0, 0, ?, ?, ?)",
            (winners[i], winners[i + 1], duration, half_length, next_stage),
        )

    # Bonus: generating the final from 2 semis also sets up the 3rd-place playoff.
    if next_stage == "final" and len(losers) == 2:
        db.execute(
            "INSERT INTO matches "
            "(group_name, team_a, team_b, status, half, score_a, score_b, "
            " duration_minutes, half_length_minutes, stage) "
            "VALUES ('', ?, ?, 'scheduled', 0, 0, 0, ?, ?, 'third_place')",
            (losers[0], losers[1], duration, half_length),
        )

    db.commit()
    return redirect(url_for("admin_knockouts"))


# ---------------------------------------------------------------------------
# PUBLIC — spectator live view
# ---------------------------------------------------------------------------

@app.route("/live/<int:match_id>")
def live_match(match_id):
    match = get_match_or_404(match_id)
    if match is None:
        return "Match not found", 404
    return render_template("live.html", match_id=match_id)


@app.route("/standings")
def standings_dashboard():
    """Public dashboard: group tables + knockout bracket, no admin controls."""
    return render_template("standings.html")


# ---------------------------------------------------------------------------
# API — this is what the LLM / chatbot layer calls to answer questions
# ---------------------------------------------------------------------------

@app.route("/api/matches/<int:match_id>")
def api_match(match_id):
    data = match_to_dict(match_id)
    if data is None:
        return jsonify({"error": "match not found"}), 404
    return jsonify(data)


@app.route("/api/matches")
def api_matches():
    db = get_db()
    ids = [r["id"] for r in db.execute("SELECT id FROM matches").fetchall()]
    return jsonify([match_to_dict(i) for i in ids])


@app.route("/api/standings/<group_name>")
def api_standings(group_name):
    return jsonify({"group": group_name, "standings": compute_standings(group_name)})


@app.route("/api/tournament")
def api_tournament():
    """
    Full tournament state in one call — groups, standings, and the knockout
    bracket. This is what a dashboard (or an LLM answering 'who's through to
    the semis') should fetch.
    """
    db = get_db()
    config = get_config()

    groups = sorted({
        r["group_name"] for r in
        db.execute("SELECT DISTINCT group_name FROM matches WHERE stage='group' AND group_name != ''")
    })
    standings = {g: compute_standings(g) for g in groups}

    knockout_matches = db.execute(
        "SELECT * FROM matches WHERE stage != 'group' ORDER BY id ASC"
    ).fetchall()
    stages_present = [s for s in STAGE_ORDER if s != "group"
                       and any(m["stage"] == s for m in knockout_matches)]
    bracket = [
        {
            "stage": s,
            "label": STAGE_LABELS[s],
            "matches": [match_to_dict(m["id"]) for m in knockout_matches if m["stage"] == s],
        }
        for s in stages_present
    ]

    return jsonify({
        "tournament_name": config["tournament_name"],
        "format": config["format"],
        "total_teams": config["total_teams"],
        "total_groups": config["total_groups"],
        "qualifiers_per_group": config["qualifiers_per_group"],
        "groups": groups,
        "standings": standings,
        "bracket": bracket,
    })


if __name__ == "__main__":
    import os
    if not os.path.exists(DATABASE):
        init_db()
        print("Initialized fresh database: vsfc.db")
    app.run(debug=True, host="0.0.0.0", port=5000)
