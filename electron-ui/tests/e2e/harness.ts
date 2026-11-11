/** Starts the scripted daemon and the built app in throw-away folders; nothing touches the user's real data, keys or daemon. */
import { type ChildProcess, spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import electronPath from "electron";
import { _electron, type ElectronApplication, type Page } from "playwright-core";

const UI = path.resolve(__dirname, "..", "..");
const PYTHON = path.resolve(UI, "..", ".venv", "Scripts", "python.exe");
const DAEMON = path.join(UI, "tests", "fake_daemon.py");

function freePort(): Promise<number> {
  return new Promise((resolve) => {
    const s = net.createServer().listen(0, "127.0.0.1", () => {
      const port = (s.address() as net.AddressInfo).port;
      s.close(() => resolve(port));
    });
  });
}

async function waitForPort(port: number, seconds = 60): Promise<void> {
  for (const end = Date.now() + seconds * 1000; Date.now() < end; await new Promise((r) => setTimeout(r, 200))) {
    const ok = await new Promise<boolean>((resolve) => {
      const s = net.connect({ host: "127.0.0.1", port });
      s.once("connect", () => (s.destroy(), resolve(true))).once("error", () => resolve(false));
    });
    if (ok) return;
  }
  throw new Error("the scripted daemon did not start");
}

export type Rig = { app: ElectronApplication; main: Page; approvals: Page; page: (role: string) => Promise<Page>; home: string; port: number; env: NodeJS.ProcessEnv; stop: () => Promise<void> };

export async function startRig(): Promise<Rig> {
  const home = mkdtempSync(path.join(os.tmpdir(), "agent-ui-e2e-"));
  const port = await freePort();
  const env = { ...process.env, E2E_HOME: home, E2E_PORT: String(port), PERSONAL_AGENT_NO_DAEMON: "1", PERSONAL_AGENT_CORE: JSON.stringify([PYTHON, DAEMON]) };
  const daemon: ChildProcess = spawn(PYTHON, [DAEMON], { env, stdio: "inherit" });
  await waitForPort(port);
  const app = await _electron.launch({ executablePath: electronPath as unknown as string, args: [UI, `--user-data-dir=${path.join(home, "electron")}`,
    "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"], // a synthetic microphone: the real one is never opened by a test
    env: env as Record<string, string> });
  const page = async (role: string): Promise<Page> => {
    for (const end = Date.now() + 30_000; Date.now() < end; await new Promise((r) => setTimeout(r, 100))) {
      const w = app.windows().find((p) => p.url().endsWith(`#/${role}`));
      if (w) return w;
    }
    throw new Error(`no ${role} window`);
  };
  const [main, approvals] = [await page("main"), await page("approvals")];
  const stop = async (): Promise<void> => {
    await app.evaluate(({ app: a }) => a.exit(0)).catch(() => undefined);
    daemon.kill();
    await new Promise((r) => setTimeout(r, 500));
    rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
  };
  return { app, main, approvals, page, home, port, env, stop };
}
