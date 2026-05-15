-- Eve knowledge DB schema (sized for Phases 1-4 of the pulse system)
-- Created 2026-04-22

CREATE TABLE IF NOT EXISTS interests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,    -- e.g. "cristiano_ronaldo", "tupac", "cre_chicago"
    label           TEXT    NOT NULL,            -- pretty label, e.g. "Cristiano Ronaldo"
    tags            TEXT    NOT NULL DEFAULT '', -- JSON array of tags
    intensity       REAL    NOT NULL DEFAULT 0.7,-- 0-1 — drives surfacing frequency
    type            TEXT    NOT NULL,            -- 'stable' or 'transient'
    origin          TEXT    NOT NULL DEFAULT 'profile',  -- 'profile' | 'adjacency:X' | 'news_repetition' | 'calendar:X' | 'self_start' | 'conversation:X'
    rss_queries     TEXT    NOT NULL DEFAULT '[]',-- JSON array of RSS URLs / Google-News query strings
    notes           TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL,            -- ISO8601 UTC
    last_reinforced TEXT,                        -- ISO8601 UTC; NULL when never reinforced
    last_decayed    TEXT                         -- ISO8601 UTC of last curator pass
);

CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL,           -- e.g. "bbc_sport_football", "pitchfork_news"
    url             TEXT    NOT NULL UNIQUE,    -- dedupe key
    title           TEXT    NOT NULL,
    summary         TEXT    NOT NULL DEFAULT '',
    published_at    TEXT,                       -- ISO8601 UTC if available
    fetched_at      TEXT    NOT NULL,           -- ISO8601 UTC
    tags            TEXT    NOT NULL DEFAULT '',-- JSON array (computed from interest matches)
    raw_excerpt     TEXT    NOT NULL DEFAULT '',-- short excerpt for relevance scoring
    relevance_score REAL,                       -- nullable; populated by Phase-2 scorer
    status          TEXT    NOT NULL DEFAULT 'new'  -- new | scored | queued | sent | skipped | expired
);

CREATE INDEX IF NOT EXISTS idx_items_status_score
    ON items(status, relevance_score DESC);

CREATE INDEX IF NOT EXISTS idx_items_published
    ON items(published_at DESC);

CREATE TABLE IF NOT EXISTS outreach_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    partner         TEXT    NOT NULL,           -- 'alex' or 'shawn'
    item_id         INTEGER REFERENCES items(id),
    decided_at      TEXT    NOT NULL,           -- ISO8601 UTC
    decision        TEXT    NOT NULL,           -- 'sent' | 'skipped'
    chat_message_id TEXT,                       -- Google Chat message resource name if sent
    reason          TEXT    NOT NULL DEFAULT ''
);

-- Static / recurring facts (birthdays, anniversaries, album drop dates).
-- These don't come from news; they're the calendar-anchored knowledge.
CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject       TEXT    NOT NULL,             -- e.g. "Cristiano Ronaldo"
    fact          TEXT    NOT NULL,             -- e.g. "born 1985-02-05 in Funchal, Madeira"
    date_anchor   TEXT,                         -- ISO8601 date this fact is relevant on (nullable)
    recurrence    TEXT    NOT NULL DEFAULT 'once', -- 'annual' | 'monthly' | 'once'
    tags          TEXT    NOT NULL DEFAULT '',  -- JSON array
    notes         TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_date
    ON facts(date_anchor);

-- Trail of curator decisions so we can audit what came/went.
CREATE TABLE IF NOT EXISTS curator_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    decided_at    TEXT    NOT NULL,             -- ISO8601 UTC
    interest_id   INTEGER REFERENCES interests(id),
    action        TEXT    NOT NULL,             -- 'created' | 'promoted' | 'decayed' | 'pruned' | 'reinforced'
    delta         REAL,                          -- intensity change if applicable
    reason        TEXT    NOT NULL DEFAULT ''
);
