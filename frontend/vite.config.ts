import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, type Plugin } from "vitest/config";

const apiTarget = process.env.MEOW_UI_API || "http://127.0.0.1:8765";

// Hosted docs build: styles.css points at root-absolute /fonts for the app
// server; bundle the same files relative to the site so it works under any path.
const hostedFonts: Plugin = {
  name: "hosted-docs-fonts",
  enforce: "pre",
  transform(code, id) {
    if (!id.endsWith("/src/styles.css")) return;
    const fonts = fileURLToPath(
      new URL("../src/meow/ui/fonts", import.meta.url),
    ).replaceAll("\\", "/");
    // Tailwind scans from the site root; also scan the shared app sources.
    return `${code}\n@source "./";`.replaceAll(
      'url("/fonts/',
      `url("${fonts}/`,
    );
  },
};

export default defineConfig(({ mode }) => ({
  plugins: [
    ...(mode === "docs-site" ? [hostedFonts] : []),
    react(),
    tailwindcss(),
  ],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  ...(mode === "docs-site"
    ? {
        root: "docs-site",
        base: "./",
        build: { outDir: "../dist-docs", emptyOutDir: true },
      }
    : { build: { outDir: "../src/meow/ui/static", emptyOutDir: true } }),
  server: {
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (proxyRequest, incoming) => {
            // Translate same-origin development requests without accepting foreign origins.
            if (incoming.headers.origin === `http://${incoming.headers.host}`) {
              proxyRequest.setHeader("Origin", new URL(apiTarget).origin);
            }
          });
        },
      },
      "/logo.png": apiTarget,
      "/fonts": apiTarget,
    },
  },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"] },
}));
