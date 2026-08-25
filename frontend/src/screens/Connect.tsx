import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, OFFLINE, api } from "../lib/api";

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

  const [key, setKey] = useState("");
  const llm = useQuery({ queryKey: ["llm"], queryFn: api.llm });
  const setLlmKey = useMutation({
    mutationFn: () => api.setLlmKey(key),
    onSuccess: () => {
      setKey("");
      queryClient.invalidateQueries({ queryKey: ["llm"] });
    },
  });
  const clearLlmKey = useMutation({
    mutationFn: api.clearLlmKey,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["llm"] }),
  });

  if (status.isLoading) return <p className="spinner">Asking Zotero…</p>;

  // With the workbench itself unreachable, whatever is still in the cache is
  // out of date — and showing "Connected to Zotero" above a banner saying
  // nothing is answering is worse than showing nothing.
  if (status.error instanceof ApiError && status.error.status === OFFLINE) {
    return (
      <div className="column">
        <h2>Connection</h2>
        <p className="lede">
          Nothing can be reported until the workbench is answering again. What
          was on screen before is out of date.
        </p>
      </div>
    );
  }

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

      <h3>Sending prompts to Claude <span className="meta">optional</span></h3>
      <p className="lede">
        Copying the prompt into a chat is the way this app is meant to be used:
        it costs nothing, needs no key, and lets you read exactly what is being
        sent before it goes. Turning this on only changes who does the pasting —
        the same prompt goes out, and what comes back is checked against the
        same evidence by the same code.
      </p>
      <p className={llm.data?.ready ? "lede" : "notice"}>
        {llm.data?.reason}
        {llm.data?.remedy && (
          <>
            <br />
            {llm.data.remedy}
          </>
        )}
      </p>
      {llm.data && (
        <dl className="stats">
          <dt>Model</dt>
          <dd>{llm.data.model}</dd>
          {llm.data.source && (
            <>
              <dt>Credentials from</dt>
              <dd>{llm.data.source}</dd>
            </>
          )}
          {llm.data.base_url && (
            <>
              <dt>Endpoint</dt>
              <dd>{llm.data.base_url}</dd>
            </>
          )}
        </dl>
      )}
      {llm.data?.ready ? (
        <p className="note-actions">
          <button
            className="button quiet"
            disabled={clearLlmKey.isPending}
            onClick={() => clearLlmKey.mutate()}
          >
            Forget the key
          </button>
        </p>
      ) : (
        <>
          <label className="stack" style={{ maxWidth: "28rem" }}>
            <span>Anthropic API key, for this run only</span>
            <input
              className="field"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={key}
              placeholder="sk-ant-…"
              onChange={(e) => setKey(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && key.trim()) setLlmKey.mutate();
              }}
            />
          </label>
          <p className="note-actions">
            <button
              className="button quiet"
              disabled={!key.trim() || setLlmKey.isPending}
              onClick={() => setLlmKey.mutate()}
            >
              Use this key
            </button>
            <span className="meta">
              Held in memory until the workbench stops. Not written to the
              database, not written to a file, not logged.
            </span>
          </p>
          {setLlmKey.isError && (
            <p className="notice bad">{(setLlmKey.error as Error).message}</p>
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
