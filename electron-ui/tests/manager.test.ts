// The management pages and the Settings form without a window: reply matching, row locks, timeouts, notes, the save patch.
import { describe, expect, it } from "vitest";
import { setLanguage } from "../src/shared/i18n";
import { Manager, ragSummary, REPLY_TIMEOUT_MS, TABLES, ts, viewRows } from "../src/shared/manager";
import type { Ev } from "../src/shared/session";
import { formFrom, hasProviderKey, settingsPatch } from "../src/shared/settings";

function rig(): { m: Manager; sent: Ev[]; fire: (ms: number) => void } {
  const sent: Ev[] = [], timers: [() => void, number][] = [];
  const m = new Manager((msg) => void sent.push(msg), undefined, (fn, ms) => timers.push([fn, ms]));
  return { m, sent, fire: (ms) => timers.filter(([, t]) => t === ms).forEach(([fn]) => fn()) };
}

const MEM = [{ id: 1, kind: "semantic", status: "active", text: "Likes tea" }, { id: 2, kind: "episodic", status: "proposed", text: "Bank PIN", sensitive: 1 }];

describe("manager pages", () => {
  setLanguage("en");

  it("loads a list, locks the row whose action is running and unlocks it with the list that follows", () => {
    const { m, sent } = rig();
    m.refresh("memory");
    expect(sent[0]).toMatchObject({ type: "memory_list" });
    m.onEvent({ type: "memory", items: MEM, rid: sent[0]!.rid });
    const row = m.tables.memory!.rows[0]!;
    m.act("memory", row, 2, { type: "memory_delete", id: 1 });
    expect(m.tables.memory!.busy(row)).toBe(2);
    expect(m.tables.memory!.busy(m.tables.memory!.rows[1]!)).toBeNull();
    m.onEvent({ type: "memory_deleted", ok: true, id: 1, rid: sent[1]!.rid });
    expect(sent[2]).toMatchObject({ type: "memory_list" }); // the change re-requests the list
    expect(m.tables.memory!.busy(row)).toBe(2); // still locked: only the fresh list is the truth
    m.onEvent({ type: "memory", items: [MEM[1]], rid: sent[2]!.rid });
    expect(m.tables.memory!.rows).toHaveLength(1);
    expect(m.tables.memory!.pending.size).toBe(0);
    expect(m.status).toBe("Deleted.");
  });

  it("drops an older list that crossed a newer request, so a deleted row cannot come back", () => {
    const { m, sent } = rig();
    m.refresh("memory");
    m.refresh("memory");
    m.onEvent({ type: "memory", items: [MEM[1]], rid: sent[1]!.rid });
    m.onEvent({ type: "memory", items: MEM, rid: sent[0]!.rid }); // stale
    expect(m.tables.memory!.rows).toHaveLength(1);
  });

  it("an error gives back only the row whose request failed", () => {
    const { m, sent } = rig();
    m.onEvent({ type: "memory", items: MEM });
    const [a, b] = m.tables.memory!.rows as [Ev, Ev];
    m.act("memory", a, 0, { type: "memory_update", id: 1, text: "x" });
    m.act("memory", b, 1, { type: "memory_update", id: 2, status: "active" });
    m.onEvent({ type: "error", code: "bad_request", message: "too long", rid: sent[0]!.rid });
    expect(m.tables.memory!.busy(a)).toBeNull();
    expect(m.tables.memory!.busy(b)).toBe(1);
    expect(m.status).toBe("! too long");
  });

  it("no answer keeps the row locked and asks for the truth instead of allowing a second click", () => {
    const { m, sent, fire } = rig();
    m.onEvent({ type: "memory", items: MEM });
    const row = m.tables.memory!.rows[0]!;
    m.act("memory", row, 2, { type: "memory_delete", id: 1 });
    fire(REPLY_TIMEOUT_MS);
    expect(m.tables.memory!.busy(row)).toBe(2);
    expect(sent.at(-1)).toMatchObject({ type: "memory_list" });
    expect(m.status).toContain("No answer");
  });

  it("documents: an indexing job and a search lock their buttons until answered", () => {
    const { m, sent } = rig();
    m.ragStart({ type: "rag_add", path: "C:\\docs" });
    expect(m.rag.job).toBe(sent[0]!.rid);
    m.onEvent({ type: "rag_added", report: ["ingested a.md", "unchanged b.md", "failed c.pdf: broken"], rid: sent[0]!.rid });
    expect(m.rag.job).toBeNull();
    expect(m.rag.output).toContain("ingested a.md");
    m.onEvent({ type: "rag", items: [], watching: true, paused: false, rid: sent[1]!.rid });
    expect(m.status).toBe("! 1 failed (failed c.pdf: broken); 1 indexed, 1 unchanged, 0 removed");
    expect(m.rag.watching).toBe(true);
    m.ragSearch(" tea ");
    m.ragSearch("again"); // one search at a time
    expect(sent.filter((s) => s.type === "rag_search")).toHaveLength(1);
    m.onEvent({ type: "rag_results", hits: [{ cite: "a.md § Intro", score: 0.9, text: "Tea is good" }], rid: 999 }); // someone else's reply
    expect(m.rag.search).not.toBeNull();
    m.onEvent({ type: "rag_results", hits: [{ cite: "a.md § Intro", score: 0.9, text: "Tea is good" }], rid: sent.at(-1)!.rid });
    expect(m.rag.output).toBe("[1] a.md § Intro  (0.9)\nTea is good");
    expect(m.rag.search).toBeNull();
  });

  it("MCP: reload reports each server and says when changes still wait for a reload", () => {
    const { m, sent } = rig();
    const servers = { fs: { enabled: true, status: "ok", kind: "stdio", target: "npx fs", tools: [{ name: "fs_read", tier: "T0" }], skipped: [] },
      web: { enabled: true, status: "error: refused", kind: "http", target: "http://x", tools: [], skipped: [] } };
    m.onEvent({ type: "mcp", servers, reload_needed: true });
    expect(m.mcp.reloadNeeded).toBe(true);
    expect(m.tables.mcp!.rows.map((r) => r.name)).toEqual(["fs", "web"]);
    m.mcpShowTools(m.tables.mcp!.rows[0]!);
    expect(m.mcp.output).toContain("[T0] fs_read  ! runs without approval");
    m.mcpReload();
    expect(m.mcp.job).toBe(sent[0]!.rid);
    m.onEvent({ type: "mcp", servers, reload_needed: false, reloaded: true, rid: sent[0]!.rid });
    expect(m.mcp.job).toBeNull();
    expect(m.status).toBe("! Reloaded: 1 ok, 1 failed.");
    expect(m.mcp.output).toBe("fs: ok\nweb: error: refused");
  });

  it("filters, searches and sorts numbers before text", () => {
    const rows = [{ id: 10, kind: "b", status: "active", text: "Zebra" }, { id: 9, kind: "a", status: "proposed", text: "apple" }, { id: 100, kind: "a", status: "active", text: "Mango" }];
    const spec = TABLES.memory!;
    expect(viewRows(rows, spec, { facet: "active" }).map((r) => r.id)).toEqual([10, 100]);
    expect(viewRows(rows, spec, { needle: "APP" }).map((r) => r.id)).toEqual([9]);
    expect(viewRows(rows, spec, { sort: [0, false] }).map((r) => r.id)).toEqual([9, 10, 100]);
    expect(viewRows(rows, spec, { sort: [3, true] }).map((r) => r.text)).toEqual(["Zebra", "Mango", "apple"]);
    expect(ragSummary([])).toBe("No supported files found.");
    expect(ts(0)).toBe("");
    expect(ts(1_700_000_000)).toMatch(/^2023-11-1\d \d\d:\d\d:\d\d$/);
  });
});

describe("settings form", () => {
  const ev: Ev = { type: "settings", agent: { name: "Mimi", language: "auto" },
    models: { planner: { provider: "anthropic", model: "claude-haiku-5-5" }, fast: { provider: "google", model: "gemini-flash" } },
    providers: ["anthropic", "google"], budgets: { daily_max_tokens: 1_000_000, daily_max_cost_usd: 2 },
    voice: { stt: { provider: "google", model: "g", max_seconds: 120 }, tts: { enabled: false, voice_vi: "vi", voice_en: "en", rate: "+0%", max_chars: 600 } },
    ui: { language: "auto", avatar_enabled: true, avatar_always_on_top: true, avatar_wander: false, avatar_scale: 1, skin: "default", highlight: "#E8742F", hotkeys: {} },
    screen: { agent_view: true, control: false, block_titles: ["bank", "password"], block_processes: ["keepass.exe"], watch_interval_s: 2,
      watch_change_threshold: 6, watch_max_minutes: 30, max_actions: 5 },
    secrets: { ANTHROPIC_API_KEY: false, GOOGLE_API_KEY: false } };

  it("saves nothing when nothing changed, and only the changed keys otherwise", () => {
    const f = formFrom(ev);
    expect(settingsPatch(ev, f)).toEqual({ patch: null, name: null });
    f.name = " Bé Na ";
    f.models.fast!.model = "gemini-pro ";
    f.ui.highlight = "#3340D9";
    f.ui.avatar_scale = 1.234;
    f.tts.enabled = true;
    f.budgets.daily_max_cost_usd = 3.456;
    f.screen!.block_titles = "bank\n\n  secret  \n";
    f.screen!.control = true;
    expect(settingsPatch(ev, f)).toEqual({ name: "Bé Na", patch: { models: { fast: { model: "gemini-pro" } }, ui: { highlight: "#3340D9", avatar_scale: 1.23 },
      voice: { tts: { enabled: true } }, budgets: { daily_max_cost_usd: 3.46 }, screen: { control: true, block_titles: ["bank", "secret"] } } });
  });

  it("knows whether any model provider key is set (first run)", () => {
    expect(hasProviderKey(ev)).toBe(false);
    expect(hasProviderKey({ ...ev, secrets: { GOOGLE_API_KEY: true } })).toBe(true);
    expect(hasProviderKey(null)).toBe(false);
  });
});
