import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Card, type FacetValue, type Project } from "../lib/api";
import { useMediaQuery } from "../lib/useMediaQuery";
import CardView from "../components/CardView";

interface Filters {
  search: string;
  kind: string | null;
  color: string | null;
  locator_type: string | null;
  prior_path: string | null;
  source_id: string | null;
  year: string | null;
  has_comment: boolean | null;
  status: string;
}

const EMPTY: Filters = {
  search: "",
  kind: null,
  color: null,
  locator_type: null,
  prior_path: null,
  source_id: null,
  year: null,
  has_comment: null,
  status: "active",
};

function Chips({
  values,
  selected,
  onPick,
}: {
  values: FacetValue[];
  selected: string | null;
  onPick: (value: string | null) => void;
}) {
  return (
    <div className="chips">
      {values.map((v) => (
        <button
          key={v.value ?? "none"}
          className="chip"
          aria-pressed={selected === v.value}
          onClick={() => onPick(selected === v.value ? null : v.value)}
        >
          {v.label ?? "—"}
          <span className="n">{v.count}</span>
        </button>
      ))}
    </div>
  );
}

function groupByPath(cards: Card[]): [string | null, Card[]][] {
  const groups = new Map<string | null, Card[]>();
  for (const card of cards) {
    const key = card.prior_path;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(card);
  }
  return [...groups.entries()];
}

export default function Cards({ project }: { project: Project }) {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    setFilters((f) => ({ ...f, [key]: value }));

  const facets = useQuery({
    queryKey: ["facets", project.id],
    queryFn: () => api.facets(project.id),
  });

  const cards = useQuery({
    queryKey: ["cards", project.id, filters],
    queryFn: () =>
      api.cards(project.id, {
        ...filters,
        status: filters.status === "any" ? "any" : filters.status,
        limit: 300,
      }),
  });

  const counts = cards.data?.counts ?? {};
  const touched = JSON.stringify(filters) !== JSON.stringify(EMPTY);
  // Beside a Zotero window there is no room for a permanent rail, and the
  // cards are what the screen is for — so the filters fold away instead of
  // pushing the reading down the page.
  const roomForRail = useMediaQuery("(min-width: 60rem)");
  // Repeating one folder name on every card is noise; naming it once, where
  // it changes, is the researcher's own outline showing through.
  const showPaths =
    new Set((cards.data?.cards ?? []).map((c) => c.prior_path)).size > 1;

  return (
    <div className="wide">
      <div className="layout">
        <details className="rail" open={roomForRail}>
          <summary>Filters{touched ? " · on" : ""}</summary>
          <h3>Search</h3>
          <input
            className="field"
            type="search"
            value={filters.search}
            placeholder="a word, a phrase"
            onChange={(e) => set("search", e.target.value)}
          />

          <h3>Whose words</h3>
          <Chips
            values={facets.data?.kinds ?? []}
            selected={filters.kind}
            onPick={(v) => set("kind", v)}
          />

          <h3>Your note on it</h3>
          <div className="chips">
            <button
              className="chip"
              aria-pressed={filters.has_comment === true}
              onClick={() => set("has_comment", filters.has_comment === true ? null : true)}
            >
              has my note
            </button>
            <button
              className="chip"
              aria-pressed={filters.has_comment === false}
              onClick={() => set("has_comment", filters.has_comment === false ? null : false)}
            >
              not yet
            </button>
          </div>

          {(facets.data?.colors.length ?? 0) > 0 && (
            <>
              <h3>Highlight colour</h3>
              <div className="swatches">
                {facets.data!.colors.map((c) => (
                  <button
                    key={c.value ?? ""}
                    className="swatch-button"
                    style={{ background: c.value ?? "transparent" }}
                    aria-pressed={filters.color === c.value}
                    aria-label={`${c.count} cards`}
                    title={`${c.count} cards`}
                    onClick={() => set("color", filters.color === c.value ? null : c.value)}
                  />
                ))}
              </div>
            </>
          )}

          <h3>Where it is filed</h3>
          <Chips
            values={facets.data?.prior_paths ?? []}
            selected={filters.prior_path}
            onPick={(v) => set("prior_path", v)}
          />

          <h3>Locator</h3>
          <Chips
            values={facets.data?.locator_types ?? []}
            selected={filters.locator_type}
            onPick={(v) => set("locator_type", v)}
          />

          <h3>Source</h3>
          <select
            className="field"
            value={filters.source_id ?? ""}
            onChange={(e) => set("source_id", e.target.value || null)}
          >
            <option value="">every source</option>
            {facets.data?.sources.map((s) => (
              <option key={s.value ?? ""} value={s.value ?? ""}>
                {s.label} ({s.count})
              </option>
            ))}
          </select>

          <h3>Set aside</h3>
          <div className="chips">
            <button
              className="chip"
              aria-pressed={filters.status === "any"}
              onClick={() => set("status", filters.status === "any" ? "active" : "any")}
            >
              show cards I set aside
            </button>
          </div>

          {touched && (
            <p style={{ marginTop: "1.5rem" }}>
              <button className="button quiet" onClick={() => setFilters(EMPTY)}>
                Clear filters
              </button>
            </p>
          )}
        </details>

        <section className="column" style={{ margin: 0 }}>
          <p className="counter">
            <strong>{counts.quotes ?? 0}</strong> quotes ·{" "}
            <strong>{counts.quotes_with_my_note ?? 0}</strong> of them have your note
            on them · <strong>{counts.ideas ?? 0}</strong> cards in your own words
            {cards.data && cards.data.total !== counts.total && (
              <> · showing {cards.data.total}</>
            )}
          </p>

          {cards.isLoading && <p className="spinner">Reading…</p>}
          {cards.isError && (
            <p className="notice bad">{String((cards.error as Error).message)}</p>
          )}

          {cards.data?.cards.length === 0 && (
            <div className="empty">
              <h3>Nothing here</h3>
              <p>
                {touched
                  ? "No card matches these filters. Clear them to see everything again."
                  : "This project has no cards yet. Highlight something in Zotero's reader, then re-read the collection from the Project screen."}
              </p>
            </div>
          )}

          <div className="cards">
            {groupByPath(cards.data?.cards ?? []).map(([path, group]) => (
              <div className="cards" key={path ?? "—"}>
                {showPaths && (
                  <h3 className="group-heading">{path ?? "not in any collection"}</h3>
                )}
                {group.map((card) => (
                  <CardView key={card.id} card={card} projectId={project.id} />
                ))}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
