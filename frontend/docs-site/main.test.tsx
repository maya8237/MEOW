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
  fetchMock = vi.fn(async () => new Response(JSON.stringify(docs)));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  window.location.hash = "";
});

test("loads ./docs.json relatively and shows the README", async () => {
  render(<Site />);
  expect(await screen.findByText("Welcome to the readme.")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][0]).toBe("./docs.json");
  expect(
    screen.getByRole("heading", { name: "MEOW documentation" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /github/i })).toHaveAttribute(
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
  const user = userEvent.setup();
  render(<Site />);
  await screen.findByText("Welcome to the readme.");
  const nav = screen.getByRole("navigation", { name: "Documentation" });
  await user.click(within(nav).getByRole("button", { name: /CLI/ }));
  expect(window.location.hash).toBe("#/docs/CLI.md");
  expect(await screen.findByText("Commands.")).toBeInTheDocument();
});

test("re-renders when the hash changes", async () => {
  render(<Site />);
  await screen.findByText("Welcome to the readme.");
  act(() => {
    window.location.hash = "#/skills/plan/SKILL.md";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  expect(await screen.findByText("Body.")).toBeInTheDocument();
});
