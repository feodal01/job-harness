CREATE TABLE IF NOT EXISTS briefs (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL
);
-- sources: status, error and metrics of every source of the run.
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, brief_id TEXT NOT NULL REFERENCES briefs(id),
    request TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
    finished_at REAL, metrics TEXT NOT NULL DEFAULT '{}',
    error TEXT, limit_reached INTEGER NOT NULL DEFAULT 0,
    sources TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS runs_by_brief ON runs(brief_id, created_at DESC);
-- Corpus of everything known about a vacancy. A card, once fetched, is reused and never fetched again.
CREATE TABLE IF NOT EXISTS vacancies (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
    detail_fetched_at REAL
);
-- Snapshot of what one run found.
CREATE TABLE IF NOT EXISTS run_vacancies (
    run_id TEXT NOT NULL REFERENCES runs(id), vacancy_id TEXT NOT NULL REFERENCES vacancies(id),
    data TEXT NOT NULL, detail_status TEXT NOT NULL, error TEXT, is_new INTEGER NOT NULL,
    PRIMARY KEY (run_id, vacancy_id)
);
-- The brief's pool: one row per vacancy, filled by its runs, worked through by the user.
CREATE TABLE IF NOT EXISTS brief_vacancies (
    brief_id TEXT NOT NULL REFERENCES briefs(id), vacancy_id TEXT NOT NULL REFERENCES vacancies(id),
    first_run_id TEXT NOT NULL REFERENCES runs(id), last_run_id TEXT NOT NULL REFERENCES runs(id),
    first_seen REAL NOT NULL, last_seen REAL NOT NULL, selection TEXT,
    user_status TEXT NOT NULL DEFAULT 'new', note TEXT NOT NULL DEFAULT '', status_updated_at REAL,
    PRIMARY KEY (brief_id, vacancy_id)
);
-- Every finding of a vacancy: which phrase, request and page of which run.
CREATE TABLE IF NOT EXISTS vacancy_origins (
    run_id TEXT NOT NULL REFERENCES runs(id), vacancy_id TEXT NOT NULL REFERENCES vacancies(id),
    query TEXT NOT NULL, request_url TEXT NOT NULL, page INTEGER NOT NULL,
    PRIMARY KEY (run_id, vacancy_id, request_url)
);
CREATE INDEX IF NOT EXISTS origins_by_vacancy ON vacancy_origins(vacancy_id);
-- Employer profiles already fetched inside a source; reused like cards. Not a company directory.
CREATE TABLE IF NOT EXISTS company_profiles (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, run_id TEXT REFERENCES runs(id),
    time REAL NOT NULL, event TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS resource_state (
    id TEXT PRIMARY KEY, data TEXT NOT NULL
);
