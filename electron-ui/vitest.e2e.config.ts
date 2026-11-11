import { defineConfig } from "vitest/config";

// End-to-end: the built app against a real daemon with a scripted model (tests/fake_daemon.py). Run with `npm run e2e`.
export default defineConfig({
  test: { include: ["tests/e2e/**/*.e2e.ts"], testTimeout: 60_000, hookTimeout: 120_000, fileParallelism: false },
});
