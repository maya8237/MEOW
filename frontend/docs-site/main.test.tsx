import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Docs } from "@/lib/docs";
import { Site } from "./main";

const docs: Docs = {
  guides: [
    {
      path: "README.md",
      title: "MEOW",
      text: "# MEOW\n\nWelcome to the readme.",
    },
    {
      path: "docs/CLI.md",
      title: "CLI",
      text: "# CLI\n\nCommands.\n\n## Plan\n\nApprove plans manually.",
    },
  ],
  skills: [
    {
      path: "skills/plan/SKILL.md",
      title: "plan",
      text: "# Plan skill\n\nBody.",
    },
  ],
};

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  window.location.hash = "";
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  fetchMock = vi.fn(async () => new Response(JSON.stringify(docs)));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  window.location.hash = "";
});

test("starts with setup instead of loading the full reference", () => {
  render(<Site />);
  expect(
    screen.getByRole("heading", { name: "Your first steps" }),
  ).toBeInTheDocument();
  expect(screen.getByText("/meow:onboard")).toBeInTheDocument();
  expect(
    screen.getByText("/meow:run Add pagination to the users API endpoint"),
  ).toBeInTheDocument();
  expect(screen.queryByText("Welcome to the readme.")).not.toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
  const nav = screen.getByRole("navigation", { name: "Main navigation" });
  expect(
    within(nav)
      .getAllByRole("link")
      .map((a) => a.textContent),
  ).toEqual(["Start here", "Skills", "Customize", "Reference"]);
  expect(screen.getByRole("link", { name: "GitHub" })).toHaveAttribute(
    "href",
    expect.stringContaining("github.com"),
  );
});

test("opens the guide and heading named by the initial hash", async () => {
  window.location.hash = "#/docs/CLI.md:plan";
  const scroll = vi.fn();
  Element.prototype.scrollIntoView = scroll;
  render(<Site />);
  expect(
    await screen.findByText("Approve plans manually."),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(scroll.mock.contexts.at(-1)).toHaveProperty("id", "plan"),
  );
});

test("writes the hash when a guide is clicked", async () => {
  window.location.hash = "#/README.md";
  const user = userEvent.setup();
  render(<Site />);
  await screen.findByText("Welcome to the readme.");
  expect(fetchMock.mock.calls[0][0]).toBe("./docs.json");
  const nav = screen.getByRole("navigation", { name: "Documentation" });
  await user.click(within(nav).getByRole("button", { name: /CLI/ }));
  expect(window.location.hash).toBe("#/docs/CLI.md");
  expect(await screen.findByText("Commands.")).toBeInTheDocument();
});

test("re-renders when the hash changes", async () => {
  render(<Site />);
  act(() => {
    window.location.hash = "#/skills/plan/SKILL.md";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  expect(await screen.findByText("Body.")).toBeInTheDocument();
});

test("shows an error when docs.json cannot be loaded", async () => {
  window.location.hash = "#/README.md";
  fetchMock.mockImplementation(
    async () =>
      new Response(JSON.stringify({ error: "Documentation unavailable" }), {
        status: 404,
      }),
  );
  render(<Site />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Documentation unavailable",
  );
});

test("switches install commands and copies the selected command", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  render(<Site />);
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Your terminal" }),
    "bash",
  );
  const command =
    "curl -fsSL https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.sh | sh";
  const code = screen.getByText(command);
  await user.click(
    within(code.parentElement!).getByRole("button", { name: "Copy" }),
  );
  expect(write).toHaveBeenCalledWith(command);
  expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Your terminal" }),
    "powershell",
  );
  expect(
    screen.queryByRole("button", { name: "Copied" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/^irm https:/)).toBeInTheDocument();
});

test("offers all skills with short examples and links to full Markdown", async () => {
  const user = userEvent.setup();
  render(<Site />);
  const nav = screen.getByRole("navigation", { name: "Main navigation" });
  await user.click(within(nav).getByRole("link", { name: "Skills" }));
  expect(
    await screen.findByRole("heading", { name: "What would you like to do?" }),
  ).toBeInTheDocument();
  expect(
    screen.getAllByRole("link", { name: "Full skill instructions on GitHub" }),
  ).toHaveLength(8);
  expect(screen.queryByText("/meow:migration")).not.toBeInTheDocument();
  const skillLinks = screen.getAllByRole("link", {
    name: "Full skill instructions on GitHub",
  });
  expect(skillLinks.at(-1)).toHaveAttribute(
    "href",
    "https://github.com/maya8237/MEOW/blob/main/skills/schedule/SKILL.md",
  );
  expect(
    screen.getByText(/saves a PASS\/FAIL report as a downloadable CI artifact/),
  ).toBeInTheDocument();
  expect(screen.getByText("/meow:onboard")).toBeInTheDocument();
  expect(screen.getByText("/meow:lint")).toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
  await user.click(within(nav).getByRole("link", { name: "Customize" }));
  expect(
    await screen.findByRole("heading", { name: "Your project. Your way." }),
  ).toBeInTheDocument();
  expect(screen.getByText("/meow:customize")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Agent & workflow formats" }),
  ).toHaveAttribute(
    "href",
    "https://github.com/maya8237/MEOW/blob/main/docs/WORKFLOWS.md",
  );
  expect(within(nav).getByRole("link", { name: "Customize" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("shows run examples by default and copies a complete prompt", async () => {
  window.location.hash = "#skills";
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  render(<Site />);
  const examples = screen
    .getByRole("heading", { name: "/meow:run" })
    .closest("section")!;
  expect(
    screen.getByRole("heading", { name: "All skills" }),
  ).toBeInTheDocument();
  expect(
    within(examples).getByRole("heading", { name: "Fix a bug" }),
  ).toBeVisible();
  const prompt = within(examples).getByText(
    /^\/meow:run Fix duplicate submissions/,
  );
  await user.click(
    within(prompt.parentElement!).getByRole("button", { name: "Copy" }),
  );
  expect(write).toHaveBeenCalledWith(
    expect.stringContaining(
      "Add regression tests for double-clicks and failed requests.",
    ),
  );
});

test("opens reference on demand and returns to the beginner guide", async () => {
  const user = userEvent.setup();
  render(<Site />);
  const nav = screen.getByRole("navigation", { name: "Main navigation" });
  await user.click(within(nav).getByRole("link", { name: "Reference" }));
  expect(await screen.findByText("Welcome to the readme.")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Read this file on GitHub" }),
  ).toHaveAttribute(
    "href",
    "https://github.com/maya8237/MEOW/blob/main/README.md",
  );
  await user.click(within(nav).getByRole("link", { name: "Start here" }));
  expect(
    await screen.findByRole("heading", { name: "Your first steps" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("Welcome to the readme.")).not.toBeInTheDocument();
});

test("opens skills using the hero link and supports direct reference links", async () => {
  const user = userEvent.setup();
  render(<Site />);
  await user.click(screen.getByRole("link", { name: "Explore the skills" }));
  expect(
    await screen.findByRole("heading", { name: "What would you like to do?" }),
  ).toBeInTheDocument();
  expect(window.location.hash).toBe("#skills");
  await user.click(screen.getByRole("link", { name: "CLI guide" }));
  expect(
    await screen.findByText("Approve plans manually."),
  ).toBeInTheDocument();
  expect(window.location.hash).toBe("#/docs/CLI.md");
});
