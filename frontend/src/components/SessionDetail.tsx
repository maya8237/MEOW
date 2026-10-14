import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  Check,
  Copy,
  Download,
  Pencil,
  Play,
  RotateCcw,
  Square,
  Undo2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { repoName } from "@/lib/catalog";
import {
  ImportedRun,
  OutputChunk,
  SessionDetail as SessionRecord,
  importedOutputUrl,
  loadOutput,
  loadRunOutput,
  outputUrl,
} from "@/lib/sessions";

const active = new Set(["starting", "running", "stopping"]);
const stateLabel: Record<string, string> = {
  needs_decision: "Needs a decision",
  interrupted: "Interrupted",
  succeeded: "Succeeded",
  failed: "Failed",
  stopped: "Stopped",
  draft: "Draft",
  scheduled: "Scheduled",
  starting: "Starting",
  running: "Running",
  stopping: "Stopping",
};
type Props = {
  repo: string;
  session: SessionRecord | null;
  importedRun?: ImportedRun | null;
  loadingSession?: boolean;
  detailError?: string;
  onRetryDetails?: () => void;
  linkedRuns?: ImportedRun[];
  repos: string[];
  onEdit: () => void;
  onRerun: () => void;
  onDuplicate: () => void;
  onResume: (attemptId?: string, request?: string, repair?: boolean) => void;
  onStop: () => void;
  onArchive: () => void;
  onUnarchive: () => void;
  onImportedResume: (request: string, repair: boolean) => void;
  onImportedCancel: () => void;
  onCancelSchedule?: (scheduleId: string) => void;
  onEditSchedule?: (scheduleId: string, at: string) => Promise<boolean>;
};

export default function SessionDetail({
  repo,
  session,
  importedRun,
  loadingSession = false,
  detailError = "",
  onRetryDetails,
  linkedRuns = [],
  repos,
  onEdit,
  onRerun,
  onDuplicate,
  onResume,
  onStop,
  onArchive,
  onUnarchive,
  onImportedResume,
  onImportedCancel,
  onCancelSchedule,
  onEditSchedule,
}: Props) {
  const [attemptId, setAttemptId] = useState("");
  const [output, setOutput] = useState("");
  const [outputError, setOutputError] = useState("");
  const [loading, setLoading] = useState(false);
  const [wrap, setWrap] = useState(true);
  const [resumeRequest, setResumeRequest] = useState("");
  const [repair, setRepair] = useState(false);
  const [copied, setCopied] = useState(false);
  const [retryOutput, setRetryOutput] = useState(0);
  const [editingSchedule, setEditingSchedule] = useState("");
  const [scheduleAt, setScheduleAt] = useState("");
  const [savingSchedule, setSavingSchedule] = useState(false);
  const validScheduleTime =
    !!scheduleAt && new Date(scheduleAt).getTime() > Date.now();
  const pane = useRef<HTMLPreElement>(null);
  const cursors = useRef(new Map<string, number>());
  const attempts = session?.attempts || [];
  const picked =
    attempts.find((item) => item.id === attemptId) || attempts.at(-1) || null;
  const selectedSessionId = session?.id || "";
  const pickedId = picked?.id || "";
  const pickedRepo = picked?.effective_recipe.repo || "";
  const pickedState = picked?.state || "";
  const importedRepo = importedRun?.repo || "";
  const importedId = importedRun?.id || "";
  const importedOutputAvailable = !!importedRun?.background_output_available;
  const importedActive = !!importedRun?.cancel_supported;
  const selectedState = session?.state || importedRun?.phase || "draft";
  const outputAvailable =
    !!picked || !!importedRun?.background_output_available;
  const outputDownload = picked
    ? outputUrl(picked.effective_recipe.repo, session!.id, picked.id)
    : importedRun
      ? importedOutputUrl(importedRun.repo, importedRun.id)
      : "";
  const isActive = picked
    ? active.has(picked.state)
    : !!importedRun && importedRun.cancel_supported;

  useEffect(() => {
    setAttemptId("");
    setOutput("");
    setOutputError("");
    setResumeRequest("");
    setRepair(false);
    setEditingSchedule("");
  }, [session?.id, importedRun?.id]);

  useEffect(() => {
    if (!selectedSessionId && !importedId) return;
    if (!pickedId && !importedId) {
      setOutput("");
      setOutputError("");
      setLoading(false);
      return;
    }
    if (importedId && !importedOutputAvailable) {
      setOutput("");
      setOutputError("");
      setLoading(false);
      return;
    }
    const key = pickedId
      ? `${selectedSessionId}:${pickedId}`
      : `run:${importedRepo}:${importedId}`;
    const controller = new AbortController();
    let cancelled = false;
    setOutput("");
    setOutputError("");
    setLoading(true);
    async function poll() {
      while (!cancelled) {
        try {
          const cursor = cursors.current.get(key) || 0;
          const chunk: OutputChunk = pickedId
            ? await loadOutput(
                pickedRepo,
                selectedSessionId,
                pickedId,
                cursor,
                controller.signal,
              )
            : await loadRunOutput(
                importedRepo,
                importedId,
                cursor,
                controller.signal,
              );
          if (cancelled) return;
          cursors.current.set(key, chunk.next_cursor);
          const before = pane.current;
          const follows =
            !before ||
            before.scrollHeight - before.scrollTop - before.clientHeight < 36;
          setOutput((current) =>
            chunk.reset ? chunk.text : current + chunk.text,
          );
          setOutputError("");
          setLoading(false);
          if (chunk.reset) cursors.current.set(key, chunk.next_cursor);
          requestAnimationFrame(() => {
            const element = pane.current;
            if (follows && element) element.scrollTop = element.scrollHeight;
          });
          if (!(pickedId ? active.has(pickedState) : importedActive)) return;
          await new Promise((resolve) => setTimeout(resolve, 1200));
        } catch (error) {
          if (controller.signal.aborted || cancelled) return;
          setLoading(false);
          setOutputError(
            error instanceof Error
              ? error.message
              : "Output connection interrupted.",
          );
          return;
        }
      }
    }
    void poll();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [
    repo,
    selectedSessionId,
    pickedId,
    pickedRepo,
    pickedState,
    importedRepo,
    importedId,
    importedOutputAvailable,
    importedActive,
    retryOutput,
  ]);

  async function copyOutput() {
    try {
      await navigator.clipboard.writeText(output);
      setCopied(true);
      setTimeout(() => setCopied(false), 1300);
    } catch {
      setOutputError(
        "Clipboard access failed. Select the output and copy it manually.",
      );
    }
  }
  const labels = useMemo(
    () => ({
      repo: repoName(session?.repo || importedRun?.repo || repo, repos),
      state: stateLabel[selectedState] || selectedState.replaceAll("_", " "),
    }),
    [session?.repo, importedRun?.repo, repo, repos, selectedState],
  );

  if (!session && !importedRun)
    return (
      <div
        className="session-empty-detail"
        role={loadingSession ? "status" : undefined}
      >
        {loadingSession ? (
          <p>Loading session details…</p>
        ) : detailError ? (
          <div className="session-error" role="alert">
            <p>{detailError}</p>
            <Button variant="outline" onClick={onRetryDetails}>
              Retry
            </Button>
          </div>
        ) : (
          <p>Select a session or run to inspect its history and output.</p>
        )}
      </div>
    );
  const title =
    session?.name ||
    importedRun?.feature_name ||
    `Run ${importedRun?.id.slice(0, 8)}`;
  const canResume = picked
    ? picked.resume_supported
    : !!importedRun?.resume_supported;

  return (
    <article className="session-detail">
      <header className="session-detail-header">
        <div>
          <h2>{title}</h2>
          <p>
            {labels.repo}{" "}
            <span className={`state-pill state-${selectedState}`}>
              {labels.state}
            </span>
          </p>
        </div>
        <div className="session-actions">
          {session && (
            <>
              <Button variant="outline" onClick={onEdit}>
                <Pencil />
                Edit
              </Button>
              {!session.archived && (
                <Button variant="outline" onClick={onRerun}>
                  <RotateCcw />
                  Rerun
                </Button>
              )}
              <Button variant="outline" onClick={onDuplicate}>
                Duplicate
              </Button>
            </>
          )}
          {session?.archived ? (
            <Button variant="outline" onClick={onUnarchive}>
              <Undo2 />
              Unarchive
            </Button>
          ) : session && !isActive ? (
            <Button variant="outline" onClick={onArchive}>
              <Archive />
              Archive
            </Button>
          ) : null}
          {isActive &&
            (session ? (
              <Button variant="outline" onClick={onStop}>
                <Square />
                Stop
              </Button>
            ) : (
              <Button variant="outline" onClick={onImportedCancel}>
                <Square />
                Cancel run
              </Button>
            ))}
          {canResume &&
            !session?.archived &&
            (session ? (
              <Button
                onClick={() => onResume(picked?.id, resumeRequest, repair)}
              >
                <Play />
                Resume
              </Button>
            ) : (
              <Button onClick={() => onImportedResume(resumeRequest, repair)}>
                <Play />
                Resume run
              </Button>
            ))}
        </div>
      </header>
      {session && (
        <div className="session-facts">
          <span>{session.mode === "cli" ? "MEOW CLI" : "Claude Code"}</span>
          <span>{session.command}</span>
          <span>
            {session.attempts.length}{" "}
            {session.attempts.length === 1 ? "attempt" : "attempts"}
          </span>
          {session.attempts.length > 1 && (
            <label>
              Attempt{" "}
              <select
                aria-label="Attempt"
                value={picked?.id || ""}
                onChange={(event) => setAttemptId(event.target.value)}
              >
                {session.attempts.map((attempt, index) => (
                  <option key={attempt.id} value={attempt.id}>
                    Attempt {index + 1} ·{" "}
                    {stateLabel[attempt.state] || attempt.state}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}
      {picked?.error && (
        <div className="session-error" role="status">
          {picked.error}
        </div>
      )}
      {!!session?.schedules?.length && (
        <div className="session-schedules">
          {session.schedules.map((job) => (
            <div className="session-schedule" key={job.id}>
              <span>
                {job.state === "scheduled"
                  ? "Scheduled for"
                  : `Schedule ${job.state}:`}{" "}
                {new Date(job.at).toLocaleString()}
              </span>
              {job.error && <span role="status">{job.error}</span>}
              {job.state === "scheduled" && (
                <>
                  {editingSchedule === job.id ? (
                    <>
                      <label className="session-field">
                        Run at
                        <Input
                          type="datetime-local"
                          aria-label="Schedule run at"
                          value={scheduleAt}
                          disabled={savingSchedule}
                          onChange={(event) =>
                            setScheduleAt(event.target.value)
                          }
                        />
                        <small>
                          Your local time (
                          {Intl.DateTimeFormat().resolvedOptions().timeZone}).
                        </small>
                      </label>
                      <Button
                        disabled={savingSchedule || !validScheduleTime}
                        onClick={async () => {
                          setSavingSchedule(true);
                          try {
                            if (
                              await onEditSchedule?.(
                                job.id,
                                new Date(scheduleAt).toISOString(),
                              )
                            )
                              setEditingSchedule("");
                          } finally {
                            setSavingSchedule(false);
                          }
                        }}
                      >
                        Save time
                      </Button>
                      <Button
                        variant="outline"
                        disabled={savingSchedule}
                        onClick={() => setEditingSchedule("")}
                      >
                        Cancel edit
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="outline"
                      onClick={() => {
                        const time = new Date(job.at);
                        setScheduleAt(
                          new Date(
                            time.getTime() - time.getTimezoneOffset() * 60000,
                          )
                            .toISOString()
                            .slice(0, 16),
                        );
                        setEditingSchedule(job.id);
                      }}
                    >
                      Edit time
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    disabled={savingSchedule}
                    onClick={() => onCancelSchedule?.(job.id)}
                  >
                    Cancel schedule
                  </Button>
                </>
              )}
            </div>
          ))}
        </div>
      )}
      {picked?.run_ids.map((id) => (
        <p className="linked-run" key={id}>
          Linked MEOW run <code>{id}</code>
        </p>
      ))}
      {!!(importedRun ? [importedRun] : linkedRuns).length && (
        <aside className="linked-run-stack">
          {(importedRun ? [importedRun] : linkedRuns).map((run) => (
            <section className="linked-run-facts" key={`${run.repo}:${run.id}`}>
              <h3>MEOW run facts</h3>
              <dl>
                <div>
                  <dt>Phase</dt>
                  <dd>{run.phase}</dd>
                </div>
                <div>
                  <dt>Workflow</dt>
                  <dd>{run.workflow?.name || "Default"}</dd>
                </div>
                <div>
                  <dt>Feature</dt>
                  <dd>{run.feature_name || "Not recorded"}</dd>
                </div>
                <div>
                  <dt>Branch</dt>
                  <dd>{run.branch || "Not recorded"}</dd>
                </div>
                <div>
                  <dt>Plan file</dt>
                  <dd>{run.plan_file || "Not recorded"}</dd>
                </div>
              </dl>
              {!run.background_output_available && (
                <p>No historical output was recorded for this run.</p>
              )}
              {run.background_output_available && (
                <p>
                  <a href={importedOutputUrl(run.repo, run.id)} download>
                    Download recorded run output
                  </a>
                </p>
              )}
              {importedRun && run.resume_needs_request && (
                <label className="session-field">
                  Clarified request for resume
                  <textarea
                    rows={3}
                    value={resumeRequest}
                    onChange={(event) => setResumeRequest(event.target.value)}
                    placeholder="Clarify the decision for the next attempt."
                  />
                </label>
              )}
              {importedRun && run.resume_supported && (
                <label className="inline-check">
                  <input
                    type="checkbox"
                    checked={repair}
                    onChange={(event) => setRepair(event.target.checked)}
                  />{" "}
                  Resume in repair mode
                </label>
              )}
            </section>
          ))}
        </aside>
      )}
      {picked && canResume && picked.resume_needs_request && (
        <label className="session-field resume-field">
          Clarified request for resume
          <textarea
            rows={3}
            value={resumeRequest}
            onChange={(event) => setResumeRequest(event.target.value)}
            placeholder="Clarify the decision for the next attempt."
          />
        </label>
      )}
      {picked && canResume && picked.run_ids.length > 0 && (
        <label className="inline-check">
          <input
            type="checkbox"
            checked={repair}
            onChange={(event) => setRepair(event.target.checked)}
          />{" "}
          Resume in repair mode
        </label>
      )}
      <section className="output-section" aria-label="Session output">
        <div className="output-heading">
          <div>
            <h3>Output</h3>
            <span>
              {picked
                ? `${(picked.output_size / 1024).toFixed(1)} KB`
                : importedRun?.background_output_available
                  ? "Recorded background output"
                  : "No output recorded"}
            </span>
          </div>
          <div className="output-tools">
            <label className="inline-check">
              <input
                type="checkbox"
                checked={wrap}
                onChange={(event) => setWrap(event.target.checked)}
              />
              Wrap lines
            </label>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void copyOutput()}
              disabled={!output}
            >
              <Copy />
              {copied ? <Check /> : "Copy"}
            </Button>
            {outputAvailable && (
              <a className="button-link" href={outputDownload} download>
                <Download />
                Download
              </a>
            )}
          </div>
        </div>
        {loading && !output && (
          <p className="output-message">Connecting to output…</p>
        )}
        {outputError && (
          <div className="output-message output-error" role="alert">
            {outputError}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setRetryOutput((value) => value + 1)}
            >
              Retry output
            </Button>
          </div>
        )}
        {!outputAvailable && !outputError ? (
          <div className="output-message">
            {importedRun
              ? "This MEOW run predates UI output capture."
              : "Launch an attempt to see its output here."}
          </div>
        ) : (
          <pre
            ref={pane}
            className={`output-console ${wrap ? "wrap" : "nowrap"}`}
            aria-label="Text output"
          >
            {output || (loading ? "" : "No output has been written yet.")}
          </pre>
        )}
      </section>
      {session && (
        <aside className="session-recipe">
          <h3>Next attempt settings</h3>
          <p>
            {session.recipe.mode === "cli" ? "MEOW CLI" : "Claude Code"} ·{" "}
            {session.recipe.command}
          </p>
          <pre>{session.recipe.request}</pre>
          <p>Repository: {labels.repo}</p>
          {session.recipe.options.permission_policy && (
            <p>
              Permission policy:{" "}
              {session.recipe.options.permission_policy === "default"
                ? "Ask when needed; unanswered prompts are denied"
                : "Bypass permissions"}
            </p>
          )}
        </aside>
      )}
    </article>
  );
}
