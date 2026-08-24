import { useQuery } from "@tanstack/react-query";
import { ApiError, api, type Project } from "../lib/api";

/* Your outline against your evidence.
 *
 * The output that matters is the misfit list. Everything above it is context
 * for reading that list — and the list is cards worth re-reading, never a
 * proposed reorganisation: this sees vocabulary, not argument. */

function Score({ name, value }: { name: string; value: number }) {
  return (
    <div className="score">
      <div className="value">{value.toFixed(2)}</div>
      <div className="name">{name}</div>
    </div>
  );
}

export default function Structure({ project }: { project: Project }) {
  const structure = useQuery({
    queryKey: ["structure", project.id],
    queryFn: () => api.structure(project.id),
  });

  if (structure.isLoading) return <p className="spinner">Comparing…</p>;

  if (structure.isError) {
    const error = structure.error as ApiError;
    return (
      <div className="column">
        <div className="empty">
          <h3>Not enough to compare yet</h3>
          <p>{error.message}</p>
          <p>
            This comparison sets your own chapters against what the card texts
            cluster into. It needs cards in at least two places before it can say
            anything.
          </p>
        </div>
      </div>
    );
  }

  const s = structure.data!;

  return (
    <div className="wide">
      <div className="column" style={{ margin: 0 }}>
        <h2>Your outline against your evidence</h2>
        <p className="lede">
          Comparing {s.cards_used} cards in {s.groups.length} places —{" "}
          {s.basis_label} — against {s.k} clusters found in the card texts
          themselves.
        </p>

        <div className="scores">
          <Score name="Adjusted Rand Index" value={s.ari} />
          <Score name="Normalized Mutual Info" value={s.nmi} />
        </div>
        <p className="lede">
          1.0 means the text falls exactly along your structure. 0.0 means your
          structure and the text agree no more than chance would — which is not a
          verdict on your outline: an argument is not vocabulary.
        </p>

        {s.warning && <p className="notice">{s.warning}</p>}
      </div>

      <h3>Where your places and the clusters overlap</h3>
      <div className="scroll-x">
        <table className="contingency">
          <thead>
            <tr>
              <th />
              {s.clusters.map((c) => (
                <th key={c.index}>cluster {c.index}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {s.groups.map((group, row) => (
              <tr key={group}>
                <th>{group}</th>
                {s.contingency[row].map((count, column) => (
                  <td
                    key={column}
                    style={{
                      background:
                        count > 0
                          ? `color-mix(in srgb, var(--ink) ${Math.min(
                              70,
                              8 + count * 9,
                            )}%, transparent)`
                          : "transparent",
                      color: count > 6 ? "var(--leaf)" : "inherit",
                    }}
                  >
                    {count || ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>What each cluster is made of</h3>
      <div className="column" style={{ margin: 0 }}>
        {s.clusters.map((cluster) => (
          <section key={cluster.index} style={{ marginBottom: "1.5rem" }}>
            <p className="meta">
              cluster {cluster.index} · {cluster.size} cards · mostly{" "}
              {cluster.mostly}
            </p>
            <ul className="members">
              {cluster.nearest.map((card) => (
                <li key={card.human_id} data-mine={card.kind === "idea"}>
                  <div className="head">
                    <span className="meta">{card.human_id}</span>
                    <span className="meta">{card.citation}</span>
                  </div>
                  <div className="text">{card.text}</div>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>

      <div className="column" style={{ margin: 0 }}>
        <h3>Cards worth re-reading</h3>
        <p className="lede">
          {s.misfits.length === 0
            ? "Every card sits with the chapter you filed it under."
            : `${s.misfits.length} cards read as though they belong somewhere else.
               A misfit is not an error: it is either a mis-filing, or a sign that
               a chapter boundary is in the wrong place. Read them and decide.`}
        </p>

        {s.misfits.map((card) => (
          <div className="misfit" key={card.id}>
            <div className="move">
              <span className="meta">{card.human_id}</span> filed in{" "}
              <strong>{card.filed_in}</strong>, reads like{" "}
              <strong>{card.clusters_with}</strong>
            </div>
            <div className="text">{card.text}</div>
            <div className="meta">{card.citation}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
