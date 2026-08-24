-- Cards and where they came from.
--
-- Several constraints here are load-bearing rather than decorative, and the
-- comments say which mistake each one prevents.

CREATE TABLE project (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    zotero_server_id    TEXT,               -- object versions are local to one
                                            -- Zotero database and mean nothing
                                            -- against another
    root_collection_key TEXT NOT NULL,
    kj_root_key         TEXT,
    kj_inbox_key        TEXT,
    research_question   TEXT,
    created_at          TEXT NOT NULL,
    last_import_at      TEXT
);

CREATE TABLE collection (
    id                    TEXT PRIMARY KEY,
    project_id            TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    zotero_collection_key TEXT NOT NULL,
    parent_key            TEXT,
    name                  TEXT NOT NULL,
    path                  TEXT NOT NULL,
    depth                 INTEGER NOT NULL,
    UNIQUE (project_id, zotero_collection_key)
);
CREATE INDEX idx_collection_path ON collection(project_id, path);

CREATE TABLE source (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    zotero_item_key   TEXT NOT NULL,
    item_type         TEXT,
    title             TEXT,
    creators_json     TEXT,
    creators_short    TEXT,
    year              TEXT,
    publication_title TEXT,
    doi               TEXT,
    isbn              TEXT,
    url               TEXT,
    raw_json          TEXT,
    -- per project, NOT global: the same book read for two papers is two rows
    UNIQUE (project_id, zotero_item_key)
);

CREATE TABLE source_collection (
    source_id     TEXT NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    collection_id TEXT NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
    PRIMARY KEY (source_id, collection_id)
);

CREATE TABLE attachment (
    id                    TEXT PRIMARY KEY,
    source_id             TEXT NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    zotero_attachment_key TEXT NOT NULL,
    content_type          TEXT,
    title                 TEXT,
    filename              TEXT,
    link_mode             TEXT,
    raw_json              TEXT,
    UNIQUE (source_id, zotero_attachment_key)
);

CREATE TABLE annotation (
    id                    TEXT PRIMARY KEY,
    attachment_id         TEXT NOT NULL REFERENCES attachment(id) ON DELETE CASCADE,
    zotero_annotation_key TEXT NOT NULL,
    annotation_type       TEXT,
    text_raw              TEXT,
    comment_raw           TEXT,
    color                 TEXT,
    page_label            TEXT,
    sort_index            TEXT,
    position_json         TEXT,
    date_modified         TEXT,
    raw_json              TEXT,
    -- covers text, comment, colour, page label, sort index AND position, so a
    -- highlight that was merely dragged still counts as changed
    content_hash          TEXT NOT NULL,
    -- not globally unique on the key: the same source in two projects must not
    -- collide
    UNIQUE (attachment_id, zotero_annotation_key)
);

CREATE TABLE card (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    human_id            TEXT NOT NULL,
    origin_key          TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('quote', 'idea', 'image')),
    origin              TEXT NOT NULL CHECK (origin IN (
                            'annotation_text', 'annotation_comment', 'child_note',
                            'standalone_note', 'group_label', 'manual')),
    text                TEXT NOT NULL,
    text_raw            TEXT,
    human_label         TEXT,
    source_id           TEXT REFERENCES source(id) ON DELETE SET NULL,
    annotation_id       TEXT REFERENCES annotation(id) ON DELETE SET NULL,
    -- these two must never be conflated: a card whose text came from a Zotero
    -- note is not a card this tool has already filed
    zotero_note_key     TEXT,   -- the standalone note THIS TOOL created
    origin_note_key     TEXT,   -- the Zotero note the card's text came from
    parent_card_id      TEXT REFERENCES card(id) ON DELETE SET NULL,
    prior_collection_id TEXT REFERENCES collection(id) ON DELETE SET NULL,
    prior_path          TEXT,
    prior_ambiguous     INTEGER NOT NULL DEFAULT 0,
    locator_type        TEXT NOT NULL DEFAULT 'none',
    locator_value       TEXT,
    locator_source      TEXT NOT NULL DEFAULT 'none',
    locator_estimated   INTEGER NOT NULL DEFAULT 0,
    locator_detail_json TEXT,
    kj_collection_keys_json TEXT,
    kj_path             TEXT,
    materialized_at     TEXT,
    color               TEXT,
    sort_index          TEXT,          -- position in the document, for reading order
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'excluded')),
    content_hash        TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    -- re-import idempotency falls out of this constraint rather than being
    -- implemented as procedural checks
    UNIQUE (project_id, origin_key),
    UNIQUE (project_id, human_id)
);
CREATE INDEX idx_card_project_kind ON card(project_id, kind);
CREATE INDEX idx_card_prior ON card(project_id, prior_collection_id);
CREATE INDEX idx_card_kj_path ON card(project_id, kj_path);
CREATE INDEX idx_card_parent ON card(parent_card_id);

-- Trigram, so search works on Japanese and French as well as English without
-- a language-specific tokeniser.
CREATE VIRTUAL TABLE card_fts USING fts5(
    text, human_label, content='card', content_rowid='rowid', tokenize='trigram'
);
CREATE TRIGGER card_fts_insert AFTER INSERT ON card BEGIN
    INSERT INTO card_fts(rowid, text, human_label)
    VALUES (new.rowid, new.text, COALESCE(new.human_label, ''));
END;
CREATE TRIGGER card_fts_delete AFTER DELETE ON card BEGIN
    INSERT INTO card_fts(card_fts, rowid, text, human_label)
    VALUES ('delete', old.rowid, old.text, COALESCE(old.human_label, ''));
END;
CREATE TRIGGER card_fts_update AFTER UPDATE ON card BEGIN
    INSERT INTO card_fts(card_fts, rowid, text, human_label)
    VALUES ('delete', old.rowid, old.text, COALESCE(old.human_label, ''));
    INSERT INTO card_fts(rowid, text, human_label)
    VALUES (new.rowid, new.text, COALESCE(new.human_label, ''));
END;

CREATE TABLE gbooks_cache (
    cache_key  TEXT PRIMARY KEY,
    page_count INTEGER,
    fetched_at TEXT NOT NULL
);

CREATE TABLE import_run (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    stats_json  TEXT
);
