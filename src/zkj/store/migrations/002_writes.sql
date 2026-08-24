-- Writing into Zotero, and being able to take it back.

CREATE TABLE write_auth (
    -- keys are per Zotero database: one granted against another library is
    -- meaningless here
    server_id  TEXT PRIMARY KEY,
    api_key    TEXT NOT NULL,
    remember   INTEGER NOT NULL DEFAULT 0,
    granted_at TEXT NOT NULL
);

-- One press of "Create notes in Zotero". Four hundred notes appearing in a
-- library is frightening without an undo, so every batch is recorded whole.
CREATE TABLE write_batch (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,      -- notes | labels
    created_at     TEXT NOT NULL,
    reverted_at    TEXT,
    note_keys_json TEXT NOT NULL,
    card_ids_json  TEXT NOT NULL,
    failures_json  TEXT
);
CREATE INDEX idx_write_batch_project ON write_batch(project_id, created_at DESC);
