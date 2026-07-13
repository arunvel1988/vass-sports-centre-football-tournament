DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS matches;
DROP TABLE IF EXISTS tournament_config;

CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT NOT NULL DEFAULT '',   -- '' for knockout-only matches with no group
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
    full_time_score_b INTEGER,

    -- 'group' / 'round_of_16' / 'quarterfinal' / 'semifinal' / 'third_place' / 'final'
    stage TEXT NOT NULL DEFAULT 'group',
    winner_team TEXT               -- set automatically, or manually for penalty-shootout draws
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

-- One row (id=1). Not enforced strictly, just a settings singleton.
CREATE TABLE tournament_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    tournament_name TEXT NOT NULL DEFAULT 'VSFC Tournament',
    format TEXT NOT NULL DEFAULT 'group_knockout',  -- league / knockout / group_knockout
    total_teams INTEGER,
    total_groups INTEGER,
    qualifiers_per_group INTEGER DEFAULT 2
);
