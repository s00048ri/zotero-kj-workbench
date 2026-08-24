import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type MaterializeResult } from "../lib/api";

/* Turning cards into notes Zotero can hold.
 *
 * A Zotero annotation cannot belong to a collection, so a highlight has to
 * become a standalone note before it can be dragged anywhere. That is what
 * this does, and why the grouping happens afterwards, in Zotero. */

export default function CreateNotes({
  projectId,
  selected,
  onDone,
}: {
  projectId: string;
  selected: string[];
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [preview, setPreview] = useState<MaterializeResult | null>(null);
  const [result, setResult] = useState<MaterializeResult | null>(null);

  const permission = useQuery({
    queryKey: ["write-permission"],
    queryFn: api.writePermission,
  });
  const pending = useQuery({
    queryKey: ["pending", projectId],
    queryFn: () => api.pending(projectId),
  });

  const cardIds = selected.length ? selected : null;
  const count = selected.length || pending.data?.count || 0;

  const dryRun = useMutation({
    mutationFn: () => api.createNotes(projectId, { card_ids: cardIds, dry_run: true }),
    onSuccess: setPreview,
  });

  const authorize = useMutation({
    mutationFn: api.authorize,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["write-permission"] }),
  });

  const create = useMutation({
    mutationFn: () => api.createNotes(projectId, { card_ids: cardIds }),
    onSuccess: (r) => {
      setResult(r);
      setPreview(null);
      queryClient.invalidateQueries({ queryKey: ["cards", projectId] });
      queryClient.invalidateQueries({ queryKey: ["pending", projectId] });
      queryClient.invalidateQueries({ queryKey: ["batches", projectId] });
      onDone();
    },
  });

  const revert = useMutation({
    mutationFn: (batchId: string) => api.revertBatch(projectId, batchId),
    onSuccess: () => {
      setResult(null);
      queryClient.invalidateQueries({ queryKey: ["cards", projectId] });
      queryClient.invalidateQueries({ queryKey: ["pending", projectId] });
    },
  });

  if (permission.data && !permission.data.available) {
    return <p className="notice">{permission.data.message}</p>;
  }

  return (
    <div>
      {permission.data && !permission.data.remembered && (
        <div className="dialog-note">
          <p style={{ margin: 0 }}>{permission.data.message}</p>
          <p style={{ margin: "0.5rem 0 0" }}>
            <button
              className="button"
              disabled={authorize.isPending}
              onClick={() => authorize.mutate()}
            >
              {authorize.isPending ? "Waiting for Zotero…" : "Ask Zotero now"}
            </button>
          </p>
          {authorize.isError && (
            <p className="notice bad">{(authorize.error as Error).message}</p>
          )}
        </div>
      )}

      <p>
        <button
          className="button quiet"
          disabled={!count || dryRun.isPending}
          onClick={() => dryRun.mutate()}
        >
          {dryRun.isPending ? "Checking…" : `Show me what would be created (${count})`}
        </button>{" "}
        <button
          className="button"
          disabled={!count || create.isPending}
          onClick={() => create.mutate()}
        >
          {create.isPending
            ? "Writing into Zotero…"
            : `Create ${count} note${count === 1 ? "" : "s"} in Zotero`}
        </button>
      </p>

      {create.isError && <p className="notice bad">{(create.error as Error).message}</p>}

      {preview && (
        <div className="dialog-note">
          <p style={{ margin: 0 }}>
            Nothing has been written. These {preview.preview.length} notes would be
            created:
          </p>
          <ul className="preview-list">
            {preview.preview.slice(0, 12).map((item) => (
              <li key={item.human_id}>
                <span className="meta">{item.human_id}</span> → {item.destination}{" "}
                <span className="meta">{item.citation}</span>
              </li>
            ))}
            {preview.preview.length > 12 && (
              <li className="meta">…and {preview.preview.length - 12} more</li>
            )}
          </ul>
        </div>
      )}

      {result && (
        <div className="dialog-note">
          <p style={{ margin: 0 }}>
            {result.created} note{result.created === 1 ? "" : "s"} created.{" "}
            {Object.entries(result.destinations)
              .map(([where, n]) => `${n} in ${where}`)
              .join(", ")}
            .
          </p>
          {result.failures.length > 0 && (
            <ul className="preview-list">
              {result.failures.map((f) => (
                <li key={f.human_id}>
                  <span className="meta">{f.human_id}</span> — {f.error}
                </li>
              ))}
            </ul>
          )}
          <p style={{ margin: "0.5rem 0 0" }}>
            Now open Zotero, make a subcollection under <code>_KJ</code> for each
            grouping you see, and drag the notes into it. Then re-read the
            collection here: where you put each card is read back as your grouping.
          </p>
          {result.batch_id && (
            <p style={{ margin: "0.5rem 0 0" }}>
              <button
                className="button quiet"
                disabled={revert.isPending}
                onClick={() => revert.mutate(result.batch_id!)}
              >
                {revert.isPending ? "Removing…" : "Take this batch back"}
              </button>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
