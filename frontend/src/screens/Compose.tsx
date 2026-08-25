import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Project, type PromptOut } from "../lib/api";
import PromptPanel from "../components/PromptPanel";
import SectionPane from "../components/SectionPane";

/* Question, claims, outline, evidence — and the text that goes to a chat.
 *
 * Nothing is sent from here. What leaves is a block of text the researcher
 * pastes, and what comes back is pasted in and checked against the evidence
 * it was supposed to use. */

const KIND_TITLES: Record<string, string> = {
  themes: "Groups → themes and tensions",
  questions: "Themes → research questions",
  outline: "Outline",
};

export default function Compose({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const [openSection, setOpenSection] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<PromptOut | null>(null);
  const [questionText, setQuestionText] = useState("");
  const [claimText, setClaimText] = useState("");
  const [sectionTitle, setSectionTitle] = useState("");

  const questions = useQuery({
    queryKey: ["questions", project.id],
    queryFn: () => api.questions(project.id),
  });
  const claims = useQuery({
    queryKey: ["claims", project.id],
    queryFn: () => api.claims(project.id),
  });
  const sections = useQuery({
    queryKey: ["sections", project.id],
    queryFn: () => api.sections(project.id),
  });
  const availability = useQuery({
    queryKey: ["prompt-availability", project.id],
    queryFn: () => api.promptAvailability(project.id),
  });

  const refresh = (key: string) => () => {
    queryClient.invalidateQueries({ queryKey: [key, project.id] });
    queryClient.invalidateQueries({ queryKey: ["prompt-availability", project.id] });
    queryClient.invalidateQueries({ queryKey: ["progress", project.id] });
  };

  const addQuestion = useMutation({
    mutationFn: () => api.addQuestion(project.id, { text: questionText }),
    onSuccess: () => {
      setQuestionText("");
      refresh("questions")();
    },
  });
  const chooseQuestion = useMutation({
    mutationFn: (id: string) => api.chooseQuestion(project.id, id),
    onSuccess: refresh("questions"),
  });
  const addClaim = useMutation({
    mutationFn: () => api.addClaim(project.id, { text: claimText }),
    onSuccess: () => {
      setClaimText("");
      refresh("claims")();
    },
  });
  const addSection = useMutation({
    mutationFn: () => api.addSection(project.id, { title: sectionTitle }),
    onSuccess: (created) => {
      setSectionTitle("");
      setOpenSection(created.id);
      refresh("sections")();
    },
  });
  const build = useMutation({
    mutationFn: (kind: string) => api.buildPrompt(project.id, { kind }),
    onSuccess: setPrompt,
  });

  const chosen = questions.data?.find((q) => q.status === "chosen");

  return (
    <div className="column">
      <h2>Compose</h2>
      <p className="lede">
        Nothing here is sent anywhere. Each of these builds a block of text you
        paste into a chat yourself, and the draft that comes back is pasted in
        below and checked against the evidence it was given.
      </p>

      <h3>Prompts you can paste now</h3>
      <div className="chips" style={{ marginBottom: "1rem" }}>
        {(["themes", "questions", "outline"] as const).map((kind) => {
          const state = availability.data?.[kind];
          return (
            <button
              key={kind}
              className="chip build"
              disabled={!state?.ready || build.isPending}
              title={state?.blocked_by ?? "Build this prompt"}
              onClick={() => build.mutate(kind)}
            >
              {KIND_TITLES[kind]}
              {state && !state.ready && (
                <span className="n">— {state.blocked_by}</span>
              )}
            </button>
          );
        })}
      </div>
      {build.isError && <p className="notice bad">{(build.error as Error).message}</p>}
      {prompt && <PromptPanel prompt={prompt} />}

      <h3>The question this paper answers</h3>
      {chosen ? (
        <p className="chosen-question">{chosen.text}</p>
      ) : (
        <p className="lede">
          It comes out of the groups, not before them. Write the candidates you
          are weighing, then choose one.
        </p>
      )}
      {/* the chosen one is shown above; repeating it here is noise */}
      <ul className="preview-list">
        {(questions.data ?? [])
          .filter((q) => q.status !== "chosen")
          .map((q) => (
            <li key={q.id}>
              <button
                className="button quiet"
                onClick={() => chooseQuestion.mutate(q.id)}
              >
                Choose
              </button>{" "}
              {q.text}
            </li>
          ))}
      </ul>
      <p className="note-actions">
        <input
          className="field"
          value={questionText}
          placeholder="A question this collection could actually answer"
          onChange={(e) => setQuestionText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && questionText.trim()) addQuestion.mutate();
          }}
        />
        <button
          className="button quiet"
          disabled={!questionText.trim()}
          onClick={() => addQuestion.mutate()}
        >
          Add
        </button>
      </p>

      <h3>Claims</h3>
      <ul className="preview-list">
        {(claims.data ?? []).map((c) => (
          <li key={c.id}>
            <span className="meta">{c.claim_type}</span> {c.text}
          </li>
        ))}
      </ul>
      <p className="note-actions">
        <input
          className="field"
          value={claimText}
          placeholder="Something this paper argues"
          onChange={(e) => setClaimText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && claimText.trim()) addClaim.mutate();
          }}
        />
        <button
          className="button quiet"
          disabled={!claimText.trim()}
          onClick={() => addClaim.mutate()}
        >
          Add
        </button>
      </p>

      <h3>Sections</h3>
      <ul className="section-list">
        {(sections.data ?? []).map((section) => (
          <li key={section.id}>
            <button
              className="open"
              aria-expanded={openSection === section.id}
              onClick={() =>
                setOpenSection(openSection === section.id ? null : section.id)
              }
            >
              <span className="name">{section.title}</span>
              <span className="meta">
                {section.evidence_count} cards
                {section.purpose ? ` · ${section.purpose.slice(0, 60)}` : ""}
              </span>
            </button>
            {openSection === section.id && (
              <div className="section-body">
                <SectionPane projectId={project.id} section={section} />
              </div>
            )}
          </li>
        ))}
      </ul>
      <p className="note-actions">
        <input
          className="field"
          value={sectionTitle}
          placeholder="A section title"
          onChange={(e) => setSectionTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && sectionTitle.trim()) addSection.mutate();
          }}
        />
        <button
          className="button quiet"
          disabled={!sectionTitle.trim()}
          onClick={() => addSection.mutate()}
        >
          Add section
        </button>
      </p>

      <h3>The paper so far</h3>
      <p className="note-actions">
        <a className="button quiet" href={api.paperUrl(project.id)} download="paper.md">
          Download paper.md
        </a>
        <span className="meta">
          Citekeys, an appendix naming which sections a model drafted, and every
          gap the drafts left open.
        </span>
      </p>
    </div>
  );
}
