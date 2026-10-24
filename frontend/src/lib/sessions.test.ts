import { afterEach, expect, test, vi } from "vitest";
import { loadOutput, listSessions } from "./sessions";

afterEach(() => vi.unstubAllGlobals());

const response = (value: unknown) =>
  new Response(JSON.stringify(value), { status: 200 });

test("output fetch carries repository, attempt, cursor, and abort signal", async () => {
  const controller = new AbortController();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      response({
        text: "line 1\n",
        next_cursor: 7,
        size: 7,
        truncated: false,
        reset: false,
      }),
    ),
  );
  await loadOutput("C:/repo", "session", "attempt", 0, controller.signal);
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining("cursor=0"),
    expect.objectContaining({ signal: controller.signal }),
  );
  expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain(
    "repo=C%3A%2Frepo",
  );
});

test("aggregate session listing omits the repo filter", async () => {
  const fetchMock = vi.fn(async () => response({ sessions: [] }));
  vi.stubGlobal("fetch", fetchMock);
  await listSessions("");
  expect(fetchMock.mock.calls[0]).toContain("/api/sessions");
});
