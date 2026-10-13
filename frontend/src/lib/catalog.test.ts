import { expect, test } from "vitest";
import { repoName } from "./catalog";

test("repository labels use only enough parent folders to distinguish duplicates", () => {
  const repos = [
    "C:\\Users\\m\\Documents\\MEOW",
    "C:\\Users\\m\\.codex\\worktrees\\react-ui\\MEOW",
    "C:\\Users\\m\\Documents\\Test",
  ];
  expect(repoName(repos[0], repos)).toBe("Documents/MEOW");
  expect(repoName(repos[1], repos)).toBe("react-ui/MEOW");
  expect(repoName(repos[2], repos)).toBe("Test");
  const deeper = ["/home/a/projects/MEOW", "/home/b/projects/MEOW"];
  expect(repoName(deeper[0], deeper)).toBe("a/projects/MEOW");
  expect(repoName(deeper[1], deeper)).toBe("b/projects/MEOW");
  expect(repoName("/MEOW", ["/MEOW", "/home/MEOW"])).toBe("/MEOW");
});
