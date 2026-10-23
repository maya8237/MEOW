import { createRef } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Docs, searchDocs } from "@/lib/docs";
import DocsView from "./DocsView";

const docs: Docs = {
  guides: [
    {
      path: "README.md",
      title: "MEOW",
      text: [
        "# MEOW",
        "",
        "Welcome to the readme.",
        "",
        "See the [CLI guide](docs/CLI.md#plan), [site](https://example.com/x),",
        "and a [missing page](docs/MISSING.md).",
        "",
        "<b>raw html</b>",
        "",
        '<p align="center">',
        '  <img src="logo.png" width="160" alt="MEOW pixel cat logo" />',
        "</p>",
        "",
        "## The `snake_case` flag",
        "",
        "Details.",
      ].join("\n"),
    },
    {
      path: "docs/CLI.md",
      title: "CLI",
      text: "# CLI\n\nCommands.\n\n## Plan\n\nApprove plans manually before running.",
    },
  ],
  skills: [
    {
      path: "skills/plan/SKILL.md",
      title: "plan",
      description: "Draft a plan",
      text: "# Plan skill\n\nSkill body text.",
    },
  ],
};

function stubDocs(response: () => Response | Promise<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response()),
  );
}
const ok = () => new Response(JSON.stringify(docs));
function renderView() {
  const ref = createRef<HTMLInputElement>();
  render(<DocsView searchRef={ref} />);
  return ref;
}
afterEach(() => vi.unstubAllGlobals());

test("lists guides and skills and renders the README", async () => {
  stubDocs(ok);
  renderView();
  const guides = await screen.findByRole("list", { name: "Guides" });
  expect(within(guides).getByText("MEOW")).toBeInTheDocument();
  expect(within(guides).getByText("CLI")).toBeInTheDocument();
  const skills = screen.getByRole("list", { name: "Skills" });
  expect(within(skills).getByText("plan")).toBeInTheDocument();
  expect(screen.getByText("Welcome to the readme.")).toBeInTheDocument();
  expect(screen.queryByText("raw html", { selector: "b" })).toBeNull();
  expect(screen.queryByText(/<p align=/)).not.toBeInTheDocument();
  expect(screen.queryByText(/<img src=/)).not.toBeInTheDocument();
});

test("navigates by skill and in-doc links; unknown and external links", async () => {
  stubDocs(ok);
  const user = userEvent.setup();
  renderView();
  await screen.findByText("Welcome to the readme.");
  const external = screen.getByRole("link", { name: "site" });
  expect(external).toHaveAttribute("href", "https://example.com/x");
  expect(external).toHaveAttribute("target", "_blank");
  expect(external).toHaveAttribute("rel", "noreferrer");
  expect(screen.getByText("missing page").closest("a")).toBeNull();
  await user.click(screen.getByRole("link", { name: "CLI guide" }));
  expect(await screen.findByText("Commands.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /plan/ }));
  expect(await screen.findByText("Skill body text.")).toBeInTheDocument();
});

test("search shows section hits, empty state, and restores the groups", async () => {
  stubDocs(ok);
  const user = userEvent.setup();
  renderView();
  await screen.findByText("Welcome to the readme.");
  const box = screen.getByRole("searchbox", { name: "Search documentation" });
  await user.type(box, "manually");
  const hit = await screen.findByRole("button", { name: /Plan/ });
  expect(hit).toHaveTextContent("Approve plans manually");
  expect(screen.queryByRole("list", { name: "Guides" })).toBeNull();
  await user.click(hit);
  expect(await screen.findByText("Commands.")).toBeInTheDocument();
  await user.clear(box);
  await user.type(box, "zzz");
  expect(
    await screen.findByText('No documentation matches "zzz".'),
  ).toBeInTheDocument();
  await user.clear(box);
  expect(screen.getByRole("list", { name: "Guides" })).toBeInTheDocument();
});

test("rendered heading ids match search anchors", async () => {
  stubDocs(ok);
  renderView();
  const heading = await screen.findByRole("heading", {
    name: "The snake_case flag",
  });
  const hit = searchDocs(docs, "snake_case").find((h) => h.anchor);
  expect(hit?.anchor).toBeTruthy();
  expect(heading.id).toBe(hit?.anchor);
});

test("reports load failures", async () => {
  stubDocs(
    () =>
      new Response(JSON.stringify({ error: "Docs offline." }), { status: 500 }),
  );
  renderView();
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("Docs offline."),
  );
});

test("opening another doc resets the scroll position", async () => {
  stubDocs(ok);
  const user = userEvent.setup();
  renderView();
  await screen.findByText("Welcome to the readme.");
  const article = screen.getByRole("article");
  article.scrollTop = 500;
  await user.click(screen.getByRole("button", { name: /plan/ }));
  await screen.findByText("Skill body text.");
  expect(article.scrollTop).toBe(0);
});

test("anchored links and hits scroll their heading into view", async () => {
  stubDocs(ok);
  const scroll = vi.fn();
  const original = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = scroll;
  try {
    const user = userEvent.setup();
    renderView();
    await screen.findByText("Welcome to the readme.");
    scroll.mockClear();
    await user.click(screen.getByRole("link", { name: "CLI guide" }));
    await screen.findByText("Commands.");
    await waitFor(() => expect(scroll).toHaveBeenCalled());
    expect(scroll.mock.contexts.at(-1)).toHaveProperty("id", "plan");
  } finally {
    Element.prototype.scrollIntoView = original;
  }
});

test("search regions are labelled", async () => {
  stubDocs(ok);
  const user = userEvent.setup();
  renderView();
  await screen.findByText("Welcome to the readme.");
  const box = screen.getByRole("searchbox", { name: "Search documentation" });
  await user.type(box, "manually");
  expect(
    await screen.findByRole("list", { name: "Search results" }),
  ).toBeInTheDocument();
  await user.clear(box);
  await user.type(box, "zzz");
  expect(await screen.findByRole("status")).toHaveTextContent("zzz");
});

test("uses a custom loader instead of the local API", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const load = vi.fn(async () => docs);
  const onNavigate = vi.fn();
  const user = userEvent.setup();
  render(
    <DocsView
      searchRef={createRef<HTMLInputElement>()}
      load={load}
      onNavigate={onNavigate}
    />,
  );
  await screen.findByText("Welcome to the readme.");
  expect(load).toHaveBeenCalledWith(expect.any(AbortSignal));
  expect(fetcher).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: /CLI/ }));
  expect(onNavigate).toHaveBeenCalledWith({ path: "docs/CLI.md", anchor: "" });
});

test("follows the location prop and falls back to the first guide", async () => {
  const load = async () => docs;
  const ref = createRef<HTMLInputElement>();
  const view = (path: string) => (
    <DocsView searchRef={ref} load={load} location={{ path, anchor: "" }} />
  );
  const { rerender } = render(view("README.md"));
  await screen.findByText("Welcome to the readme.");
  rerender(view("skills/plan/SKILL.md"));
  expect(await screen.findByText("Skill body text.")).toBeInTheDocument();
  rerender(view("nope.md"));
  expect(await screen.findByText("Welcome to the readme.")).toBeInTheDocument();
});
