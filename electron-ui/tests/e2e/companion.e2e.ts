// The avatar and its satellite windows against a real daemon (scripted model, synthetic screenshots).
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { Page } from "playwright-core";
import { afterAll, beforeAll, expect, it } from "vitest";
import { type Rig, startRig } from "./harness";

let r: Rig, avatar: Page;
const SHOTS = process.env.E2E_SHOTS;
const shot = async (page: Page, name: string): Promise<void> => void (SHOTS && (await page.screenshot({ path: path.join(SHOTS, `${name}.png`), omitBackground: true })));
const PYTHON = path.resolve(__dirname, "..", "..", "..", ".venv", "Scripts", "python.exe");
const sleep = (ms: number): Promise<void> => new Promise((res) => setTimeout(res, ms));

type Info = { visible: boolean; bounds: { x: number; y: number; width: number; height: number }; hwnd: string; title: string } | null;
/** A companion window as the main process sees it. */
const win = (role: string): Promise<Info> => r.app.evaluate(({ BrowserWindow }, role) => {
  const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().endsWith(`#/${role}`));
  return w ? { visible: w.isVisible(), bounds: w.getBounds(), hwnd: String(w.getNativeWindowHandle().readBigUInt64LE()), title: w.getTitle() } : null;
}, role);
const until = async (ok: () => Promise<boolean>, seconds = 15): Promise<void> => {
  for (const end = Date.now() + seconds * 1000; Date.now() < end; await sleep(100)) if (await ok()) return;
  throw new Error("timed out waiting for a window state");
};
const shown = (role: string): Promise<void> => until(async () => Boolean((await win(role))?.visible));
// Window bounds land on whole physical pixels, so on a scaled display (125 %, 150 %) they can be one point off.
const near = (got: number[], want: number[]): void => got.forEach((v, i) => expect(Math.abs(v - want[i]!), `${got} vs ${want}`).toBeLessThanOrEqual(1));
const hidden = (role: string): Promise<void> => until(async () => !(await win(role))?.visible);

/** Which top-level window (and whose process) Windows itself finds at a screen point, in physical pixels. */
function windowAt(x: number, y: number): { hwnd: string; pid: number } {
  const code = "import ctypes,sys;from ctypes import wintypes;ctypes.windll.shcore.SetProcessDpiAwareness(2);u=ctypes.windll.user32;"
    + "u.WindowFromPoint.restype=wintypes.HWND;u.WindowFromPoint.argtypes=[wintypes.POINT];u.GetAncestor.restype=wintypes.HWND;u.GetAncestor.argtypes=[wintypes.HWND,ctypes.c_uint];"
    + "h=u.GetAncestor(u.WindowFromPoint(wintypes.POINT(int(sys.argv[1]),int(sys.argv[2]))),2);p=wintypes.DWORD();u.GetWindowThreadProcessId(wintypes.HWND(h),ctypes.byref(p));print(h or 0,p.value)";
  const [hwnd, pid] = execFileSync(PYTHON, ["-c", code, String(x), String(y)]).toString().trim().split(" ");
  return { hwnd: hwnd!, pid: Number(pid) };
}

/** Open the ring around the avatar and click the slot at `index` of `n`. */
async function ring(index: number, n: number, again = false): Promise<Page> {
  if (!again) {
    await avatar.locator("canvas").click({ button: "right" });
    await shown("radial");
  }
  const page = await r.page("radial");
  await page.waitForSelector(".radial .slot");
  const side = (await win("radial"))!.bounds.width, c = side / 2, rad = c - 6 - 35, a = (index * 2 * Math.PI) / n;
  await page.mouse.click(c + rad * Math.sin(a), c - rad * Math.cos(a));
  return page;
}

beforeAll(async () => {
  r = await startRig();
  avatar = await r.page("avatar");
  await shown("avatar");
  await r.main.waitForSelector(".wizard");
  await r.main.locator(".wizard button", { hasText: "Close" }).click();
});
afterAll(async () => r?.stop());

it("the avatar sits in the bottom-right corner, as big as its picture, and belongs to the process the daemon was told about", async () => {
  const a = (await win("avatar"))!;
  const area = await r.app.evaluate(({ screen }) => screen.getPrimaryDisplay().workArea);
  near([a.bounds.x, a.bounds.y, a.bounds.width, a.bounds.height], [area.x + area.width - 128 - 24, area.y + area.height - 128 - 24, 128, 128]);
  expect(a.title).toBe("personal-agent");
  const pid = await r.app.evaluate(() => process.pid);
  const pt = await r.app.evaluate(({ screen }, b) => ({ mid: screen.dipToScreenPoint({ x: b.x + 64, y: b.y + 74 }), corner: screen.dipToScreenPoint({ x: b.x + 3, y: b.y + 3 }) }), a.bounds);
  expect(windowAt(pt.mid.x, pt.mid.y)).toEqual({ hwnd: a.hwnd, pid }); // the picture is the window, owned by the pid sent as ui_pid
  expect(windowAt(pt.corner.x, pt.corner.y).hwnd).not.toBe(a.hwnd); // a transparent corner belongs to whatever is underneath: clicks pass through
  await shot(avatar, "avatar");
});

it("a click opens the quick box, and what is typed there reaches the agent; the answer shows in a bubble", async () => {
  await avatar.locator("canvas").click();
  await shown("quick");
  const quick = await r.page("quick"), a = (await win("avatar"))!.bounds, q = (await win("quick"))!.bounds;
  near([q.x + q.width, q.y + q.height + 6], [a.x + a.width, a.y]); // right-aligned, just above the avatar
  await quick.locator("textarea").fill("ping from the avatar");
  await quick.locator("textarea").press("Enter");
  await hidden("quick");
  await r.main.waitForFunction(() => document.querySelector(".msg.agent .md strong")?.textContent === "ok");
  expect(await r.main.locator(".msg.user .md").first().innerText()).toContain("ping from the avatar");
  await shown("bubble");
  const bubble = await r.page("bubble");
  expect(await bubble.locator(".bubble").innerText()).toContain("pong");
  const b = (await win("bubble"))!.bounds;
  near([b.y + b.height + 4], [a.y]); // above the avatar
  await shot(bubble, "bubble");
});

it("a quick box left empty closes when the avatar is dragged, and the avatar snaps to the screen edge", async () => {
  await avatar.locator("canvas").click();
  await shown("quick");
  const a = (await win("avatar"))!.bounds;
  await avatar.mouse.move(64, 74);
  await avatar.mouse.down();
  await avatar.mouse.move(74, 84, { steps: 3 }); // more than 4 px: a drag, not a click
  await hidden("quick");
  await avatar.mouse.up();
  const after = (await win("avatar"))!.bounds, area = await r.app.evaluate(({ screen }) => screen.getPrimaryDisplay().workArea);
  expect(after.width).toBe(128);
  expect([after.x + after.width <= area.x + area.width, after.y + after.height <= area.y + area.height]).toEqual([true, true]); // never off screen
  expect(a.width).toBe(after.width);
});

it("the ring opens on a right click: number keys and clicks pick slots, a sub-ring goes back, the kill switch needs a second click", async () => {
  await avatar.locator("canvas").click({ button: "right" });
  await shown("radial");
  const page = await r.page("radial");
  await page.waitForSelector(".radial .slot");
  expect(await page.locator(".slot .label").allInnerTexts()).toEqual(["Chat", "Talk", "Capture ›", "Watch screen", "Approvals", "Manage ›", "More ›", "Pause agent"]);
  const a = (await win("avatar"))!.bounds, g = (await win("radial"))!.bounds;
  expect([Math.abs(g.x + g.width / 2 - (a.x + 64)) <= 1 || g.x + g.width >= a.x + 128, g.width]).toEqual([true, 300]); // centred on the avatar (or pushed in at the edge)
  await shot(page, "radial");
  await page.keyboard.press("6"); // Manage ›
  await page.waitForFunction(() => document.querySelectorAll(".radial .slot").length === 9);
  expect((await page.locator(".slot .label").allInnerTexts()).at(-1)).toBe("Back");
  await page.keyboard.press("Escape"); // back to the root ring
  await page.waitForFunction(() => document.querySelectorAll(".radial .slot").length === 8);
  await ring(7, 8, true); // Pause agent: the first click only arms it
  await page.waitForFunction(() => document.querySelector(".radial")?.textContent?.includes("Click again"));
  expect(await r.main.locator(".paused").count()).toBe(0);
  await ring(7, 8, true);
  await hidden("radial");
  await r.main.waitForSelector(".paused");
  await r.main.locator(".paused button").click();
  await r.main.waitForSelector(".paused", { state: "detached" });
});

it("Capture › Whole screen attaches a screenshot from the daemon to the chat; you decide whether to send it", async () => {
  await ring(2, 8);
  const page = await r.page("radial");
  await page.waitForFunction(() => document.querySelectorAll(".radial .slot").length === 4);
  await ring(0, 4, true);
  await r.main.waitForSelector(".chip");
  expect(await r.main.locator(".chip").innerText()).toMatch(/^screen-\d{6}\.png/);
  await until(async () => (await (await r.page("bubble")).locator(".bubble").innerText()).includes("Captured: screen (1 private window hidden)"));
});

it("Select area: the dragged rectangle reaches the daemon in physical pixels, and the dim layer is gone before the shot", async () => {
  await ring(2, 8);
  const page = await r.page("radial");
  await page.waitForFunction(() => document.querySelectorAll(".radial .slot").length === 4);
  await ring(2, 4, true);
  await shown("region");
  const region = await r.page("region"), g = (await win("region"))!.bounds;
  const display = await r.app.evaluate(({ screen }) => screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).bounds);
  expect(g).toEqual(display); // covers the screen the pointer is on
  await region.mouse.move(100, 120);
  await region.mouse.down();
  await region.mouse.move(340, 300, { steps: 4 });
  await shot(region, "region");
  await region.mouse.up();
  await hidden("region");
  const want = await r.app.evaluate(({ screen }, o) => {
    const p = screen.dipToScreenRect(null, { x: o.x + 100, y: o.y + 120, width: 240, height: 180 });
    return [p.x, p.y, p.x + p.width, p.y + p.height];
  }, g);
  await until(async () => (await (await r.page("bubble")).locator(".bubble").innerText()).includes(`region [${want.join(", ")}]`));
  expect(await r.main.locator(".chip").count()).toBe(2);
  // Esc cancels without a screenshot
  await ring(2, 8);
  await page.waitForFunction(() => document.querySelectorAll(".radial .slot").length === 4);
  await ring(2, 4, true);
  await shown("region");
  await region.keyboard.press("Escape");
  await hidden("region");
  await until(async () => (await (await r.page("bubble")).locator(".bubble").innerText()).includes("Capture cancelled"));
  expect(await r.main.locator(".chip").count()).toBe(2);
});

it("watching the screen is never invisible: a badge stays up, the avatar and the menu say so, and a click on the badge stops it", async () => {
  await ring(3, 8);
  await shown("badge");
  const badge = await r.page("badge");
  expect(await badge.locator(".watchbadge").innerText()).toMatch(/Watching the screen \(\d+ min left\)/);
  const b = (await win("badge"))!.bounds, area = await r.app.evaluate(({ screen }) => screen.getPrimaryDisplay().workArea);
  near([b.x + b.width / 2, b.y], [area.x + area.width / 2, area.y + 6]);
  await shot(badge, "badge");
  await avatar.locator("canvas").click({ button: "right" });
  await shown("radial");
  const page = await r.page("radial");
  await page.waitForFunction(() => document.querySelector(".radial")?.textContent?.includes("Stop watching"));
  await page.keyboard.press("Escape");
  await hidden("radial");
  await badge.locator(".watchbadge").click();
  await hidden("badge");
});

it("the element the agent is about to touch is outlined where the daemon says, then the outline goes away by itself", async () => {
  await r.main.locator(".composer").fill("highlight it");
  await r.main.locator(".composer").press("Enter");
  await shown("highlight");
  const want = await r.app.evaluate(({ screen }) => screen.screenToDipRect(null, { x: 200, y: 300, width: 300, height: 80 }));
  const h = (await win("highlight"))!.bounds;
  near([h.x, h.y, h.width, h.height], [want.x - 6, want.y - 28, want.width + 12, want.height + 34]);
  const page = await r.page("highlight");
  expect(await page.locator(".tag").innerText()).toBe("click: Save");
  await shot(page, "highlight");
  await hidden("highlight");
});

const ui = (): Promise<{ recording: boolean; speaking: boolean }> => r.main.evaluate(() => (window as any).agent.init());

it("Talk on the ring records from the microphone; what was said goes straight to the agent", async () => {
  await ring(1, 8);
  await until(async () => (await ui()).recording);
  await until(async () => (await (await r.page("bubble")).locator(".bubble").innerText()).includes("Listening"));
  expect(await r.main.locator(".row button", { hasText: "Stop" }).first().getAttribute("aria-pressed")).toBe("true"); // the chat's button follows
  await sleep(900); // long enough to be worth transcribing
  if ((await ui()).recording) await r.main.evaluate(() => (window as any).agent.ui("record", false)); // unless silence already ended it
  await until(async () => !(await ui()).recording);
  await r.main.waitForFunction(() => [...document.querySelectorAll(".msg.user .md")].some((m) => m.textContent?.includes("ping by voice")));
  await r.main.waitForFunction(() => !document.querySelector(".row .status")?.textContent);
});

it("Talk in the chat puts the transcript into the message box for you to check, without sending it", async () => {
  const sentBefore = await r.main.locator(".msg.user").count();
  await r.main.locator(".row button", { hasText: "Talk" }).click();
  await until(async () => (await ui()).recording);
  await sleep(900);
  if ((await ui()).recording) await r.main.locator(".row button", { hasText: "Stop" }).first().click();
  await r.main.waitForFunction(() => (document.querySelector(".composer") as HTMLTextAreaElement).value === "ping by voice");
  expect(await r.main.locator(".msg.user").count()).toBe(sentBefore);
  await r.main.locator(".composer").fill("");
});

it("answers are read aloud when that is switched on, and sending a new message interrupts the voice", async () => {
  await r.main.evaluate(() => (window as any).agent.send({ type: "settings_set", patch: { voice: { tts: { enabled: true } } } }));
  await r.main.waitForFunction(() => (window as any).agent.init().then((i: any) => i.settings.voice.tts.enabled));
  await r.main.locator(".composer").fill("say it");
  await r.main.locator(".composer").press("Enter");
  await until(async () => (await ui()).speaking);
  await until(async () => !(await ui()).speaking); // the clips ended by themselves
  await r.main.locator(".composer").fill("again");
  await r.main.locator(".composer").press("Enter");
  await until(async () => (await ui()).speaking);
  await r.main.locator(".composer").fill("interrupt");
  await r.main.locator(".composer").press("Enter"); // a new message stops the voice at once
  await until(async () => !(await ui()).speaking, 2);
  await r.main.evaluate(() => (window as any).agent.send({ type: "settings_set", patch: { voice: { tts: { enabled: false } } } }));
  await r.main.waitForFunction(() => !document.querySelector(".row .status")?.textContent);
});

it("a custom skin resizes the avatar, a broken manifest entry falls back, and hiding the avatar takes its bubble along", async () => {
  const png = await r.app.evaluate(({ nativeImage }) => nativeImage.createFromBitmap(Buffer.alloc(48 * 48 * 4, 255), { width: 48, height: 48 }).toPNG().toString("base64"));
  const folder = path.join(r.home, "cfg", "avatar", "mine");
  mkdirSync(folder, { recursive: true });
  writeFileSync(path.join(folder, "a.png"), Buffer.from(png, "base64"));
  writeFileSync(path.join(folder, "manifest.json"), JSON.stringify({ name: "mine", size: [48, 48], fallback: "idle",
    states: { idle: { frames: ["a.png"], fps: 2 }, success: { frames: ["../../outside.png"] }, thinking: { frames: ["missing.png"] } } }));
  expect(await r.main.evaluate(() => (window as any).agent.skins())).toEqual(["default", "mine"]);
  await r.main.evaluate(() => (window as any).agent.send({ type: "settings_set", patch: { ui: { skin: "mine", avatar_scale: 2 } } }));
  await until(async () => (await win("avatar"))!.bounds.width === 96); // 48 px picture at scale 2: a full square, so no click-through
  const a = (await win("avatar"))!;
  const pt = await r.app.evaluate(({ screen }, b) => screen.dipToScreenPoint({ x: b.x + 3, y: b.y + 3 }), a.bounds);
  expect(windowAt(pt.x, pt.y).hwnd).toBe(a.hwnd); // this skin is opaque to its corner
  await r.main.evaluate(() => (window as any).agent.send({ type: "settings_set", patch: { ui: { avatar_enabled: false } } }));
  await hidden("avatar");
  await hidden("bubble");
});
