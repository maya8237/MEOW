import { StrictMode, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import DocsView from "@/components/DocsView";
import { request } from "@/lib/catalog";
import type { Docs } from "@/lib/docs";
import { DocLocation, formatDocHash, parseDocHash } from "@/lib/docs-hash";
import logo from "../../src/meow/ui/logo.png";
import "@/styles.css";
import "./site.css";

const REPO_URL = "https://github.com/maya8237/MEOW";

// Stable identity: DocsView refetches whenever `load` changes.
const loadSiteDocs = (signal: AbortSignal) =>
  request<Docs>("./docs.json", { signal });

export function Site() {
  const searchRef = useRef<HTMLInputElement>(null);
  const [location, setLocation] = useState<DocLocation>(() =>
    parseDocHash(window.location.hash),
  );

  useEffect(() => {
    const onHashChange = () => setLocation(parseDocHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const navigate = useCallback((next: DocLocation) => {
    window.location.hash = formatDocHash(next);
  }, []);

  return (
    <div className="docs-site">
      <header className="docs-site-header">
        <img src={logo} alt="" />
        <h1>MEOW documentation</h1>
        <a href={REPO_URL} rel="noreferrer">
          GitHub
        </a>
      </header>
      <main className="docs-site-main">
        <DocsView
          searchRef={searchRef}
          load={loadSiteDocs}
          location={location}
          onNavigate={navigate}
        />
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
