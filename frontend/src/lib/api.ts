/* The only place the interface knows the shape of the backend. */

export interface ConnectionStatus {
  reachable: boolean;
  permitted: boolean;
  api_version: string | null;
  zotero_version: string | null;
  server_id: string | null;
  schema_version: string | null;
  writes_available: boolean;
  collection_count: number | null;
  message: string;
  remedy: string | null;
}

export interface Collection {
  key: string;
  name: string;
  path: string;
  depth: number;
  parent_key: string | null;
  children: Collection[];
}

export interface CollectionPreview {
  key: string;
  name: string;
  path: string;
  counts: Record<string, number>;
  unreadable_attachments: number;
  sources_without_annotations: number;
  sample_highlights: string[];
}

export interface Project {
  id: string;
  name: string;
  root_collection_key: string;
  root_path: string | null;
  zotero_server_id: string | null;
  research_question: string | null;
  created_at: string;
  last_import_at: string | null;
  counts: Record<string, number>;
  writable_here: boolean;
}

export interface ImportResult {
  project: Project;
  stats: Record<string, number>;
}

export interface Locator {
  type: "page" | "chapter" | "cfi" | "none";
  value: string | null;
  source: string;
  estimated: boolean;
  rendered: string;
  estimated_page: number | null;
  detail: Record<string, unknown>;
}

export interface LinkedCard {
  id: string;
  human_id: string;
  kind: string;
  origin: string | null;
  text: string;
}

export interface Card {
  id: string;
  human_id: string;
  kind: "quote" | "idea" | "image";
  origin: string;
  text: string;
  text_raw: string | null;
  human_label: string | null;
  color: string | null;
  status: string;
  prior_path: string | null;
  prior_ambiguous: boolean;
  kj_path: string | null;
  zotero_note_key: string | null;
  origin_note_key: string | null;
  materialized_at: string | null;
  citation: string;
  source: {
    id: string | null;
    key: string | null;
    title: string | null;
    creators_short: string | null;
    year: string | null;
    publication_title: string | null;
  } | null;
  locator: Locator;
  linked_ideas: LinkedCard[];
  parent: LinkedCard | null;
}

export interface CardPage {
  cards: Card[];
  total: number;
  counts: Record<string, number>;
}

export interface FacetValue {
  value: string | null;
  label: string | null;
  count: number;
}

export interface Facets {
  sources: FacetValue[];
  years: FacetValue[];
  colors: FacetValue[];
  kinds: FacetValue[];
  origins: FacetValue[];
  locator_types: FacetValue[];
  prior_paths: FacetValue[];
  groups: FacetValue[];
}

export interface WritePermission {
  available: boolean;
  remembered: boolean;
  message: string;
}

export interface PendingCards {
  count: number;
  by_kind: Record<string, number>;
}

export interface MaterializeResult {
  batch_id: string | null;
  created: number;
  destinations: Record<string, number>;
  failures: { human_id: string; error: string }[];
  dialogs_shown: number;
  dry_run: boolean;
  preview: {
    human_id: string;
    kind: string;
    destination: string;
    citation: string;
    text: string;
  }[];
}

export interface WriteBatch {
  id: string;
  kind: string;
  created_at: string;
  reverted_at: string | null;
  notes: number;
  failures: number;
}

export interface GroupCard {
  id: string;
  human_id: string;
  kind: string;
  origin: string;
  text: string;
  citation: string;
  locator_estimated: boolean;
  color: string | null;
}

export interface Group {
  path: string;
  name: string;
  collection_key: string | null;
  size: number;
  least_alike: [string, string] | null;
  label: { id: string; human_id: string; text: string; in_zotero: boolean } | null;
  cards: GroupCard[];
}

export interface GroupsPage {
  groups: Group[];
  summary: {
    groups: number;
    labelled: number;
    cards_grouped: number;
    ungrouped: number;
  };
}

export interface Structure {
  basis: string;
  basis_label: string;
  cards_used: number;
  groups: string[];
  k: number;
  ari: number;
  nmi: number;
  contingency: number[][];
  clusters: {
    index: number;
    size: number;
    mostly: string;
    nearest: { human_id: string; kind: string; text: string; citation: string }[];
  }[];
  misfits: {
    id: string;
    human_id: string;
    kind: string;
    text: string;
    citation: string;
    filed_in: string;
    clusters_with: string;
  }[];
  degenerate: boolean;
  warning: string | null;
}

export interface ProgressStep {
  key: "read" | "notes" | "sort" | "label" | "compare" | "question" | "write";
  done: boolean;
  detail: string;
  count: number;
  /** Never what the loop points at: a way of taking control, not a gate. */
  optional: boolean;
}

export interface Progress {
  current: ProgressStep["key"];
  steps: ProgressStep[];
  counts: Record<string, number>;
  kj_root_key: string | null;
  kj_inbox_key: string | null;
  writes_available: boolean;
  last_import_at: string | null;
}

/** Jump straight to a collection in the Zotero window. */
export function zoteroCollectionUrl(key: string): string {
  return `zotero://select/library/collections/${key}`;
}

export interface Question {
  id: string;
  text: string;
  rationale: string | null;
  status: "candidate" | "chosen" | "set_aside";
  origin: string;
}

export interface Claim {
  id: string;
  text: string;
  claim_type: string;
  research_question_id: string | null;
}

export interface Section {
  id: string;
  title: string;
  purpose: string | null;
  thesis: string | null;
  target_words: number | null;
  sort_order: number;
  evidence_count: number;
}

export interface Evidence {
  id: string;
  human_id: string;
  kind: string;
  origin: string;
  text: string;
  citation: string;
  locator_estimated: boolean;
  citation_mode: "direct_quote" | "paraphrase" | "reference_only";
  argument_role: string;
  user_instruction: string | null;
  kj_path: string | null;
}

export interface PromptOut {
  id: string | null;
  kind: string;
  title: string;
  section_id: string | null;
  content: string;
  chars: number;
  tokens: number;
  warning: string | null;
  /** What this prompt will work out for itself, given what you left blank. */
  note: string | null;
}

export interface PromptAvailability {
  [kind: string]: {
    ready: boolean;
    blocked_by: string | null;
    infers: string;
    specified: string;
  };
}

export interface Finding {
  kind: string;
  severity: "stop" | "look";
  message: string;
  human_id: string | null;
  detail: string | null;
}

export interface ValidationOut {
  cited: string[];
  unknown: string[];
  unused: {
    human_id: string;
    kind: string;
    citation_mode: string;
    argument_role: string;
    text: string;
  }[];
  evidence_needed: string[];
  unsupported: string[];
  findings: Finding[];
  rendered: string;
  stats: Record<string, number | string>;
  clean: boolean;
}

export interface DraftResult {
  validation: ValidationOut;
  draft: { id: string; version: number } | null;
  markdown: string;
}

export class ApiError extends Error {
  remedy: string | null;
  status: number;
  detail: unknown;
  constructor(
    message: string,
    status: number,
    remedy: string | null = null,
    detail: unknown = null,
  ) {
    super(message);
    this.status = status;
    this.remedy = remedy;
    this.detail = detail;
  }
}

/** The app's own backend being unreachable is status 0 — a different problem
 *  from any HTTP error, and the one a raw "Failed to fetch" hides. */
export const OFFLINE = 0;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(
      `The workbench itself is not answering at ${window.location.origin}.`,
      OFFLINE,
      "Check that it is still running — `python -m zkj` in the project " +
        "directory — and that this page is on the port it is serving.",
    );
  }
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = body?.detail;
    const message =
      body?.message ??
      (typeof detail === "string" ? detail : detail?.message) ??
      response.statusText ??
      "Request failed";
    throw new ApiError(String(message), response.status, body?.remedy ?? null, detail);
  }
  return body as T;
}

function query(params: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const q = search.toString();
  return q ? `?${q}` : "";
}

export const api = {
  status: () => request<ConnectionStatus>("/api/status"),
  collections: () => request<Collection[]>("/api/collections"),
  preview: (key: string) => request<CollectionPreview>(`/api/collections/${key}/preview`),
  projects: () => request<Project[]>("/api/projects"),
  createProject: (body: { name: string; collection_key: string; use_google_books: boolean }) =>
    request<ImportResult>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  reimport: (id: string, useGoogleBooks = false) =>
    request<ImportResult>(
      `/api/projects/${id}/import${query({ use_google_books: useGoogleBooks })}`,
      { method: "POST" },
    ),
  cards: (id: string, filters: Record<string, string | number | boolean | null | undefined>) =>
    request<CardPage>(`/api/projects/${id}/cards${query(filters)}`),
  facets: (id: string) => request<Facets>(`/api/projects/${id}/facets`),
  patchCard: (projectId: string, cardId: string, body: Record<string, unknown>) =>
    request<Card>(`/api/projects/${projectId}/cards/${cardId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  myNote: (
    projectId: string,
    cardId: string,
    body: { text: string; push_to_zotero?: boolean; overwrite?: boolean },
  ) =>
    request<Card>(`/api/projects/${projectId}/cards/${cardId}/my-note`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  writePermission: () => request<WritePermission>("/api/write-permission"),
  authorize: () =>
    request<WritePermission>("/api/write-permission", { method: "POST" }),
  forgetPermission: () =>
    request<WritePermission>("/api/write-permission", { method: "DELETE" }),

  pending: (projectId: string) =>
    request<PendingCards>(`/api/projects/${projectId}/pending`),
  createNotes: (
    projectId: string,
    body: { card_ids?: string[] | null; kinds?: string[]; dry_run?: boolean },
  ) =>
    request<MaterializeResult>(`/api/projects/${projectId}/notes`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  batches: (projectId: string) =>
    request<WriteBatch[]>(`/api/projects/${projectId}/batches`),
  revertBatch: (projectId: string, batchId: string) =>
    request<{ deleted: number; already_gone: number; failures: string[] }>(
      `/api/projects/${projectId}/batches/${batchId}/revert`,
      { method: "POST" },
    ),

  groups: (projectId: string) => request<GroupsPage>(`/api/projects/${projectId}/groups`),
  saveLabel: (projectId: string, body: { path: string; label: string; note?: string }) =>
    request<{ id: string; human_id: string; text: string }>(
      `/api/projects/${projectId}/groups/label`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  pushLabels: (projectId: string) =>
    request<MaterializeResult>(`/api/projects/${projectId}/groups/push`, {
      method: "POST",
    }),

  progress: (projectId: string) => request<Progress>(`/api/projects/${projectId}/progress`),
  deleteProject: (projectId: string) =>
    request<{ deleted: boolean; notes_left_in_zotero: number }>(
      `/api/projects/${projectId}`,
      { method: "DELETE" },
    ),

  structure: (projectId: string, params: { basis?: string; k?: number } = {}) =>
    request<Structure>(`/api/projects/${projectId}/structure${query(params)}`),

  questions: (projectId: string) =>
    request<Question[]>(`/api/projects/${projectId}/questions`),
  addQuestion: (projectId: string, body: { text: string; rationale?: string }) =>
    request<Question>(`/api/projects/${projectId}/questions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  chooseQuestion: (projectId: string, questionId: string) =>
    request<Question>(`/api/projects/${projectId}/questions/${questionId}/choose`, {
      method: "POST",
    }),
  deleteQuestion: (projectId: string, questionId: string) =>
    request<{ deleted: boolean }>(`/api/projects/${projectId}/questions/${questionId}`, {
      method: "DELETE",
    }),

  claims: (projectId: string) => request<Claim[]>(`/api/projects/${projectId}/claims`),
  addClaim: (projectId: string, body: { text: string; claim_type?: string }) =>
    request<Claim>(`/api/projects/${projectId}/claims`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteClaim: (projectId: string, claimId: string) =>
    request<{ deleted: boolean }>(`/api/projects/${projectId}/claims/${claimId}`, {
      method: "DELETE",
    }),

  sections: (projectId: string) =>
    request<Section[]>(`/api/projects/${projectId}/sections`),
  addSection: (projectId: string, body: { title: string; purpose?: string }) =>
    request<Section>(`/api/projects/${projectId}/sections`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  adoptGroups: (projectId: string) =>
    request<{ created: number; sections: Section[] }>(
      `/api/projects/${projectId}/sections/adopt-groups`,
      { method: "POST" },
    ),
  moveSection: (projectId: string, sectionId: string, delta: number) =>
    request<Section[]>(
      `/api/projects/${projectId}/sections/${sectionId}/move${query({ delta })}`,
      { method: "POST" },
    ),
  patchSection: (projectId: string, sectionId: string, body: Record<string, unknown>) =>
    request<Section>(`/api/projects/${projectId}/sections/${sectionId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteSection: (projectId: string, sectionId: string) =>
    request<{ deleted: boolean }>(`/api/projects/${projectId}/sections/${sectionId}`, {
      method: "DELETE",
    }),

  evidence: (projectId: string, sectionId: string) =>
    request<Evidence[]>(`/api/projects/${projectId}/sections/${sectionId}/evidence`),
  assign: (
    projectId: string,
    sectionId: string,
    cardId: string,
    body: {
      citation_mode?: string;
      argument_role?: string;
      user_instruction?: string;
      include?: boolean;
    },
  ) =>
    request<unknown>(
      `/api/projects/${projectId}/sections/${sectionId}/evidence/${cardId}`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  unassign: (projectId: string, sectionId: string, cardId: string) =>
    request<{ deleted: boolean }>(
      `/api/projects/${projectId}/sections/${sectionId}/evidence/${cardId}`,
      { method: "DELETE" },
    ),

  promptAvailability: (projectId: string) =>
    request<PromptAvailability>(`/api/projects/${projectId}/prompts`),
  buildPrompt: (
    projectId: string,
    body: {
      kind: string;
      section_id?: string;
      mode?: "draft" | "assess";
      quoting?: "model" | "quote" | "ideas";
    },
  ) =>
    request<PromptOut>(`/api/projects/${projectId}/prompts`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  pasteDraft: (
    projectId: string,
    sectionId: string,
    body: { content: string; prompt_export_id?: string | null; save?: boolean },
  ) =>
    request<DraftResult>(`/api/projects/${projectId}/sections/${sectionId}/draft`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  drafts: (projectId: string, sectionId: string) =>
    request<
      { id: string; version: number; created_at: string; content: string }[]
    >(`/api/projects/${projectId}/sections/${sectionId}/drafts`),

  pastePaper: (
    projectId: string,
    body: { content: string; prompt_export_id?: string | null; save?: boolean },
  ) =>
    request<DraftResult>(`/api/projects/${projectId}/draft`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  paperDrafts: (projectId: string) =>
    request<{ id: string; version: number; created_at: string }[]>(
      `/api/projects/${projectId}/drafts`,
    ),

  paperUrl: (projectId: string) => `/api/projects/${projectId}/paper.md`,
};
