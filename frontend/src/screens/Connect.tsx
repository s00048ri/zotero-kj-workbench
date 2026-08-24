import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

/* What this machine can and cannot do, and what to change if it cannot. */
export default function Connect() {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const permission = useQuery({
    queryKey: ["write-permission"],
    queryFn: api.writePermission,
  });
  const authorize = useMutation({
    mutationFn: api.authorize,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["write-permission"] }),
  });
  const forget = useMutation({
    mutationFn: api.forgetPermission,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["write-permission"] }),
  });

  if (status.isLoading) return <p className="spinner">Asking Zotero…</p>;
  const s = status.data;
  if (!s) return <p className="notice bad">{String(status.error)}</p>;

  const bad = !s.reachable || !s.permitted;

  return (
    <div className="column">
      <h2>Connection</h2>
      <p className={bad ? "notice bad" : s.writes_available ? "lede" : "notice"}>
        {s.message}
        {s.remedy && (
          <>
            <br />
            {s.remedy}
          </>
        )}
      </p>

      <dl className="stats">
        <dt>Zotero</dt>
        <dd>{s.zotero_version ?? "—"}</dd>
        <dt>Local API</dt>
        <dd>{s.api_version ?? "—"}</dd>
        <dt>Database</dt>
        <dd>{s.server_id ?? "—"}</dd>
        <dt>Schema</dt>
        <dd>{s.schema_version ?? "—"}</dd>
        <dt>Collections</dt>
        <dd>{s.collection_count ?? "—"}</dd>
        <dt>Notes can be written</dt>
        <dd>{s.writes_available ? "yes" : "no"}</dd>
      </dl>

      {s.writes_available && (
        <>
          <h3>Permission to write notes</h3>
          <p className="lede">{permission.data?.message}</p>
          <p>
            {permission.data?.remembered ? (
              <button
                className="button quiet"
                disabled={forget.isPending}
                onClick={() => forget.mutate()}
              >
                Forget this permission
              </button>
            ) : (
              <button
                className="button"
                disabled={authorize.isPending}
                onClick={() => authorize.mutate()}
              >
                {authorize.isPending
                  ? "Waiting for you in Zotero…"
                  : "Ask Zotero for permission"}
              </button>
            )}
          </p>
          {authorize.isError && (
            <p className="notice bad">{(authorize.error as Error).message}</p>
          )}
        </>
      )}

      <h3>What is read, and what is written</h3>
      <p className="lede">
        This app never changes a highlighted passage, and never deletes
        anything you did not ask it to delete. What it writes, when you ask: a
        standalone note for each card, the <code>_KJ</code> collections those
        notes live in, and — only if you write one — your own comment on a
        highlight. Every batch of notes can be taken back whole.
      </p>
    </div>
  );
}
