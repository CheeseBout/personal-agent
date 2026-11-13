// The whole UI against a real daemon (scripted model): connect, chat, approvals, and what a window is NOT allowed to do.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { afterAll, beforeAll, expect, it } from "vitest";
import { type Rig, startRig } from "./harness";

let r: Rig;
const SHOTS = process.env.E2E_SHOTS; // a folder: keep screenshots of each step for a human to look at
const shot = async (page: Rig["main"], name: string): Promise<void> => void (SHOTS && (await page.screenshot({ path: path.join(SHOTS, `${name}.png`) })));

beforeAll(async () => {
  r = await startRig();
});
afterAll(async () => r?.stop());

it("connects, shows the sidebar and chats through the real daemon", async () => {
  await r.main.waitForSelector(".rail .nav");
  await r.main.waitForFunction(() => !document.querySelector(".topbar .warn")); // connected: the offline note is gone
  await r.main.waitForSelector(".wizard"); // no provider key yet: the first-run wizard is up
  await r.main.locator(".wizard select").selectOption("vi");
  await r.main.waitForFunction(() => document.querySelector(".rail .nav")?.textContent?.includes("Trò chuyện")); // the whole UI switched language
  await r.main.locator(".wizard select").selectOption("en");
  await r.main.waitForFunction(() => document.querySelector(".rail .nav")?.textContent?.includes("Chat"));
  await r.main.locator(".wizard button.primary").click();
  await r.main.locator(".wizard input[type=password]").fill("sk-test-123");
  await r.main.locator(".wizard button", { hasText: "Save key and test" }).click();
  await r.main.waitForFunction(() => document.querySelector(".wizard")?.textContent?.includes("Connected"));
  expect(await r.main.locator(".wizard input[type=password]").inputValue()).toBe(""); // the key left the page as soon as it was sent
  await shot(r.main, "wizard");
  await r.main.locator(".wizard button", { hasText: "Close" }).click();
  await r.main.locator(".composer").fill("ping");
  await r.main.locator(".composer").press("Enter");
  await r.main.waitForSelector(".msg.agent .md strong");
  expect(await r.main.locator(".msg.agent .md strong").last().innerText()).toBe("ok");
  expect(await r.main.locator(".msg.user .md").first().innerText()).toContain("ping");
  await r.main.waitForFunction(() => /\d/.test(document.querySelector(".meter span")?.textContent ?? "")); // budget meters refreshed by themselves
  await shot(r.main, "chat");
});

it("shows model output as text: markup in an answer never becomes live HTML", async () => {
  expect(await r.main.locator(".transcript img, .transcript script, .transcript iframe").count()).toBe(0);
  expect(await r.main.locator(".msg.agent .md").last().innerText()).toContain("onerror"); // visible, inert
  expect(await r.main.title()).not.toBe("pwned");
});

it("a page that renders untrusted text cannot approve, start turns behind the user's back or reach Node", async () => {
  const probe = await r.main.evaluate(async () => {
    const w = window as any;
    const tried = async (f: () => Promise<unknown>): Promise<string> => f().then(() => "allowed", () => "refused");
    return { decide: await tried(() => w.agent.decide("default", { x: { action: "approve" } })),
      approve: await tried(() => w.agent.send({ type: "approve", thread_id: "default", decisions: {} })),
      user: await tried(() => w.agent.send({ type: "user", text: "hi" })), node: typeof w.require + typeof w.process };
  });
  expect(probe).toEqual({ decide: "refused", approve: "refused", user: "refused", node: "undefinedundefined" });
});

it("a risky action opens the approval window and the decision goes back", async () => {
  await r.main.locator(".composer").fill("please write the file");
  await r.main.locator(".composer").press("Enter");
  await r.approvals.waitForSelector(".card");
  expect(await r.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().some((w) => w.isVisible() && w.webContents.getURL().endsWith("#/approvals")))).toBe(true);
  expect(await r.approvals.locator(".card b").innerText()).toBe("file_write");
  expect(await r.approvals.locator(".card .preview").innerText()).toContain("hello");
  expect(await r.main.locator(".row .primary").isDisabled()).toBe(true); // nothing new can be sent while an answer is owed
  await shot(r.approvals, "approvals");
  await r.approvals.locator("button.primary").click();
  expect(await r.approvals.locator(".error").innerText()).toContain("choose an answer");
  await r.approvals.locator(".radio", { hasText: "Approve" }).first().click();
  await r.approvals.locator("button.primary").click();
  await r.approvals.waitForSelector(".card", { state: "detached" });
  await r.main.waitForFunction(() => document.querySelectorAll(".msg.agent").length >= 2);
  expect(readFileSync(path.join(r.home, "ws", "out.txt"), "utf8")).toBe("hello");
  expect(await r.main.locator(".row .primary").isDisabled()).toBe(false);
});

it("the sidebar collapses, remembers it in the data folder, and Stop all pauses the agent until resumed", async () => {
  await r.main.locator(".burger").click();
  await r.main.waitForSelector(".rail", { state: "detached" });
  const state = path.join(r.home, "home", "ui_state.json");
  expect(existsSync(state) && JSON.parse(readFileSync(state, "utf8")).rail_collapsed).toBe(true);
  await r.main.keyboard.press("Control+b");
  await r.main.waitForSelector(".rail");
  await r.main.locator("button.danger").click();
  await r.main.waitForSelector(".paused");
  await r.main.locator(".paused button").click();
  await r.main.waitForSelector(".paused", { state: "detached" });
});

it("takes every hotkey from the system, or says which one another program holds", async () => {
  const ui = await r.main.evaluate(() => (window as any).agent.init());
  const keys: [string, string][] = [["kill", "Control+Alt+K"], ["chat", "Control+Alt+A"], ["talk", "Control+Alt+Space"], ["capture", "Control+Alt+S"], ["watch", "Control+Alt+W"]];
  const taken = await r.app.evaluate(({ globalShortcut }, list) => list.map(([, a]) => globalShortcut.isRegistered(a!)), keys);
  // Another program (the old Qt UI, for one) may already hold a key: then it must be reported, not silently missing.
  expect(keys.map(([name], i) => taken[i] !== name! in ui.hotkeyProblems)).toEqual([true, true, true, true, true]);
  expect(typeof (await r.main.evaluate(() => (window as any).agent.autostart()))).toBe("boolean"); // read only: the test never touches the Run key
});

it("manager lists load from the daemon, and the audit log shows what was approved", async () => {
  await r.main.locator(".nav", { hasText: "Memory" }).click();
  await r.main.waitForSelector(".mgr");
  expect(await r.main.locator(".empty").innerText()).toContain("No memories yet");
  await r.main.locator(".nav", { hasText: "Audit log" }).click();
  await r.main.waitForSelector("td"); // the approved file_write from the earlier test
  expect(await r.main.locator("tbody").innerText()).toContain("file_write");
  await r.main.locator("th", { hasText: "Tool" }).click();
  expect(await r.main.locator("th", { hasText: "Tool" }).innerText()).toContain("▲");
  await shot(r.main, "audit");
});

it("MCP servers are added disabled, and enabling shows exactly what would run before anything changes", async () => {
  await r.main.locator(".nav", { hasText: "Tools / MCP" }).click();
  await r.main.locator("button", { hasText: "Add server" }).click();
  await r.main.locator(".modal input[type=text]").first().fill("echo");
  await r.main.locator(".modal input[type=text]").nth(1).fill("python");
  await r.main.locator(".modal textarea").first().fill("-m\nmcp_echo");
  await r.main.locator(".modal button.primary").click();
  await r.main.waitForSelector("td");
  const row = r.main.locator("tbody tr", { hasText: "echo" });
  expect(await row.innerText()).toContain("No"); // stored disabled
  expect(await r.main.locator(".topbar .status").innerText()).toContain("Server added (disabled)");
  await row.locator("button", { hasText: "Enable" }).click();
  expect(await r.main.locator(".modal").innerText()).toContain("python -m mcp_echo");
  await shot(r.main, "mcp-confirm");
  await r.main.locator(".modal button", { hasText: "No" }).click();
  expect(await row.innerText()).toContain("No"); // declined: nothing changed
  await row.locator("button", { hasText: "Remove" }).click();
  await r.main.locator(".modal button.primary").click();
  await r.main.waitForSelector(".empty");
});

it("usage shows the daemon's numbers, and a broken policy is refused while a valid one is saved", async () => {
  await r.main.locator(".nav", { hasText: "Usage" }).click();
  await r.main.waitForFunction(() => /Today: \d/.test(document.querySelector(".mgr")?.textContent ?? ""));
  await r.main.locator(".nav", { hasText: "Policy" }).click();
  await r.main.waitForFunction(() => (document.querySelector(".policy") as HTMLTextAreaElement | null)?.value.includes("paths"));
  const before = await r.main.locator(".policy").inputValue();
  await r.main.locator(".policy").fill("this is : not [ valid");
  await r.main.locator(".mgr button.primary").click();
  await r.main.waitForFunction(() => document.querySelector(".topbar .status")?.textContent?.startsWith("!")); // refused by the daemon
  await r.main.locator(".policy").fill(before);
  await r.main.locator(".mgr button.primary").click();
  await r.main.waitForFunction(() => document.querySelector(".mgr .row .muted")?.textContent === "Saved.");
});

it("settings: only the change is saved, a rename reaches every title, and an unsaved colour preview is dropped", async () => {
  await r.main.locator(".nav", { hasText: "Settings" }).click();
  await r.main.waitForSelector(".settings .form");
  await r.main.getByLabel("Agent name").fill("Mimi");
  await r.main.getByLabel("Avatar size").fill("1.7");
  await r.main.locator(".savebar button").click();
  await r.main.waitForFunction(() => document.querySelector(".brand .title")?.textContent === "Mimi");
  const titles = await r.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((w) => w.getTitle()));
  expect(titles).toEqual(expect.arrayContaining(["Mimi", "Mimi — Approvals"]));
  await r.main.waitForFunction(() => (window as any).agent.init().then((i: any) => i.settings.ui.avatar_scale === 1.7)); // the rename and the patch are two replies
  await r.main.locator(".swatch").nth(1).click();
  expect(await r.main.evaluate(() => document.documentElement.style.getPropertyValue("--accent"))).toBe("#E8742F");
  await r.main.locator(".tab", { hasText: "Models" }).click();
  await r.main.locator(".form button", { hasText: "Test" }).first().click();
  await r.main.waitForFunction(() => document.querySelector(".testres")?.textContent === "✔");
  await r.main.locator(".tab", { hasText: "API keys" }).click();
  expect(await r.main.locator(".keystate").first().innerText()).toContain("set");
  await r.main.locator(".tab", { hasText: "About" }).click();
  await r.main.waitForFunction(() => /RAM \d+ MB/.test(document.querySelector(".tabbody")?.textContent ?? ""));
  await shot(r.main, "settings");
  await r.main.locator(".nav", { hasText: "Chat" }).click();
  expect(await r.main.evaluate(() => document.documentElement.style.getPropertyValue("--accent"))).toBe("#3340D9"); // not saved: the real colour is back
});
