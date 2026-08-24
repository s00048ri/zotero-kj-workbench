import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Card, type LinkedCard } from "../lib/api";
import MyNote from "./MyNote";

/* Whose words these are is carried by the ground and the typeface, not by a
 * label — the label only confirms what the eye already read. */

function isJapanese(text: string) {
  return /[぀-ヿ一-鿿]/.test(text);
}

function Voice({ mine }: { mine: boolean }) {
  return <span className="voice">{mine ? "my words" : "source"}</span>;
}

function LinkedIdea({ idea }: { idea: LinkedCard }) {
  return (
    <article className="card mine">
      <div className="card-head">
        <span className="id">{idea.human_id}</span>
        <Voice mine />
      </div>
      <p className="body" lang={isJapanese(idea.text) ? "ja" : undefined}>
        {idea.text}
      </p>
    </article>
  );
}

export default function CardView({
  card,
  projectId,
  selected,
  onSelect,
}: {
  card: Card;
  projectId: string;
  selected?: boolean;
  onSelect?: (id: string, selected: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [writing, setWriting] = useState(false);
  const [label, setLabel] = useState(card.human_label ?? "");
  const hasMyNote = card.linked_ideas.some((i) => i.origin === "annotation_comment");

  const mine = card.kind === "idea";
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.patchCard(projectId, card.id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cards", projectId] }),
  });

  const body = (
    <article
      className={[
        "card",
        mine ? "mine" : "",
        card.status === "excluded" ? "excluded" : "",
      ].join(" ")}
    >
      <div className="card-head">
        {onSelect && (
          <input
            type="checkbox"
            checked={!!selected}
            aria-label={`Select ${card.human_id}`}
            onChange={(e) => onSelect(card.id, e.target.checked)}
          />
        )}
        <span className="id">{card.human_id}</span>
        <Voice mine={mine} />
        {card.color && <span className="swatch" style={{ background: card.color }} />}
        {card.zotero_note_key && (
          <span className="meta" title="This card is a note in Zotero">
            {card.kj_path ? card.kj_path.split("/").pop() : "in Inbox"}
          </span>
        )}
        <span className="actions">
          {!mine && (
            <button className="button quiet" onClick={() => setWriting((v) => !v)}>
              {hasMyNote ? "Edit my note" : "Add my note"}
            </button>
          )}
          <button className="button quiet" onClick={() => setEditing((v) => !v)}>
            {card.human_label ? "Edit heading" : "Add a heading"}
          </button>
          <button
            className="button quiet"
            onClick={() =>
              patch.mutate({ status: card.status === "excluded" ? "active" : "excluded" })
            }
          >
            {card.status === "excluded" ? "Bring back" : "Set aside"}
          </button>
        </span>
      </div>

      {card.human_label && !editing && <p className="label-line">{card.human_label}</p>}

      {editing && (
        <div style={{ marginBottom: "0.8rem" }}>
          <input
            className="field"
            value={label}
            autoFocus
            placeholder="A few words to find this card by"
            onChange={(e) => setLabel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                patch.mutate({ human_label: label.trim() });
                setEditing(false);
              }
              if (e.key === "Escape") setEditing(false);
            }}
          />
          <p className="meta" style={{ marginTop: "0.3rem" }}>
            Your own heading for this card. It does not change the quotation.
          </p>
        </div>
      )}

      {mine ? (
        <p className="body" lang={isJapanese(card.text) ? "ja" : undefined}>
          {card.text}
        </p>
      ) : (
        <blockquote className="quote" lang={isJapanese(card.text) ? "ja" : undefined}>
          {card.text}
        </blockquote>
      )}

      {writing && (
        <MyNote card={card} projectId={projectId} onDone={() => setWriting(false)} />
      )}

      <div className="card-foot">
        {card.citation && <cite className="citation">{card.citation}</cite>}
        {card.locator.estimated && (
          <span className="meta estimated">
            estimated — verify before citing
          </span>
        )}
        {card.locator.type === "none" && card.kind === "quote" && (
          <span className="meta">no page recorded</span>
        )}
        {card.source?.title && <span className="source-title">{card.source.title}</span>}
        {card.parent && <span className="meta">my reading of {card.parent.human_id}</span>}
      </div>
    </article>
  );

  if (!card.linked_ideas.length) return body;

  return (
    <div className="linked">
      {body}
      {/* the signature element: the note is tied to the passage it answers */}
      {card.linked_ideas.map((idea) => (
        <div className="tie" key={idea.id}>
          <LinkedIdea idea={idea} />
        </div>
      ))}
    </div>
  );
}
