import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Collection, type Project } from "../lib/api";

interface Props {
  projects: Project[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

function flatten(nodes: Collection[]): Collection[] {
  return nodes.flatMap((n) => [n, ...flatten(n.children)]);
}

export default function ProjectScreen({ projects, selectedId, onSelect }: Props) {
  const queryClient = useQueryClient();
  const [chosen, setChosen] = useState<Collection | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections });
  const preview = useQuery({
    queryKey: ["preview", chosen?.key],
    queryFn: () => api.preview(chosen!.key),
    enabled: !!chosen,
  });

  const create = useMutation({
    mutationFn: () =>
      api.createProject({
        name: name.trim(),
        collection_key: chosen!.key,
        use_google_books: false,
      }),
    onSuccess: (result) => {
      setError(null);
      // Put the new project in the cache before switching to it, so the Cards
      // screen never opens against a list that predates it.
      queryClient.setQueryData<Project[]>(["projects"], (old) =>
        old ? [result.project, ...old] : [result.project],
      );
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      onSelect(result.project.id);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  const reimport = useMutation({
    mutationFn: (id: string) => api.reimport(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects"] }),
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  const flat = flatten(collections.data ?? []);
  const counts = preview.data?.counts;

  return (
    <div className="wide">
      {error && <p className="notice bad">{error}</p>}

      {projects.length > 0 && (
        <>
          <h2>Your projects</h2>
          <ul className="tree" style={{ maxHeight: "none", marginBottom: "2.5rem" }}>
            {projects.map((p) => (
              <li key={p.id}>
                <button
                  aria-pressed={p.id === selectedId}
                  onClick={() => onSelect(p.id)}
                >
                  <span>{p.name}</span>
                  <span className="meta">{p.root_path ?? p.root_collection_key}</span>
                  <span className="meta count">
                    {p.counts.quotes ?? 0} quotes · {p.counts.ideas ?? 0} of your own
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <p className="meta">
            {selectedId && (
              <button
                className="button quiet"
                disabled={reimport.isPending}
                onClick={() => reimport.mutate(selectedId)}
              >
                {reimport.isPending ? "Reading Zotero…" : "Re-read this collection"}
              </button>
            )}
            {reimport.data && (
              <> {reimport.data.stats.quote_cards} new quotes, {reimport.data.stats.updated} changed</>
            )}
          </p>
        </>
      )}

      <h2>Start a project from a collection</h2>
      <p className="lede">
        A project is one collection and everything under it. Its subcollections
        are your chapters, and they stay yours: this app reads them, it does not
        rearrange them.
      </p>

      <div className="layout" style={{ marginTop: "1.5rem" }}>
        <div>
          <h3>Collections</h3>
          {collections.isLoading && <p className="spinner">Reading your library…</p>}
          {collections.isError && (
            <p className="notice bad">{String((collections.error as Error).message)}</p>
          )}
          <ul className="tree">
            {flat.map((node) => (
              <li key={node.key}>
                <button
                  aria-pressed={chosen?.key === node.key}
                  style={{ paddingLeft: `${0.4 + node.depth * 0.9}rem` }}
                  onClick={() => {
                    setChosen(node);
                    if (!name.trim()) setName(node.name);
                  }}
                >
                  {node.name}
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div>
          {!chosen && (
            <div className="empty">
              <h3>Pick a collection</h3>
              <p>
                Choose the collection you have been reading in. You will see what
                an import would find before anything is created.
              </p>
            </div>
          )}

          {chosen && (
            <>
              <h3>{chosen.path}</h3>
              {preview.isLoading && <p className="spinner">Counting what is in there…</p>}
              {counts && (
                <>
                  <dl className="stats">
                    <dt>Sources</dt>
                    <dd>{counts.sources}</dd>
                    <dt>Highlights</dt>
                    <dd>{counts.highlights}</dd>
                    <dt>Your comments on them</dt>
                    <dd>{counts.comments}</dd>
                    <dt>Notes on items</dt>
                    <dd>{counts.child_notes}</dd>
                    <dt>Notes filed in collections</dt>
                    <dd>{counts.standalone_notes}</dd>
                    <dt>Subcollections</dt>
                    <dd>{counts.collections - 1}</dd>
                    <dt>Sources you have not highlighted</dt>
                    <dd>{preview.data?.sources_without_annotations}</dd>
                  </dl>

                  {counts.highlights === 0 && (
                    <p className="notice">
                      Nothing is highlighted in this collection yet. Read something
                      in Zotero's reader first — the highlights are the cards.
                    </p>
                  )}

                  {preview.data!.sample_highlights.map((text, i) => (
                    <blockquote
                      key={i}
                      className="quote"
                      style={{ fontFamily: "var(--serif)", marginLeft: 0, paddingLeft: "1rem", borderLeft: "1px solid var(--rule-strong)" }}
                    >
                      {text}
                    </blockquote>
                  ))}

                  <label className="stack" style={{ marginTop: "1.5rem" }}>
                    <span>Project name</span>
                    <input
                      className="field"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="agentic-governance"
                    />
                  </label>
                  <button
                    className="button"
                    disabled={!name.trim() || create.isPending}
                    onClick={() => create.mutate()}
                  >
                    {create.isPending ? "Reading Zotero…" : "Make cards from this collection"}
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
