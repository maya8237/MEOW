import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { Catalog } from "@/lib/catalog";
import SessionLauncher from "./SessionLauncher";

const catalog = {
  repos: ["/repo"],
  workflows: [],
  agents: [],
  skills: [],
  notes: [],
  error: null,
} as unknown as Catalog;
const props = (mode: "cli" | "claude" = "cli") => ({
  repo: "/repo",
  catalog,
  onClose: vi.fn(),
  onSaved: vi.fn(),
  initialRecipe:
    mode === "claude"
      ? { mode, command: "prompt", request: "Review this change" }
      : undefined,
});

test("advanced flags use parser choices and remove the entire saved option", async () => {
  const user = userEvent.setup();
  const previews: string[][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/cli-options")
        return new Response(
          JSON.stringify({
            commands: {
              run: [
                {
                  flag: "--test",
                  aliases: ["--test"],
                  kind: "switch",
                  help: "Run tests",
                  nargs: 0,
                  choices: null,
                  controlled: null,
                },
                {
                  flag: "--resume-at",
                  aliases: ["--resume-at"],
                  kind: "value",
                  help: "Resume stage",
                  nargs: 1,
                  choices: ["generate", "review"],
                  controlled: null,
                },
              ],
            },
          }),
        );
      if (String(input).endsWith("/preview")) {
        previews.push(JSON.parse(String(init?.body)).recipe.arguments);
        return new Response(
          JSON.stringify({
            valid: true,
            argv: [],
            display: "meow run",
            warnings: [],
            capabilities: {
              cli: { available: true },
              claude: { available: false },
            },
          }),
        );
      }
      throw new Error(`Unexpected ${input}`);
    }),
  );
  render(
    <SessionLauncher
      {...props()}
      initialRecipe={{
        request: "Fix search",
        arguments: ["--resume-at=review"],
      }}
    />,
  );
  await user.click(screen.getByText("Advanced arguments"));
  expect(await screen.findByLabelText("--resume-at value")).toHaveValue(
    "review",
  );
  expect(screen.queryByLabelText("Argument 1")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Remove --resume-at" }));
  expect(screen.queryByLabelText("--resume-at value")).toBeNull();
  await user.click(screen.getByLabelText("--test"));
  await user.click(screen.getByLabelText("--resume-at"));
  await user.selectOptions(
    screen.getByLabelText("--resume-at value"),
    "generate",
  );
  await user.click(screen.getByRole("button", { name: "Preview command" }));
  await screen.findByText("meow run");
  expect(previews.at(-1)).toEqual(["--test", "--resume-at", "generate"]);
  await user.click(screen.getByRole("button", { name: "Remove --test" }));
  expect(screen.queryByText("meow run")).toBeNull();
});

test("CLI launcher offers orchestration without inheriting run options", async () => {
  const user = userEvent.setup();
  render(<SessionLauncher {...props()} />);
  await user.selectOptions(screen.getByLabelText("Command"), "orchestrate");
  expect(screen.getByLabelText("Command")).toHaveValue("orchestrate");
});

test.each(["skill", "agent"] as const)(
  "catalog %s preview starts with Claude options only",
  async (kind) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input, init?: RequestInit) => {
        const recipe = JSON.parse(String(init?.body)).recipe;
        const valid =
          recipe.mode === "claude" &&
          recipe.options.permission_policy === "default" &&
          !Object.hasOwn(recipe.options, "auto") &&
          !Object.hasOwn(recipe.options, "no_worktree") &&
          (kind !== "agent" || recipe.options.agent === "helper");
        return new Response(
          JSON.stringify(
            valid
              ? {
                  valid: true,
                  argv: ["claude"],
                  display: "claude preview",
                  warnings: [],
                  capabilities: {
                    cli: { available: true },
                    claude: { available: true },
                  },
                }
              : { error: "Invalid Claude options" },
          ),
          { status: valid ? 200 : 400 },
        );
      }),
    );
    const user = userEvent.setup();
    render(
      <SessionLauncher
        {...props()}
        catalogItem={{
          kind,
          item: {
            name: "helper",
            description: "Help",
            scope: "project",
            repo: "/repo",
            file: "/repo/helper",
            text: "Help",
            claude_resolvable: true,
          },
        }}
      />,
    );
    await user.type(screen.getByLabelText("Request"), "Check this change");
    await user.click(screen.getByRole("button", { name: "Preview command" }));
    expect(await screen.findByText("claude preview")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Launch session" }),
    ).toBeEnabled();
  },
);

test("review source flags can preview with an empty request", async () => {
  const user = userEvent.setup();
  const bodies: { recipe: { request: string; arguments: string[] } }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)));
      return new Response(
        JSON.stringify({
          valid: true,
          argv: [],
          display: "meow review --branch feature",
          warnings: [],
          capabilities: {
            cli: { available: true },
            claude: { available: false },
          },
        }),
      );
    }),
  );
  render(
    <SessionLauncher
      {...props()}
      initialRecipe={{
        command: "review",
        request: "",
        arguments: ["--branch", "feature", "--target", "main"],
        options: {},
      }}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Preview command" }));
  await screen.findByText("meow review --branch feature");
  expect(bodies[0].recipe.request).toBe("");
  expect(bodies[0].recipe.arguments).toEqual([
    "--branch",
    "feature",
    "--target",
    "main",
  ]);
});

test("scheduling sends a timestamp and never launches immediately", async () => {
  const user = userEvent.setup();
  const id = "a".repeat(32);
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith("/preview"))
        return new Response(
          JSON.stringify({
            valid: true,
            argv: [],
            display: "meow run task",
            warnings: [],
            capabilities: {
              cli: { available: true },
              claude: { available: false },
            },
          }),
        );
      if (url === "/api/sessions")
        return new Response(JSON.stringify({ session: { id } }));
      if (url.endsWith("/schedule")) {
        expect(JSON.parse(String(options?.body)).at).toBe(
          "2099-10-14T19:30:00.000Z",
        );
        return new Response(
          JSON.stringify({ session: { id }, schedule: { id: "b".repeat(32) } }),
        );
      }
      throw new Error(`Unexpected ${url}`);
    }),
  );
  const onSaved = vi.fn();
  render(<SessionLauncher {...props()} onSaved={onSaved} />);
  await user.type(screen.getByLabelText("Session name"), "Search");
  await user.type(screen.getByLabelText("Request"), "Fix search");
  await user.selectOptions(screen.getByLabelText("Start"), "later");
  // datetime-local is interpreted in the browser's timezone.
  const local = new Date("2099-10-14T19:30:00.000Z");
  const value = new Date(local.getTime() - local.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
  fireEvent.change(screen.getByLabelText("Run at"), { target: { value } });
  expect(
    screen.getByRole("button", { name: "Schedule session" }),
  ).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Preview command" }));
  await screen.findByText("meow run task");
  await user.click(screen.getByRole("button", { name: "Schedule session" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(id));
  expect(calls.some((url) => url.endsWith("/launch"))).toBe(false);
});

test("CLI draft defaults to auto and cannot launch before its preview", async () => {
  const user = userEvent.setup();
  render(<SessionLauncher {...props()} />);
  expect(
    screen.queryByRole("checkbox", { name: "Automatic execution" }),
  ).not.toBeInTheDocument();
  const launch = screen.getByRole("button", { name: "Launch session" });
  expect(launch).toBeDisabled();
  await user.type(screen.getByLabelText("Session name"), "Fix search");
  await user.type(screen.getByLabelText("Request"), "Fix search behavior");
  expect(launch).toBeDisabled();
});

test("Claude bypass stays unchecked until explicitly selected", () => {
  render(<SessionLauncher {...props("claude")} />);
  expect(
    screen.getByRole("checkbox", { name: /Bypass Claude permission checks/i }),
  ).not.toBeChecked();
});

test("editing a reviewed field invalidates the reviewed command", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            valid: true,
            argv: ["python", "-m", "meow"],
            display: "meow run",
            warnings: [],
            capabilities: {
              cli: { available: true },
              claude: {
                available: false,
                missing: [],
                version: null,
                permission_policy: "unsupported",
                bypass_supported: false,
                error: "Claude unavailable",
              },
            },
          }),
        ),
    ),
  );
  render(<SessionLauncher {...props()} />);
  await user.type(screen.getByLabelText("Session name"), "Search");
  await user.type(screen.getByLabelText("Request"), "Fix search");
  await user.click(screen.getByRole("button", { name: "Preview command" }));
  expect(await screen.findByText("meow run")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Launch session" })).toBeEnabled();
  await user.type(screen.getByLabelText("Request"), " more");
  expect(screen.queryByText("meow run")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Launch session" })).toBeDisabled();
});

test("Claude bypass selection is explicit and persists into the launch recipe", async () => {
  const user = userEvent.setup();
  const session = {
    id: "a".repeat(32),
    name: "Review",
    repo: "/repo",
    state: "draft",
    mode: "claude",
    command: "prompt",
    created_at: "now",
    updated_at: "now",
    archived: false,
    active_attempt_id: null,
    attempt_count: 0,
    last_attempt: null,
    recipe: {},
    attempts: [],
  };
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/preview"))
        return new Response(
          JSON.stringify({
            valid: true,
            argv: ["claude", "--permission-mode", "bypassPermissions"],
            display: "claude --permission-mode bypassPermissions",
            warnings: [],
            capabilities: {
              cli: { available: true },
              claude: {
                available: true,
                version: "2",
                missing: [],
                permission_policy: "dontAsk+none",
                bypass_supported: true,
                error: null,
              },
            },
          }),
        );
      if (url === "/api/sessions") {
        expect(
          JSON.parse(String(options?.body)).recipe.options.permission_policy,
        ).toBe("bypass");
        return new Response(JSON.stringify({ session }), { status: 201 });
      }
      if (url.endsWith("/launch"))
        return new Response(JSON.stringify({ session, attempt: {} }));
      throw new Error(`Unexpected ${url}`);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  const onSaved = vi.fn();
  render(<SessionLauncher {...props("claude")} onSaved={onSaved} />);
  const bypass = screen.getByRole("checkbox", {
    name: /Bypass Claude permission checks/i,
  });
  expect(bypass).not.toBeChecked();
  await user.click(bypass);
  await user.type(screen.getByLabelText("Session name"), "Review");
  await user.click(screen.getByLabelText("Request"));
  await user.clear(screen.getByLabelText("Request"));
  await user.type(screen.getByLabelText("Request"), "Review this change");
  await user.click(screen.getByRole("button", { name: "Preview command" }));
  await screen.findByText("claude --permission-mode bypassPermissions");
  await user.click(screen.getByRole("button", { name: "Launch session" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(session.id));
});

test("preview errors preserve entered recipe fields", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "Unsupported command option" }), {
          status: 400,
        }),
    ),
  );
  render(<SessionLauncher {...props()} />);
  await user.type(screen.getByLabelText("Session name"), "Keep this");
  await user.type(screen.getByLabelText("Request"), "Keep this request");
  await user.click(screen.getByRole("button", { name: "Preview command" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Unsupported command option",
  );
  expect(screen.getByLabelText("Session name")).toHaveValue("Keep this");
  expect(screen.getByLabelText("Request")).toHaveValue("Keep this request");
});

test("editing keeps the saved repository as the update owner", async () => {
  const user = userEvent.setup();
  const ownerRepo = "C:/saved/project";
  const selectedRepo = "D:/currently-selected/project";
  const id = "a".repeat(32);
  const session = {
    id,
    name: "Saved session",
    repo: ownerRepo,
    state: "draft",
    mode: "cli",
    command: "run",
    created_at: "now",
    updated_at: "now",
    archived: false,
    active_attempt_id: null,
    attempt_count: 0,
    last_attempt: null,
    recipe: {},
    attempts: [],
  };
  const calls: [string, RequestInit | undefined][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      calls.push([url, options]);
      if (url === "/api/sessions/preview")
        return new Response(
          JSON.stringify({
            valid: true,
            argv: ["python", "-m", "meow"],
            display: "meow run",
            warnings: [],
            capabilities: {
              cli: { available: true },
              claude: {
                available: false,
                missing: [],
                version: null,
                permission_policy: "unsupported",
                bypass_supported: false,
                error: "Claude unavailable",
              },
            },
          }),
        );
      if (options?.method === "PUT")
        return new Response(JSON.stringify({ session }));
      throw new Error(`Unexpected ${options?.method || "GET"} ${url}`);
    }),
  );

  render(
    <SessionLauncher
      {...props()}
      catalog={{ ...catalog, repos: [ownerRepo, selectedRepo] }}
      repo={selectedRepo}
      initialRecipe={{
        name: "Saved session",
        repo: ownerRepo,
        mode: "cli",
        command: "run",
        request: "Keep working",
        workflow: null,
        inputs: {},
        arguments: [],
        options: { auto: true, no_worktree: false },
      }}
      sessionId={id}
      editOnly
    />,
  );
  const repository = screen.getByLabelText("Repository");
  expect(repository).toHaveValue(ownerRepo);
  expect(repository).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Preview command" }));
  await screen.findByText("meow run");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(
      calls.some(
        ([url, options]) =>
          url === `/api/sessions/${id}?repo=${encodeURIComponent(ownerRepo)}` &&
          options?.method === "PUT" &&
          JSON.parse(String(options.body)).repo === ownerRepo,
      ),
    ).toBe(true),
  );
  expect(
    calls.some(([url]) => url.includes(encodeURIComponent(selectedRepo))),
  ).toBe(false);
});

test("imported recipe can be saved as an editable draft with unknown flags called out", async () => {
  const user = userEvent.setup();
  const id = "b".repeat(32);
  const session = {
    id,
    name: "Imported recipe",
    repo: "/repo",
    state: "draft",
    mode: "cli",
    command: "run",
    created_at: "now",
    updated_at: "now",
    archived: false,
    active_attempt_id: null,
    attempt_count: 0,
    last_attempt: null,
    recipe: {},
    attempts: [],
  };
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      calls.push(`${options?.method || "GET"} ${url}`);
      if (url === "/api/sessions" && options?.method === "POST")
        return new Response(JSON.stringify({ session }), { status: 201 });
      throw new Error(`Unexpected ${options?.method || "GET"} ${url}`);
    }),
  );
  const onSaved = vi.fn();
  render(
    <SessionLauncher
      {...props()}
      catalog={{
        ...catalog,
        workflows: [
          {
            name: "quick",
            description: "",
            scope: "project",
            repo: "/repo",
            text: "",
          },
        ],
      }}
      unknownFlags
      initialRecipe={{
        name: "Imported recipe",
        repo: "/repo",
        mode: "cli",
        command: "run",
        request: "Recorded request",
        workflow: "quick",
        inputs: { area: "ui" },
        arguments: [],
        options: { auto: true, no_worktree: false },
      }}
      onSaved={onSaved}
    />,
  );

  expect(
    screen.getByText(
      /Historical run facts do not include the original command flags/i,
    ),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Session name")).toHaveValue("Imported recipe");
  expect(screen.getByLabelText("Workflow")).toHaveValue("quick");
  await user.click(screen.getByRole("button", { name: "Save as draft" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(id));
  expect(calls).toContain("POST /api/sessions");
  expect(calls.some((call) => call.endsWith("/launch"))).toBe(false);
});
