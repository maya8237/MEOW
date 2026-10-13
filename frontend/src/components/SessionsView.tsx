import { useCallback, useEffect, useMemo, useState } from "react";
import { Archive, Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Catalog, repoName } from "@/lib/catalog";
import {
  ImportedRun,
  Recipe,
  SessionDetail as SessionRecord,
  SessionSummary,
  importedRunAction,
  listRuns,
  listSessions,
  loadSession,
  sessionAction,
} from "@/lib/sessions";
import SessionDetail from "@/components/SessionDetail";

type CatalogRunItem = {
  kind: "workflow" | "skill" | "agent";
  item: Catalog["workflows"][number] & { claude_resolvable?: boolean };
};
type Props = {
  repo: string;
  catalog: Catalog;
  focusSessionId?: string;
  onSessionFocused?: () => void;
  onRunRecipe: (
    recipe?: Partial<Recipe>,
    item?: CatalogRunItem,
    sessionId?: string,
    unknownFlags?: boolean,
    editOnly?: boolean,
  ) => void;
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

export default function SessionsView({
  repo,
  catalog,
  focusSessionId,
  onSessionFocused,
  onRunRecipe,
}: Props) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [runs, setRuns] = useState<ImportedRun[]>([]);
  const [selection, setSelection] = useState<Selection>(null);
  const [detail, setDetail] = useState<SessionRecord | null>(null);
  const [detailError, setDetailError] = useState("");
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const selectedSessionId = selection?.session?.id || "";
  const selectedSessionRepo = selection?.session?.repo || "";

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const [sessionResult, runResult] = await Promise.all([
          listSessions(repo, signal),
          listRuns(repo, signal),
        ]);
        if (signal?.aborted) return;
        setSessions(sessionResult.sessions);
        setRuns(runResult.runs);
        setError("");
        setLoading(false);
      } catch (reason) {
        if (!signal?.aborted) {
          setLoading(false);
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load sessions.",
          );
        }
      }
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
  useEffect(() => {
    const entry = entries.find(
      (candidate) => candidate.session?.id === focusSessionId,
    );
    if (focusSessionId && entry) {
      setSelection(entry);
      onSessionFocused?.();
    }
  }, [entries, focusSessionId, onSessionFocused]);
  const selectedSession = activeSelection?.session || null;
  const selectedRun = activeSelection?.run || null;
  const visibleDetail = detail?.id === selectedSession?.id ? detail : null;
  const linkedRuns = runs.filter((run) =>
    visibleDetail?.attempts.some((attempt) => attempt.run_ids.includes(run.id)),
  );

  async function action(
    actionName:
      "stop" | "archive" | "unarchive" | "duplicate" | "cancel-schedule",
    values: object = {},
  ) {
    if (!selectedSession) return;
    setBusy(true);
    setError("");
    try {
      const result = await sessionAction(
        selectedSession.repo,
        selectedSession.id,
        actionName,
        values,
      );
      if (actionName === "duplicate")
        setSelection({
          key: `session:${result.session.repo}:${result.session.id}`,
          session: result.session,
        });
      setRevision((value) => value + 1);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : `Could not ${actionName} this session.`,
      );
    } finally {
      setBusy(false);
    }
  }
  async function resume(
    attemptId?: string,
    requestText?: string,
    repair?: boolean,
  ) {
    if (!selectedSession || !attemptId) return;
    setBusy(true);
    setError("");
    try {
      await sessionAction(selectedSession.repo, selectedSession.id, "resume", {
        attempt_id: attemptId,
        ...(requestText ? { request: requestText } : {}),
        ...(repair ? { repair: true } : {}),
      });
      setRevision((value) => value + 1);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not resume this attempt.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function resumeRun(requestText: string, repair: boolean) {
    if (!selectedRun) return;
    setBusy(true);
    setError("");
    try {
      const result = await importedRunAction(
        selectedRun.repo,
        selectedRun.id,
        "resume",
        {
          ...(requestText ? { request: requestText } : {}),
          ...(repair ? { repair: true } : {}),
        },
      );
      setRevision((value) => value + 1);
      if (result.session)
        setSelection({
          key: `session:${result.session.repo}:${result.session.id}`,
          session: result.session,
        });
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not resume this run.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function cancelRun() {
    if (!selectedRun) return;
    setBusy(true);
    setError("");
    try {
      await importedRunAction(selectedRun.repo, selectedRun.id, "cancel");
      setRevision((value) => value + 1);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not cancel this run.",
      );
    } finally {
      setBusy(false);
    }
  }
  function duplicateRun() {
    if (!selectedRun) return;
    onRunRecipe(recipeFromRun(selectedRun), undefined, undefined, true);
  }
  function rerunImported() {
    if (!selectedRun) return;
    onRunRecipe(recipeFromRun(selectedRun), undefined, undefined, true);
  }

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
          <Button
            size="icon"
            aria-label="New session"
            onClick={() => onRunRecipe()}
          >
            <Plus />
          </Button>
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
                  : "Launch a MEOW command to keep its recipe, attempts, and output together."}
              </p>
              <Button variant="outline" onClick={() => onRunRecipe()}>
                <Plus />
                New session
              </Button>
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
                    onClick={() => setSelection(entry)}
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
        {busy && (
          <span className="sr-only" role="status">
            Updating session…
          </span>
        )}
        {error && activeSelection && (
          <div className="session-error session-action-error" role="alert">
            {error}
          </div>
        )}
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
          onEdit={() =>
            visibleDetail &&
            onRunRecipe(
              visibleDetail.recipe,
              undefined,
              visibleDetail.id,
              false,
              true,
            )
          }
          onRerun={() =>
            visibleDetail &&
            onRunRecipe(visibleDetail.recipe, undefined, visibleDetail.id)
          }
          onDuplicate={() => void action("duplicate")}
          onResume={(attemptId, requestText, repair) =>
            void resume(attemptId, requestText, repair)
          }
          onStop={() => void action("stop")}
          onCancelSchedule={(scheduleId) =>
            void action("cancel-schedule", { schedule_id: scheduleId })
          }
          onArchive={() => void action("archive")}
          onUnarchive={() => void action("unarchive")}
          onImportedResume={(requestText, repair) =>
            void resumeRun(requestText, repair)
          }
          onImportedCancel={() => void cancelRun()}
        />
        {selectedRun && (
          <div className="imported-actions">
            <Button variant="outline" onClick={duplicateRun}>
              <Plus />
              Duplicate as draft
            </Button>
            <Button variant="outline" onClick={rerunImported}>
              Prepare rerun
            </Button>
          </div>
        )}
      </section>
    </div>
  );
}

function recipeFromRun(run: ImportedRun): Recipe {
  return {
    name: run.feature_name || `run-${run.id.slice(0, 8)}`,
    repo: run.repo,
    mode: "cli",
    command: "run",
    request: run.request || `Continue work from MEOW run ${run.id}`,
    workflow: run.workflow?.name || null,
    inputs: run.workflow?.inputs || {},
    arguments: [],
    options: { unattended: true, no_worktree: false },
  };
}
