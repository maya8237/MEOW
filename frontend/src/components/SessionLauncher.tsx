import { useMemo, useState } from "react";
import { Eye, Play, X } from "lucide-react";
import { Catalog, CatalogItem, repoName } from "@/lib/catalog";
import {
  Capabilities,
  Mode,
  Preview,
  Recipe,
  createSession,
  previewRecipe,
  sessionAction,
  updateSession,
} from "@/lib/sessions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import AdvancedOptions from "@/components/AdvancedOptions";

type LauncherItem = CatalogItem & { claude_resolvable?: boolean };
type Props = {
  repo: string;
  initialRecipe?: Partial<Recipe>;
  catalogItem?: { kind: "workflow" | "skill" | "agent"; item: LauncherItem };
  catalog: Catalog;
  sessionId?: string;
  editOnly?: boolean;
  unknownFlags?: boolean;
  onClose: () => void;
  onSaved: (id: string) => void;
};

const defaults = (repo: string): Recipe => ({
  name: "",
  repo,
  mode: "cli",
  command: "run",
  request: "",
  workflow: null,
  inputs: {},
  arguments: [],
  options: { unattended: true, no_worktree: false },
});

export default function SessionLauncher({
  repo,
  initialRecipe,
  catalogItem,
  catalog,
  sessionId,
  editOnly = false,
  unknownFlags: hasUnknownFlags = false,
  onClose,
  onSaved,
}: Props) {
  const seed = useMemo(() => {
    const value = {
      ...defaults(repo),
      ...initialRecipe,
      repo: sessionId ? initialRecipe?.repo || repo : repo,
    } as Recipe;
    if (value.mode === "claude" && !initialRecipe?.options)
      value.options = { permission_policy: "default" };
    if (catalogItem?.kind === "workflow") {
      value.name = catalogItem.item.name;
      value.mode = "cli";
      value.command = "run";
      value.workflow = catalogItem.item.name;
    }
    if (catalogItem?.kind === "skill") {
      value.name = catalogItem.item.name;
      value.mode = "claude";
      value.command = `/${catalogItem.item.qualified_name || catalogItem.item.name}`;
    }
    if (catalogItem?.kind === "agent") {
      value.name = catalogItem.item.name;
      value.mode = "claude";
      value.command = "prompt";
      value.options = { ...value.options, agent: catalogItem.item.name };
    }
    if (value.mode === "cli" && value.command === "run") {
      value.options = {
        ...value.options,
        unattended: true,
        no_worktree: false,
      };
    }
    return value;
  }, [repo, initialRecipe, catalogItem, sessionId]);
  const [draft, setDraft] = useState<Recipe>(seed);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [reviewed, setReviewed] = useState("");
  const [currentSessionId, setCurrentSessionId] = useState(sessionId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [start, setStart] = useState("now");
  const [runAt, setRunAt] = useState("");
  const unknownFlags = hasUnknownFlags;
  const [bypassAcknowledged, setBypassAcknowledged] = useState(false);
  const [inputRows, setInputRows] = useState<[string, string][]>(
    Object.entries(seed.inputs || {}).length
      ? Object.entries(seed.inputs || {})
      : [["", ""]],
  );
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [claudeCapabilities, setClaudeCapabilities] =
    useState<Capabilities | null>(null);
  const serialized = JSON.stringify({ draft, start, runAt });
  const scheduleTime = runAt ? new Date(runAt) : null;
  const validTime =
    !!scheduleTime &&
    Number.isFinite(scheduleTime.getTime()) &&
    scheduleTime.getTime() > Date.now();
  const requestRequired =
    draft.mode !== "cli" || !["review", "orchestrate"].includes(draft.command);
  const itemCanRun =
    catalogItem?.kind !== "agent" || catalogItem.item.claude_resolvable;
  const modeError =
    catalogItem?.kind === "skill" && draft.mode === "cli"
      ? "Skills run in Claude mode. Switch to Claude to use this qualified slash command."
      : catalogItem?.kind === "agent" && draft.mode === "cli"
        ? "Agent roles run in Claude mode. Switch to Claude to select this agent."
        : draft.mode === "claude" && draft.workflow
          ? "Workflows run in CLI mode. Switch to CLI to use this workflow."
          : draft.mode === "cli" && draft.command.startsWith("/")
            ? "Slash commands run in Claude mode. Switch to Claude to continue."
            : "";

  function patch(values: Partial<Recipe>) {
    setDraft((current) => ({ ...current, ...values }));
    setPreview(null);
    setReviewed("");
    setError("");
  }
  async function review() {
    if (start === "later" && !validTime) {
      setError("Choose a future date and time.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await previewRecipe(draft.repo, draft);
      setPreview(result);
      setReviewed(serialized);
      setClaudeCapabilities(result.capabilities.claude);
    } catch (reason) {
      setPreview(null);
      setReviewed("");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not preview this launch.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function launch() {
    if (!preview || reviewed !== serialized) return;
    setBusy(true);
    setError("");
    try {
      const ownerRepo = sessionId ? initialRecipe?.repo || repo : draft.repo;
      const saved = currentSessionId
        ? await updateSession(ownerRepo, currentSessionId, draft)
        : await createSession(draft.repo, draft);
      setCurrentSessionId(saved.session.id);
      if (editOnly) onSaved(saved.session.id);
      else {
        const result = await sessionAction(
          draft.repo,
          saved.session.id,
          start === "later" ? "schedule" : "launch",
          start === "later" ? { at: scheduleTime!.toISOString() } : {},
        );
        onSaved(result.session.id);
      }
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Launch failed. Your settings are still here.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function saveDraft() {
    if (
      !unknownFlags ||
      currentSessionId ||
      !draft.repo ||
      (requestRequired && !draft.request.trim())
    )
      return;
    setBusy(true);
    setError("");
    try {
      const saved = await createSession(draft.repo, draft);
      setCurrentSessionId(saved.session.id);
      onSaved(saved.session.id);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not save this draft.",
      );
    } finally {
      setBusy(false);
    }
  }
  function modeChanged(mode: Mode) {
    patch({
      mode,
      command:
        mode === "cli"
          ? draft.command.startsWith("/")
            ? draft.command
            : "run"
          : seed.command.startsWith("/")
            ? seed.command
            : "prompt",
      workflow: draft.workflow,
      inputs: mode === "cli" ? draft.inputs : {},
      arguments: mode === "cli" ? draft.arguments : [],
      options:
        mode === "cli"
          ? { unattended: true, no_worktree: false }
          : {
              permission_policy: "default",
              ...(catalogItem?.kind === "agent"
                ? { agent: catalogItem.item.name }
                : {}),
            },
    });
  }
  const repoChoices = catalog.repos.length
    ? catalog.repos
    : [repo].filter(Boolean);
  const previewMatches = !!preview && reviewed === serialized;

  return (
    <div className="session-overlay" role="presentation">
      <section
        className="session-launcher"
        role="dialog"
        aria-modal="true"
        aria-labelledby="launcher-title"
      >
        <header className="launcher-header">
          <div>
            <h2 id="launcher-title">
              {sessionId ? "Edit next attempt" : "New session"}
            </h2>
            <p>Review the exact command before it starts.</p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close launcher"
            onClick={onClose}
          >
            <X />
          </Button>
        </header>
        <form
          className="launcher-form"
          onSubmit={(event) => {
            event.preventDefault();
            void review();
          }}
        >
          <div className="launcher-grid">
            {!editOnly && (
              <>
                <label className="session-field">
                  Start
                  <select
                    aria-label="Start"
                    value={start}
                    onChange={(event) => {
                      setStart(event.target.value);
                      setPreview(null);
                      setReviewed("");
                    }}
                  >
                    <option value="now">Run now</option>
                    <option value="later">Schedule for later</option>
                  </select>
                </label>
                {start === "later" && (
                  <label className="session-field">
                    Run at
                    <Input
                      type="datetime-local"
                      aria-label="Run at"
                      value={runAt}
                      onChange={(event) => {
                        setRunAt(event.target.value);
                        setPreview(null);
                        setReviewed("");
                      }}
                    />
                    <small>
                      Your local time (
                      {Intl.DateTimeFormat().resolvedOptions().timeZone}). The
                      computer must be available to run.
                    </small>
                  </label>
                )}
              </>
            )}
            <label className="session-field">
              Repository
              <select
                aria-label="Repository"
                value={draft.repo}
                disabled={!!sessionId}
                onChange={(event) => patch({ repo: event.target.value })}
              >
                <option value="">Choose a repository</option>
                {repoChoices.map((value) => (
                  <option value={value} key={value}>
                    {repoName(value, repoChoices)}
                  </option>
                ))}
              </select>
            </label>
            <label className="session-field">
              Execution mode
              <select
                aria-label="Execution mode"
                value={draft.mode}
                onChange={(event) => modeChanged(event.target.value as Mode)}
              >
                <option value="cli">MEOW CLI</option>
                <option value="claude">Claude Code</option>
              </select>
            </label>
            <label className="session-field">
              Session name
              <Input
                aria-label="Session name"
                value={draft.name}
                onChange={(event) => patch({ name: event.target.value })}
                placeholder="e.g. Improve search"
              />
            </label>
            <label className="session-field">
              Command
              <select
                aria-label="Command"
                value={draft.command}
                disabled={draft.command.startsWith("/")}
                onChange={(event) =>
                  patch({
                    command: event.target.value,
                    arguments: [],
                    ...(draft.mode === "cli"
                      ? {
                          options:
                            event.target.value === "run"
                              ? { unattended: true, no_worktree: false }
                              : event.target.value === "plan"
                                ? { no_worktree: false }
                                : {},
                        }
                      : {}),
                  })
                }
              >
                {draft.mode === "cli" ? (
                  <>
                    {draft.command.startsWith("/") && (
                      <option value={draft.command}>{draft.command}</option>
                    )}
                    <option value="run">run</option>
                    <option value="plan">plan</option>
                    <option value="review">review</option>
                    <option value="orchestrate">orchestrate</option>
                  </>
                ) : draft.command.startsWith("/") ? (
                  <option value={draft.command}>{draft.command}</option>
                ) : (
                  <option value="prompt">Claude request</option>
                )}
              </select>
            </label>
            <label className="session-field launcher-wide">
              Request
              <textarea
                aria-label="Request"
                rows={4}
                value={draft.request}
                onChange={(event) => patch({ request: event.target.value })}
                placeholder="Describe the work this session should do."
              />
              {!requestRequired && (
                <small>
                  Leave empty when a source flag supplies the work. MEOW checks
                  the selected combination before launch.
                </small>
              )}
            </label>
            {draft.mode === "cli" && (
              <>
                <label className="session-field">
                  Workflow
                  <select
                    aria-label="Workflow"
                    value={draft.workflow || ""}
                    onChange={(event) =>
                      patch({ workflow: event.target.value || null })
                    }
                  >
                    <option value="">Default workflow</option>
                    {catalog.workflows.map((item) => (
                      <option key={item.file} value={item.name}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                </label>
                {draft.command === "plan" && (
                  <fieldset className="launcher-options launcher-wide">
                    <legend>CLI options</legend>
                    <label>
                      <input
                        type="checkbox"
                        checked={!!draft.options.no_worktree}
                        onChange={(event) =>
                          patch({
                            options: {
                              ...draft.options,
                              no_worktree: event.target.checked,
                            },
                          })
                        }
                      />{" "}
                      Work in this repository directly
                    </label>
                  </fieldset>
                )}
                {draft.workflow && (
                  <div className="launcher-wide launcher-subfields">
                    <div>
                      <strong>Workflow inputs</strong>
                      {inputRows.map(([key, value], index) => (
                        <div className="recipe-row" key={index}>
                          <Input
                            aria-label={`Input key ${index + 1}`}
                            value={key}
                            onChange={(event) => {
                              const rows = inputRows.map((row, i) =>
                                i === index
                                  ? ([event.target.value, value] as [
                                      string,
                                      string,
                                    ])
                                  : row,
                              );
                              setInputRows(rows);
                              patch({
                                inputs: Object.fromEntries(
                                  rows.filter(([k]) => k),
                                ),
                              });
                            }}
                            placeholder="key"
                          />
                          <Input
                            aria-label={`Input value ${index + 1}`}
                            value={value}
                            onChange={(event) => {
                              const rows = inputRows.map((row, i) =>
                                i === index
                                  ? ([key, event.target.value] as [
                                      string,
                                      string,
                                    ])
                                  : row,
                              );
                              setInputRows(rows);
                              patch({
                                inputs: Object.fromEntries(
                                  rows.filter(([k]) => k),
                                ),
                              });
                            }}
                            placeholder="value"
                          />
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => {
                              const rows = inputRows.filter(
                                (_, i) => i !== index,
                              );
                              setInputRows(rows);
                              patch({
                                inputs: Object.fromEntries(
                                  rows.filter(([k]) => k),
                                ),
                              });
                            }}
                          >
                            Remove
                          </Button>
                        </div>
                      ))}
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() =>
                          setInputRows((rows) => [...rows, ["", ""]])
                        }
                      >
                        Add input
                      </Button>
                    </div>
                  </div>
                )}
                <details
                  className="launcher-wide"
                  onToggle={(event) =>
                    setAdvancedOpen(event.currentTarget.open)
                  }
                >
                  <summary>Advanced arguments</summary>
                  <p>
                    Choose the flags for this command. Values and choices come
                    from MEOW's command parser.
                  </p>
                  {unknownFlags && (
                    <p className="unknown-flags">
                      Original flags were not recorded. Select only the flags
                      you want on this new attempt.
                    </p>
                  )}
                  {advancedOpen && (
                    <AdvancedOptions
                      key={draft.command}
                      command={draft.command}
                      arguments={draft.arguments}
                      onChange={(arguments_) =>
                        patch({ arguments: arguments_ })
                      }
                    />
                  )}
                </details>
              </>
            )}
            {draft.mode === "claude" && (
              <fieldset className="launcher-options launcher-wide">
                <legend>Claude permissions</legend>
                <p>Unanswered permission prompts are denied by default.</p>
                <label className="danger-check">
                  <input
                    type="checkbox"
                    checked={draft.options.permission_policy === "bypass"}
                    onChange={(event) => {
                      setBypassAcknowledged(event.target.checked);
                      patch({
                        options: {
                          ...draft.options,
                          permission_policy: event.target.checked
                            ? "bypass"
                            : "default",
                        },
                      });
                    }}
                  />{" "}
                  Bypass Claude permission checks
                </label>
                {!!draft.options.agent && (
                  <p>Agent: {String(draft.options.agent)}</p>
                )}
              </fieldset>
            )}
          </div>
          {modeError && (
            <p className="session-error" role="alert">
              {modeError}
            </p>
          )}
          {draft.mode === "claude" && !itemCanRun && (
            <p className="session-error" role="alert">
              This agent is a MEOW role definition and Claude cannot resolve it.
              Choose a verified Markdown agent from the Claude catalog.
            </p>
          )}
          {draft.mode === "claude" &&
            claudeCapabilities &&
            !claudeCapabilities.available && (
              <p className="session-error" role="alert">
                Claude Code cannot run this recipe:{" "}
                {claudeCapabilities.error ||
                  claudeCapabilities.missing.join(", ") ||
                  "required capabilities are unavailable"}
              </p>
            )}
          {unknownFlags && (
            <p className="unknown-flags">
              Historical run facts do not include the original command flags.
              Review the editable settings before running.
            </p>
          )}
          {draft.mode === "cli" && draft.command === "run" && (
            <p className="session-note">
              UI feature runs execute unattended in an isolated worktree and
              follow the repository delivery policy. They may commit and push
              verified work in their worktree.
            </p>
          )}
          {error && (
            <p className="session-error" role="alert">
              {error}
            </p>
          )}
          {previewMatches && (
            <div className="command-review" aria-live="polite">
              <div>
                <Eye />
                <strong>Command preview</strong>
              </div>
              <pre>{preview.display}</pre>
              {start === "later" && scheduleTime && (
                <p>Scheduled for {scheduleTime.toLocaleString()}.</p>
              )}
              {preview.warnings.map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
            </div>
          )}
          <footer className="launcher-actions">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="outline"
              disabled={
                busy ||
                !!modeError ||
                !itemCanRun ||
                !draft.repo ||
                (requestRequired && !draft.request.trim())
              }
            >
              <Eye />
              {busy ? "Checking…" : "Preview command"}
            </Button>
            {unknownFlags && !currentSessionId && (
              <Button
                type="button"
                variant="outline"
                disabled={
                  busy ||
                  !draft.repo ||
                  (requestRequired && !draft.request.trim())
                }
                onClick={() => void saveDraft()}
              >
                Save as draft
              </Button>
            )}
            <Button
              type="button"
              disabled={
                !previewMatches ||
                busy ||
                (start === "later" && !validTime) ||
                (draft.options.permission_policy === "bypass" &&
                  !bypassAcknowledged) ||
                (draft.mode === "claude" && !itemCanRun)
              }
              onClick={() => void launch()}
            >
              <Play />
              {editOnly
                ? "Save changes"
                : start === "later"
                  ? "Schedule session"
                  : currentSessionId
                    ? "Save and launch"
                    : "Launch session"}
            </Button>
          </footer>
        </form>
      </section>
    </div>
  );
}
