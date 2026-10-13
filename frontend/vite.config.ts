import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

const apiTarget = process.env.MEOW_UI_API || "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: { outDir: "../src/meow/ui/static", emptyOutDir: true },
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
});
