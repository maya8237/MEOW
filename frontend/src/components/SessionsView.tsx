import { useCallback, useEffect, useMemo, useState } from "react";
import { Archive, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Catalog, repoName } from "@/lib/catalog";
import {
  ImportedRun,
  SessionDetail as SessionRecord,
  SessionSummary,
  listRuns,
  listSessions,
  loadSession,
} from "@/lib/sessions";
import SessionDetail from "@/components/SessionDetail";

type Props = {
  repo: string;
  catalog: Catalog;
};
type Selection = {
  key: string;
  session?: SessionSummary;
  run?: ImportedRun;
} | null;
type Entry = { key: string; session?: SessionSummary; run?: ImportedRun };

const stateText = (state: string) =>
  ({
    needs_decision: "Needs a decision",
    succeeded: "Succeeded",
    failed: "Failed",
    interrupted: "Interrupted",
    running: "Running",
    starting: "Starting",
    stopping: "Stopping",
    stopped: "Stopped",
    draft: "Draft",
    scheduled: "Scheduled",
  })[state] || state.replaceAll("_", " ");

export default function SessionsView({ repo, catalog }: Props) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [runs, setRuns] = useState<ImportedRun[]>([]);
  const [selection, setSelection] = useState<Selection>(null);
  const [detail, setDetail] = useState<SessionRecord | null>(null);
  const [detailError, setDetailError] = useState("");
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const selectedSessionId = selection?.session?.id || "";
  const selectedSessionRepo = selection?.session?.repo || "";

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const failure = (reason: unknown) =>
        reason instanceof Error ? reason.message : "Could not load sessions.";
      const [sessionResult, runResult] = await Promise.allSettled([
        listSessions(repo, signal).then((result) => {
          if (signal?.aborted) return;
          setSessions(result.sessions);
          setLoading(false);
        }),
        listRuns(repo, signal).then((result) => {
          if (!signal?.aborted) setRuns(result.runs);
        }),
      ]);
      if (signal?.aborted) return;
      const failed = [sessionResult, runResult].find(
        (result) => result.status === "rejected",
      );
      setError(failed ? failure(failed.reason) : "");
      setLoading(false);
    },
    [repo],
  );
  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    setLoading(true);
    const tick = () => {
      if (pending || document.visibilityState === "hidden") return;
      pending = true;
      void refresh(controller.signal).finally(() => {
        pending = false;
      });
    };
    tick();
    const timer = setInterval(tick, 2200);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [refresh, revision]);
  useEffect(() => {
    if (!selectedSessionId || !selectedSessionRepo) {
      setDetail(null);
      setDetailError("");
      return;
    }
    setDetail(null);
    setDetailError("");
    const controller = new AbortController();
    let pending = false;
    const tick = async () => {
      if (pending) return;
      pending = true;
      try {
        const result = await loadSession(
          selectedSessionRepo,
          selectedSessionId,
          controller.signal,
        );
        if (!controller.signal.aborted) setDetail(result.session);
      } catch (reason) {
        if (!controller.signal.aborted)
          setDetailError(
            reason instanceof Error
              ? reason.message
              : "Could not load the selected session.",
          );
      } finally {
        pending = false;
      }
    };
    void tick();
    const timer = setInterval(() => {
      if (document.visibilityState !== "hidden") void tick();
    }, 2200);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [selection?.key, selectedSessionId, selectedSessionRepo, revision]);

  const entries = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const sessionEntries: Entry[] = sessions
      .filter((item) => showArchived || !item.archived)
      .filter(
        (item) =>
          !needle ||
          `${item.name} ${item.command} ${item.state} ${item.repo}`
            .toLowerCase()
            .includes(needle),
      )
      .map((item) => ({
        key: `session:${item.repo}:${item.id}`,
        session: item,
      }));
    const runEntries: Entry[] = runs
      .filter(
        (item) =>
          !needle ||
          `${item.feature_name || ""} ${item.request} ${item.phase} ${item.id} ${item.repo}`
            .toLowerCase()
            .includes(needle),
      )
      .map((item) => ({ key: `run:${item.repo}:${item.id}`, run: item }));
    return [...sessionEntries, ...runEntries].sort((left, right) => {
      const a = left.session?.updated_at || left.run!.updated_at;
      const b = right.session?.updated_at || right.run!.updated_at;
      return b.localeCompare(a);
    });
  }, [sessions, runs, query, showArchived]);
  const activeSelection =
    entries.find((entry) => entry.key === selection?.key) || null;
  const selectedSession = activeSelection?.session || null;
  const selectedRun = activeSelection?.run || null;
  const visibleDetail = detail?.id === selectedSession?.id ? detail : null;
  const linkedRuns = runs.filter((run) =>
    visibleDetail?.attempts.some((attempt) => attempt.run_ids.includes(run.id)),
  );

  return (
    <div className="sessions-view">
      <aside className="sessions-list" aria-label="Sessions and MEOW runs">
        <div className="sessions-list-top">
          <label className="session-search">
            <Search />
            <Input
              aria-label="Search sessions"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search sessions and runs"
            />
          </label>
        </div>
        <label className="archived-toggle">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => setShowArchived(event.target.checked)}
          />
          <Archive />
          Show archived sessions
        </label>
        {error && (
          <div className="session-error session-list-error" role="alert">
            {error}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setRevision((value) => value + 1)}
            >
              Retry
            </Button>
          </div>
        )}
        {!entries.length ? (
          loading ? (
            <div className="sessions-empty" role="status">
              <h2>Loading sessions…</h2>
              <p>Checking saved sessions and MEOW run history.</p>
            </div>
          ) : (
            <div className="sessions-empty">
              <h2>{query ? "No matches" : "No sessions yet"}</h2>
              <p>
                {query
                  ? "Try another name, command, or request."
                  : "Completed and active MEOW sessions and runs appear here."}
              </p>
            </div>
          )
        ) : (
          <ul className="sessions-items">
            {entries.map((entry) => {
              const session = entry.session;
              const run = entry.run;
              const title =
                session?.name ||
                run?.feature_name ||
                `Run ${run?.id.slice(0, 8)}`;
              const state = session?.state || run?.phase || "";
              const itemRepo = session?.repo || run?.repo || "";
              return (
                <li key={entry.key}>
                  <button
                    className="sessions-item"
                    aria-current={
                      selection?.key === entry.key ? "true" : undefined
                    }
                    onClick={() =>
                      setSelection((current) =>
                        current?.key === entry.key ? null : entry,
                      )
                    }
                  >
                    <span className="sessions-item-top">
                      <strong>{title}</strong>
                      <span className={`state-pill state-${state}`}>
                        {stateText(state)}
                      </span>
                    </span>
                    <span className="sessions-item-meta">
                      {run
                        ? "MEOW run"
                        : session?.mode === "cli"
                          ? "MEOW CLI"
                          : "Claude Code"}{" "}
                      · {session?.command || run?.workflow?.name || "run"}
                    </span>
                    <span className="sessions-item-repo" title={itemRepo}>
                      {repoName(itemRepo, catalog.repos)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <div className="sessions-list-footer">
          {entries.length} {entries.length === 1 ? "record" : "records"}
        </div>
      </aside>
      <section
        className="sessions-inspector"
        aria-label="Selected session details"
      >
        <SessionDetail
          repo={selectedSession?.repo || selectedRun?.repo || repo}
          session={visibleDetail}
          importedRun={selectedRun}
          loadingSession={!!selectedSession && !visibleDetail && !detailError}
          detailError={detailError}
          onRetryDetails={() => {
            setDetailError("");
            setRevision((value) => value + 1);
          }}
          linkedRuns={linkedRuns}
          repos={catalog.repos}
        />
      </section>
    </div>
  );
}
