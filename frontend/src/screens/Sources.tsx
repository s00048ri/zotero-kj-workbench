import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Project, type SummariseResult } from "../lib/api";

/* Machine summaries of what you have not read yet.
 *
 * This is the one screen where a machine reads the source instead of you. It
 * is here because deciding whether a paper is worth your attention comes
 * before highlighting it — not because a summary can stand in for the reading.
 * Nothing on this screen can become a card, and the notes it writes into
 * Zotero say so on their first line. */

export default function Sources({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const [chosen, setChosen] = useState<string[]>([]);
  const [run, setRun] = useState<SummariseResult | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const page = useQuery({
    queryKey: ["attachments", project.id],
    queryFn: () => api.attachments(project.id),
  });
  const summaries = useQuery({
    queryKey: ["summaries", project.id],
    queryFn: () => api.summaries(project.id),
  });
  const permission = useQuery({
    queryKey: ["write-permission"],
    queryFn: api.writePermission,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["attachments", project.id] });
    queryClient.invalidateQueries({ queryKey: ["summaries", project.id] });
    queryClient.invalidateQueries({ queryKey: ["batches", project.id] });
  };

  const summarise = useMutation({
    mutationFn: (replace: boolean) =>
      api.summarise(project.id, { attachment_ids: chosen, replace }),
    onSuccess: (r) => {
      setRun(r);
      setChosen([]);
      refresh();
    },
  });

  const file = useMutation({
    mutationFn: (ids?: string[]) => api.writeSummaryNotes(project.id, ids),
    onSuccess: refresh,
  });

  const forget = useMutation({
    mutationFn: (id: string) => api.forgetSummary(project.id, id),
    onSuccess: refresh,
  });

  const authorize = useMutation({
    mutationFn: api.authorize,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["write-permission"] }),
  });

  if (page.isLoading) return <p className="spinner">Reading the attachments…</p>;

  const rows = page.data?.attachments ?? [];
  const ready = page.data?.llm.ready ?? false;
  const unfiled = (summaries.data ?? []).filter((s) => !s.zotero_note_key);
  const anyChosenHasSummary = rows.some(
    (r) => chosen.includes(r.id) && r.summary_id,
  );

  return (
    <div className="column">
      <p className="teaching">
        A summary written here is for deciding whether to read something, and
        nothing more. It is not evidence, it never becomes a card, and in Zotero
        it sits beside the item marked as machine-written — never in{" "}
        <code>_KJ/Inbox</code>, where your own grouping happens.
      </p>

      {!ready && (
        <p className="notice">
          {page.data?.llm.reason} {page.data?.llm.remedy} Set it up on the Connect
          screen — until then this screen can list attachments but not read them.
        </p>
      )}

      <table className="sources-table">
        <thead>
          <tr>
            <th />
            <th>Attachment</th>
            <th>Source</th>
            <th>Summary</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} data-sendable={row.sendable}>
              <td>
                <input
                  type="checkbox"
                  disabled={!row.sendable || !ready}
                  checked={chosen.includes(row.id)}
                  onChange={(e) =>
                    setChosen((c) =>
                      e.target.checked ? [...c, row.id] : c.filter((x) => x !== row.id),
                    )
                  }
                />
              </td>
              <td>
                {row.title || row.filename || row.attachment_key}
                {!row.sendable && (
                  <>
                    {" "}
                    <span className="meta">
                      {row.link_mode === "linked_url"
                        ? "a link, not a file"
                        : `${row.content_type ?? "no file type"} — cannot be read`}
                    </span>
                  </>
                )}
              </td>
              <td className="meta">{row.citation || row.source_title}</td>
              <td className="meta">
                {row.summary_id ? (
                  row.summary_note_key ? (
                    "in Zotero"
                  ) : (
                    "here only"
                  )
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="note-actions">
        <button
          className="button"
          disabled={!chosen.length || summarise.isPending}
          onClick={() => summarise.mutate(false)}
        >
          {summarise.isPending
            ? "Reading…"
            : `Summarise ${chosen.length} attachment${chosen.length === 1 ? "" : "s"}`}
        </button>
        {anyChosenHasSummary && (
          <button
            className="button quiet"
            disabled={summarise.isPending}
            onClick={() => summarise.mutate(true)}
          >
            Summarise again, replacing what is there
          </button>
        )}
      </p>

      {summarise.isError && (
        <p className="notice bad">{(summarise.error as ApiError).message}</p>
      )}

      {run && (
        <div className="dialog-note">
          <p style={{ margin: 0 }}>
            {run.summarised} summarised, {run.failed} not — about $
            {run.cost_usd.toFixed(2)}. Nothing has been written into Zotero yet.
          </p>
          {run.attempts.some((a) => !a.ok) && (
            <ul className="preview-list">
              {run.attempts
                .filter((a) => !a.ok)
                .map((a) => (
                  <li key={a.attachment_key}>
                    <span className="meta">{a.title}</span> — {a.reason}
                  </li>
                ))}
            </ul>
          )}
        </div>
      )}

      {unfiled.length > 0 && (
        <div className="dialog-note">
          <p style={{ margin: 0 }}>
            {unfiled.length} summar{unfiled.length === 1 ? "y is" : "ies are"} kept
            here and not yet in Zotero.
          </p>
          {permission.data && !permission.data.remembered && permission.data.available && (
            <p style={{ margin: "0.5rem 0 0" }}>
              {permission.data.message}{" "}
              <button
                className="button quiet"
                disabled={authorize.isPending}
                onClick={() => authorize.mutate()}
              >
                {authorize.isPending ? "Waiting for Zotero…" : "Ask Zotero now"}
              </button>
            </p>
          )}
          <p style={{ margin: "0.5rem 0 0" }}>
            <button
              className="button"
              disabled={file.isPending || !permission.data?.available}
              onClick={() => file.mutate(undefined)}
            >
              {file.isPending
                ? "Writing into Zotero…"
                : "File them in Zotero, beside their sources"}
            </button>
          </p>
          {file.isError && (
            <p className="notice bad">{(file.error as Error).message}</p>
          )}
          {file.data && (
            <p className="meta" style={{ marginTop: "0.5rem" }}>
              {file.data.created} note{file.data.created === 1 ? "" : "s"} written.
              Take the batch back from the Project screen if you would rather not
              keep them.
            </p>
          )}
        </div>
      )}

      {(summaries.data ?? []).map((summary) => (
        <div className="summary-block" key={summary.id}>
          <p className="stamp">Machine summary — not your reading</p>
          <p style={{ margin: 0 }}>
            <strong>{summary.attachment_title || summary.source_title}</strong>{" "}
            <span className="meta">
              {summary.citation} · {summary.model} ·{" "}
              {summary.zotero_note_key ? "in Zotero" : "not in Zotero"}
            </span>
          </p>
          {open === summary.id ? (
            <>
              {summary.text.split("\n\n").map((para, i) => (
                <p key={i}>{para}</p>
              ))}
              {summary.truncated ? (
                <p className="notice">
                  Only part of the file was sent — this covers that part and no
                  more.
                </p>
              ) : null}
              <p className="note-actions">
                <button className="button quiet" onClick={() => setOpen(null)}>
                  Close
                </button>
                <button
                  className="button quiet"
                  disabled={forget.isPending}
                  onClick={() => forget.mutate(summary.id)}
                >
                  Forget this summary
                </button>
              </p>
            </>
          ) : (
            <p className="note-actions">
              <button className="button quiet" onClick={() => setOpen(summary.id)}>
                Read it
              </button>
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
