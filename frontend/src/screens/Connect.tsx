import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

/* What this machine can and cannot do, and what to change if it cannot. */
export default function Connect() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });

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

      <h3>What is read, and what is written</h3>
      <p className="lede">
        This app reads your library and never modifies a highlight, an
        annotation, or an item. When it does write, from the next milestone
        onward, it writes only standalone notes and collections it created
        itself, and only when you ask it to.
      </p>
    </div>
  );
}
