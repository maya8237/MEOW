export type DocLocation = { path: string; anchor: string };

const HOME: DocLocation = { path: "README.md", anchor: "" };

// Hash form is `#/<path>` or `#/<path>:<anchor>`; anything else is the README.
export function parseDocHash(hash: string): DocLocation {
  if (!hash.startsWith("#/")) return { ...HOME };
  const [path, ...rest] = hash.slice(2).split(":");
  return path ? { path, anchor: rest.join(":") } : { ...HOME };
}

export function formatDocHash({ path, anchor }: DocLocation) {
  return `#/${path}${anchor ? `:${anchor}` : ""}`;
}
