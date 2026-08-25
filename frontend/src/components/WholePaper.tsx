import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type DraftResult, type PromptOut } from "../lib/api";
import PromptPanel from "./PromptPanel";
import SendToClaude from "./SendToClaude";
import ValidationReport from "./ValidationReport";

/* From the groups straight to a paper.
 *
 * Nothing has to be specified first. Whatever the researcher has fixed — a
 * question, a section, a label — is carried through as given; everything else
 * the model is asked to propose and to mark as its own. Specifying is how you
 * take a decision back, not a gate you pass before the tool will work. */

type Mode = "draft" | "assess";
type Quoting = "model" | "quote" | "ideas";

const QUOTING_LABEL: Record<Quoting, string> = {
  model: "let it choose per passage",
  quote: "quote the sources",
  ideas: "take the ideas only",
};

export default function WholePaper({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<Mode>("draft");
  const [quoting, setQuoting] = useState<Quoting>("model");
  const [prompt, setPrompt] = useState<PromptOut | null>(null);
  const [pasted, setPasted] = useState("");
  const [result, setResult] = useState<DraftResult | null>(null);

  const drafts = useQuery({
    queryKey: ["paper-drafts", projectId],
    queryFn: () => api.paperDrafts(projectId),
  });

  const build = useMutation({
    mutationFn: () => api.buildPrompt(projectId, { kind: "paper", mode, quoting }),
    onSuccess: setPrompt,
  });

  const paste = useMutation({
    mutationFn: () =>
      api.pastePaper(projectId, {
        content: pasted,
        prompt_export_id: prompt?.id ?? null,
      }),
    onSuccess: (r) => {
      setResult(r);
      queryClient.invalidateQueries({ queryKey: ["paper-drafts", projectId] });
      queryClient.invalidateQueries({ queryKey: ["progress", projectId] });
    },
  });

  return (
    <section className="whole-paper">
      <h3>Write the whole paper</h3>
      <p className="lede">
        One prompt built from every card you have, in the groups you put them
        in. Your groups are what sets the paper going — not a set to be
        exhausted. What you have decided is carried through; the rest is worked
        out from the passages and marked as proposals.
      </p>

      <div className="chips" style={{ marginBottom: "0.75rem" }}>
        <button
          className="chip"
          aria-pressed={mode === "draft"}
          onClick={() => setMode("draft")}
        >
          write the paper
        </button>
        <button
          className="chip"
          aria-pressed={mode === "assess"}
          onClick={() => setMode("assess")}
        >
          tell me what this can answer
        </button>
      </div>

      {mode === "draft" && (
        <div className="chips" style={{ marginBottom: "0.75rem" }}>
          {(["model", "quote", "ideas"] as const).map((q) => (
            <button
              key={q}
              className="chip"
              aria-pressed={quoting === q}
              onClick={() => setQuoting(q)}
            >
              {QUOTING_LABEL[q]}
            </button>
          ))}
        </div>
      )}

      <p className="note-actions">
        <button className="button" disabled={build.isPending} onClick={() => build.mutate()}>
          {build.isPending
            ? "Building…"
            : mode === "draft"
              ? "Build the prompt"
              : "Build the reading"}
        </button>
        <SendToClaude
          projectId={projectId}
          body={{ kind: "paper", mode, quoting }}
          label={mode === "draft" ? "Send it to Claude" : "Ask Claude"}
        />
        {drafts.data?.length ? (
          <span className="meta">
            {drafts.data.length} draft{drafts.data.length === 1 ? "" : "s"} kept so far
          </span>
        ) : null}
      </p>
      {build.isError && <p className="notice bad">{(build.error as Error).message}</p>}
      {prompt && (
        <>
          {prompt.note && <p className="meta">{prompt.note}</p>}
          <PromptPanel prompt={prompt} />
        </>
      )}

      <label className="stack">
        <span>Paste the paper back</span>
        <textarea
          className="field"
          rows={8}
          value={pasted}
          placeholder="Everything that came back, markers and all."
          onChange={(e) => setPasted(e.target.value)}
        />
      </label>
      <p className="note-actions">
        <button
          className="button"
          disabled={!pasted.trim() || paste.isPending}
          onClick={() => paste.mutate()}
        >
          {paste.isPending ? "Checking…" : "Check it against every card"}
        </button>
        <span className="meta">
          Every quotation against its source, every restatement against the
          original's wording.
        </span>
      </p>
      {paste.isError && <p className="notice bad">{(paste.error as Error).message}</p>}
      {result && <ValidationReport result={result} />}
    </section>
  );
}
