import { useState } from "react";
import { copyText, downloadText } from "../lib/clipboard";
import type { DraftResult } from "../lib/api";

/* What the draft did with the evidence it was given.
 *
 * The paraphrase finding is the one to read: a paraphrase that tracks its
 * original carries no quotation marks, so nothing in the sentence looks
 * wrong. */

export default function ValidationReport({ result }: { result: DraftResult }) {
  const v = result.validation;
  const [view, setView] = useState<"findings" | "reading" | "markdown">("findings");
  const stop = v.findings.filter((f) => f.severity === "stop");
  const look = v.findings.filter((f) => f.severity === "look");

  return (
    <div className="validation">
      <p className={v.clean && !v.unknown.length ? "counter" : "notice bad"}>
        {v.clean
          ? `Nothing to stop you: ${v.stats.cards_cited} of ${v.stats.cards_available} cards cited, ${v.stats.words} words.`
          : `${stop.length + v.unknown.length} things to fix before this text is usable.`}
      </p>

      <div className="chips" style={{ marginBottom: "1rem" }}>
        {(["findings", "reading", "markdown"] as const).map((mode) => (
          <button
            key={mode}
            className="chip"
            aria-pressed={view === mode}
            onClick={() => setView(mode)}
          >
            {mode === "findings"
              ? "What to fix"
              : mode === "reading"
                ? "Read it"
                : "Markdown with citekeys"}
          </button>
        ))}
      </div>

      {view === "findings" && (
        <>
          {stop.map((f, i) => (
            <div className="misfit" key={`s${i}`}>
              <div className="move">
                <strong>{f.human_id ?? "—"}</strong> · {f.kind.replace(/_/g, " ")}
              </div>
              <p style={{ margin: "0 0 0.3rem" }}>{f.message}</p>
              {f.detail && <pre className="finding-detail">{f.detail}</pre>}
            </div>
          ))}

          {look.map((f, i) => (
            <div className="look" key={`l${i}`}>
              <div className="move">
                <strong>{f.human_id ?? "—"}</strong> · {f.kind.replace(/_/g, " ")}
              </div>
              <p style={{ margin: 0 }}>{f.message}</p>
              {f.detail && <pre className="finding-detail">{f.detail}</pre>}
            </div>
          ))}

          {v.evidence_needed.length > 0 && (
            <>
              <h4>Gaps the draft left open, as it was asked to</h4>
              <ul className="preview-list">
                {v.evidence_needed.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </>
          )}

          {v.unused.length > 0 && (
            <>
              <h4>Assigned but unused</h4>
              <ul className="preview-list">
                {v.unused.map((card) => (
                  <li key={card.human_id}>
                    <span className="meta">
                      {card.human_id} · {card.citation_mode}
                    </span>{" "}
                    {card.text.slice(0, 120)}
                  </li>
                ))}
              </ul>
            </>
          )}

          {!stop.length && !look.length && !v.unused.length && (
            <p className="lede">
              Every quotation matches its source, no paraphrase tracks its
              original, and every assigned card was used.
            </p>
          )}
        </>
      )}

      {view === "reading" && <div className="draft-read">{v.rendered}</div>}

      {view === "markdown" && (
        <>
          <p className="note-actions">
            <button className="button" onClick={() => copyText(result.markdown)}>
              Copy Markdown
            </button>
            <button
              className="button quiet"
              onClick={() => downloadText("section.md", result.markdown)}
            >
              Download .md
            </button>
            <span className="meta">
              citekeys, not author-year strings — so pandoc and Zotero can still
              resolve them
            </span>
          </p>
          <pre className="prompt-text">{result.markdown}</pre>
        </>
      )}
    </div>
  );
}
