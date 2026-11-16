// The approval center with several requests at once. The requests are pushed straight into the approval window (no daemon turn is
// needed to test how they are shown and answered), and nothing is ever sent back: the test never presses Send on a complete answer.
import { afterAll, beforeAll, expect, it } from "vitest";
import { type Rig, startRig } from "./harness";

let r: Rig;
const soon = (s: number): number => Date.now() / 1000 + s;

beforeAll(async () => {
  r = await startRig();
  await r.main.waitForFunction(() => !document.querySelector(".topbar .warn")); // connected: the daemon has sent its own (empty) list of pending approvals
  await new Promise((s) => setTimeout(s, 1000));
  const items = [
    { call_id: "c1", tool: "file_write", args: { path: "a.txt", content: "hi" }, tier: "T2", risk: "high", tainted: true, reason: "needs your approval",
      preview: "--- /dev/null\n+++ a.txt\n@@ -0,0 +1 @@\n+hi", expires_at: soon(300), thread_id: "t1" },
    { call_id: "c2", tool: "shell", args: { command: "format x:" }, tier: "T3", risk: "critical", tainted: false, reason: "runs a program",
      preview: "format x:", expires_at: soon(120), thread_id: "t1" }];
  await r.app.evaluate(({ BrowserWindow }, evs) => {
    const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().endsWith("#/approvals"))!;
    w.webContents.send("agent:event", { type: "approval_request", thread_id: "t1", approvals: evs, _seq: 1e9 }); // a sequence number past the window's snapshot
  }, items);
  await r.approvals.waitForSelector(".card");
}, 60_000);
afterAll(async () => { await r?.stop(); });

const SHOTS = process.env.E2E_SHOTS; // a folder: keep a screenshot for a human to look at
const tool = (): Promise<string> => r.approvals.locator(".card b").innerText();

it("shows a queue chip per request and moves between them with the arrow keys", async () => {
  expect(await r.approvals.locator(".queue .q").count()).toBe(2);
  expect(await tool()).toBe("file_write");
  await r.approvals.keyboard.press("ArrowRight");
  expect(await tool()).toBe("shell");
  await r.approvals.keyboard.press("ArrowLeft");
  expect(await tool()).toBe("file_write");
  expect(await r.approvals.locator(".card .taint").count()).toBe(1); // untrusted content is flagged
});

it("the keys answer the request on screen, and the chip shows it", async () => {
  await r.approvals.keyboard.press("a");
  await r.approvals.waitForSelector(".q.done .qmark.yes");
  if (SHOTS) await r.approvals.screenshot({ path: `${SHOTS}/approvals-queue.png` });
  expect(await r.approvals.locator(".radio input:checked").count()).toBe(1);
  expect(await r.approvals.locator(".send p.muted").innerText()).toContain("1"); // "1 of 2 answered"
});

it("sending with a request unanswered says which one and takes you to it", async () => {
  await r.approvals.keyboard.press("Enter");
  await r.approvals.waitForSelector(".error");
  expect(await r.approvals.locator(".error").innerText()).toContain("choose an answer for shell");
  expect(await tool()).toBe("shell");
});

it("a dangerous action needs CONFIRM; rejecting asks for a reason instead", async () => {
  await r.approvals.keyboard.press("a");
  await r.approvals.waitForSelector("input[placeholder*=CONFIRM]");
  await r.approvals.keyboard.press("Enter");
  expect(await r.approvals.locator(".error").innerText()).toContain("CONFIRM");
  await r.approvals.keyboard.press("r");
  await r.approvals.waitForSelector("input[placeholder*=Reason]");
  expect(await r.approvals.locator("input[placeholder*=CONFIRM]").count()).toBe(0);
});

it("editing the arguments is visible and can be undone", async () => {
  await r.approvals.keyboard.press("ArrowLeft");
  await r.approvals.locator("summary").click();
  const box = r.approvals.locator("textarea.args");
  const before = await box.inputValue();
  await box.fill(before.replace("hi", "bye"));
  expect(await r.approvals.locator("summary em").innerText()).toContain("edited");
  await r.approvals.locator("details button", { hasText: "Undo" }).click();
  expect(await box.inputValue()).toBe(before);
  expect(await r.approvals.locator("summary em").count()).toBe(0);
});
