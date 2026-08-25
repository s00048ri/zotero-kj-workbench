import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type SendResult } from "../lib/api";
import ValidationReport from "./ValidationReport";

/* Posting the prompt instead of pasting it.
 *
 * Off unless it is turned on, and it changes nothing else: the same prompt
 * goes out, and what comes back is checked by the same validator against the
 * same evidence. Copying remains the way to see exactly what is sent before it
 * goes. */

export default function SendToClaude({
  projectId,
  body,
  label = "Send it to Claude",
}: {
  projectId: string;
  body: { kind: string; section_id?: string; mode?: string; quoting?: string };
  label?: string;
}) {
  const queryClient = useQueryClient();
  const [result, setResult] = useState<SendResult | null>(null);

  const llm = useQuery({ queryKey: ["llm"], queryFn: api.llm });

  const send = useMutation({
    mutationFn: () => api.send(projectId, body),
    onSuccess: (r) => {
      setResult(r);
      queryClient.invalidateQueries({ queryKey: ["progress", projectId] });
      queryClient.invalidateQueries({ queryKey: ["paper-drafts", projectId] });
    },
  });

  if (!llm.data?.ready) return null;

  return (
    <>
      <button
        className="button quiet"
        disabled={send.isPending}
        onClick={() => send.mutate()}
      >
        {send.isPending ? "Claude is writing…" : label}
      </button>

      {send.isError && (
        <p className="notice bad">
          {(send.error as ApiError).message}
          {(send.error as ApiError).status === 409 && " Set it up on the Connect screen."}
        </p>
      )}

      {result && (
        <div style={{ width: "100%" }}>
          <p className="meta">
            {result.llm.model} · {result.llm.input_tokens.toLocaleString()} in ·{" "}
            {result.llm.output_tokens.toLocaleString()} out · about $
            {result.llm.cost_usd.toFixed(2)}
            {result.draft && ` · saved as draft v${result.draft.version}`}
          </p>
          {result.llm.warnings.map((w, i) => (
            <p className="notice" key={i}>
              {w}
            </p>
          ))}
          {result.llm.refusal && <p className="notice bad">{result.llm.refusal}</p>}
          {result.content && (
            <details className="draft-details">
              <summary>Read what came back</summary>
              <div className="draft-read">{result.content}</div>
            </details>
          )}
          {result.validation && result.markdown && (
            <ValidationReport
              result={{
                validation: result.validation,
                draft: result.draft,
                markdown: result.markdown,
              }}
            />
          )}
        </div>
      )}
    </>
  );
}
