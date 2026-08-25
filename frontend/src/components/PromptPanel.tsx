import { useState } from "react";
import { copyText, downloadText } from "../lib/clipboard";
import type { PromptOut } from "../lib/api";

/* The deliverable: a block of text complete enough to paste into a chat with
 * nothing else. So the panel shows the whole thing, says how big it is, and
 * the Copy button reports whether it actually copied. */

export default function PromptPanel({ prompt }: { prompt: PromptOut }) {
  const [copied, setCopied] = useState<null | boolean>(null);
  const [open, setOpen] = useState(false);

  return (
    <div className="prompt-panel">
      <div className="prompt-head">
        <strong>{prompt.title}</strong>
        <span className="meta">
          {prompt.chars.toLocaleString()} characters · about{" "}
          {prompt.tokens.toLocaleString()} tokens
        </span>
        <span className="prompt-actions">
          <button
            className="button"
            onClick={async () => {
              const ok = await copyText(prompt.content);
              setCopied(ok);
              setTimeout(() => setCopied(null), 2500);
            }}
          >
            {copied === true ? "Copied" : copied === false ? "Copy failed" : "Copy"}
          </button>
          <button
            className="button quiet"
            onClick={() =>
              downloadText(`${prompt.kind}-prompt.md`, prompt.content)
            }
          >
            Download .md
          </button>
          <button className="button quiet" onClick={() => setOpen((v) => !v)}>
            {open ? "Hide" : "Read it"}
          </button>
        </span>
      </div>

      {copied === false && (
        <p className="notice bad">
          The browser refused the clipboard. Use “Read it” and copy by hand, or
          download the file.
        </p>
      )}
      {prompt.warning && <p className="notice">{prompt.warning}</p>}

      {open && <pre className="prompt-text">{prompt.content}</pre>}
    </div>
  );
}
