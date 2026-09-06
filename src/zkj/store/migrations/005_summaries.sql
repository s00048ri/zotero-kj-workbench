-- A summary is not a card and must never become one.
--
-- It lives in its own table, keyed to the attachment it was read from, so
-- that nothing in the card pipeline can pick it up by accident. One row per
-- attachment: re-summarising replaces, and only when asked.
CREATE TABLE summary (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    attachment_id   TEXT NOT NULL REFERENCES attachment(id) ON DELETE CASCADE,
    source_id       TEXT NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    model           TEXT,
    effort          TEXT,
    text            TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    file_bytes      INTEGER,
    media_type      TEXT,
    truncated       INTEGER NOT NULL DEFAULT 0,
    -- the note in Zotero, once the researcher asks for one
    zotero_note_key TEXT,
    written_at      TEXT,
    UNIQUE (project_id, attachment_id)
);

CREATE INDEX idx_summary_project ON summary(project_id, created_at DESC);
CREATE INDEX idx_summary_note ON summary(zotero_note_key);
