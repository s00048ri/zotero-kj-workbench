import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type DraftResult, type PromptOut } from "../lib/api";
import PromptPanel from "./PromptPanel";
import ValidationReport from "./ValidationReport";

/* From the groups straight to a paper.
 *
 * Nothing has to be specified first. Whatever the researcher has fixed — a
 * question, a section, a label — is carried through as given; everything else
 * the model is asked to propose and to mark as its own. Specifying is how you
 * take a decision back, not a gate you pass before the tool will work. */

export default function WholePaper({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [prompt, setPrompt] = useState<PromptOut | null>(null);
  const [pasted, setPasted] = useState("");
  const [result, setResult] = useState<DraftResult | null>(null);

  const drafts = useQuery({
    queryKey: ["paper-drafts", projectId],
    queryFn: () => api.paperDrafts(projectId),
  });

  const build = useMutation({
    mutationFn: () => api.buildPrompt(projectId, { kind: "paper" }),
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
        in. What you have decided is carried through; the argument, the
        sections and what each one claims are worked out from the passages
        themselves and marked as proposals.
      </p>

      <p className="note-actions">
        <button className="button" disabled={build.isPending} onClick={() => build.mutate()}>
          {build.isPending ? "Building…" : "Build the prompt"}
        </button>
        {drafts.data?.length ? (
          <span className="meta">
            {drafts.data.length} draft{drafts.data.length === 1 ? "" : "s"} pasted
            back so far
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
