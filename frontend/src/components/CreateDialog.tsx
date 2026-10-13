import { useEffect, useId, useMemo, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Catalog,
  CatalogItem,
  CreatedItem,
  CreatePayload,
  Kind,
  kinds,
  loadCatalog,
  repoName,
  request,
} from "@/lib/catalog";

export default function CreateDialog({
  catalog,
  initialKind,
  selected,
  initialRepo,
  onClose,
  onCreated,
}: {
  catalog: Catalog;
  initialKind: Kind;
  selected: CatalogItem | null;
  initialRepo: string;
  onClose: () => void;
  onCreated: (kind: Kind, item: CreatedItem, payload: CreatePayload) => void;
}) {
  const id = useId();
  const returnFocus = useRef(document.activeElement as HTMLElement | null);
  const [kind, setKind] = useState(initialKind);
  const [repo, setRepo] = useState(initialRepo || catalog.repos[0] || "");
  const [scope, setScope] = useState<CreatePayload["scope"]>("project");
  const [source, setSource] = useState(selected?.name || "");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sources, setSources] = useState(catalog);
  const [loading, setLoading] = useState(true);
  const [sourceError, setSourceError] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [revision, setRevision] = useState(0);
  const singular = kinds.find((entry) => entry.key === kind)!.singular;
  const options = useMemo(
    () => sources[kind].filter((item) => !item.repo || item.repo === repo),
    [sources, kind, repo],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setSourceError("");
    loadCatalog(repo, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        if (data.error) throw new Error(data.error);
        setSources(data);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setSourceError(String(reason.message || reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [repo, revision]);

  useEffect(() => {
    if (loading) return;
    if (!options.some((item) => item.name === source))
      setSource(kind === "skills" ? "" : options[0]?.name || "");
  }, [kind, options, source, loading]);

  function field(label: string, key: string, control: React.ReactNode) {
    return (
      <div className="form-field">
        <label htmlFor={`${id}-${key}`}>{label}</label>
        {control}
      </div>
    );
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <DialogContent
        className="create-dialog"
        showCloseButton={!saving}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          const target = returnFocus.current?.isConnected
            ? returnFocus.current
            : document.getElementById("new-item");
          target?.focus();
        }}
      >
        <DialogHeader>
          <DialogTitle>Create a {singular}</DialogTitle>
          <DialogDescription>
            Create a starter file, then run <code>/meow:customize</code> in
            Claude Code to write the contents of your {singular}.
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (saving) return;
            setSaving(true);
            setError("");
            const payload = { name, description, source, scope, repo };
            try {
              const result = await request<CreatedItem>(`/api/${kind}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
              });
              onCreated(kind, result, payload);
            } catch (reason) {
              setError(
                reason instanceof Error
                  ? reason.message
                  : "Could not save. Try again.",
              );
            } finally {
              setSaving(false);
            }
          }}
        >
          <fieldset disabled={saving} className="create-fields">
            <div className="form-pair">
              {field(
                "Kind",
                "kind",
                <select
                  id={`${id}-kind`}
                  value={kind}
                  onChange={(event) => {
                    setKind(event.target.value as Kind);
                    setSource("");
                  }}
                >
                  {kinds.map((entry) => (
                    <option key={entry.key} value={entry.key}>
                      {entry.label}
                    </option>
                  ))}
                </select>,
              )}
              {field(
                "Save to",
                "scope",
                <select
                  id={`${id}-scope`}
                  value={scope}
                  onChange={(event) =>
                    setScope(event.target.value as CreatePayload["scope"])
                  }
                >
                  <option value="project">
                    Project · shared with the repo
                  </option>
                  <option value="local">Local · only on this machine</option>
                  <option value="user">User · across repositories</option>
                </select>,
              )}
            </div>
            {field(
              scope === "user" ? "Source repository" : "Repository",
              "repo",
              <select
                id={`${id}-repo`}
                value={repo}
                onChange={(event) => {
                  setRepo(event.target.value);
                  setSource("");
                }}
              >
                {catalog.repos.map((path) => (
                  <option key={path} value={path} title={path}>
                    {repoName(path, catalog.repos)}
                  </option>
                ))}
              </select>,
            )}
            {field(
              "Name",
              "name",
              <Input
                id={`${id}-name`}
                autoFocus
                required
                pattern="[a-z][a-z0-9_\-]{0,62}"
                maxLength={63}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. nightly-docs"
              />,
            )}
            <p className="hint">
              Start with a lowercase letter. Use letters, numbers, hyphens, or
              underscores.
            </p>
            {field(
              "Description",
              "description",
              <Input
                id={`${id}-description`}
                required
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="What should this help you do?"
              />,
            )}
            {field(
              "Start from",
              "source",
              <select
                id={`${id}-source`}
                value={source}
                disabled={loading || !!sourceError}
                onChange={(event) => setSource(event.target.value)}
              >
                {loading ? (
                  <option value="">Loading definitions…</option>
                ) : (
                  <>
                    {kind === "skills" && <option value="">Blank skill</option>}
                    {options.map((item) => (
                      <option
                        key={item.file || item.directory}
                        value={item.name}
                      >
                        {item.name} ({item.scope})
                      </option>
                    ))}
                  </>
                )}
              </select>,
            )}
            <p className="hint">
              {kind === "skills"
                ? "Creates a skill and registers its MEOW plugin with Claude Code."
                : "Copies the definition into a new starter file."}
            </p>
          </fieldset>
          {sourceError && (
            <div className="form-error" role="alert">
              Could not load source definitions: {sourceError}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setRevision((value) => value + 1)}
              >
                Retry sources
              </Button>
            </div>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={saving}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={
                saving ||
                loading ||
                !!sourceError ||
                !repo ||
                (kind !== "skills" && !source)
              }
            >
              {saving && <LoaderCircle className="animate-spin" />}
              {saving ? "Creating…" : `Create ${singular}`}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
