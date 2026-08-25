import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type Card,
  type DraftResult,
  type Evidence,
  type PromptOut,
  type Section,
} from "../lib/api";
import PromptPanel from "./PromptPanel";
import SendToClaude from "./SendToClaude";
import ValidationReport from "./ValidationReport";

const MODES: Evidence["citation_mode"][] = [
  "direct_quote",
  "paraphrase",
  "reference_only",
];
const ROLES = [
  "evidence",
  "counterevidence",
  "background",
  "definition",
  "method",
  "example",
];

const MODE_LABEL: Record<string, string> = {
  direct_quote: "quote it",
  paraphrase: "restate it",
  reference_only: "refer to it",
};

/* One section: what it has to establish, which cards do that work, the prompt
 * that goes out, and the draft that comes back. No dragging — what is being
 * decided is what a passage *does* in an argument, and that is a choice from a
 * short list. */

function EvidencePicker({
  projectId,
  sectionId,
  assigned,
  onAssigned,
}: {
  projectId: string;
  sectionId: string;
  assigned: Set<string>;
  onAssigned: () => void;
}) {
  const [search, setSearch] = useState("");
  const cards = useQuery({
    queryKey: ["cards", projectId, { search, limit: 60 }],
    queryFn: () => api.cards(projectId, { search, limit: 60 }),
  });
  const assign = useMutation({
    mutationFn: (card: Card) =>
      api.assign(projectId, sectionId, card.id, {
        citation_mode: card.kind === "quote" ? "paraphrase" : "reference_only",
      }),
    onSuccess: onAssigned,
  });

  return (
    <div className="picker">
      <input
        className="field"
        type="search"
        value={search}
        placeholder="find a card by a word in it"
        onChange={(e) => setSearch(e.target.value)}
      />
      <ul className="preview-list">
        {(cards.data?.cards ?? [])
          .filter((c) => !assigned.has(c.id) && c.kind !== "image")
          .slice(0, 25)
          .map((card) => (
            <li key={card.id}>
              <button className="button quiet" onClick={() => assign.mutate(card)}>
                Use
              </button>{" "}
              <span className="meta">
                {card.human_id} · {card.kind === "idea" ? "my words" : card.citation}
              </span>{" "}
              {card.text.slice(0, 110)}
            </li>
          ))}
      </ul>
    </div>
  );
}

export default function SectionPane({
  projectId,
  section,
}: {
  projectId: string;
  section: Section;
}) {
  const queryClient = useQueryClient();
  const [prompt, setPrompt] = useState<PromptOut | null>(null);
  const [pasted, setPasted] = useState("");
  const [result, setResult] = useState<DraftResult | null>(null);
  const [picking, setPicking] = useState(false);

  const evidence = useQuery({
    queryKey: ["evidence", projectId, section.id],
    queryFn: () => api.evidence(projectId, section.id),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["evidence", projectId, section.id] });
    queryClient.invalidateQueries({ queryKey: ["sections", projectId] });
    queryClient.invalidateQueries({ queryKey: ["progress", projectId] });
  };

  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.patchSection(projectId, section.id, body),
    onSuccess: refresh,
  });
  const change = useMutation({
    mutationFn: ({ cardId, body }: { cardId: string; body: Record<string, unknown> }) =>
      api.assign(projectId, section.id, cardId, body),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (cardId: string) => api.unassign(projectId, section.id, cardId),
    onSuccess: refresh,
  });
  const build = useMutation({
    mutationFn: () => api.buildPrompt(projectId, { kind: "section", section_id: section.id }),
    onSuccess: setPrompt,
  });
  const paste = useMutation({
    mutationFn: () =>
      api.pasteDraft(projectId, section.id, {
        content: pasted,
        prompt_export_id: prompt?.id ?? null,
      }),
    onSuccess: (r) => {
      setResult(r);
      queryClient.invalidateQueries({ queryKey: ["progress", projectId] });
    },
  });

  const assigned = new Set((evidence.data ?? []).map((e) => e.id));

  return (
    <div>
      <label className="stack">
        <span>What does this section have to establish?</span>
        <textarea
          className="field"
          rows={2}
          defaultValue={section.purpose ?? ""}
          placeholder="Not a topic — the thing a reader must accept by the end of it."
          onBlur={(e) => {
            if (e.target.value !== (section.purpose ?? "")) {
              update.mutate({ purpose: e.target.value });
            }
          }}
        />
      </label>
      <label className="stack">
        <span>Target length (words)</span>
        <input
          className="field"
          type="number"
          defaultValue={section.target_words ?? ""}
          style={{ maxWidth: "10rem" }}
          onBlur={(e) =>
            update.mutate({ target_words: Number(e.target.value) || null })
          }
        />
      </label>

      <h3>
        Evidence{" "}
        <span className="meta">{evidence.data?.length ?? 0} cards</span>
      </h3>

      {(evidence.data ?? []).length === 0 && !picking && (
        <p className="lede">
          Nothing assigned yet. A prompt with no cards would be an invitation to
          invent some, so this section cannot be drafted until it has evidence.
        </p>
      )}

      <ul className="evidence">
        {(evidence.data ?? []).map((card) => (
          <li key={card.id} data-mine={card.kind === "idea"}>
            <div className="head">
              <span className="meta">{card.human_id}</span>
              <span className="meta">
                {card.kind === "idea" ? "my words" : card.citation}
                {card.locator_estimated && " (estimated)"}
              </span>
              <span className="controls">
                <select
                  aria-label="How to use it"
                  value={card.citation_mode}
                  onChange={(e) =>
                    change.mutate({
                      cardId: card.id,
                      body: {
                        citation_mode: e.target.value,
                        argument_role: card.argument_role,
                      },
                    })
                  }
                >
                  {MODES.map((m) => (
                    <option key={m} value={m}>
                      {MODE_LABEL[m]}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="What it is doing"
                  value={card.argument_role}
                  onChange={(e) =>
                    change.mutate({
                      cardId: card.id,
                      body: {
                        citation_mode: card.citation_mode,
                        argument_role: e.target.value,
                      },
                    })
                  }
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
                <button className="button quiet" onClick={() => remove.mutate(card.id)}>
                  Remove
                </button>
              </span>
            </div>
            <div className="text">{card.text}</div>
          </li>
        ))}
      </ul>

      <p className="note-actions">
        <button className="button quiet" onClick={() => setPicking((v) => !v)}>
          {picking ? "Done adding" : "Add evidence"}
        </button>
      </p>

      {picking && (
        <EvidencePicker
          projectId={projectId}
          sectionId={section.id}
          assigned={assigned}
          onAssigned={refresh}
        />
      )}

      <h3>Draft it</h3>
      <p className="note-actions">
        <button
          className="button"
          disabled={!evidence.data?.length || build.isPending}
          onClick={() => build.mutate()}
        >
          {build.isPending ? "Building…" : "Build the prompt"}
        </button>
        <SendToClaude
          projectId={projectId}
          body={{ kind: "section", section_id: section.id }}
        />
        <span className="meta">
          Paste it into a chat, then bring the answer back below.
        </span>
      </p>
      {build.isError && <p className="notice bad">{(build.error as Error).message}</p>}
      {prompt && <PromptPanel prompt={prompt} />}

      <label className="stack" style={{ marginTop: "1.5rem" }}>
        <span>Paste the draft back</span>
        <textarea
          className="field"
          rows={8}
          value={pasted}
          placeholder="The text that came back, markers and all."
          onChange={(e) => setPasted(e.target.value)}
        />
      </label>
      <p className="note-actions">
        <button
          className="button"
          disabled={!pasted.trim() || paste.isPending}
          onClick={() => paste.mutate()}
        >
          {paste.isPending ? "Checking…" : "Check it against the evidence"}
        </button>
      </p>
      {paste.isError && <p className="notice bad">{(paste.error as Error).message}</p>}
      {result && <ValidationReport result={result} />}
    </div>
  );
}
