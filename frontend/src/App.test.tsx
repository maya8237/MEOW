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
