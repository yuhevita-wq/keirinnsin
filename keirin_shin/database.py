"""SQLite schema and DB connection utilities (Python standard library only)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS races (
    race_id TEXT PRIMARY KEY,
    race_date TEXT NOT NULL,
    venue_code TEXT NOT NULL,
    venue_name TEXT NOT NULL,
    race_no INTEGER NOT NULL CHECK (race_no BETWEEN 1 AND 30),
    scheduled_start TEXT NOT NULL,
    race_class TEXT,
    UNIQUE (race_date, venue_code, race_no)
);
CREATE INDEX IF NOT EXISTS idx_races_date ON races(race_date);
CREATE TABLE IF NOT EXISTS riders (
    rider_id TEXT PRIMARY KEY,
    latest_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
    digest TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source_name TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    original_filename TEXT,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS race_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL REFERENCES races(race_id),
    captured_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    before_start INTEGER NOT NULL CHECK (before_start IN (0,1)),
    digest TEXT NOT NULL,
    UNIQUE (race_id, captured_at, source_name)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_race_time ON race_snapshots(race_id, captured_at);
CREATE TABLE IF NOT EXISTS entrant_snapshots (
    snapshot_id INTEGER NOT NULL REFERENCES race_snapshots(snapshot_id),
    car_no INTEGER NOT NULL CHECK(car_no BETWEEN 1 AND 20),
    rider_id TEXT NOT NULL REFERENCES riders(rider_id),
    rider_name TEXT NOT NULL,
    race_score REAL,
    rider_class TEXT,
    running_style TEXT,
    line_group TEXT,
    line_position INTEGER,
    features_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(snapshot_id,car_no),
    UNIQUE(snapshot_id,rider_id)
);
CREATE TABLE IF NOT EXISTS final_results (
    race_id TEXT PRIMARY KEY REFERENCES races(race_id),
    source_name TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('official','cancelled'))
);
CREATE TABLE IF NOT EXISTS rider_results (
    race_id TEXT NOT NULL REFERENCES final_results(race_id),
    car_no INTEGER NOT NULL CHECK (car_no BETWEEN 1 AND 20),
    rider_id TEXT NOT NULL REFERENCES riders(rider_id),
    rank INTEGER CHECK (rank BETWEEN 1 AND 20),
    finish_status TEXT NOT NULL CHECK(finish_status IN ('finished','dns','dnf','disqualified','other')),
    agari_seconds REAL CHECK (agari_seconds IS NULL OR agari_seconds > 0),
    winning_method TEXT,
    margin TEXT,
    PRIMARY KEY(race_id,car_no),
    UNIQUE(race_id,rider_id)
);
CREATE TABLE IF NOT EXISTS payouts (
    race_id TEXT NOT NULL REFERENCES final_results(race_id),
    bet_type TEXT NOT NULL,
    combination TEXT NOT NULL,
    payout_yen INTEGER NOT NULL CHECK(payout_yen >= 0),
    PRIMARY KEY(race_id,bet_type,combination)
);
CREATE TABLE IF NOT EXISTS expected_races (
    race_date TEXT NOT NULL,
    race_id TEXT NOT NULL,
    venue_code TEXT NOT NULL,
    race_no INTEGER NOT NULL,
    PRIMARY KEY(race_date,race_id),
    UNIQUE(race_date,venue_code,race_no)
);
"""

def connect(path: str | Path) -> sqlite3.Connection:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(file))
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=5000')
    con.executescript(SCHEMA)
    return con
