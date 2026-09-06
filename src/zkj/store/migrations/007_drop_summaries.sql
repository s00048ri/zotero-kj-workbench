-- The machine-summary feature is gone; NotebookLM is the whole answer now.
--
-- Migration 005 created this table and has been removed, which leaves a gap in
-- the numbering. That is deliberate: renumbering 006 down into the gap would
-- be skipped by any database that had already applied 005, and would never
-- create the notebook tables. A drop that tolerates the table's absence is
-- correct for every database, whether it saw 005 or not.
DROP TABLE IF EXISTS summary;
