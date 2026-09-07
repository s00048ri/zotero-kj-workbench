import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  api,
  type NotebookBundle,
  type Project,
  type StageResult,
} from "../lib/api";
import CopyBlock from "../components/CopyBlock";

/* NotebookLM, joined to Zotero by a link you carry across.
 *
 * The workbench never talks to Google. It prepares what goes into a notebook,
 * holds the address you paste back, writes that address into Zotero where the
 * reading happens, and keeps whatever the notebook produced. Everything the
 * notebook can do — briefing, study guide, timeline, mind map, audio — you ask
 * it for there, as many times as you like. */

const KIND_LABELS: Record<string, string> = {
  briefing: "Briefing doc",
  study_guide: "Study guide",
  faq: "FAQ",
  timeline: "Timeline",
  mind_map: "Mind map",
  audio_overview: "Audio overview",
  video_overview: "Video overview",
  blog_post: "Blog post",
  answer: "An answer to a question",
  other: "Something else",
};

export default function Notebook({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const [scope, setScope] = useState<string>("");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [staged, setStaged] = useState<StageResult | null>(null);
  const [pasteInto, setPasteInto] = useState<string | null>(null);

  const page = useQuery({
    queryKey: ["notebooks", project.id],
    queryFn: () => api.notebooks(project.id),
  });
  const bundle = useQuery({
    queryKey: ["notebook-bundle", project.id, scope],
    queryFn: () => api.notebookBundle(project.id, scope || null),
  });
  const sources = useQuery({
    queryKey: ["sources", project.id],
    queryFn: () => api.sources(project.id),
  });
  const permission = useQuery({
    queryKey: ["write-permission"],
    queryFn: api.writePermission,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["notebooks", project.id] });
    queryClient.invalidateQueries({ queryKey: ["batches", project.id] });
  };

  const add = useMutation({
    mutationFn: () =>
      api.addNotebook(project.id, {
        url,
        title: title || null,
        source_id: scope || null,
      }),
    onSuccess: () => {
      setUrl("");
      setTitle("");
      refresh();
    },
  });
  const stage = useMutation({
    mutationFn: () => api.stageForNotebook(project.id, scope || null),
    onSuccess: setStaged,
  });
  const link = useMutation({
    mutationFn: () => api.writeNotebookLinks(project.id),
    onSuccess: refresh,
  });
  const forget = useMutation({
    mutationFn: (id: string) => api.forgetNotebook(project.id, id),
    onSuccess: refresh,
  });
  const authorize = useMutation({
    mutationFn: api.authorize,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["write-permission"] }),
  });

  const notebooks = page.data?.notebooks ?? [];
  const reports = page.data?.reports ?? [];
  const unlinked = notebooks.filter((n) => !n.zotero_note_key);
  const prepared: NotebookBundle | undefined = bundle.data;

  const sourceOptions = (sources.data ?? []).map(
    (s) => [s.id, s.title || s.citation || s.id] as const,
  );

  return (
    <div className="column">
      <p className="teaching">
        NotebookLM has no interface a program can use, so the carrying across is
        yours: paste what is below into a notebook, then paste the notebook’s
        address back here. The workbench writes that address into Zotero — into
        the item, and into every card note it makes afterwards — so the notebook
        is one click away from wherever you are reading. What you ask it for
        there is up to you, and you can ask as often as you like.
      </p>

      <label className="stack">
        <span>This notebook is for</span>
        <select
          className="field"
          value={scope}
          onChange={(e) => {
            setScope(e.target.value);
            setStaged(null);
          }}
        >
          <option value="">the whole project — {project.name}</option>
          {sourceOptions.map(([id, label]) => (
            <option key={id} value={id}>
              one source — {label}
            </option>
          ))}
        </select>
      </label>

      <h3>1 · What to put in it</h3>
      {bundle.isLoading && <p className="spinner">Gathering…</p>}
      {prepared && (
        <>
          <CopyBlock
            label={`Your own passages (${prepared.card_count})`}
            hint="Paste as a “Copied text” source. This is the one thing no crawler can fetch, and the only reason the notebook knows what you found worth keeping."
            text={prepared.cards_text}
            rows={10}
          />
          <CopyBlock
            label={`Sources reachable by link (${prepared.urls.length})`}
            hint="Paste the whole block into Add sources → Website. NotebookLM takes a list at once, one per line."
            text={prepared.urls_text}
            rows={5}
            disabled={!prepared.urls.length}
          />

          {prepared.without_url.length > 0 && (
            <div className="carry-block">
              <p className="note-actions">
                <strong>Files with no public address ({prepared.without_url.length})</strong>
                <button
                  className="button"
                  disabled={stage.isPending}
                  onClick={() => stage.mutate()}
                >
                  {stage.isPending ? "Gathering…" : "Gather them into one folder"}
                </button>
              </p>
              <p className="teaching">
                Zotero files each attachment under a hashed directory, so
                uploading twenty by hand is twenty trips through a file dialog.
                This puts them in one folder, named by citekey, to select all of.
              </p>
              {staged && (
                <p className="notice">
                  {staged.staged} file{staged.staged === 1 ? "" : "s"} in{" "}
                  <code>{staged.folder}</code>. Open that folder from
                  NotebookLM’s upload dialog and select everything in it.
                  {staged.failures.length > 0 &&
                    ` ${staged.failures.length} could not be gathered.`}
                </p>
              )}
            </div>
          )}
          <p className="teaching">{prepared.source_limit_note}</p>
        </>
      )}

      <h3>2 · The notebook’s address</h3>
      <p className="teaching">
        Make the notebook at{" "}
        <a href="https://notebook.google.com/" target="_blank" rel="noreferrer">
          notebook.google.com
        </a>
        , paste the above into it, then copy the address from your browser.
      </p>
      <label className="stack">
        <span>Address</span>
        <input
          className="field"
          placeholder="https://notebook.google.com/notebook/…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      </label>
      <label className="stack">
        <span>Call it (optional)</span>
        <input
          className="field"
          placeholder="Everything on oversight"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <p className="note-actions">
        <button
          className="button"
          disabled={!url.trim() || add.isPending}
          onClick={() => add.mutate()}
        >
          {add.isPending ? "Keeping…" : "Keep this notebook"}
        </button>
      </p>
      {add.isError && <p className="notice bad">{(add.error as ApiError).message}</p>}

      {notebooks.length > 0 && (
        <>
          <h3>3 · Your notebooks</h3>
          {unlinked.length > 0 && (
            <div className="dialog-note">
              <p style={{ margin: 0 }}>
                {unlinked.length} notebook{unlinked.length === 1 ? "" : "s"} not yet
                linked from Zotero.
              </p>
              {permission.data?.available && !permission.data.remembered && (
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
                  disabled={link.isPending || !permission.data?.available}
                  onClick={() => link.mutate()}
                >
                  {link.isPending
                    ? "Writing into Zotero…"
                    : "Write the links into Zotero"}
                </button>
              </p>
              {link.isError && (
                <p className="notice bad">{(link.error as Error).message}</p>
              )}
              {link.data && (
                <p className="meta" style={{ marginTop: "0.5rem" }}>
                  {link.data.created} written.
                  {link.data.adopted > 0 &&
                    ` ${link.data.adopted} were already in Zotero — written on` +
                      " another machine and synced here, so they were adopted" +
                      " rather than written twice."}
                </p>
              )}
            </div>
          )}

          {notebooks.map((notebook) => (
            <div className="carry-block" key={notebook.id}>
              <p style={{ margin: 0 }}>
                <a href={notebook.url} target="_blank" rel="noreferrer">
                  {notebook.title || notebook.source_title || project.name}
                </a>{" "}
                <span className="meta">
                  {notebook.source_id ? "one source" : "the whole project"} ·{" "}
                  {notebook.zotero_note_key ? "linked from Zotero" : "not yet in Zotero"}
                  {notebook.reports > 0 && ` · ${notebook.reports} kept`}
                </span>
              </p>
              <p className="note-actions">
                <button
                  className="button quiet"
                  onClick={() =>
                    setPasteInto(pasteInto === notebook.id ? null : notebook.id)
                  }
                >
                  {pasteInto === notebook.id ? "Close" : "Paste something back"}
                </button>
                <button
                  className="button quiet"
                  disabled={forget.isPending}
                  onClick={() => forget.mutate(notebook.id)}
                >
                  Forget it
                </button>
              </p>
              {pasteInto === notebook.id && (
                <PasteBack
                  projectId={project.id}
                  notebookId={notebook.id}
                  kinds={page.data?.report_kinds ?? []}
                  onDone={() => {
                    setPasteInto(null);
                    refresh();
                  }}
                />
              )}
            </div>
          ))}
        </>
      )}

      {reports.length > 0 && (
        <Kept projectId={project.id} onChange={refresh} />
      )}
    </div>
  );
}

function PasteBack({
  projectId,
  notebookId,
  kinds,
  onDone,
}: {
  projectId: string;
  notebookId: string;
  kinds: string[];
  onDone: () => void;
}) {
  const [kind, setKind] = useState("briefing");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api.addNotebookReport(projectId, notebookId, {
        kind,
        title: title || null,
        text,
      }),
    onSuccess: onDone,
  });

  return (
    <div>
      <label className="stack">
        <span>What is it</span>
        <select className="field" value={kind} onChange={(e) => setKind(e.target.value)}>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {KIND_LABELS[k] ?? k}
            </option>
          ))}
        </select>
      </label>
      <label className="stack">
        <span>Call it (optional)</span>
        <input
          className="field"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label className="stack">
        <span>Paste it here</span>
        <textarea
          className="field"
          rows={8}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <p className="note-actions">
        <button
          className="button"
          disabled={!text.trim() || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Keeping…" : "Keep it"}
        </button>
      </p>
      {save.isError && <p className="notice bad">{(save.error as Error).message}</p>}
    </div>
  );
}

function Kept({ projectId, onChange }: { projectId: string; onChange: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const page = useQuery({
    queryKey: ["notebooks", projectId],
    queryFn: () => api.notebooks(projectId),
  });
  const permission = useQuery({
    queryKey: ["write-permission"],
    queryFn: api.writePermission,
  });

  const file = useMutation({
    mutationFn: () => api.writeNotebookReports(projectId),
    onSuccess: onChange,
  });
  const forget = useMutation({
    mutationFn: (id: string) => api.forgetNotebookReport(projectId, id),
    onSuccess: onChange,
  });

  const reports = page.data?.reports ?? [];
  const unfiled = reports.filter((r) => !r.zotero_note_key);

  return (
    <>
      <h3>4 · What the notebook made</h3>
      {unfiled.length > 0 && (
        <p className="note-actions">
          <button
            className="button"
            disabled={file.isPending || !permission.data?.available}
            onClick={() => file.mutate()}
          >
            {file.isPending
              ? "Writing into Zotero…"
              : `File ${unfiled.length} in Zotero, beside their sources`}
          </button>
        </p>
      )}
      {reports.map((report) => (
        <div className="report-block" key={report.id}>
          <p className="stamp">
            NotebookLM {KIND_LABELS[report.kind] ?? report.kind} — not your reading
          </p>
          <p style={{ margin: 0 }}>
            <strong>{report.title || KIND_LABELS[report.kind] || report.kind}</strong>{" "}
            <span className="meta">
              {report.zotero_note_key ? "in Zotero" : "here only"}
            </span>
          </p>
          {open === report.id ? (
            <>
              {report.text.split("\n\n").map((para, i) => (
                <p key={i}>{para}</p>
              ))}
              <p className="note-actions">
                <button className="button quiet" onClick={() => setOpen(null)}>
                  Close
                </button>
                <button
                  className="button quiet"
                  disabled={forget.isPending}
                  onClick={() => forget.mutate(report.id)}
                >
                  Forget it
                </button>
              </p>
            </>
          ) : (
            <p className="note-actions">
              <button className="button quiet" onClick={() => setOpen(report.id)}>
                Read it
              </button>
            </p>
          )}
        </div>
      ))}
    </>
  );
}
