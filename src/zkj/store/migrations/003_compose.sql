-- The argument layer: a question, the claims under it, the sections that carry
-- them, and which cards do the work in each.

CREATE TABLE research_question (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    rationale  TEXT,
    status     TEXT NOT NULL DEFAULT 'candidate'
               CHECK (status IN ('candidate', 'chosen', 'set_aside')),
    origin     TEXT NOT NULL DEFAULT 'mine'
               CHECK (origin IN ('mine', 'pasted')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_question_project ON research_question(project_id, sort_order);

CREATE TABLE claim (
    id                   TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    research_question_id TEXT REFERENCES research_question(id) ON DELETE SET NULL,
    text                 TEXT NOT NULL,
    claim_type           TEXT NOT NULL DEFAULT 'supporting'
                         CHECK (claim_type IN ('thesis', 'supporting', 'objection',
                                               'qualification')),
    parent_claim_id      TEXT REFERENCES claim(id) ON DELETE SET NULL,
    sort_order           INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL
);
CREATE INDEX idx_claim_project ON claim(project_id, sort_order);

CREATE TABLE outline_section (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    parent_section_id TEXT REFERENCES outline_section(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    purpose           TEXT,
    thesis            TEXT,
    target_words      INTEGER,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);
CREATE INDEX idx_section_project ON outline_section(project_id, sort_order);

-- One card can do different work in different sections, so the role lives on
-- the pairing rather than on the card.
CREATE TABLE section_card_usage (
    id               TEXT PRIMARY KEY,
    section_id       TEXT NOT NULL REFERENCES outline_section(id) ON DELETE CASCADE,
    card_id          TEXT NOT NULL REFERENCES card(id) ON DELETE CASCADE,
    include          INTEGER NOT NULL DEFAULT 1,
    citation_mode    TEXT NOT NULL DEFAULT 'paraphrase'
                     CHECK (citation_mode IN ('direct_quote', 'paraphrase',
                                              'reference_only')),
    argument_role    TEXT NOT NULL DEFAULT 'evidence'
                     CHECK (argument_role IN ('evidence', 'counterevidence',
                                              'background', 'definition',
                                              'method', 'example')),
    user_instruction TEXT,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (section_id, card_id)
);

-- The exact text handed to a model, kept so the researcher can see later which
-- bundle produced which draft.
CREATE TABLE prompt_export (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    section_id TEXT REFERENCES outline_section(id) ON DELETE SET NULL,
    content    TEXT NOT NULL,
    chars      INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_prompt_project ON prompt_export(project_id, created_at DESC);

-- Drafts are append-only and versioned. A draft is never overwritten.
CREATE TABLE draft (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    section_id       TEXT REFERENCES outline_section(id) ON DELETE CASCADE,
    prompt_export_id TEXT REFERENCES prompt_export(id) ON DELETE SET NULL,
    version          INTEGER NOT NULL,
    content          TEXT NOT NULL,
    validation_json  TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX idx_draft_section ON draft(section_id, version DESC);
