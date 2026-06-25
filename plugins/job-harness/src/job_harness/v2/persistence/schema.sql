CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS append_attempts (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    append_sequence INTEGER NOT NULL CHECK (append_sequence >= 0),
    request_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    PRIMARY KEY (run_id, append_sequence)
);

CREATE TABLE IF NOT EXISTS raw_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    append_sequence INTEGER NOT NULL,
    query_variant TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    description_availability TEXT NOT NULL,
    detail_fetched INTEGER NOT NULL CHECK (detail_fetched IN (0, 1)),
    detail_parse_error TEXT,
    source_url TEXT,
    listing_json TEXT NOT NULL,
    record_json TEXT NOT NULL,
    FOREIGN KEY (run_id, append_sequence)
        REFERENCES append_attempts(run_id, append_sequence)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS raw_listings_source_idx
    ON raw_listings (run_id, append_sequence, source, query_variant);

CREATE TABLE IF NOT EXISTS source_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    append_sequence INTEGER NOT NULL,
    source TEXT NOT NULL,
    query_variant TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    outcome TEXT NOT NULL,
    raw_listings_written INTEGER NOT NULL CHECK (raw_listings_written >= 0),
    pages_visited INTEGER NOT NULL CHECK (pages_visited >= 0),
    elapsed_ms INTEGER NOT NULL CHECK (elapsed_ms >= 0),
    payload_json TEXT NOT NULL,
    FOREIGN KEY (run_id, append_sequence)
        REFERENCES append_attempts(run_id, append_sequence)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS source_attempts_source_idx
    ON source_attempts (run_id, append_sequence, source, query_variant, attempt);

CREATE TABLE IF NOT EXISTS run_manifest (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_results (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    append_sequence INTEGER NOT NULL CHECK (append_sequence >= 0),
    phase TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, append_sequence, phase),
    FOREIGN KEY (run_id, append_sequence)
        REFERENCES append_attempts(run_id, append_sequence)
        ON DELETE CASCADE
);
