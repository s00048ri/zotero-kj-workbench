-- A highlight that runs across a page break arrives from Zotero as two
-- annotations, and "…what forces may ultimately close these" / "windows?" is
-- not two passages. The link is recorded rather than the texts being merged:
-- both cards keep their own identity, their own locator and their own note, and
-- everything downstream emits them as the one quotation they are.
ALTER TABLE card ADD COLUMN continues_card_id TEXT REFERENCES card(id) ON DELETE SET NULL;
CREATE INDEX idx_card_continues ON card(continues_card_id);
