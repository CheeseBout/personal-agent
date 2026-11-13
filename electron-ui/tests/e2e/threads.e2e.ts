// Conversations against a real daemon (scripted model): the list, new chat, drafts per conversation, search, pin, rename, delete,
// and getting the latest conversation back after the UI is restarted.
import path from "node:path";
import electronPath from "electron";
import { _electron, type Page } from "playwright-core";
import { afterAll, beforeAll, expect, it } from "vitest";
import { type Rig, startRig } from "./harness";

let r: Rig;
const wait = (ms: number): Promise<void> => new Promise((s) => setTimeout(s, ms));
/** The window is narrower than the "wide" layout, so the list opens over the chat when asked for (picking a conversation closes it again). */
async function list(p: Page): Promise<Page> {
  if (!(await p.locator(".threads").count())) await p.locator("button[aria-label=Conversations]").click();
  await p.waitForSelector(".threads");
  return p;
}
const rows = async (p: Page): Promise<string[]> => (await list(p)).locator(".thread .pick").allInnerTexts();

async function say(p: Page, text: string, answers: number): Promise<void> {
  await p.locator(".composer").fill(text);
  await p.locator(".composer").press("Enter");
  await p.waitForFunction((n) => document.querySelectorAll(".msg.agent").length >= n, answers);
}

beforeAll(async () => {
  r = await startRig();
  await r.main.evaluate(() => (window as any).agent.send({ type: "secret_set", name: "ANTHROPIC_API_KEY", value: "sk-test" })); // no first-run wizard
  await r.main.waitForFunction(() => (window as any).agent.init().then((i: any) => i.settings.secrets.ANTHROPIC_API_KEY === true));
  await r.main.locator(".wizard button", { hasText: "Close" }).click({ timeout: 5000 }).catch(() => undefined); // the first-run wizard opened before the key was set
}, 60_000);
afterAll(async () => {
  await r?.app.close().catch(() => undefined); // let Windows release the folders before they are removed
  await wait(1500);
  await r?.stop();
});

it("a first message creates a conversation named after it", async () => {
  await say(r.main, "báo cáo doanh thu tuần này", 1);
  await list(r.main);
  await r.main.waitForFunction(() => document.querySelectorAll(".thread .pick").length === 1);
  expect(await rows(r.main)).toEqual(["báo cáo doanh thu tuần này"]);
  expect(await r.main.locator(".ttitle").innerText()).toBe("báo cáo doanh thu tuần này");
});

it("New chat starts empty, keeps a draft per conversation and switches back", async () => {
  await (await list(r.main)).locator(".threads button.primary").click();
  await r.main.waitForFunction(() => document.querySelectorAll(".transcript .msg").length === 0);
  expect(await r.main.locator(".ttitle").innerText()).toBe("New conversation");
  await r.main.locator(".composer").fill("a draft nobody sent");
  await say(r.main, "chuyện khác hẳn", 1);
  await list(r.main);
  await r.main.waitForFunction(() => document.querySelectorAll(".thread .pick").length === 2);
  await r.main.locator(".composer").fill("second draft");

  await r.main.locator(".thread .pick", { hasText: "báo cáo" }).click(); // back to the first (the list closes)
  await r.main.waitForFunction(() => document.querySelector(".ttitle")?.textContent?.startsWith("báo cáo"));
  expect(await r.main.locator(".transcript .msg.user").first().innerText()).toContain("báo cáo doanh thu");
  await r.main.waitForFunction(() => (document.querySelector(".composer") as HTMLTextAreaElement).value === ""); // the first conversation had no draft
  await list(r.main);
  await r.main.locator(".thread .pick", { hasText: "chuyện khác" }).click();
  await r.main.waitForFunction(() => (document.querySelector(".composer") as HTMLTextAreaElement).value === "second draft");
});

it("search finds a message across conversations, ignoring accents", async () => {
  await list(r.main);
  await r.main.locator(".find input").fill("bao cao");
  await r.main.waitForSelector(".hit");
  expect(await r.main.locator(".hit").count()).toBe(1);
  expect(await r.main.locator(".hit b").innerText()).toContain("báo cáo");
  await r.main.locator(".find input").fill("zzzz nothing");
  await r.main.waitForFunction(() => document.querySelector(".tlist")?.textContent?.includes("Nothing found"));
  await r.main.locator(".find input").fill("bao cao");
  await r.main.locator(".hit").click(); // opens that conversation
  await r.main.waitForFunction(() => document.querySelector(".ttitle")?.textContent?.startsWith("báo cáo"));
});

it("pin moves a conversation to the top; rename sticks", async () => {
  await list(r.main);
  await r.main.locator(".thread", { hasText: "chuyện khác" }).locator(".pin").click();
  await r.main.waitForFunction(() => document.querySelector(".tlist .grp")?.textContent === "Pinned");
  expect((await rows(r.main))[0]).toContain("chuyện khác");
  await r.main.locator(".thread .pick", { hasText: "chuyện khác" }).click();
  await r.main.waitForFunction(() => document.querySelector(".ttitle")?.textContent?.startsWith("chuyện khác"));
  await r.main.locator("button[aria-label=Rename]").click();
  await r.main.locator(".modal input[type=text]").fill("Doanh thu");
  await r.main.locator(".modal button.primary").click();
  await r.main.waitForFunction(() => document.querySelector(".ttitle")?.textContent === "Doanh thu");
  expect(await rows(r.main)).toContain("Doanh thu");
});

it("answers can be copied or asked again; a user message can be edited back into the composer", async () => {
  if (await r.main.locator(".threads").count()) await r.main.locator(".transcript").click({ position: { x: 5, y: 5 } }); // click away: the list closes
  await r.main.waitForSelector(".threads", { state: "detached" });
  const before = await r.main.locator(".msg.agent").count();
  await r.main.locator(".msg.agent").last().hover();
  await r.main.locator("button[aria-label='Ask again']").last().click();
  await r.main.waitForFunction((n) => document.querySelectorAll(".msg.agent").length > n, before);
  await r.main.locator(".msg.user").first().hover();
  await r.main.locator("button[aria-label='Edit and send again']").first().click();
  expect(await r.main.locator(".composer").inputValue()).toContain("chuyện khác hẳn");
  await r.main.locator(".composer").fill("");
});

it("the latest conversation comes back after the UI restarts", async () => {
  await r.app.close();
  const app = await _electron.launch({ executablePath: electronPath as unknown as string, args: [path.resolve(__dirname, "..", ".."), `--user-data-dir=${path.join(r.home, "electron2")}`],
    env: r.env as Record<string, string> });
  let main: Page | undefined;
  for (const end = Date.now() + 30_000; !main && Date.now() < end; await wait(100)) main = app.windows().find((p) => p.url().endsWith("#/main"));
  await main!.waitForFunction(() => document.querySelectorAll(".transcript .msg.user").length >= 1, undefined, { timeout: 15_000 });
  await main!.locator(".wizard button", { hasText: "Close" }).click({ timeout: 3000 }).catch(() => undefined); // the first-run wizard may be up again: it is not what this test is about
  await list(main!);
  expect(await main!.locator(".thread .pick").count()).toBe(2); // the list is back
  expect(await main!.locator(".transcript .msg.user").first().innerText()).toMatch(/báo cáo|chuyện khác/); // so is the conversation
  r.main = main!;
  r.app = app;
}, 60_000);

it("a conversation can be deleted after a confirmation; nothing else goes with it", async () => {
  await list(r.main);
  await r.main.locator(".thread", { hasText: /^Doanh thu$/ }).locator(".pick").click();
  await r.main.waitForFunction(() => document.querySelector(".ttitle")?.textContent === "Doanh thu");
  await r.main.locator("button[aria-label=Delete]").click();
  await r.main.locator(".modal button", { hasText: "No" }).click(); // declined: nothing changes
  expect((await rows(r.main)).length).toBe(2);
  await r.main.locator("button[aria-label=Delete]").click();
  await r.main.locator(".modal button.primary").click(); // "Yes"
  await list(r.main);
  await r.main.waitForFunction(() => ![...document.querySelectorAll(".thread .pick")].some((e) => e.textContent === "Doanh thu")); // gone once the list refreshes
  expect((await rows(r.main)).length).toBe(2); // the empty "new conversation" it fell back to, and the other one
  await r.main.locator(".find input").fill("khac han"); // words only the deleted conversation had
  await r.main.waitForFunction(() => document.querySelector(".tlist")?.textContent?.includes("Nothing found")); // its messages are gone from search too
}, 30_000);
