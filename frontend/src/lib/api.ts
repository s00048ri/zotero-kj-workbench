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

export class ApiError extends Error {
  remedy: string | null;
  status: number;
  constructor(message: string, status: number, remedy: string | null = null) {
    super(message);
    this.status = status;
    this.remedy = remedy;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const message =
      body?.message ?? body?.detail ?? response.statusText ?? "Request failed";
    throw new ApiError(
      typeof message === "string" ? message : JSON.stringify(message),
      response.status,
      body?.remedy ?? null,
    );
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
};
