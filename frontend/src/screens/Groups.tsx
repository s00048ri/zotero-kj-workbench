import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Group, type Project } from "../lib/api";

/* The labelling surface, and the heart of the product.
 *
 * The grouping itself happened in Zotero. What Zotero has no place for is the
 * proposition — the one sentence saying what a group of passages actually
 * claims — and writing one is where the thinking happens. */

function LabelBlock({ group, projectId }: { group: Group; projectId: string }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(!group.label);
  const [label, setLabel] = useState(group.label?.text.split("\n\n")[0] ?? "");
  const [note, setNote] = useState(group.label?.text.split("\n\n")[1] ?? "");

  const save = useMutation({
    mutationFn: () =>
      api.saveLabel(projectId, { path: group.path, label, note }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["groups", projectId] });
      setEditing(false);
    },
  });

  if (!editing && group.label) {
    return (
      <div className="label-block">
        <p className="written">{group.label.text.split("\n\n")[0]}</p>
        {group.label.text.split("\n\n")[1] && (
          <p className="meta" style={{ marginTop: "0.5rem" }}>
            {group.label.text.split("\n\n")[1]}
          </p>
        )}
        <p className="note-actions">
          <button className="button quiet" onClick={() => setEditing(true)}>
            Rewrite this label
          </button>
          <span className="meta">
            {group.label.in_zotero ? "filed in Zotero" : "not yet in Zotero"}
          </span>
        </p>
      </div>
    );
  }

  return (
    <div className="label-block">
      <label className="stack">
        <span>What does this group claim?</span>
        <input
          className="field"
          value={label}
          placeholder="One sentence, in your own words"
          onChange={(e) => setLabel(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && label.trim()) save.mutate();
          }}
        />
      </label>
      <p className="teaching">
        <em>“Competition”</em> is a heading.{" "}
        <em>“The competition frame borrows its urgency from security language”</em>{" "}
        is a label.
      </p>
      <label className="stack" style={{ marginTop: "0.75rem" }}>
        <span>Anything longer you want to say (optional)</span>
        <textarea
          className="field note-field"
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <p className="note-actions">
        <button
          className="button"
          disabled={!label.trim() || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Saving…" : "Write this label"}
        </button>
        {group.label && (
          <button className="button quiet" onClick={() => setEditing(false)}>
            Cancel
          </button>
        )}
      </p>
      {save.isError && <p className="notice bad">{(save.error as Error).message}</p>}
    </div>
  );
}

export default function Groups({
  project,
  onGoToCards,
}: {
  project: Project;
  onGoToCards: () => void;
}) {
  const queryClient = useQueryClient();
  const groups = useQuery({
    queryKey: ["groups", project.id],
    queryFn: () => api.groups(project.id),
  });

  const push = useMutation({
    mutationFn: () => api.pushLabels(project.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["groups", project.id] }),
  });

  if (groups.isLoading) return <p className="spinner">Reading your groups…</p>;
  const summary = groups.data?.summary;

  if (!groups.data?.groups.length) {
    return (
      <div className="column">
        <div className="empty">
          <h3>Grouping happens in Zotero</h3>
          <p>
            Create the notes for your cards, then open Zotero. Under{" "}
            <code>_KJ</code> make a subcollection for each grouping you see, and
            drag notes from <code>_KJ/Inbox</code> into it. Nothing here needs a
            card board: Zotero already has one you know, with search, tags,
            colours and undo.
          </p>
          <p>
            Then re-read the collection on the Project screen — where you put each
            card is read back as your grouping.
          </p>
          {summary && summary.ungrouped > 0 && (
            <p>
              {summary.ungrouped} card{summary.ungrouped === 1 ? "" : "s"} are
              waiting in <code>_KJ/Inbox</code>.
            </p>
          )}
          <p>
            <button className="button quiet" onClick={onGoToCards}>
              Go to the cards
            </button>
          </p>
        </div>
      </div>
    );
  }

  const unlabelled = groups.data.groups.filter((g) => !g.label).length;

  return (
    <div className="column">
      <p className="counter">
        <strong>{summary?.groups}</strong> groups ·{" "}
        <strong>{summary?.labelled}</strong> of them have a label ·{" "}
        <strong>{summary?.ungrouped}</strong> cards still in Inbox
      </p>

      {summary && summary.labelled > 0 && (
        <p>
          <button
            className="button quiet"
            disabled={push.isPending}
            onClick={() => push.mutate()}
          >
            {push.isPending ? "Filing in Zotero…" : "Push labels to Zotero"}
          </button>
          {push.data && (
            <span className="meta"> {push.data.created} filed with their groups</span>
          )}
          {push.isError && (
            <span className="notice bad">{(push.error as Error).message}</span>
          )}
        </p>
      )}

      {unlabelled > 0 && (
        <p className="lede">
          Write the label after grouping, not before. The sentence you have to
          find is the thinking.
        </p>
      )}

      {groups.data.groups.map((group) => (
        <section className="group" key={group.path}>
          <header>
            <h3>{group.name}</h3>
            <span className="meta count">{group.size} cards</span>
          </header>

          <LabelBlock group={group} projectId={project.id} />

          {group.least_alike && (
            <p className="tension">
              Least alike in this group: <strong>{group.least_alike[0]}</strong> and{" "}
              <strong>{group.least_alike[1]}</strong> — they share almost no
              vocabulary, so why did they end up together?
            </p>
          )}

          <ul className="members">
            {group.cards.map((card) => (
              <li key={card.id} data-mine={card.kind === "idea"}>
                <div className="head">
                  <span className="meta">{card.human_id}</span>
                  <span className="meta">
                    {card.kind === "idea" ? "my words" : "source"}
                  </span>
                  {card.color && (
                    <span className="swatch" style={{ background: card.color }} />
                  )}
                  <span className="meta" style={{ marginLeft: "auto" }}>
                    {card.citation}
                    {card.locator_estimated && " (estimated)"}
                  </span>
                </div>
                <div className="text">{card.text}</div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
