import { act, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
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
