import react from "@vitejs/plugin-react";
import { defineConfig } from "electron-vite";

// Hot reload needs an inline preamble and a websocket; the built app keeps the strict policy written in index.html.
const devCsp = {
  name: "dev-csp",
  apply: "serve" as const,
  transformIndexHtml: (html: string): string =>
    html.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'").replace("connect-src 'self'", "connect-src 'self' ws:"),
};

export default defineConfig({
  main: {},
  preload: { build: { rollupOptions: { output: { format: "cjs" } } } }, // sandboxed preloads must be CommonJS
  renderer: { plugins: [react(), devCsp] },
});
