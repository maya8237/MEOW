import { expect, test } from "vitest";
import { resolveDocLink, searchDocs, type Docs } from "./docs";

const docs: Docs = {
  guides: [
    {
      path: "docs/CLI.md",
      title: "CLI",
      text: [
        "Intro text about the command line.",
        "",
        "## Plan",
        "",
        "Use `meow plan approve` to continue.",
        "",
        "```sh",
        "# not a heading",
        "meow run -m --manually-approve",
        "```",
        "",
        "## x",
        "first",
        "",
        "## x",
        "second",
      ].join("\n"),
    },
    { path: "docs/RUNS.md", title: "Runs", text: "# Runs\n\nAbout runs." },
    { path: "docs/INTEGRATIONS.md", title: "Integrations", text: "# I" },
  ],
  skills: [
    {
      path: "skills/run/SKILL.md",
      title: "/meow:run",
      description: "Run it",
      text: "Launch a sprint.",
    },
  ],
};

test("resolveDocLink resolves relative links within the doc set", () => {
  expect(resolveDocLink(docs, "docs/CLI.md", "RUNS.md")).toEqual({
    path: "docs/RUNS.md",
    anchor: "",
  });
  expect(
    resolveDocLink(
      docs,
      "docs/CLI.md",
      "INTEGRATIONS.md#browser-tester-providers",
    ),
  ).toEqual({
    path: "docs/INTEGRATIONS.md",
    anchor: "browser-tester-providers",
  });
  expect(resolveDocLink(docs, "docs/CLI.md", "../skills/run/SKILL.md")).toEqual(
    { path: "skills/run/SKILL.md", anchor: "" },
  );
  expect(resolveDocLink(docs, "docs/CLI.md", "./RUNS.md")?.path).toBe(
    "docs/RUNS.md",
  );
  expect(resolveDocLink(docs, "docs/CLI.md", "#plan")).toEqual({
    path: "docs/CLI.md",
    anchor: "plan",
  });
});

test("resolveDocLink rejects external and unknown links", () => {
  expect(
    resolveDocLink(docs, "docs/CLI.md", "../skills/_shared/native-mode.md"),
  ).toBeNull();
  expect(resolveDocLink(docs, "docs/CLI.md", "https://example.com")).toBeNull();
  expect(resolveDocLink(docs, "docs/CLI.md", "//example.com/a")).toBeNull();
  expect(resolveDocLink(docs, "docs/CLI.md", "mailto:a@b.c")).toBeNull();
});

test("searchDocs finds sections by all terms, case-insensitively", () => {
  expect(searchDocs(docs, "")).toEqual([]);
  expect(searchDocs(docs, "   ")).toEqual([]);
  const manual = searchDocs(docs, "manually-approve");
  expect(manual).toHaveLength(1);
  expect(manual[0]).toMatchObject({
    path: "docs/CLI.md",
    heading: "Plan",
    anchor: "plan",
  });
  expect(manual[0].snippet).toContain("manually-approve");
  expect(searchDocs(docs, "-m")[0].anchor).toBe("plan");
  expect(searchDocs(docs, "PLAN APPROVE")).toHaveLength(1);
  expect(searchDocs(docs, "approve zzz")).toEqual([]);
  expect(searchDocs(docs, "(")).toEqual([]);
  expect(searchDocs(docs, "[a-")).toEqual([]);
});

test("searchDocs covers intro sections, skills and duplicate headings", () => {
  const intro = searchDocs(docs, "command line");
  expect(intro[0]).toMatchObject({ heading: "CLI", anchor: "" });
  expect(searchDocs(docs, "launch")[0]).toMatchObject({
    path: "skills/run/SKILL.md",
    title: "/meow:run",
  });
  expect(searchDocs(docs, "# not a heading")).toHaveLength(1);
  expect(searchDocs(docs, "first")[0].anchor).toBe("x");
  expect(searchDocs(docs, "second")[0].anchor).toBe("x-1");
});

test("a closing hash needs preceding whitespace", () => {
  const sample: Docs = {
    guides: [
      { path: "a.md", title: "A", text: "## C#\n\nsharp\n\n## Go ##\n\ngo" },
    ],
    skills: [],
  };
  expect(searchDocs(sample, "sharp")[0].heading).toBe("C#");
  expect(searchDocs(sample, "go")[0].heading).toBe("Go");
});

test("a title query yields one hit per section with a snippet", () => {
  const hits = searchDocs(docs, "runs");
  const own = hits.filter((hit) => hit.path === "docs/RUNS.md");
  expect(own).toHaveLength(1);
  expect(hits.every((hit) => hit.snippet)).toBe(true);
});

test("pre-heading content stays a section titled with the doc title", () => {
  const hits = searchDocs(docs, "command line");
  expect(hits[0]).toMatchObject({ heading: "CLI", anchor: "" });
});
