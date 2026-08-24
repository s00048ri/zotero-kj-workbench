import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  zoteroCollectionUrl,
  type ImportResult,
  type Progress,
  type ProgressStep,
  type Project,
} from "../lib/api";
import CreateNotes from "../components/CreateNotes";

/* The loop, made visible.
 *
 * Every action here existed before this screen did, scattered across the
 * others — and a researcher still granted write permission and then went
 * straight to re-reading, with nothing written to read back. Knowing the
 * steps is not the same as seeing which one you are on. */

const TITLES: Record<ProgressStep["key"], string> = {
  read: "Read your collection",
  notes: "Give each card a note Zotero can file",
  sort: "Sort them into groups — in Zotero",
  label: "Say what each group claims",
  compare: "Set your outline against your evidence",
};

function ImportSummary({ result }: { result: ImportResult }) {
  const s = result.stats;
  const made = s.quote_cards + s.idea_cards + s.image_cards;
  const parts = [
    made ? `${made} new cards` : "nothing new",
    s.updated ? `${s.updated} changed` : null,
    s.placements_read ? `${s.placements_read} of your notes found` : null,
    s.still_in_inbox ? `${s.still_in_inbox} still in Inbox` : null,
    // Notes written by another project, or by an older database. Saying so
    // explains a count that would otherwise look wrong.
    s.unknown_kj_notes
      ? `${s.unknown_kj_notes} notes from elsewhere left alone`
      : null,
  ].filter(Boolean);
  return <span className="meta"> {parts.join(" · ")}</span>;
}

export default function Steps({
  project,
  go,
}: {
  project: Project;
  go: (screen: "cards" | "groups" | "structure" | "project") => void;
}) {
  const queryClient = useQueryClient();
  const [writing, setWriting] = useState(false);

  const progress = useQuery<Progress>({
    queryKey: ["progress", project.id],
    queryFn: () => api.progress(project.id),
  });

  const reread = useMutation({
    mutationFn: () => api.reimport(project.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["progress", project.id] });
      queryClient.invalidateQueries({ queryKey: ["groups", project.id] });
      queryClient.invalidateQueries({ queryKey: ["cards", project.id] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  if (progress.isLoading) return <p className="spinner">Looking at where you are…</p>;
  const p = progress.data!;
  const counts = p.counts;

  const rereadButton = (primary: boolean, label: string) => (
    <button
      className={primary ? "button" : "button quiet"}
      disabled={reread.isPending}
      onClick={() => reread.mutate()}
    >
      {reread.isPending ? "Reading Zotero…" : label}
    </button>
  );

  const actions: Record<ProgressStep["key"], React.ReactNode> = {
    read: (
      <>
        {rereadButton(p.current === "read", "Re-read this collection")}
        {reread.data && <ImportSummary result={reread.data} />}
      </>
    ),
    notes: (
      <>
        <button
          className="button"
          disabled={!counts.pending_notes || !p.writes_available}
          onClick={() => setWriting((v) => !v)}
        >
          {writing
            ? "Close"
            : counts.pending_notes
              ? `Create ${counts.pending_notes} notes in Zotero`
              : "Every card already has a note"}
        </button>
        {!p.writes_available && (
          <span className="meta"> Needs Zotero 10 or newer.</span>
        )}
      </>
    ),
    sort: counts.in_zotero ? (
      <>
        {p.kj_inbox_key && (
          <a className="button" href={zoteroCollectionUrl(p.kj_inbox_key)}>
            Open _KJ/Inbox in Zotero
          </a>
        )}
        {rereadButton(false, "I have sorted them — read it back")}
        {reread.data && <ImportSummary result={reread.data} />}
      </>
    ) : (
      <span className="meta">Nothing is in Zotero yet — step 2 first.</span>
    ),
    label: (
      <button className="button" onClick={() => go("groups")}>
        {counts.group_groups
          ? counts.group_labelled
            ? "Keep writing labels"
            : "Write the labels"
          : "See the Groups screen"}
      </button>
    ),
    compare: (
      <button className="button" onClick={() => go("structure")}>
        Compare
      </button>
    ),
  };

  return (
    <div className="column">
      <h2>{project.name}</h2>
      <p className="lede">
        {project.root_path} · last read{" "}
        {p.last_import_at ? p.last_import_at.slice(0, 16).replace("T", " ") : "never"}
      </p>

      {reread.isError && (
        <p className="notice bad">{(reread.error as Error).message}</p>
      )}

      <ol className="loop">
        {p.steps.map((step, index) => {
          const state = step.done ? "done" : step.key === p.current ? "now" : "later";
          return (
            <li className="step" data-state={state} key={step.key}>
              <span className="marker" aria-hidden="true">
                {step.done ? "✓" : index + 1}
              </span>
              <div className="body">
                <h3>{TITLES[step.key]}</h3>
                <p className="detail">{step.detail}</p>
                {step.key === "sort" && !step.done && counts.in_zotero > 0 && (
                  <p className="detail">
                    In Zotero, make a subcollection under <code>_KJ</code> for each
                    grouping you see and drag notes out of <code>_KJ/Inbox</code>{" "}
                    into it. A note may sit in two places; the theme wins.
                  </p>
                )}
                <div className="step-actions">{actions[step.key]}</div>
                {step.key === "notes" && writing && (
                  <CreateNotes
                    projectId={project.id}
                    selected={[]}
                    onDone={() => {
                      queryClient.invalidateQueries({
                        queryKey: ["progress", project.id],
                      });
                    }}
                  />
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {counts.quotes > 0 && (
        <div className="aside-block">
          <p style={{ margin: 0 }}>
            <strong>{counts.quotes_with_my_note}</strong> of{" "}
            <strong>{counts.quotes}</strong> passages carry a note of your own.
          </p>
          <p className="lede" style={{ margin: "0.4rem 0 0.6rem" }}>
            This is the part no model can supply for you, and the part that is
            usually missing. It can be written at any point in the loop.
          </p>
          <button className="button quiet" onClick={() => go("cards")}>
            Go to the cards
          </button>
        </div>
      )}
    </div>
  );
}
