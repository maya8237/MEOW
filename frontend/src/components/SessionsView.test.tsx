import { render, screen } from "@testing-library/react";
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
  options: {},
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
              run_ids: ["c".repeat(32)],
              error: null,
            },
          ],
  };
  return { summary, detail, id, attemptId };
}
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });

test("clicking the selected session clears it and allows selecting it again", async () => {
  const user = userEvent.setup();
  const data = fixture();
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/sessions?"))
        return Promise.resolve(json({ sessions: [data.summary] }));
      if (url.startsWith("/api/runs"))
        return Promise.resolve(json({ runs: [] }));
      if (url.startsWith(`/api/sessions/${data.id}?`))
        return Promise.resolve(json({ session: data.detail }));
      throw new Error(`Unexpected ${url}`);
    }),
  );
  render(<SessionsView repo={repo} catalog={catalog} />);
  const row = await screen.findByRole("button", { name: /Search work/ });
  await user.click(row);
  await screen.findByRole("heading", { name: "Search work" });
  expect(row).toHaveAttribute("aria-current", "true");
  await user.click(row);
  expect(row).not.toHaveAttribute("aria-current");
  expect(screen.queryByRole("heading", { name: "Search work" })).toBeNull();
  await user.click(row);
  await screen.findByRole("heading", { name: "Search work" });
});

test("shows aggregate loading feedback while its first request is pending", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>(() => {})),
  );
  render(<SessionsView repo="" catalog={catalog} />);
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
  render(<SessionsView repo={repo} catalog={catalog} />);
  await user.click(await screen.findByRole("button", { name: /Search work/ }));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Loading session details…",
  );
});

test("detail load failure clears loading and offers a retry", async () => {
  const user = userEvent.setup();
  const data = fixture();
  let fail = true;
  let detailRequests = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/sessions?") || url === "/api/sessions")
        return Promise.resolve(json({ sessions: [data.summary] }));
      if (url.startsWith("/api/runs"))
        return Promise.resolve(json({ runs: [] }));
      if (url.startsWith(`/api/sessions/${data.id}?`)) {
        detailRequests += 1;
        return fail
          ? Promise.reject(new Error("Session detail unavailable"))
          : Promise.resolve(json({ session: data.detail }));
      }
      throw new Error(`Unexpected ${url}`);
    }),
  );
  render(<SessionsView repo={repo} catalog={catalog} />);
  await user.click(await screen.findByRole("button", { name: /Search work/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Session detail unavailable",
  );
  expect(screen.queryByText("Loading session details…")).toBeNull();
  fail = false;
  await user.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("heading", { name: "Search work" });
  expect(detailRequests).toBeGreaterThanOrEqual(2);
});

test("archived sessions are hidden until requested and offer no actions", async () => {
  const user = userEvent.setup();
  const data = fixture("needs_decision", true);
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/sessions?") || url === "/api/sessions")
        return Promise.resolve(json({ sessions: [data.summary] }));
      if (url.startsWith("/api/runs"))
        return Promise.resolve(json({ runs: [] }));
      if (url.startsWith(`/api/sessions/${data.id}?`))
        return Promise.resolve(json({ session: data.detail }));
      if (url.includes("/output"))
        return Promise.resolve(
          json({
            text: "",
            next_cursor: 0,
            size: 0,
            truncated: false,
            reset: false,
          }),
        );
      throw new Error(`Unexpected ${url}`);
    }),
  );
  render(<SessionsView repo={repo} catalog={catalog} />);
  await screen.findByText("No sessions yet");
  await user.click(
    screen.getByRole("checkbox", { name: "Show archived sessions" }),
  );
  await user.click(await screen.findByRole("button", { name: /Search work/ }));
  await screen.findByRole("heading", { name: "Search work" });
  for (const name of [
    /Unarchive/,
    /Rerun/,
    /Resume/,
    /Stop/,
    /Edit/,
    /Duplicate/,
  ])
    expect(screen.queryByRole("button", { name })).toBeNull();
});

test("imported runs explain missing history and offer no actions", async () => {
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
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/sessions")) return json({ sessions: [] });
      if (url.startsWith("/api/runs")) return json({ runs: [run] });
      throw new Error(`Unexpected ${url}`);
    }),
  );
  render(<SessionsView repo="" catalog={catalog} />);
  await user.click(await screen.findByRole("button", { name: /Search/ }));
  expect(
    await screen.findByText("No historical output was recorded for this run."),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /rerun|duplicate|cancel/i }),
  ).toBeNull();
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([, options]) => options?.method === "POST"),
  ).toBe(false);
});
