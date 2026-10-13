export type Kind = "workflows" | "agents" | "skills";
export type Scope = "builtin" | "user" | "project" | "local";
export interface CatalogItem {
  name: string;
  description: string;
  scope: Scope;
  repo: string | null;
  text: string;
  file?: string;
  directory?: string;
  ejected?: boolean;
  steps?: { id: string; kind: string }[];
  role?: string;
  tools?: string[] | null;
  model?: string | null;
  skills?: string[];
  qualified_name?: string;
  delegate?: boolean;
}
export interface Catalog {
  workflows: CatalogItem[];
  agents: CatalogItem[];
  skills: CatalogItem[];
  repos: string[];
  notes: string[];
  error: string | null;
}
export interface CreatePayload {
  name: string;
  description: string;
  source: string;
  scope: Exclude<Scope, "builtin">;
  repo: string;
}
export interface CreatedItem {
  name: string;
  file: string;
}

export const kinds: {
  key: Kind;
  label: string;
  singular: string;
  description: string;
}[] = [
  {
    key: "workflows",
    label: "Workflows",
    singular: "workflow",
    description: "Choose how work moves from request to delivery.",
  },
  {
    key: "agents",
    label: "Agents",
    singular: "agent",
    description: "Explore the roles that plan, build, and review your work.",
  },
  {
    key: "skills",
    label: "Skills",
    singular: "skill",
    description: "Find reusable instructions for your agents.",
  },
];
export const scopes: { value: Scope; label: string }[] = [
  { value: "builtin", label: "Built in" },
  { value: "project", label: "Project" },
  { value: "local", label: "Local" },
  { value: "user", label: "User" },
];
export const itemKey = (item: CatalogItem) => item.file || item.directory || "";
export function repoName(path: string, repos: string[] = []) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  const others = repos
    .filter((repo) => repo !== path)
    .map((repo) => repo.split(/[\\/]/).filter(Boolean));
  for (let depth = 1; depth <= parts.length; depth++) {
    const label = parts.slice(-depth).join("/");
    if (
      !others.some(
        (other) =>
          other.slice(-depth).join("/").toLowerCase() === label.toLowerCase(),
      )
    )
      return label;
  }
  return path;
}
export const scopeLabel = (scope: Scope) =>
  scopes.find((s) => s.value === scope)?.label || scope;

export async function request<T>(
  url: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || `Request failed (${response.status}).`);
  return result;
}
export function loadCatalog(repo: string, signal?: AbortSignal) {
  return request<Catalog>(
    `/api/catalog${repo ? `?repo=${encodeURIComponent(repo)}` : ""}`,
    { cache: "no-store", signal },
  );
}
