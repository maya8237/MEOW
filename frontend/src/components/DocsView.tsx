import { RefObject, useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, Search } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
import { Input } from "@/components/ui/input";
import { Doc, Docs, loadDocs, resolveDocLink, searchDocs } from "@/lib/docs";
import type { DocLocation } from "@/lib/docs-hash";

type Props = {
  searchRef: RefObject<HTMLInputElement | null>;
  load?: (signal: AbortSignal) => Promise<Docs>;
  location?: DocLocation;
  onNavigate?: (loc: DocLocation) => void;
};
type Open = DocLocation & { nonce: number };

export default function DocsView({
  searchRef,
  load = loadDocs,
  location,
  onNavigate,
}: Props) {
  const [docs, setDocs] = useState<Docs | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Open>({
    path: location?.path ?? "README.md",
    anchor: location?.anchor ?? "",
    nonce: 0,
  });

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal)
      .then(setDocs)
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load documentation.",
          );
      });
    return () => controller.abort();
  }, [load]);

  const hits = useMemo(
    () => (docs && query.trim() ? searchDocs(docs, query) : []),
    [docs, query],
  );
  const current = docs
    ? [...docs.guides, ...docs.skills].find((d) => d.path === open.path) ||
      docs.guides[0] ||
      null
    : null;

  const content = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!current) return;
    const target = open.anchor ? document.getElementById(open.anchor) : null;
    if (target) target.scrollIntoView?.();
    else if (content.current) content.current.scrollTop = 0;
  }, [current, open]);

  // Follow an externally controlled location; nonce re-runs the anchor scroll.
  const wantPath = location?.path;
  const wantAnchor = location?.anchor ?? "";
  useEffect(() => {
    if (wantPath === undefined) return;
    setOpen((previous) =>
      previous.path === wantPath && previous.anchor === wantAnchor
        ? previous
        : { path: wantPath, anchor: wantAnchor, nonce: previous.nonce + 1 },
    );
  }, [wantPath, wantAnchor]);

  function go(path: string, anchor = "") {
    setOpen((previous) => ({ path, anchor, nonce: previous.nonce + 1 }));
    onNavigate?.({ path, anchor });
  }

  if (error)
    return (
      <div className="empty-state" role="alert">
        <h2>Documentation unavailable</h2>
        <p>{error}</p>
      </div>
    );
  if (!docs)
    return (
      <div className="empty-state">
        <LoaderCircle className="animate-spin" />
        <h2>Loading documentation…</h2>
      </div>
    );

  const group = (label: string, list: Doc[]) =>
    !!list.length && (
      <section>
        <h2 className="docs-group">{label}</h2>
        <ul aria-label={label} className="docs-list">
          {list.map((doc) => (
            <li key={doc.path}>
              <button
                type="button"
                className="docs-entry"
                aria-current={doc.path === current?.path ? "true" : undefined}
                onClick={() => go(doc.path)}
              >
                <span>{doc.title}</span>
                {doc.description && <small>{doc.description}</small>}
              </button>
            </li>
          ))}
        </ul>
      </section>
    );

  return (
    <div className="docs-layout">
      <nav className="docs-pane" aria-label="Documentation">
        <div className="catalog-filters">
          <div className="search-field">
            <Search aria-hidden="true" />
            <Input
              ref={searchRef}
              type="search"
              aria-label="Search documentation"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search documentation"
            />
            <kbd>Ctrl K</kbd>
          </div>
        </div>
        {query.trim() ? (
          hits.length ? (
            <ul className="docs-list docs-hits" aria-label="Search results">
              {hits.map((hit, index) => (
                <li key={`${hit.path}#${hit.anchor}:${index}`}>
                  <button
                    type="button"
                    className="docs-entry"
                    onClick={() => go(hit.path, hit.anchor)}
                  >
                    <span>{hit.heading}</span>
                    <small>{hit.title}</small>
                    <p>{hit.snippet}</p>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="docs-empty" role="status" aria-live="polite">
              No documentation matches &quot;{query.trim()}&quot;.
            </p>
          )
        ) : (
          <>
            {group("Guides", docs.guides)}
            {group("Skills", docs.skills)}
          </>
        )}
      </nav>
      <article
        ref={content}
        className="docs-content"
        aria-label={current?.title}
      >
        {current && (
          <Markdown
            skipHtml
            key={`${current.path}:${open.nonce}`}
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeSlug]}
            components={{
              a({ href = "", children }) {
                const target = resolveDocLink(docs, current.path, href);
                if (target)
                  return (
                    <a
                      href={`#${target.anchor}`}
                      onClick={(event) => {
                        event.preventDefault();
                        go(target.path, target.anchor);
                      }}
                    >
                      {children}
                    </a>
                  );
                if (/^([a-z][a-z0-9+.-]*:|\/\/)/i.test(href))
                  return (
                    <a href={href} target="_blank" rel="noreferrer">
                      {children}
                    </a>
                  );
                return <span>{children}</span>;
              },
            }}
          >
            {current.text}
          </Markdown>
        )}
      </article>
    </div>
  );
}
