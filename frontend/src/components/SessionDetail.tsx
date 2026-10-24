import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
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
}: Props) {
  const [attemptId, setAttemptId] = useState("");
  const [output, setOutput] = useState("");
  const [outputError, setOutputError] = useState("");
  const [loading, setLoading] = useState(false);
  const [wrap, setWrap] = useState(true);
  const [copied, setCopied] = useState(false);
  const [retryOutput, setRetryOutput] = useState(0);
  const pane = useRef<HTMLPreElement>(null);
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
  const importedActive =
    !!importedRun && ["running", "starting"].includes(importedRun.phase);
  const selectedState = session?.state || importedRun?.phase || "draft";
  const outputAvailable = !!picked || importedOutputAvailable;
  const outputDownload = picked
    ? outputUrl(picked.effective_recipe.repo, session!.id, picked.id)
    : importedRun
      ? importedOutputUrl(importedRun.repo, importedRun.id)
      : "";

  useEffect(() => {
    setAttemptId("");
    setOutput("");
    setOutputError("");
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

    const controller = new AbortController();
    let cancelled = false;
    let cursor = 0;
    setOutput("");
    setOutputError("");
    setLoading(true);

    async function poll() {
      while (!cancelled) {
        try {
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
          cursor = chunk.next_cursor;
          const before = pane.current;
          const follows =
            !before ||
            before.scrollHeight - before.scrollTop - before.clientHeight < 36;
          setOutput((current) =>
            chunk.reset ? chunk.text : current + chunk.text,
          );
          setOutputError("");
          setLoading(false);
          requestAnimationFrame(() => {
            const element = pane.current;
            if (follows && element) element.scrollTop = element.scrollHeight;
          });
          if (chunk.truncated) continue;
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
            </div>
          ))}
        </div>
      )}
      {picked?.run_ids.map((id) => (
        <p className="linked-run" key={id}>
          Linked MEOW run <code>{id}</code>
        </p>
      ))}
      {(session || !!(importedRun ? [importedRun] : linkedRuns).length) && (
        <aside className="linked-run-stack" aria-label="Run details">
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
            </section>
          ))}
          {session && (
            <section className="session-recipe">
              <h3>Saved run settings</h3>
              <p>Recipe details recorded for this session.</p>
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
            </section>
          )}
        </aside>
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
              Wrap long output lines
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
          <p className="output-message">Loading recorded output…</p>
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
              : "No output is available for this attempt."}
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
    </article>
  );
}
