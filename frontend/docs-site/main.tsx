import { StrictMode, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import DocsView from "@/components/DocsView";
import { request } from "@/lib/catalog";
import type { Docs } from "@/lib/docs";
import { DocLocation, formatDocHash, parseDocHash } from "@/lib/docs-hash";
import GuidePages, { type Page } from "./GuidePages";
import { REPO_URL, sourceUrl } from "./content";
import logo from "../../src/meow/ui/logo.png";
import "@/styles.css";
import "./site.css";

// Stable identity: DocsView refetches whenever `load` changes.
const loadSiteDocs = (signal: AbortSignal) =>
  request<Docs>("./docs.json", { signal });

export function Site() {
  const searchRef = useRef<HTMLInputElement>(null);
  const [hash, setHash] = useState(() => window.location.hash);
  const isReference = hash.startsWith("#/");
  const page: Page =
    hash === "#skills"
      ? "skills"
      : hash === "#customize"
        ? "customize"
        : "start";
  const location = parseDocHash(hash);
  const headingRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const onHashChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    if (isReference || hash === "#setup") return;
    window.scrollTo?.(0, 0);
    if (hash) headingRef.current?.focus();
  }, [hash, isReference]);

  const navigate = useCallback((next: DocLocation) => {
    window.location.hash = formatDocHash(next);
  }, []);

  return (
    <div className={`docs-site${isReference ? " docs-site-reference" : ""}`}>
      <a
        className="guide-skip"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          headingRef.current?.focus();
        }}
      >
        Skip to content
      </a>
      <header className="docs-site-header">
        <a className="guide-brand" href="#start">
          <img src={logo} alt="" />
          <strong>MEOW</strong>
        </a>
        <nav aria-label="Main navigation">
          {(
            [
              ["start", "Start here"],
              ["skills", "Skills"],
              ["customize", "Customize"],
            ] as const
          ).map(([id, label]) => (
            <a
              key={id}
              href={`#${id}`}
              aria-current={!isReference && page === id ? "page" : undefined}
            >
              {label}
            </a>
          ))}
          <a href="#/README.md" aria-current={isReference ? "page" : undefined}>
            Reference
          </a>
        </nav>
        <a className="guide-github" href={REPO_URL}>
          GitHub
        </a>
      </header>
      <main
        id="main-content"
        ref={headingRef}
        tabIndex={-1}
        className="docs-site-main"
      >
        {isReference ? (
          <>
            <div className="guide-reference-bar">
              <span>Full documentation</span>
              <a href={sourceUrl(location.path)}>Read this file on GitHub</a>
            </div>
            <DocsView
              searchRef={searchRef}
              load={loadSiteDocs}
              location={location}
              onNavigate={navigate}
              sourceUrl={sourceUrl}
            />
          </>
        ) : (
          <GuidePages key={page} page={page} />
        )}
      </main>
    </div>
  );
}

const root = document.getElementById("root");
if (root)
  createRoot(root).render(
    <StrictMode>
      <Site />
    </StrictMode>,
  );
