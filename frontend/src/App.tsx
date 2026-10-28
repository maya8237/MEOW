import { Suspense, lazy, useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  BookText,
  Bot,
  FolderGit2,
  LoaderCircle,
  RefreshCw,
  Search,
  Webhook,
  Workflow,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Catalog,
  Kind,
  Scope,
  itemKey,
  kinds,
  loadCatalog,
  repoName,
  scopeLabel,
  scopes,
} from "@/lib/catalog";
import ItemDetail from "@/components/ItemDetail";
import OnlineDocsLink from "@/components/OnlineDocsLink";
import SessionsView from "@/components/SessionsView";

const DocsView = lazy(() => import("@/components/DocsView"));
const icons = {
  workflows: Workflow,
  agents: Bot,
  skills: BookOpen,
  hooks: Webhook,
};

export default function App() {
  const [data, setData] = useState<Catalog | null>(null);
  const [repo, setRepo] = useState("");
  const [kind, setKind] = useState<Kind>("workflows");
  const [showSessions, setShowSessions] = useState(false);
  const [showDocs, setShowDocs] = useState(false);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<Scope | "">("");
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const search = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    async function refresh(manual = false) {
      if (pending) return;
      pending = true;
      if (manual) setBusy(true);
      try {
        const catalog = await loadCatalog(repo, controller.signal);
        if (controller.signal.aborted) return;
        setData((previous) =>
          JSON.stringify(previous) === JSON.stringify(catalog)
            ? previous
            : catalog,
        );
        setError("");
      } catch (reason) {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load the catalog.",
          );
      } finally {
        pending = false;
        if (!controller.signal.aborted) setBusy(false);
      }
    }
    refresh(true);
    const interval = setInterval(() => {
      if (document.visibilityState !== "hidden") refresh();
    }, 2000);
    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [repo, revision]);

  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        search.current?.focus();
      }
    }
    document.addEventListener("keydown", shortcut);
    return () => document.removeEventListener("keydown", shortcut);
  }, []);

  const tab = kinds.find((entry) => entry.key === kind)!;
  const all = data?.[kind] || [];
  const needle = query.trim().toLowerCase();
  const items = all.filter(
    (item) =>
      (!scope || item.scope === scope) &&
      (!needle ||
        `${item.name} ${item.description} ${item.qualified_name || ""} ${item.event || ""}`
          .toLowerCase()
          .includes(needle)),
  );
  const active =
    items.find((item) => itemKey(item) === selected) || items[0] || null;
  const Icon = icons[kind];
  function clearFilters() {
    setQuery("");
    setScope("");
  }

  return (
    <div className="app-shell">
      <aside className="navigation" aria-label="Catalog navigation">
        <a className="brand" href="/" aria-label="MEOW catalog home">
          <img src="/logo.png" alt="" />
          <strong>MEOW</strong>
        </a>
        <div className="repo-control">
          <label htmlFor="repository">
            <FolderGit2 />
            Repository
          </label>
          <select
            id="repository"
            aria-label="Repository"
            value={repo}
            onChange={(event) => {
              setRepo(event.target.value);
              setSelected("");
            }}
          >
            <option value="">All repositories</option>
            {data?.repos.map((path) => (
              <option key={path} value={path} title={path}>
                {repoName(path, data.repos)}
              </option>
            ))}
          </select>
          {repo && <p title={repo}>{repo}</p>}
        </div>
        <nav aria-label="Catalog kinds">
          {kinds.map((entry) => {
            const NavIcon = icons[entry.key];
            return (
              <button
                key={entry.key}
                type="button"
                className="nav-item"
                aria-pressed={!showSessions && !showDocs && kind === entry.key}
                onClick={() => {
                  setShowSessions(false);
                  setShowDocs(false);
                  setKind(entry.key);
                  setSelected("");
                }}
              >
                <NavIcon />
                <span>{entry.label}</span>
                <span className="nav-count">
                  {data?.[entry.key].length ?? "—"}
                </span>
              </button>
            );
          })}
          <button
            type="button"
            className="nav-item"
            aria-pressed={showSessions}
            onClick={() => {
              setShowSessions(true);
              setShowDocs(false);
            }}
          >
            <Workflow />
            <span>Sessions</span>
          </button>
          <button
            type="button"
            className="nav-item"
            aria-pressed={showDocs}
            onClick={() => {
              setShowDocs(true);
              setShowSessions(false);
            }}
          >
            <BookText />
            <span>Docs</span>
          </button>
        </nav>
        <div className="navigation-bottom">
          <div className="connection">
            <span className={error ? "status-dot offline" : "status-dot"} />
            {error
              ? "Connection interrupted"
              : data
                ? "Catalog syncs automatically"
                : "Connecting…"}
          </div>
          <p>Definitions for the way you work.</p>
        </div>
      </aside>
      <main className="workspace">
        <header className="workspace-header">
          <div>
            <h1>
              {showDocs
                ? "Documentation"
                : showSessions
                  ? "Sessions"
                  : tab.label}
            </h1>
            <p>
              {showDocs
                ? "Guides, commands, flags, and skills."
                : showSessions
                  ? "Review past sessions, runs, and their recorded output."
                  : tab.description}
            </p>
          </div>
          <div className="header-actions">
            {showDocs && <OnlineDocsLink />}
            {!showDocs && !showSessions && (
              <Button
                variant="outline"
                size="icon"
                aria-label="Refresh catalog"
                disabled={busy}
                onClick={() => setRevision((value) => value + 1)}
              >
                <RefreshCw className={busy ? "animate-spin" : ""} />
              </Button>
            )}
          </div>
        </header>
        {error && (
          <div className="banner error-banner" role="alert">
            <span>
              Could not update the catalog: {error}
              {data && " Showing the last loaded catalog."}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => setRevision((value) => value + 1)}
            >
              Retry
            </Button>
          </div>
        )}
        {data?.error && (
          <div className="banner error-banner" role="alert">
            {data.error}
          </div>
        )}
        {notice && (
          <div className="banner error-banner" role="alert">
            <span>{notice}</span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Dismiss message"
              onClick={() => setNotice("")}
            >
              <X />
            </Button>
          </div>
        )}
        {showDocs ? (
          <Suspense
            fallback={
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <LoaderCircle className="animate-spin" /> Loading documentation…
              </div>
            }
          >
            <DocsView searchRef={search} />
          </Suspense>
        ) : showSessions ? (
          data ? (
            <SessionsView repo={repo} catalog={data} />
          ) : (
            <div className="empty-state">
              <LoaderCircle className="animate-spin" />
              <h2>Loading sessions…</h2>
            </div>
          )
        ) : (
          <>
            <aside className="customize-callout" aria-label="Customize MEOW">
              <div className="customize-callout-icon">
                <Workflow aria-hidden="true" />
              </div>
              <div className="customize-callout-copy">
                <h2>Make MEOW fit this project</h2>
                <p>
                  Describe a change in Claude Code. MEOW helps shape the right
                  workflow, agent, skill, or hook.
                </p>
              </div>
              <code>/meow:customize</code>
            </aside>
            <div className="catalog-layout">
              <section
                className="catalog-pane"
                aria-label={`${tab.label} catalog`}
              >
                <div className="catalog-filters">
                  <div className="search-field">
                    <Search aria-hidden="true" />
                    <Input
                      ref={search}
                      type="search"
                      aria-label="Search catalog"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder={`Search ${tab.label.toLowerCase()}`}
                    />
                    <kbd>Ctrl K</kbd>
                  </div>
                  <div className="scope-row">
                    <select
                      aria-label="Scope"
                      value={scope}
                      onChange={(event) =>
                        setScope(event.target.value as Scope | "")
                      }
                    >
                      <option value="">All scopes</option>
                      {scopes.map((entry) => (
                        <option key={entry.value} value={entry.value}>
                          {entry.label}
                        </option>
                      ))}
                    </select>
                    <span>
                      {items.length} {items.length === 1 ? "item" : "items"}
                    </span>
                  </div>
                </div>
                {!data ? (
                  <div className="empty-state">
                    <LoaderCircle className="animate-spin" />
                    <h2>
                      {error ? "Catalog unavailable" : "Loading catalog…"}
                    </h2>
                    <p>
                      {error
                        ? "Check the server connection and retry."
                        : "Reading workflows, agents, skills, and hooks."}
                    </p>
                  </div>
                ) : items.length ? (
                  <ul className="catalog-list">
                    {items.map((item) => (
                      <li key={itemKey(item)}>
                        <button
                          className="catalog-item"
                          aria-current={
                            itemKey(item) === (active && itemKey(active))
                              ? "true"
                              : undefined
                          }
                          onClick={() => setSelected(itemKey(item))}
                        >
                          <div className="catalog-item-heading">
                            <span className="item-name">{item.name}</span>
                            <ArrowUpRight aria-hidden="true" />
                          </div>
                          <p>
                            {item.description || "No description provided."}
                          </p>
                          <div className="catalog-item-meta">
                            <Badge variant="secondary">
                              {scopeLabel(item.scope)}
                            </Badge>
                            <span>
                              {item.repo
                                ? repoName(item.repo, data.repos)
                                : "All repositories"}
                            </span>
                            {kind === "workflows" && (
                              <span>{item.steps?.length || 0} steps</span>
                            )}
                            {kind === "hooks" && <span>{item.event}</span>}
                          </div>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="empty-state">
                    <Search />
                    <h2>
                      {query || scope
                        ? "No matches found"
                        : `No ${tab.label.toLowerCase()} yet`}
                    </h2>
                    <p>
                      {query || scope
                        ? "Try another search or widen the scope."
                        : "Use /meow:customize in Claude Code to create a definition for this project."}
                    </p>
                    {query || scope ? (
                      <Button variant="outline" onClick={clearFilters}>
                        Clear filters
                      </Button>
                    ) : null}
                  </div>
                )}
                {!!data?.notes.length && (
                  <details className="catalog-notes">
                    <summary>
                      {data.notes.length} catalog{" "}
                      {data.notes.length === 1 ? "notice" : "notices"}
                    </summary>
                    {data.notes.map((note, index) => (
                      <p key={index}>{note}</p>
                    ))}
                  </details>
                )}
              </section>
              <section className="detail-pane" aria-label="Selected definition">
                {active ? (
                  <ItemDetail
                    key={`${kind}:${itemKey(active)}`}
                    item={active}
                    repos={data?.repos || []}
                    kind={kind}
                    onCopyError={setNotice}
                  />
                ) : (
                  <div className="empty-state detail-empty">
                    <Icon />
                    <h2>Choose a definition to inspect</h2>
                    <p>
                      Review its scope, workflow steps, settings, and source.
                    </p>
                  </div>
                )}
              </section>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
