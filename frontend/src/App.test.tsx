import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import App from "./App";
import { DOCS_SITE_URL } from "@/lib/links";

const workflow = {
  name: "quick",
  description: "Plan and implement directly.",
  scope: "builtin",
  repo: null,
  file: "/builtin/quick.toml",
  ejected: false,
  steps: [
    { id: "plan", kind: "builtin" },
    { id: "rounds", kind: "loop" },
  ],
  text: 'description = "Quick"',
};
const catalog = {
  error: null,
  notes: [],
  repos: ["/repo"],
  workflows: [
    workflow,
    {
      ...workflow,
      name: "nightly",
      scope: "project",
      file: "/repo/.meow/workflows/nightly.toml",
      repo: "/repo",
    },
  ],
  agents: [
    {
      name: "explorer",
      description: "Explore the code.",
      scope: "builtin",
      repo: null,
      file: "/builtin/explorer.toml",
      text: "Explorer source",
      ejected: false,
      delegate: false,
      role: "explorer",
      tools: null,
      model: null,
      skills: [],
    },
  ],
  skills: [],
  hooks: [
    {
      name: "guard",
      description: "Block edits to generated files.",
      scope: "project",
      repo: "/repo",
      file: "/repo/.meow/hooks/guard.toml",
      text: "event = 'PreToolUse'",
      event: "PreToolUse",
      matcher: "Write|Edit",
      command: "python guard.py",
      timeout: 30,
      roles: [],
      synced: true,
    },
  ],
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(catalog))),
  );
});

test("scope and text filters narrow the catalog and can be cleared", async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.selectOptions(screen.getByLabelText("Scope"), "project");
  expect(screen.getByRole("heading", { name: "nightly" })).toBeInTheDocument();
  await user.type(screen.getByRole("searchbox"), "missing");
  expect(screen.getByText("No matches found")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.getByRole("heading", { name: "quick" })).toBeInTheDocument();
});

test("the catalog is read-only and points customization to Claude Code", async () => {
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  expect(screen.getByText("/meow:customize")).toBeInTheDocument();
  for (const name of [/^New/, /^Run /, /^Edit/, /^Make a copy/])
    expect(screen.queryByRole("button", { name })).toBeNull();
});

test("a catalog connection failure offers a working retry", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockRejectedValueOnce(new Error("Offline"))
      .mockResolvedValue(new Response(JSON.stringify(catalog))),
  );
  const user = userEvent.setup();
  render(<App />);
  await screen.findByText(/Offline/);
  await user.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("heading", { name: "quick" });
});

test("a stale refresh cannot replace the newly selected repository", async () => {
  const user = userEvent.setup();
  let resolveOld!: (value: Response) => void;
  let calls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("repo="))
        return new Response(
          JSON.stringify({
            ...catalog,
            repos: ["/repo", "/other"],
            workflows: [{ ...workflow, name: "other-workflow" }],
          }),
        );
      if (++calls === 2)
        return new Promise<Response>((resolve) => {
          resolveOld = resolve;
        });
      return new Response(
        JSON.stringify({ ...catalog, repos: ["/repo", "/other"] }),
      );
    }),
  );
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: "Refresh catalog" }));
  await user.selectOptions(screen.getByLabelText("Repository"), "/other");
  await screen.findByRole("heading", { name: "other-workflow" });
  await act(async () => resolveOld(new Response(JSON.stringify(catalog))));
  expect(
    screen.getByRole("heading", { name: "other-workflow" }),
  ).toBeInTheDocument();
});

test("clipboard errors give a recovery message", async () => {
  const user = userEvent.setup();
  vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(
    new Error("Denied"),
  );
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: "Copy source" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Select the text and copy it manually",
  );
});

test("Sessions aggregates repositories and disambiguates duplicate basenames", async () => {
  const user = userEvent.setup();
  const repos = ["C:/teams/alpha/project", "D:/teams/beta/project"];
  const makeSession = (id: string, repo: string, name: string) => ({
    id,
    name,
    repo,
    state: "draft",
    mode: "cli",
    command: "run",
    created_at: "2026-10-13T10:00:00Z",
    updated_at: "2026-10-13T10:00:00Z",
    archived: false,
    active_attempt_id: null,
    attempt_count: 0,
    last_attempt: null,
  });
  const sessions = [
    makeSession("a".repeat(32), repos[0], "Alpha work"),
    makeSession("b".repeat(32), repos[1], "Beta work"),
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/catalog"))
        return new Response(JSON.stringify({ ...catalog, repos }));
      if (url.startsWith("/api/sessions?") || url === "/api/sessions") {
        const selected = new URL(url, "http://local").searchParams.get("repo");
        return new Response(
          JSON.stringify({
            sessions: selected
              ? sessions.filter((item) => item.repo === selected)
              : sessions,
          }),
        );
      }
      if (url.startsWith("/api/runs"))
        return new Response(JSON.stringify({ runs: [] }));
      if (url.startsWith("/api/sessions/")) {
        const id = url.split("/")[3].split("?")[0];
        const entry = sessions.find((item) => item.id === id)!;
        return new Response(
          JSON.stringify({
            session: {
              ...entry,
              recipe: {
                name: entry.name,
                repo: entry.repo,
                mode: "cli",
                command: "run",
                request: "",
                workflow: null,
                inputs: {},
                arguments: [],
                options: {},
              },
              attempts: [],
            },
          }),
        );
      }
      throw new Error(`Unexpected URL ${url}`);
    }),
  );
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: "Sessions" }));
  expect(await screen.findByText("Alpha work")).toBeInTheDocument();
  expect(screen.getByText("Beta work")).toBeInTheDocument();
  expect(screen.getAllByText("alpha/project").length).toBeGreaterThan(0);
  expect(screen.getAllByText("beta/project").length).toBeGreaterThan(0);
  await user.selectOptions(screen.getByLabelText("Repository"), repos[0]);
  expect(await screen.findByText("Alpha work")).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.queryByText("Beta work")).not.toBeInTheDocument(),
  );
});

test.each(["Workflows", "Agents", "Skills"])(
  "returns from Sessions to the %s catalog",
  async (kind) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        return new Response(
          JSON.stringify(
            url.startsWith("/api/catalog")
              ? catalog
              : url.startsWith("/api/runs")
                ? { runs: [] }
                : { sessions: [] },
          ),
        );
      }),
    );
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "quick" });
    const navigation = within(
      screen.getByRole("navigation", { name: "Catalog kinds" }),
    );
    await user.click(navigation.getByRole("button", { name: "Sessions" }));
    await screen.findByRole("heading", { name: "Sessions" });
    await user.click(
      navigation.getByRole("button", { name: new RegExp(kind) }),
    );
    expect(screen.getByRole("heading", { name: kind })).toBeInTheDocument();
    expect(
      navigation.getByRole("button", { name: "Sessions" }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(
      screen.getByRole("searchbox", { name: "Search catalog" }),
    ).toBeInTheDocument();
  },
);

test("Docs navigation shows documentation and Ctrl+K focuses its search", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      String(input).startsWith("/api/docs")
        ? new Response(
            JSON.stringify({
              guides: [{ path: "README.md", title: "MEOW", text: "# MEOW" }],
              skills: [],
            }),
          )
        : new Response(JSON.stringify(catalog)),
    ),
  );
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: "Docs" }));
  expect(
    await screen.findByRole("heading", { name: "Documentation" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Refresh catalog" })).toBeNull();
  expect(screen.getByRole("button", { name: "Docs" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const box = await screen.findByRole("searchbox", {
    name: "Search documentation",
  });
  await user.keyboard("{Control>}k{/Control}");
  expect(box).toHaveFocus();
});

test("Docs shows an Online docs link without fetching the hosted site", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
    String(input).startsWith("/api/docs")
      ? new Response(JSON.stringify({ guides: [], skills: [] }))
      : new Response(JSON.stringify(catalog)),
  );
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: "Docs" }));
  const link = await screen.findByRole("link", { name: "Online docs" });
  expect(link).toHaveAttribute("href", DOCS_SITE_URL);
  expect(link).toHaveAttribute("target", "_blank");
  expect(link.getAttribute("rel")).toContain("noreferrer");
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).startsWith("http")),
  ).toBe(false);
});

test("Hooks lists each definition with its event and sync state", async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: /Hooks/ }));
  await screen.findByRole("heading", { name: "guard" });
  expect(screen.getAllByText("PreToolUse").length).toBeGreaterThan(0);
  expect(screen.getByText("Synced by meow sync")).toBeTruthy();
  expect(screen.getByText("Every role")).toBeTruthy();
});
