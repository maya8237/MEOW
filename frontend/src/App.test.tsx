import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import App from "./App";

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

test("creating a different kind selects the saved item and clears restrictive filters", async () => {
  const user = userEvent.setup();
  const created = {
    ...catalog.agents[0],
    name: "helper",
    file: "/repo/.meow/agents/helper.toml",
    repo: "/repo",
    scope: "project",
  };
  let saved = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      if (options?.method === "POST") {
        expect(url).toBe("/api/agents");
        expect(JSON.parse(options.body as string)).toEqual({
          name: "helper",
          description: "Helps out",
          source: "explorer",
          scope: "project",
          repo: "/repo",
        });
        saved = true;
        return new Response(
          JSON.stringify({ name: "helper", file: created.file }),
          { status: 201 },
        );
      }
      return new Response(
        JSON.stringify(
          saved
            ? { ...catalog, agents: [...catalog.agents, created] }
            : catalog,
        ),
      );
    }),
  );
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.selectOptions(screen.getByLabelText("Scope"), "builtin");
  await user.type(screen.getByRole("searchbox"), "quick");
  await user.click(screen.getByRole("button", { name: "New item" }));
  const dialog = screen.getByRole("dialog");
  await user.selectOptions(within(dialog).getByLabelText("Kind"), "agents");
  await user.type(within(dialog).getByLabelText("Name"), "helper");
  await user.type(within(dialog).getByLabelText("Description"), "Helps out");
  await user.click(
    within(dialog).getByRole("button", { name: "Create agent" }),
  );
  await screen.findByRole("heading", { name: "helper" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Agents/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await user.click(
    screen.getByRole("button", { name: "Copy customize command" }),
  );
  const command = await navigator.clipboard.readText();
  expect(command).toContain("/meow:customize");
  expect(command).toContain(created.file);
  expect(command).toContain('agent "helper"');
  expect(command).toContain('repository "/repo"');
  expect(command).toContain("project scope");
  expect(command).toContain("Helps out");
});

test("failed saves keep the entered fields and allow retry", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, options) => {
      if (options?.method === "POST") throw new Error("Connection lost");
      return new Response(JSON.stringify(catalog));
    }),
  );
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: "New item" }));
  const dialog = screen.getByRole("dialog");
  await user.type(within(dialog).getByLabelText("Name"), "nightly-copy");
  await user.type(within(dialog).getByLabelText("Description"), "Helps out");
  await user.click(
    within(dialog).getByRole("button", { name: "Create workflow" }),
  );
  await waitFor(() =>
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Connection lost",
    ),
  );
  expect(within(dialog).getByLabelText("Name")).toHaveValue("nightly-copy");
  expect(
    within(dialog).getByRole("button", { name: "Create workflow" }),
  ).toBeEnabled();
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

test("canceling creation restores focus without saving", async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: "New item" }));
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "New item" })).toHaveFocus(),
  );
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([, options]) => options?.method === "POST"),
  ).toBe(false);
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

test("clipboard errors give a recovery message and leave the command available", async () => {
  const user = userEvent.setup();
  vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(
    new Error("Denied"),
  );
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getAllByRole("button", { name: /^Copy$/ })[0]);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Select the text and copy it manually",
  );
  expect(
    screen.getByText(
      'meow run "<request>" --workflow quick --name "<feature-name>"',
    ),
  ).toBeInTheDocument();
});

test("making a copy of a skill keeps its selected source after loading", async () => {
  const skill = {
    name: "notes",
    qualified_name: "meow-custom:notes",
    description: "Take notes",
    scope: "project",
    repo: "/repo",
    directory: "/repo/skills/notes",
    text: "Instructions with supporting files",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () => new Response(JSON.stringify({ ...catalog, skills: [skill] })),
    ),
  );
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: /Skills/ }));
  await user.click(screen.getByRole("button", { name: "Make a copy" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Start from")).toBeEnabled(),
  );
  expect(screen.getByLabelText("Start from")).toHaveValue("notes");
  await user.selectOptions(screen.getByLabelText("Start from"), "");
  expect(screen.getByLabelText("Start from")).toHaveValue("");
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

test("workflow Run opens the CLI launcher with its workflow selected", async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: "Run workflow quick" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByLabelText("Execution mode")).toHaveValue("cli");
  expect(screen.getByLabelText("Command")).toHaveValue("run");
  expect(screen.getByLabelText("Workflow")).toHaveValue("quick");
});

test("skill Run uses its qualified Claude command and unverified agents have no Run action", async () => {
  const skill = {
    name: "notes",
    qualified_name: "meow-custom:notes",
    description: "Take notes",
    scope: "project",
    repo: "/repo",
    directory: "/repo/skills/notes",
    text: "Skill source",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () => new Response(JSON.stringify({ ...catalog, skills: [skill] })),
    ),
  );
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: /Agents/ }));
  expect(
    screen.queryByRole("button", { name: "Run agent explorer" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Skills/ }));
  await user.click(
    screen.getByRole("button", { name: "Run skill meow-custom:notes" }),
  );
  expect(screen.getByLabelText("Execution mode")).toHaveValue("claude");
  expect(screen.getByLabelText("Command")).toHaveValue("/meow-custom:notes");
});

test("a verified Claude agent Run preselects the agent option", async () => {
  const agent = { ...catalog.agents[0], claude_resolvable: true };
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () => new Response(JSON.stringify({ ...catalog, agents: [agent] })),
    ),
  );
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "quick" });
  await user.click(screen.getByRole("button", { name: /Agents/ }));
  await user.click(screen.getByRole("button", { name: "Run agent explorer" }));
  expect(screen.getByLabelText("Execution mode")).toHaveValue("claude");
  expect(screen.getByText("Agent: explorer")).toBeInTheDocument();
});
