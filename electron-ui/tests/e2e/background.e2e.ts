// scripts/run.ps1 starts the UI with PERSONAL_AGENT_BACKGROUND=1: only the avatar shows, unless the first-run wizard still needs the main window.
import path from "node:path";
import electronPath from "electron";
import { _electron, type ElectronApplication } from "playwright-core";
import { afterAll, beforeAll, expect, it } from "vitest";
import { type Rig, startRig } from "./harness";

let r: Rig;
beforeAll(async () => {
  process.env.PERSONAL_AGENT_BACKGROUND = "1";
  r = await startRig();
}, 60_000);
afterAll(async () => {
  delete process.env.PERSONAL_AGENT_BACKGROUND;
  await r?.stop();
});

const visible = (app: ElectronApplication): Promise<Record<string, boolean>> =>
  app.evaluate(({ BrowserWindow }) => Object.fromEntries(BrowserWindow.getAllWindows().map((w) => [w.webContents.getURL().split("#")[1]!, w.isVisible()])));
const wait = (ms: number): Promise<void> => new Promise((s) => setTimeout(s, ms));

it("shows the main window when no provider key is set (the wizard lives there), and only the avatar once one is", async () => {
  await wait(5000);
  expect((await visible(r.app))["/main"]).toBe(true); // no key yet
  await r.main.evaluate(() => (window as any).agent.send({ type: "secret_set", name: "ANTHROPIC_API_KEY", value: "sk-test" }));
  await r.main.waitForFunction(() => (window as any).agent.init().then((i: any) => i.settings.secrets.ANTHROPIC_API_KEY === true));
  await r.app.close();
  const app = await _electron.launch({ executablePath: electronPath as unknown as string, args: [path.resolve(__dirname, "..", ".."), `--user-data-dir=${path.join(r.home, "electron2")}`],
    env: r.env as Record<string, string> });
  await wait(7000);
  expect(await visible(app)).toEqual({ "/avatar": true, "/approvals": false, "/main": false });
  await app.close().catch(() => undefined);
}, 60_000);
