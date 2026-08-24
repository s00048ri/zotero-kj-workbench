import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

/* Four hundred notes appearing in a library is frightening without an undo,
 * so every batch this app wrote is listed here and can be taken back whole. */

export default function Batches({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const batches = useQuery({
    queryKey: ["batches", projectId],
    queryFn: () => api.batches(projectId),
  });
  const revert = useMutation({
    mutationFn: (batchId: string) => api.revertBatch(projectId, batchId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["batches", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["cards", projectId] });
    },
  });

  if (!batches.data?.length) return null;

  return (
    <>
      <h3>Notes this app wrote into Zotero</h3>
      <ul className="preview-list" style={{ marginBottom: "2.5rem" }}>
        {batches.data.map((batch) => (
          <li key={batch.id}>
            <span className="meta">{batch.created_at.slice(0, 16).replace("T", " ")}</span>{" "}
            {batch.notes} {batch.kind}
            {batch.failures > 0 && (
              <span className="meta"> · {batch.failures} failed</span>
            )}{" "}
            {batch.reverted_at ? (
              <span className="meta">taken back</span>
            ) : (
              <button
                className="button quiet"
                disabled={revert.isPending}
                onClick={() => revert.mutate(batch.id)}
              >
                Take back
              </button>
            )}
          </li>
        ))}
      </ul>
      {revert.isError && <p className="notice bad">{(revert.error as Error).message}</p>}
    </>
  );
}
