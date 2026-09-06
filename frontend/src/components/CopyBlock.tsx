import { useState } from "react";
import { copyText } from "../lib/clipboard";

/* One block of text to carry across, with a Copy that says whether it worked.
 *
 * A Copy button that silently fails is worse than no button: the researcher
 * pastes the last thing they copied into NotebookLM and finds out much later. */

export default function CopyBlock({
  label,
  hint,
  text,
  rows = 6,
  disabled = false,
}: {
  label: string;
  hint?: string;
  text: string;
  rows?: number;
  disabled?: boolean;
}) {
  const [copied, setCopied] = useState<boolean | null>(null);
  const [open, setOpen] = useState(false);

  return (
    <div className="carry-block">
      <p className="note-actions">
        <strong>{label}</strong>
        <button
          className="button"
          disabled={disabled || !text}
          onClick={async () => {
            setCopied(await copyText(text));
            setTimeout(() => setCopied(null), 3000);
          }}
        >
          {copied === true ? "Copied" : copied === false ? "Copy failed" : "Copy"}
        </button>
        <button
          className="button quiet"
          disabled={!text}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "Hide" : "Show"}
        </button>
      </p>
      {hint && <p className="teaching">{hint}</p>}
      {copied === false && (
        <p className="notice">
          The browser refused the clipboard. Use “Show” and copy by hand.
        </p>
      )}
      {open && <textarea className="field" readOnly rows={rows} value={text} />}
    </div>
  );
}
