import { request } from "@/lib/catalog";

export type Mode = "cli" | "claude";
export interface Recipe {
  id?: string;
  name: string;
  repo: string;
  mode: Mode;
  command: string;
  request: string;
  workflow: string | null;
  inputs: Record<string, string>;
  arguments: string[];
  options: Record<string, boolean | string>;
}
export interface AttemptSummary {
  id: string;
  state: string;
  updated_at: string;
  exit_code: number | null;
}
export interface SessionSummary {
  id: string;
  name: string;
  repo: string;
  state: string;
  mode: Mode;
  command: string;
  created_at: string;
  updated_at: string;
  archived: boolean;
  active_attempt_id: string | null;
  attempt_count: number;
  last_attempt: AttemptSummary | null;
  schedules?: {
    id: string;
    at: string;
    state: string;
    error?: string | null;
  }[];
}
export interface Attempt {
  id: string;
  state: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  effective_recipe: Recipe;
  exit_code: number | null;
  output_size: number;
  claude_session_id: string | null;
  run_ids: string[];
  error: string | null;
  worker_alive: boolean;
  resume_supported: boolean;
  resume_needs_request: boolean;
}
export interface SessionDetail extends SessionSummary {
  recipe: Recipe;
  attempts: Attempt[];
}
export interface OutputChunk {
  text: string;
  next_cursor: number;
  size: number;
  truncated: boolean;
  reset: boolean;
}
export interface ImportedRun {
  id: string;
  repo: string;
  source: string;
  request: string;
  phase: string;
  created_at: string;
  updated_at: string;
  worktree: string | null;
  branch: string | null;
  feature_name: string | null;
  workflow: {
    name: string;
    scope: string;
    inputs: Record<string, string>;
  } | null;
  plan_file: string | null;
  background_output_available: boolean;
  ui_session_id: string | null;
  ui_attempt_id: string | null;
  resume_supported: boolean;
  resume_needs_request: boolean;
  cancel_supported: boolean;
}
export interface Capabilities {
  available: boolean;
  version: string | null;
  missing: string[];
  permission_policy: "dontAsk" | "dontAsk+none" | "unsupported";
  bypass_supported: boolean;
  error: string | null;
}
export interface Preview {
  valid: true;
  argv: string[];
  display: string;
  warnings: string[];
  capabilities: { cli: { available: boolean }; claude: Capabilities };
}

const query = (repo: string) =>
  repo ? `?repo=${encodeURIComponent(repo)}` : "";
const body = (repo: string, extra: object = {}) =>
  JSON.stringify({ repo, ...extra });
const detailPath = (repo: string, id: string) =>
  `/api/sessions/${encodeURIComponent(id)}${query(repo)}`;

export const listSessions = (repo: string, signal?: AbortSignal) =>
  request<{ sessions: SessionSummary[] }>(`/api/sessions${query(repo)}`, {
    signal,
  });
export const listRuns = (repo: string, signal?: AbortSignal) =>
  request<{ runs: ImportedRun[] }>(`/api/runs${query(repo)}`, { signal });
export const loadSession = (repo: string, id: string, signal?: AbortSignal) =>
  request<{ session: SessionDetail }>(detailPath(repo, id), { signal });
export const loadCapabilities = (repo: string, signal?: AbortSignal) =>
  request<{ cli: { available: boolean }; claude: Capabilities }>(
    `/api/capabilities${query(repo)}`,
    { signal },
  );
export const previewRecipe = (
  repo: string,
  recipe: Partial<Recipe>,
  signal?: AbortSignal,
) =>
  request<Preview>("/api/sessions/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body(repo, { recipe }),
    signal,
  });
export const createSession = (repo: string, recipe: Recipe) =>
  request<{ session: SessionDetail }>("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body(repo, { recipe }),
  });
export const updateSession = (repo: string, id: string, recipe: Recipe) =>
  request<{ session: SessionDetail }>(detailPath(repo, id), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: body(repo, { recipe }),
  });
export const sessionAction = (
  repo: string,
  id: string,
  action:
    | "launch"
    | "resume"
    | "stop"
    | "duplicate"
    | "archive"
    | "unarchive"
    | "schedule"
    | "edit-schedule"
    | "cancel-schedule",
  values: object = {},
) =>
  request<{ session: SessionDetail; attempt?: Attempt }>(
    `/api/sessions/${encodeURIComponent(id)}/${action}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body(repo, values),
    },
  );
export const importedRunAction = (
  repo: string,
  id: string,
  action: "resume" | "cancel",
  values: object = {},
) =>
  request<{ session?: SessionDetail; attempt?: Attempt; run?: ImportedRun }>(
    `/api/runs/${encodeURIComponent(id)}/${action}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body(repo, values),
    },
  );
export const loadOutput = (
  repo: string,
  id: string,
  attempt: string,
  cursor: number,
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ repo, attempt, cursor: String(cursor) });
  return request<OutputChunk>(
    `/api/sessions/${encodeURIComponent(id)}/output?${params}`,
    { cache: "no-store", signal },
  );
};
export const loadRunOutput = (
  repo: string,
  id: string,
  cursor: number,
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ repo, cursor: String(cursor) });
  return request<OutputChunk>(
    `/api/runs/${encodeURIComponent(id)}/output?${params}`,
    {
      cache: "no-store",
      signal,
    },
  );
};
export const outputUrl = (repo: string, id: string, attempt: string) => {
  const params = new URLSearchParams({ repo, attempt });
  return `/api/sessions/${encodeURIComponent(id)}/download?${params}`;
};
export const importedOutputUrl = (repo: string, id: string) =>
  `/api/runs/${encodeURIComponent(id)}/download?repo=${encodeURIComponent(repo)}`;
