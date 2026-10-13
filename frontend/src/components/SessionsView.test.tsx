import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { Catalog } from "@/lib/catalog";
import { SessionDetail as SessionRecord, SessionSummary } from "@/lib/sessions";
import SessionsView from "./SessionsView";

const repo = "C:/work/project";
const catalog = {
  repos: [repo],
  workflows: [],
  agents: [],
  skills: [],
  notes: [],
  error: null,
} as unknown as Catalog;
const recipe = {
  name: "Search",
  repo,
  mode: "cli" as const,
  command: "run",
  request: "Improve search",
  workflow: null,
  inputs: {},
  arguments: [],
  options: { unattended: true },
};
function fixture(state = "draft", archived = false) {
  const attemptState = state === "needs_decision" ? "needs_decision" : state;
  const id = "a".repeat(32),
    attemptId = "b".repeat(32);
  const summary: SessionSummary = {
    id,
    name: "Search work",
    repo,
    state,
    mode: "cli",
    command: "run",
    created_at: "2026-10-13",
    updated_at: "2026-10-13",
    archived,
    active_attempt_id: ["running", "starting"].includes(state)
      ? attemptId
      : null,
    attempt_count: state === "draft" ? 0 : 1,
    last_attempt:
      state === "draft"
        ? null
        : {
            id: attemptId,
            state: attemptState,
            updated_at: "2026-10-13",
            exit_code: null,
          },
  };
  const detail: SessionRecord = {
    ...summary,
    recipe,
    attempts:
      state === "draft"
        ? []
        : [
            {
              id: attemptId,
              state: attemptState,
              created_at: "2026-10-13",
              started_at: "2026-10-13",
              finished_at: null,
              effective_recipe: recipe,
              exit_code: null,
              output_size: 0,
              claude_session_id: null,
              run_ids: ["c".repeat(32)],
              error: null,
              worker_alive: ["running", "starting"].includes(state),
            },
          ],
  };
  return { summary, detail, id, attemptId };
}
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });

test("shows aggregate loading feedback while its first request is pending", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>(() => {})),
  );
  render(<SessionsView repo="" catalog={catalog} onRunRecipe={vi.fn()} />);
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Loading sessions…",
  );
  expect(screen.queryByRole("heading", { name: "No sessions yet" })).toBeNull();
});

test("shows session detail loading feedback after selecting a record", async () => {
  const user = userEvent.setup();
  const data = fixture();
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/sessions?") || url === "/api/sessions")
        return Promise.resolve(json({ sessions: [data.summary] }));
      if (url.startsWith("/api/runs"))
        return Promise.resolve(json({ runs: [] }));
      if (url.startsWith(`/api/sessions/${data.id}?`))
        return new Promise<Response>(() => {});
      throw new Error(`Unexpected ${url}`);
    }),
  );
  render(<SessionsView repo={repo} catalog={catalog} onRunRecipe={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: /Search work/ }));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Loading session details…",
  );
});

test("edit, rerun, duplicate, archive, and unarchive use distinct actions", async () => {
  const user = userEvent.setup();
  const data = fixture();
  let archived = false;
  let current = data.summary;
  let currentDetail = data.detail;
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (
        url.startsWith("/api/sessions?") ||
        (url === "/api/sessions" && options?.method !== "POST")
      )
        return json({ sessions: [{ ...current, archived }] });
      if (url.startsWith("/api/runs")) return json({ runs: [] });
      if (url.startsWith(`/api/sessions/${current.id}?`))
        return json({ session: { ...currentDetail, archived } });
      if (url.endsWith("/duplicate")) {
        current = {
          ...current,
          id: "d".repeat(32),
          name: "Copy of Search work",
        };
        currentDetail = { ...currentDetail, ...current, attempts: [] };
        return json({ session: currentDetail }, 201);
      }
      if (url.endsWith("/archive")) {
        archived = true;
        return json({ session: currentDetail });
      }
      if (url.endsWith("/unarchive")) {
        archived = false;
        return json({ session: currentDetail });
      }
      throw new Error(`Unexpected ${options?.method || "GET"} ${url}`);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  const onRunRecipe = vi.fn();
  render(<SessionsView repo="" catalog={catalog} onRunRecipe={onRunRecipe} />);
  await user.click(await screen.findByRole("button", { name: /Search work/ }));
  await screen.findByRole("heading", { name: "Search work" });
  await user.click(screen.getByRole("button", { name: "Edit" }));
  expect(onRunRecipe).toHaveBeenLastCalledWith(
    data.detail.recipe,
    undefined,
    data.id,
    false,
    true,
  );
  await user.click(screen.getByRole("button", { name: "Rerun" }));
  expect(onRunRecipe).toHaveBeenLastCalledWith(
    data.detail.recipe,
    undefined,
    data.id,
  );
  await user.click(screen.getByRole("button", { name: "Duplicate" }));
  await screen.findByRole("heading", { name: "Copy of Search work" });
  expect(
    fetchMock.mock.calls.some(([url]) => String(url).endsWith("/duplicate")),
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Archive" }));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).endsWith("/archive")),
    ).toBe(true),
  );
  await user.click(
    screen.getByRole("checkbox", { name: "Show archived sessions" }),
  );
  await screen.findByRole("button", { name: "Unarchive" });
  await user.click(screen.getByRole("button", { name: "Unarchive" }));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).endsWith("/unarchive")),
    ).toBe(true),
  );
});

test("resume sends the clarified request and repair choice", async () => {
  const user = userEvent.setup();
  const data = fixture("needs_decision");
  const calls: [string, string][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (options?.method === "POST") {
        calls.push([url, String(options.body)]);
        return json({ session: data.detail });
      }
      if (url.startsWith("/api/sessions?") || url === "/api/sessions")
        return json({ sessions: [data.summary] });
      if (url.startsWith("/api/runs")) return json({ runs: [] });
      if (url.startsWith(`/api/sessions/${data.id}?`))
        return json({ session: data.detail });
      throw new Error(`Unexpected ${url}`);
    }),
  );
  render(<SessionsView repo={repo} catalog={catalog} onRunRecipe={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: /Search work/ }));
  await user.type(
    await screen.findByLabelText("Clarified request for resume"),
    "Use the safer option",
  );
  await user.click(
    screen.getByRole("checkbox", { name: "Resume in repair mode" }),
  );
  await user.click(screen.getByRole("button", { name: "Resume" }));
  await waitFor(() =>
    expect(calls.some(([url]) => url.endsWith("/resume"))).toBe(true),
  );
  expect(
    JSON.parse(calls.find(([url]) => url.endsWith("/resume"))![1]),
  ).toMatchObject({
    repo,
    attempt_id: data.attemptId,
    request: "Use the safer option",
    repair: true,
  });
});

test("Stop targets the selected running session", async () => {
  const user = userEvent.setup();
  const data = fixture("running");
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (options?.method === "POST") {
        calls.push(url);
        return json({ session: data.detail, attempt: data.detail.attempts[0] });
      }
      if (url.startsWith("/api/sessions?") || url === "/api/sessions")
        return json({ sessions: [data.summary] });
      if (url.startsWith("/api/runs")) return json({ runs: [] });
      if (url.startsWith(`/api/sessions/${data.id}?`))
        return json({ session: data.detail });
      throw new Error(`Unexpected ${url}`);
    }),
  );
  render(<SessionsView repo={repo} catalog={catalog} onRunRecipe={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: /Search work/ }));
  await user.click(await screen.findByRole("button", { name: "Stop" }));
  await waitFor(() =>
    expect(calls.some((url) => url.endsWith("/stop"))).toBe(true),
  );
});

test("imported runs explain missing history and open editable recipes with unknown flags", async () => {
  const user = userEvent.setup();
  const run = {
    id: "e".repeat(32),
    repo,
    source: "cli",
    request: "Improve search",
    phase: "succeeded",
    created_at: "2026-10-13",
    updated_at: "2026-10-13",
    worktree: null,
    branch: "work/search",
    feature_name: "Search",
    workflow: { name: "quick", scope: "project", inputs: { area: "ui" } },
    plan_file: null,
    background_output_available: false,
    ui_session_id: null,
    ui_attempt_id: null,
    resume_supported: false,
    cancel_supported: false,
  };
  let draftSummary: SessionSummary | null = null;
  let draftDetail: SessionRecord | null = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (
        (url.startsWith("/api/sessions?") || url === "/api/sessions") &&
        options?.method !== "POST"
      )
        return json({ sessions: draftSummary ? [draftSummary] : [] });
      if (url.startsWith("/api/runs")) return json({ runs: [run] });
      if (url === "/api/sessions" && options?.method === "POST") {
        const parsed = JSON.parse(String(options.body));
        const base = fixture();
        draftSummary = {
          ...base.summary,
          id: "f".repeat(32),
          name: parsed.recipe.name,
          repo: parsed.recipe.repo,
        };
        draftDetail = {
          ...base.detail,
          ...draftSummary,
          recipe: parsed.recipe,
          attempts: [],
        };
        return json({ session: draftDetail }, 201);
      }
      if (draftDetail && url.startsWith(`/api/sessions/${draftDetail.id}?`))
        return json({ session: draftDetail });
      throw new Error(`Unexpected ${url}`);
    }),
  );
  const onRunRecipe = vi.fn();
  render(<SessionsView repo="" catalog={catalog} onRunRecipe={onRunRecipe} />);
  await user.click(await screen.findByRole("button", { name: /Search/ }));
  expect(
    await screen.findByText("No historical output was recorded for this run."),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Prepare rerun" }));
  expect(onRunRecipe).toHaveBeenCalledWith(
    expect.objectContaining({
      workflow: "quick",
      inputs: { area: "ui" },
      arguments: [],
    }),
    undefined,
    undefined,
    true,
  );
  await user.click(screen.getByRole("button", { name: "Duplicate as draft" }));
  expect(onRunRecipe).toHaveBeenLastCalledWith(
    expect.objectContaining({
      name: "Search",
      repo,
      workflow: "quick",
      inputs: { area: "ui" },
      arguments: [],
      options: { unattended: true, no_worktree: false },
    }),
    undefined,
    undefined,
    true,
  );
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([, options]) => options?.method === "POST"),
  ).toBe(false);
});
