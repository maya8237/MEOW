import { expect, test } from "vitest";
import { formatDocHash, parseDocHash } from "./docs-hash";

const readme = { path: "README.md", anchor: "" };

test("round-trips a path with an anchor", () => {
  const loc = { path: "docs/CLI.md", anchor: "global-flags" };
  expect(formatDocHash(loc)).toBe("#/docs/CLI.md:global-flags");
  expect(parseDocHash(formatDocHash(loc))).toEqual(loc);
});

test("a path without an anchor omits the colon", () => {
  expect(formatDocHash({ path: "docs/CLI.md", anchor: "" })).toBe(
    "#/docs/CLI.md",
  );
  expect(parseDocHash("#/docs/CLI.md")).toEqual({
    path: "docs/CLI.md",
    anchor: "",
  });
});

test("empty or malformed hashes fall back to the README", () => {
  for (const hash of ["", "#", "#/", "#garbage"])
    expect(parseDocHash(hash)).toEqual(readme);
});
