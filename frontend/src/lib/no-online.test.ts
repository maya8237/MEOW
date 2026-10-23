import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { expect, test } from "vitest";

const root = join(__dirname, "..", "..");
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory())
      return name === "public" || name === "node_modules" ? [] : sources(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}
const files = [
  ...sources(join(root, "src")),
  ...sources(join(root, "docs-site")),
].map((path) => ({
  name: relative(root, path).replace(/\\/g, "/"),
  text: readFileSync(path, "utf8"),
}));

test("local UI never fetches an absolute URL", () => {
  const offenders = files.filter(({ text }) =>
    /\b(fetch|request)\(\s*[`"']https?:\/\//.test(text),
  );
  expect(offenders.map((file) => file.name)).toEqual([]);
});

test("docs-site code is never imported by the local app", () => {
  const offenders = files.filter(
    ({ name, text }) =>
      name.startsWith("src/") && /from\s+["'][^"']*docs-site\//.test(text),
  );
  expect(offenders.map((file) => file.name)).toEqual([]);
});

test("DOCS_SITE_URL is referenced only by its definition and the link", () => {
  const users = files
    .filter(({ text }) => text.includes("DOCS_SITE_URL"))
    .map((file) => file.name)
    .sort();
  expect(users).toEqual([
    "src/components/OnlineDocsLink.tsx",
    "src/lib/links.ts",
  ]);
});
