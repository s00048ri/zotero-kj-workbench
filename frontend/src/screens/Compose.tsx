import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Project, type PromptOut } from "../lib/api";
import PromptPanel from "../components/PromptPanel";
import SectionPane from "../components/SectionPane";
import WholePaper from "../components/WholePaper";

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
  const adopt = useMutation({
    mutationFn: () => api.adoptGroups(project.id),
    onSuccess: refresh("sections"),
  });
  const move = useMutation({
    mutationFn: ({ id, delta }: { id: string; delta: number }) =>
      api.moveSection(project.id, id, delta),
    onSuccess: refresh("sections"),
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
        and checked against the evidence it was given.
      </p>
      <p className="lede">
        None of it has to be filled in first. Leave the question, the sections
        and the labels blank and they are worked out from your groups and
        marked as proposals; fill any of them in and it is carried through as
        yours.
      </p>

      <WholePaper projectId={project.id} />

      <h3>Or work a piece at a time</h3>
      <ul className="build-list">
        {(["themes", "questions", "outline"] as const).map((kind) => {
          const state = availability.data?.[kind];
          return (
            <li key={kind}>
              <button
                className="button quiet"
                disabled={!state?.ready || build.isPending}
                onClick={() => build.mutate(kind)}
              >
                {KIND_TITLES[kind]}
              </button>
              {state && (
                <span className="meta">
                  {state.blocked_by
                    ? state.blocked_by
                    : `${state.specified} · works out ${state.infers}`}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {build.isError && <p className="notice bad">{(build.error as Error).message}</p>}
      {prompt && (
        <>
          {prompt.note && <p className="meta">{prompt.note}</p>}
          <PromptPanel prompt={prompt} />
        </>
      )}

      <h3>The question this paper answers <span className="meta">optional</span></h3>
      {chosen ? (
        <p className="chosen-question">{chosen.text}</p>
      ) : (
        <p className="lede">
          Leave this and the model proposes one out of your groups, marked as
          its proposal. Choose one here and every prompt keeps it instead.
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

      <h3>Claims <span className="meta">optional</span></h3>
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

      <h3>Sections <span className="meta">optional</span></h3>
      <p className="lede">
        Your groups are already a claim about what belongs with what. Leave
        this and the model reads them that way. Make them into sections and you
        can rename them, put them in the order the argument wants, and say what
        each one uses.
      </p>
      <p className="note-actions">
        <button
          className="button quiet"
          disabled={adopt.isPending}
          onClick={() => adopt.mutate()}
        >
          {adopt.isPending ? "Making sections…" : "Make sections from my groups"}
        </button>
        {adopt.data && (
          <span className="meta">
            {adopt.data.created
              ? `${adopt.data.created} added`
              : "every group already has one"}
          </span>
        )}
      </p>
      <ul className="section-list">
        {(sections.data ?? []).map((section, index, all) => (
          <li key={section.id}>
            <span className="order">
              <button
                className="button quiet"
                aria-label={`Move ${section.title} earlier`}
                disabled={index === 0}
                onClick={() => move.mutate({ id: section.id, delta: -1 })}
              >
                ↑
              </button>
              <button
                className="button quiet"
                aria-label={`Move ${section.title} later`}
                disabled={index === all.length - 1}
                onClick={() => move.mutate({ id: section.id, delta: 1 })}
              >
                ↓
              </button>
            </span>
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

      <h3>Everything, as one file</h3>
      <p className="lede">
        Not the prompt — that is the button at the top. This is the project
        itself: what has been drafted, and the full text of every passage
        behind what has not. Citekeys rather than author-year strings, an
        appendix naming which parts a model drafted, and every gap left open.
      </p>
      <p className="lede">
        The drafting task is written into the top of the file, so handing the
        file to a chat works on its own — material with no task is read and
        reported on, which is reasonable and not what you wanted.
      </p>
      <p className="note-actions">
        <a className="button quiet" href={api.paperUrl(project.id)} download="paper.md">
          Download paper.md
        </a>
        <a
          className="button quiet"
          href={`${api.paperUrl(project.id)}?instructions=false`}
          download="paper.md"
        >
          …without the task, for reading
        </a>
      </p>
    </div>
  );
}
