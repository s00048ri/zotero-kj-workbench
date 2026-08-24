import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./lib/api";
import Connect from "./screens/Connect";
import ProjectScreen from "./screens/Project";
import Cards from "./screens/Cards";

type Screen = "connect" | "project" | "cards";

const LAST_PROJECT = "zkj.project";

export default function App() {
  const [screen, setScreen] = useState<Screen>("project");
  const [projectId, setProjectId] = useState<string | null>(
    () => localStorage.getItem(LAST_PROJECT),
  );

  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });

  useEffect(() => {
    if (projectId) localStorage.setItem(LAST_PROJECT, projectId);
  }, [projectId]);

  // A project remembered from a previous session may no longer exist. Only
  // forget it once the list has settled: during a refetch — including the one
  // that follows creating a project — the list is stale by definition.
  useEffect(() => {
    if (!projects.isSuccess || projects.isFetching || !projectId) return;
    if (!projects.data.some((p) => p.id === projectId)) setProjectId(null);
  }, [projects.isSuccess, projects.isFetching, projects.data, projectId]);

  const project = projects.data?.find((p) => p.id === projectId) ?? null;

  const state = !status.data?.reachable || !status.data?.permitted
    ? "down"
    : status.data.writes_available
      ? "ok"
      : "read-only";
  const stateLabel = { down: "Zotero unreachable", ok: "Zotero connected", "read-only": "read-only" }[state];

  return (
    <div className="app">
      <header className="topbar">
        <h1 className="wordmark">Zotero KJ Workbench</h1>
        {project && <span className="meta">{project.name}</span>}
        <nav className="tabs">
          <button
            className="tab"
            aria-current={screen === "project" ? "page" : undefined}
            onClick={() => setScreen("project")}
          >
            Project
          </button>
          <button
            className="tab"
            aria-current={screen === "cards" ? "page" : undefined}
            disabled={!project}
            onClick={() => setScreen("cards")}
          >
            Cards
          </button>
          <button
            className="tab"
            aria-current={screen === "connect" ? "page" : undefined}
            onClick={() => setScreen("connect")}
          >
            Connect
          </button>
        </nav>
        <button
          className="connection"
          onClick={() => setScreen("connect")}
          title={status.data?.message ?? "Checking Zotero…"}
        >
          <span className="dot" data-state={state} />
          {stateLabel}
        </button>
      </header>

      <main>
        {screen === "connect" && <Connect />}
        {screen === "project" && (
          <ProjectScreen
            projects={projects.data ?? []}
            selectedId={projectId}
            onSelect={(id) => {
              setProjectId(id);
              setScreen("cards");
            }}
          />
        )}
        {screen === "cards" &&
          (project ? (
            <Cards project={project} />
          ) : (
            <p className="spinner">Opening the project…</p>
          ))}
      </main>
    </div>
  );
}
