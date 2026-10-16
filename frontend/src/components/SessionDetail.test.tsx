import { act, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { Recipe, SessionDetail as SessionRecord } from "@/lib/sessions";
import SessionDetail from "./SessionDetail";

const repo = "/repo";
function session(id: string, attemptId: string, name: string): SessionRecord {
  const recipe: Recipe = {
    name,
    repo,
    mode: "cli",
    command: "run",
    request: "Do work",
    workflow: null,
    inputs: {},
    arguments: [],
    options: {},
  };
  return {
    id,
    name,
    repo,
    state: "stopped",
    mode: "cli",
    command: "run",
    created_at: "now",
    updated_at: "now",
    archived: false,
    active_attempt_id: null,
    attempt_count: 1,
    last_attempt: {
      id: attemptId,
      state: "stopped",
      updated_at: "now",
      exit_code: 1,
    },
    recipe,
    attempts: [
      {
        id: attemptId,
        state: "stopped",
        created_at: "now",
        started_at: "now",
        finished_at: "now",
        effective_recipe: recipe,
        exit_code: 1,
        output_size: 0,
        claude_session_id: null,
        run_ids: [],
        error: null,
        worker_alive: false,
        resume_supported: true,
        resume_needs_request: false,
      },
    ],
  };
}

const noop = () => {};
const callbacks = {
  onEdit: noop,
  onRerun: noop,
  onDuplicate: noop,
  onResume: noop,
  onStop: noop,
  onArchive: noop,
  onUnarchive: noop,
  onImportedResume: noop,
  onImportedCancel: noop,
};
const chunk = (text: string) =>
  new Response(
    JSON.stringify({
      text,
      next_cursor: text.length,
      size: text.length,
      truncated: false,
      reset: false,
    }),
  );

test("groups linked run facts and saved settings in one sidebar", () => {
  const record = session("s1", "a1", "Work");
  record.attempts[0].run_ids = ["run1"];
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(chunk("")));
  render(
    <SessionDetail
      repo={repo}
      session={record}
      repos={[repo]}
      linkedRuns={[
        {
          id: "run1",
          repo,
          source: "prompt",
          request: "Do work",
          phase: "failed",
          created_at: "now",
          updated_at: "now",
          worktree: repo,
          branch: "feature",
          feature_name: "work",
          workflow: null,
          plan_file: null,
          background_output_available: false,
          ui_session_id: "s1",
          ui_attempt_id: "a1",
          resume_supported: true,
          resume_needs_request: false,
          cancel_supported: false,
        },
      ]}
      {...callbacks}
    />,
  );

  const sidebar = screen.getByRole("complementary", { name: "Run details" });
  expect(sidebar).toContainElement(
    screen.getByRole("heading", { name: "MEOW run facts" }),
  );
  expect(sidebar).toContainElement(
    screen.getByRole("heading", { name: "Saved run settings" }),
  );
});

test("edits a pending schedule in local time and preserves invalid drafts", async () => {
  const record = session("scheduled", "unused", "Scheduled session");
  record.attempts = [];
  record.schedules = [
    { id: "job", at: "2099-12-01T12:30:00Z", state: "scheduled" },
  ];
  const edit = vi.fn().mockResolvedValue(false);
  const user = userEvent.setup();
  render(
    <SessionDetail
      repo={repo}
      session={record}
      repos={[repo]}
      {...callbacks}
      onEditSchedule={edit}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Edit time" }));
  const input = screen.getByLabelText("Schedule run at");
  expect(
    screen.queryByText(new Date(record.schedules[0].at).toLocaleString(), {
      exact: false,
    }),
  ).not.toBeInTheDocument();
  expect((input as HTMLInputElement).value).toBe(
    new Date(
      new Date(record.schedules[0].at).getTime() -
        new Date(record.schedules[0].at).getTimezoneOffset() * 60000,
    )
      .toISOString()
      .slice(0, 16),
  );
  await user.clear(input);
  expect(screen.getByRole("button", { name: "Save time" })).toBeDisabled();
  await user.type(input, "2099-12-02T09:15");
  await user.click(screen.getByRole("button", { name: "Save time" }));
  expect(edit).toHaveBeenCalledWith(
    "job",
    new Date("2099-12-02T09:15").toISOString(),
  );
  expect(screen.getByLabelText("Schedule run at")).toHaveValue(
    "2099-12-02T09:15",
  );
  edit.mockResolvedValue(true);
  await user.click(screen.getByRole("button", { name: "Save time" }));
  expect(screen.queryByLabelText("Schedule run at")).not.toBeInTheDocument();
});

test("selection changes abort pending output and ignore its late response", async () => {
  let releaseOld!: (value: Response) => void;
  let oldSignal: AbortSignal | undefined;
  const old = session("s1", "a1", "Old session");
  const current = session("s2", "a2", "Current session");
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("attempt=a1")) {
        oldSignal = init?.signal as AbortSignal;
        return new Promise<Response>((resolve) => {
          releaseOld = resolve;
        });
      }
      return Promise.resolve(chunk("current output"));
    }),
  );
  const view = render(
    <SessionDetail repo={repo} session={old} repos={[repo]} {...callbacks} />,
  );
  await waitFor(() => expect(oldSignal).toBeDefined());
  view.rerender(
    <SessionDetail
      repo={repo}
      session={current}
      repos={[repo]}
      {...callbacks}
    />,
  );
  expect(oldSignal?.aborted).toBe(true);
  await waitFor(() =>
    expect(screen.getByLabelText("Text output")).toHaveTextContent(
      "current output",
    ),
  );
  await act(async () => {
    releaseOld(chunk("stale output"));
  });
  expect(screen.getByLabelText("Text output")).not.toHaveTextContent(
    "stale output",
  );
});

test("stopping a running attempt preserves its recorded output", async () => {
  const record = session("s1", "a1", "Work");
  record.state = "running";
  record.attempts[0].state = "running";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const cursor = Number(
        new URL(String(input), "http://local").searchParams.get("cursor"),
      );
      return chunk("recorded output".slice(cursor));
    }),
  );
  const view = render(
    <SessionDetail
      repo={repo}
      session={record}
      repos={[repo]}
      {...callbacks}
    />,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Text output")).toHaveTextContent(
      "recorded output",
    ),
  );
  const stopped = {
    ...record,
    state: "stopped",
    attempts: [{ ...record.attempts[0], state: "stopped" }],
  };
  view.rerender(
    <SessionDetail
      repo={repo}
      session={stopped}
      repos={[repo]}
      {...callbacks}
    />,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Text output")).toHaveTextContent(
      "recorded output",
    ),
  );
});

test("completed attempts load every recorded output chunk", async () => {
  const record = session("s1", "a1", "Work");
  const text = "x".repeat(65536) + "final output";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const cursor = Number(
        new URL(String(input), "http://local").searchParams.get("cursor"),
      );
      const end = Math.min(cursor + 65536, text.length);
      return new Response(
        JSON.stringify({
          text: text.slice(cursor, end),
          next_cursor: end,
          size: text.length,
          truncated: end < text.length,
          reset: false,
        }),
      );
    }),
  );
  render(
    <SessionDetail
      repo={repo}
      session={record}
      repos={[repo]}
      {...callbacks}
    />,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Text output")).toHaveTextContent(
      "final output",
    ),
  );
});

test("returning to a previously viewed attempt reloads its output", async () => {
  const record = session("s1", "a1", "Work");
  record.attempts.push({ ...record.attempts[0], id: "a2" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://local");
      const text =
        url.searchParams.get("attempt") === "a1"
          ? "first output"
          : "second output";
      return chunk(text.slice(Number(url.searchParams.get("cursor"))));
    }),
  );
  const user = userEvent.setup();
  render(
    <SessionDetail
      repo={repo}
      session={record}
      repos={[repo]}
      {...callbacks}
    />,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Text output")).toHaveTextContent(
      "second output",
    ),
  );
  const attempt = screen.getByRole("combobox", { name: "Attempt" });
  await user.selectOptions(attempt, "a1");
  await waitFor(() =>
    expect(screen.getByLabelText("Text output")).toHaveTextContent(
      "first output",
    ),
  );
  await user.selectOptions(attempt, "a2");
  await waitFor(() =>
    expect(screen.getByLabelText("Text output")).toHaveTextContent(
      "second output",
    ),
  );
});
