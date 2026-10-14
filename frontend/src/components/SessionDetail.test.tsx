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
