import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Card } from "../lib/api";

/* The scarce input. A general model can summarise a literature; it cannot say
 * what you take a passage to mean. So this control gets room, a real writing
 * field, and no cleverness. */

export default function MyNote({
  card,
  projectId,
  onDone,
}: {
  card: Card;
  projectId: string;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const existing = card.linked_ideas.find((i) => i.origin === "annotation_comment");
  const [text, setText] = useState(existing?.text ?? "");
  const [conflict, setConflict] = useState<string | null>(null);
  const [keepLocal, setKeepLocal] = useState(false);

  const save = useMutation({
    mutationFn: (overwrite: boolean) =>
      api.myNote(projectId, card.id, {
        text,
        push_to_zotero: !keepLocal,
        overwrite,
      }),
    onSuccess: () => {
      setConflict(null);
      queryClient.invalidateQueries({ queryKey: ["cards", projectId] });
      onDone();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        const detail = e.detail as { existing?: string } | null;
        setConflict(detail?.existing ?? "");
      }
    },
  });

  return (
    <div className="my-note">
      <label className="stack">
        <span>What do you take this passage to mean?</span>
        <textarea
          className="field note-field"
          rows={4}
          autoFocus
          value={text}
          placeholder="Not a summary — the thing you would say about it in an argument."
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onDone();
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save.mutate(false);
          }}
        />
      </label>

      {conflict !== null && (
        <div className="notice">
          <p>
            This highlight already carries a different comment in Zotero:
          </p>
          <blockquote className="quote">{conflict}</blockquote>
          <p>
            <button className="button" onClick={() => save.mutate(true)}>
              Replace it in Zotero
            </button>{" "}
            <button
              className="button quiet"
              onClick={() => {
                setKeepLocal(true);
                setConflict(null);
              }}
            >
              Keep mine here only
            </button>
          </p>
        </div>
      )}

      {save.isError && conflict === null && (
        <p className="notice bad">{(save.error as Error).message}</p>
      )}

      <p className="note-actions">
        <button
          className="button"
          disabled={!text.trim() || save.isPending}
          onClick={() => save.mutate(false)}
        >
          {save.isPending ? "Saving…" : "Save my note"}
        </button>
        <button className="button quiet" onClick={onDone}>
          Cancel
        </button>
        <label className="inline-check">
          <input
            type="checkbox"
            checked={keepLocal}
            onChange={(e) => setKeepLocal(e.target.checked)}
          />
          keep it here, not in Zotero
        </label>
      </p>
      {!keepLocal && card.origin === "annotation_text" && (
        <p className="aside">
          Saved here and written into this highlight's comment in Zotero, so the
          two agree. The highlighted text itself is never changed.
        </p>
      )}
    </div>
  );
}
