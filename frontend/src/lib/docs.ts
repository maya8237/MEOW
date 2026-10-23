import { createElement, type ReactNode } from "react";
import GithubSlugger from "github-slugger";
import { request } from "@/lib/catalog";

export interface Doc {
  path: string;
  title: string;
  text: string;
  description?: string;
}
export interface Docs {
  guides: Doc[];
  skills: Doc[];
}
export interface Hit {
  path: string;
  title: string;
  heading: string;
  anchor: string;
  snippet: string;
}

export function loadDocs(signal?: AbortSignal) {
  return request<Docs>("/api/docs", { signal });
}

export function resolveDocLink(docs: Docs, fromPath: string, href: string) {
  if (/^([a-z][a-z0-9+.-]*:|\/\/)/i.test(href)) return null;
  const hash = href.indexOf("#");
  const target = hash < 0 ? href : href.slice(0, hash);
  const anchor = hash < 0 ? "" : href.slice(hash + 1);
  if (!target) return { path: fromPath, anchor };
  const parts = fromPath.split("/").slice(0, -1);
  for (const part of target.split("/")) {
    if (part === "..") parts.pop();
    else if (part && part !== ".") parts.push(part);
  }
  const path = parts.join("/");
  const known = [...docs.guides, ...docs.skills].some((d) => d.path === path);
  return known ? { path, anchor } : null;
}

const plain = (heading: string) =>
  heading
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[`*~]/g, "")
    .replace(/(^|\W)_+|_+(?=\W|$)/g, "$1")
    .trim();

function sections(doc: Doc) {
  const slugger = new GithubSlugger();
  const result = [{ heading: doc.title, anchor: "", lines: [] as string[] }];
  let fence = "";
  for (const line of doc.text.split("\n")) {
    const marker = /^ {0,3}(`{3,}|~{3,})/.exec(line)?.[1];
    if (marker) {
      if (!fence) fence = marker;
      else if (marker[0] === fence[0] && marker.length >= fence.length)
        fence = "";
    }
    const match =
      fence || marker ? null : /^ {0,3}#{1,6}\s+(.*?)(?:\s+#+)?\s*$/.exec(line);
    if (match) {
      const heading = plain(match[1]);
      result.push({ heading, anchor: slugger.slug(heading), lines: [] });
    } else result[result.length - 1].lines.push(line);
  }
  // The title pseudo-section only exists when real content precedes any heading.
  if (!result[0].lines.some((line) => line.trim())) result.shift();
  return result;
}

function snippetAround(body: string, lower: string, terms: string[]) {
  const at = Math.max(0, lower.indexOf(terms[0]));
  const start = Math.max(0, at - 60);
  const end = Math.min(body.length, at + terms[0].length + 100);
  return `${start > 0 ? "…" : ""}${body.slice(start, end).trim()}${end < body.length ? "…" : ""}`;
}

export function searchDocs(docs: Docs, query: string): Hit[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  const scored: { hit: Hit; score: number }[] = [];
  for (const doc of [...docs.guides, ...docs.skills]) {
    for (const { heading, anchor, lines } of sections(doc)) {
      const body = lines.join("\n").replace(/\s+/g, " ").trim();
      const lowerHeading = heading.toLowerCase();
      const lowerBody = body.toLowerCase();
      const lower = `${lowerHeading}\n${lowerBody}`;
      if (!terms.every((term) => lower.includes(term))) continue;
      const score =
        terms.filter((term) => lowerHeading.includes(term)).length * 3 +
        (anchor === "" ? 2 : 0) +
        (lowerHeading === terms.join(" ") ? 5 : 0);
      scored.push({
        hit: {
          path: doc.path,
          title: doc.title,
          heading,
          anchor,
          snippet: snippetAround(body, lowerBody, terms),
        },
        score,
      });
    }
  }
  return scored
    .map((entry, index) => ({ ...entry, index }))
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map(({ hit }) => hit);
}

// Group hits per document, in order of each document's best hit.
export function hitGroups(hits: Hit[]) {
  const groups = new Map<
    string,
    { path: string; title: string; hits: Hit[] }
  >();
  for (const hit of hits) {
    const group = groups.get(hit.path);
    if (group) group.hits.push(hit);
    else
      groups.set(hit.path, { path: hit.path, title: hit.title, hits: [hit] });
  }
  return [...groups.values()];
}

export function highlight(text: string, query: string): ReactNode[] {
  const terms = query.trim().split(/\s+/).filter(Boolean);
  if (!terms.length) return [text];
  const escaped = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escaped.join("|")})`, "gi");
  return text
    .split(pattern)
    .map((part, index) =>
      index % 2 ? createElement("mark", { key: index }, part) : part,
    );
}
