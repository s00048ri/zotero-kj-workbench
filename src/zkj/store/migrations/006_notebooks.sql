-- A NotebookLM notebook the researcher made, and what they brought back.
--
-- The workbench never talks to NotebookLM. It prepares what goes in, holds
-- the link the researcher pastes back, writes that link into Zotero, and
-- keeps whatever they paste back out.
CREATE TABLE notebook (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    -- NULL means the notebook is the project's, not one source's
    source_id    TEXT REFERENCES source(id) ON DELETE CASCADE,
    title        TEXT,
    url          TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    -- the note carrying the link, once it has been written
    zotero_note_key TEXT,
    linked_at    TEXT
);

-- SQLite treats NULLs as distinct, so "one project notebook" needs its own
-- partial index rather than a UNIQUE column pair.
CREATE UNIQUE INDEX idx_notebook_project_wide
    ON notebook(project_id) WHERE source_id IS NULL;
CREATE UNIQUE INDEX idx_notebook_per_source
    ON notebook(project_id, source_id) WHERE source_id IS NOT NULL;

-- What NotebookLM produced, pasted back by hand. Not evidence, and never a
-- card: the same rule the summary table lives under.
CREATE TABLE notebook_report (
    id           TEXT PRIMARY KEY,
    notebook_id  TEXT NOT NULL REFERENCES notebook(id) ON DELETE CASCADE,
    project_id   TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    title        TEXT,
    text         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    zotero_note_key TEXT,
    written_at   TEXT
);

CREATE INDEX idx_notebook_report_notebook
    ON notebook_report(notebook_id, created_at DESC);
