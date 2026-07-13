DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS matches;

CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT NOT NULL,
    team_a TEXT NOT NULL,
    team_b TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled/live/half_time/full_time
    half INTEGER NOT NULL DEFAULT 0,
    score_a INTEGER NOT NULL DEFAULT 0,
    score_b INTEGER NOT NULL DEFAULT 0,

    duration_minutes INTEGER NOT NULL DEFAULT 90,     -- full match length (e.g. 90 or 16)
    half_length_minutes INTEGER NOT NULL DEFAULT 45,  -- length of ONE half (duration/2)

    kickoff_time TEXT,             -- when 1st half started (UTC iso)
    second_half_start_time TEXT,   -- when 2nd half started (UTC iso)

    half_time_score_a INTEGER,     -- score locked in at half-time whistle
    half_time_score_b INTEGER,
    full_time_score_a INTEGER,     -- score locked in at full-time whistle
    full_time_score_b INTEGER
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    type TEXT NOT NULL,        -- goal / yellow / red
    team TEXT,
    player TEXT,
    minute INTEGER,
    timestamp TEXT NOT NULL
);
