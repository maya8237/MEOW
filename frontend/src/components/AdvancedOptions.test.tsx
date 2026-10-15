import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import AdvancedOptions from "./AdvancedOptions";

const options = [
  {
    flag: "--max-parallel",
    aliases: ["--max-parallel"],
    kind: "number",
    nargs: 1,
    choices: null,
    help: "Task limit",
    controlled: null,
  },
  {
    flag: "--jira",
    aliases: ["--jira"],
    kind: "value",
    nargs: "+",
    choices: null,
    help: "Issue keys",
    controlled: null,
  },
  {
    flag: "--work-dir",
    aliases: ["--work-dir", "-d"],
    kind: "value",
    nargs: 1,
    choices: null,
    help: "Repository",
    controlled: "Choose Repository in the launcher",
  },
];
function Harness() {
  const [args, setArgs] = useState([
    "--max-parallel",
    "2",
    "--jira",
    "ABC-1",
    "ABC-2",
  ]);
  return (
    <>
      <AdvancedOptions
        command="orchestrate"
        arguments={args}
        onChange={setArgs}
      />
      <output aria-label="Selected arguments">{JSON.stringify(args)}</output>
    </>
  );
}

test("numeric and multiple values remain grouped when another option is removed", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ commands: { orchestrate: options } })),
    ),
  );
  const user = userEvent.setup();
  render(<Harness />);
  expect(await screen.findByLabelText("--max-parallel value")).toHaveAttribute(
    "type",
    "number",
  );
  expect(screen.getByLabelText("--work-dir")).toBeDisabled();
  expect(screen.getByLabelText("--jira value 2")).toHaveValue("ABC-2");
  await user.click(screen.getByRole("button", { name: "Remove --jira" }));
  expect(screen.getByLabelText("Selected arguments")).toHaveTextContent(
    '["--max-parallel","2"]',
  );
  await user.click(screen.getByLabelText("--jira"));
  await user.type(screen.getByLabelText("--jira value 1"), "ABC-3");
  await user.click(
    screen.getByRole("button", { name: "Add value for --jira" }),
  );
  await user.type(screen.getByLabelText("--jira value 2"), "ABC-4");
  await user.click(
    screen.getByRole("button", { name: "Add value for --jira" }),
  );
  await user.type(screen.getByLabelText("--jira value 3"), "ABC-5");
  await user.click(
    screen.getByRole("button", { name: "Remove --jira value 2" }),
  );
  await user.click(
    screen.getByRole("button", { name: "Remove --max-parallel" }),
  );
  expect(screen.getByLabelText("Selected arguments")).toHaveTextContent(
    '["--jira","ABC-3","ABC-5"]',
  );
  await user.click(
    screen.getByRole("button", { name: "Remove --jira value 1" }),
  );
  await user.click(
    screen.getByRole("button", { name: "Remove --jira value 1" }),
  );
  expect(screen.getByLabelText("--jira")).not.toBeChecked();
  expect(screen.getByLabelText("Selected arguments")).toHaveTextContent("[]");
});

test("failed flag discovery can be retried", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: "Options unavailable" }), {
          status: 500,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ commands: { orchestrate: options } })),
      ),
  );
  const user = userEvent.setup();
  render(<Harness />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Options unavailable",
  );
  await user.click(screen.getByRole("button", { name: "Retry options" }));
  expect(await screen.findByLabelText("--max-parallel value")).toHaveValue(2);
});
