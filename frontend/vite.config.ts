/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, URL } from "node:url";

// Dev-server proxy: every /api call goes to the FastAPI backend so the browser never
// needs CORS in development. Override with VITE_DEV_PROXY_TARGET when the backend runs elsewhere.
const proxyTarget = process.env.VITE_DEV_PROXY_TARGET ?? "http://127.0.0.1:8000";

/**
 * Turns the built index.html into `artifact.html`: the same page without the document wrappers
 * (doctype, <html>, <head>, <body>) so a host that supplies its own skeleton can embed it. The
 * <title>, the theme bootstrap script, the stylesheet, the module script (relative `assets/...`
 * paths) and <div id="root"> are kept as they are.
 */
export function stripDocumentWrappers(html: string): string {
  return html
    .replace(/<!doctype[^>]*>/i, "")
    .replace(/<\/?html[^>]*>/gi, "")
    .replace(/<\/?head[^>]*>/gi, "")
    .replace(/<\/?body[^>]*>/gi, "")
    .replace(/<meta\s+charset=[^>]*>/gi, "")
    .replace(/<meta\s+name="viewport"[^>]*>/gi, "")
    .replace(/(src|href)="\.\/(assets\/)/g, '$1="$2')
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "")
    .join("\n")
    .concat("\n");
}

function artifactHtml(outDir: string): Plugin {
  return {
    name: "ppse-artifact-html",
    apply: "build",
    closeBundle() {
      const built = readFileSync(join(outDir, "index.html"), "utf8");
      writeFileSync(join(outDir, "artifact.html"), stripDocumentWrappers(built));
    },
  };
}

export default defineConfig(({ mode }) => {
  // DEMO MODE (`vite build --mode demo`, .env.demo): a static bundle with relative asset URLs that
  // runs from any path with no backend (see src/demo/). Output goes to dist-demo/ plus artifact.html.
  const demo = mode === "demo";
  const outDir = demo ? "dist-demo" : "dist";
  return {
    base: demo ? "./" : "/",
    plugins: [react(), ...(demo ? [artifactHtml(outDir)] : [])],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    server: {
      port: 5173,
      strictPort: false,
      proxy: {
        "/api": {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir,
      sourcemap: !demo,
      chunkSizeWarningLimit: 900,
      rollupOptions: {
        output: {
          manualChunks: {
            vendor: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"],
            charts: ["recharts"],
          },
        },
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      css: false,
      include: ["src/**/*.test.{ts,tsx}"],
    },
  };
});
