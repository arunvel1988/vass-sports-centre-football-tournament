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
    score_b INTEGER NOT NULL DEFAULT 0
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
