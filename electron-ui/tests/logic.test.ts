// UI logic that needs no windows: session model, avatar state machine, decisions, theme, i18n (ported from tests/unit/test_ui_logic.py).
import { describe, expect, it } from "vitest";
import { AvatarState } from "../src/shared/avatarState";
import { buildDecisions, type CardState } from "../src/shared/decisions";
import { resolveLanguage, setLanguage, tr } from "../src/shared/i18n";
import { normalizeApproval, Session } from "../src/shared/session";
import { mix, onColor, tokens } from "../src/shared/theme";

describe("session model", () => {
  it("follows a planned task from intent to final", () => {
    const s = new Session("t", "Mimi");
    s.apply({ type: "_user", text: "do it", attachments: ["a.png"] });
    expect(s.busy).toBe(true);
    expect(s.transcript[0]!.meta.attachments).toEqual(["a.png"]);
    s.apply({ type: "intent", intent: "task", confidence: 0.9, needs_plan: true });
    const ch = s.apply({ type: "plan", objective: "O", success_criteria: ["c1"], steps: ["a", "b"], roles: ["web", "main"] });
    expect(ch.has("plan")).toBe(true);
    expect(s.steps.map((x) => x.role)).toEqual(["web", "main"]);
    s.apply({ type: "worker", step: "s1", role: "web", status: "done", summary: "found it" });
    expect(s.steps.map((x) => x.status)).toEqual(["done", "todo"]);
    s.apply({ type: "worker", step: "s9", role: "web", status: "done", summary: "x" }); // unknown id is ignored
    s.apply({ type: "verdict", status: "done", notes: "" });
    expect(s.steps.every((x) => x.status === "done")).toBe(true);
    const end = s.apply({ type: "final", text: "ok", stopped: null, used: { steps: 2, tokens: 10, cost: 0.001 } });
    expect(s.busy).toBe(false);
    expect(s.transcript.at(-1)!.kind).toBe("agent");
    expect(s.used.steps).toBe(2);
    expect(end.has("usage")).toBe(true);
  });

  it("handles error, stop reason and the kill switch", () => {
    const s = new Session();
    s.apply({ type: "_user", text: "x" });
    s.apply({ type: "final", text: "partial", stopped: "budget", used: {} });
    expect(s.transcript.at(-1)!.text).toContain("stopped: budget");
    s.apply({ type: "_user", text: "y" });
    s.apply({ type: "error", code: "quota_exhausted", message: "out of credit" });
    expect(s.busy).toBe(false);
    expect(s.transcript.at(-1)!.kind).toBe("error");
    s.apply({ type: "killed", threads: 2 });
    expect(s.killed).toBe(true);
    s.apply({ type: "kill_released" });
    expect(s.killed).toBe(false);
    expect([...s.apply({ type: "agent_name", name: "Bé Na" })]).toEqual(["name"]);
    expect(s.agentName).toBe("Bé Na");
  });

  it("normalises JSON args and approvals survive a reconnect", () => {
    const row = { call_id: "c1", thread_id: "t", tool: "shell", args: JSON.stringify({ command: "dir" }), tainted: 1, tier: "T2" };
    const s = new Session();
    s.apply({ type: "approvals", items: [row] }); // pushed by the daemon on every (re)connect
    expect(s.approvals.c1!.args).toEqual({ command: "dir" });
    expect(s.approvals.c1!.tainted).toBe(true);
    s.apply({ type: "approval_request", thread_id: "t", approvals: [{ ...row, call_id: "c2", args: { a: 1 } }] });
    expect(Object.keys(s.approvals)).toEqual(["c1", "c2"]);
    expect(Object.keys(s.pendingForThread())).toEqual(["t"]);
    s.apply({ type: "_decided", call_ids: ["c1"] });
    expect(Object.keys(s.approvals)).toEqual(["c2"]);
    s.apply({ type: "approvals", items: [] });
    expect(s.approvals).toEqual({});
    expect(normalizeApproval({ args: "not json" }).args).toEqual({ raw: "not json" });
  });

  it("streams the answer token by token and drops prose that preceded a tool call", () => {
    const s = new Session();
    s.apply({ type: "_user", text: "hi" });
    s.apply({ type: "token", text: "Let me " });
    s.apply({ type: "token", text: "check" });
    expect(s.transcript.at(-1)).toMatchObject({ kind: "agent", text: "Let me check" });
    s.apply({ type: "tool_call", name: "web_search", args: { q: "x" } });
    expect(s.transcript.some((e) => e.text === "Let me check")).toBe(false);
    s.apply({ type: "token", text: "Done" });
    s.apply({ type: "final", text: "Done.", used: {} });
    expect(s.transcript.filter((e) => e.kind === "agent").map((e) => e.text)).toEqual(["Done."]);
  });

  it("a window started from a snapshot continues exactly like the original", () => {
    const a = new Session("t", "Mimi");
    a.apply({ type: "_user", text: "hi" });
    a.apply({ type: "token", text: "He" });
    const b = Session.from(a.snapshot());
    for (const s of [a, b]) s.apply({ type: "token", text: "llo" });
    expect(b.transcript).toEqual(a.transcript);
    for (const s of [a, b]) s.apply({ type: "final", text: "Hello", used: {} });
    expect(b.snapshot()).toEqual(a.snapshot());
  });
});

describe("avatar state machine", () => {
  const clock = (): { t: number; now: () => number } => {
    const c = { t: 1000, now: () => c.t };
    return c;
  };

  it("follows the agent and falls asleep", () => {
    const c = clock();
    const a = new AvatarState(60, c.now);
    expect(a.current()).toBe("idle");
    expect(a.nextChangeIn()).toBeCloseTo(60);
    a.userSent();
    expect(a.current()).toBe("thinking");
    expect(a.onEvent({ type: "tool_call" })).toBe("working");
    expect(a.onEvent({ type: "plan" })).toBe("thinking");
    expect(a.onEvent({ type: "final", text: "x" })).toBe("success");
    c.t += 3;
    expect(a.current()).toBe("idle");
    c.t += 60;
    expect(a.current()).toBe("sleeping");
    a.onEvent({ type: "error", code: "x" });
    expect(a.current()).toBe("error");
  });

  it("puts approval over listening over speaking", () => {
    const a = new AvatarState(300, clock().now);
    a.speaking = true;
    expect(a.current()).toBe("speaking");
    a.listening = true;
    expect(a.current()).toBe("listening");
    a.onEvent({ type: "approval_request", approvals: [{ call_id: "1" }] });
    expect(a.current()).toBe("awaiting_approval");
    a.pendingApprovals = 0;
    a.listening = a.speaking = false;
    expect(a.current()).not.toBe("awaiting_approval");
    a.onEvent({ type: "error", code: "approval_pending" }); // a normal condition, not a failure animation
    expect(a.current()).not.toBe("error");
  });

  it("sleeps while disconnected and shows error for a stop reason", () => {
    const a = new AvatarState(300, clock().now);
    a.connected = false;
    expect(a.current()).toBe("sleeping");
    a.connected = true;
    expect(a.onEvent({ type: "final", text: "x", stopped: "budget" })).toBe("error");
  });
});

describe("approval decisions", () => {
  const card = (kw: Partial<CardState> = {}): CardState => ({ call_id: "c1", tool: "shell", tier: "T2", choice: "approve",
    args: { command: "dir" }, args_text: JSON.stringify({ command: "dir" }), reason: "", confirm: "", ...kw });

  it("covers approve, remember, edit and reject", () => {
    const d = buildDecisions([card(), card({ call_id: "c2", choice: "remember" }), card({ call_id: "c3", args_text: '{"command": "whoami"}' }),
      card({ call_id: "c4", choice: "reject", reason: " too risky " })]);
    expect(d.c1).toEqual({ action: "approve", remember: false });
    expect(d.c2).toEqual({ action: "approve", remember: true });
    expect(d.c3).toEqual({ action: "edit", args: { command: "whoami" } });
    expect(d.c4).toEqual({ action: "reject", feedback: "too risky" });
  });

  it("does not call reformatted but unchanged arguments an edit", () => {
    const args = { a: 1, b: { c: [1, 2] } };
    expect(buildDecisions([card({ args, args_text: '{ "b": {"c": [1, 2]},\n "a": 1 }' })]).c1.action).toBe("approve");
    expect(buildDecisions([card({ args, args_text: '{"a": 1, "b": {"c": [2, 1]}}' })]).c1.action).toBe("edit");
  });

  it.each([[{ choice: null }, "choose an answer"], [{ args_text: "{nope" }, "not valid JSON"], [{ args_text: "[1]" }, "JSON object"],
    [{ tier: "T3" }, "CONFIRM"]] as [Partial<CardState>, string][])("rejects incomplete answers %#", (kw, msg) => {
    expect(() => buildDecisions([card(kw)])).toThrow(msg);
  });

  it("T3 needs the confirm word", () => {
    expect(buildDecisions([card({ tier: "T3", confirm: "CONFIRM" })]).c1).toEqual({ action: "approve", remember: false, confirm: true });
  });
});

describe("theme and i18n", () => {
  it("derives the same tokens as the Python theme", () => {
    expect(tokens(false)).toMatchObject({ accent: "#E8742F", accent_hover: "#BE5F27", on_accent: "#111111", accent_soft: "#F5E4DA", accent_text: "#925031" });
    expect(tokens(true, "#3340D9")).toMatchObject({ accent_hover: "#2A34B2", on_accent: "#FFFFFF", accent_soft: "#1B1E3C", accent_text: "#7D85E1", bg: "#0A0A0A" });
    expect(onColor("#E0A21F")).toBe("#111111");
    expect(mix("#FFFFFF", "#000000", 0.5)).toBe("#808080");
  });

  it("translates with placeholders and falls back to English", () => {
    expect(resolveLanguage("auto", "vi-VN")).toBe("vi");
    expect(resolveLanguage("auto", "fr-FR")).toBe("en");
    setLanguage("vi");
    expect(tr("Send", "Gửi")).toBe("Gửi");
    expect(tr("{n} items", "{n} mục", { n: 3 })).toBe("3 mục");
    expect(tr("Only English")).toBe("Only English");
    setLanguage("en");
    expect(tr("{n} items", "{n} mục", { n: 3 })).toBe("3 items");
  });
});

describe("attachments", () => {
  it("allows at most 4 files and 15 MB per message, and names the type from the extension", async () => {
    const { checkSize, mimeOf, roomFor } = await import("../src/shared/attach");
    const att = (bytes: number): { name: string; mime: string; data: string } => ({ name: "a", mime: "x", data: "A".repeat(Math.ceil((bytes * 4) / 3)) });
    expect(roomFor([att(1), att(1), att(1)], att(1))).toBe(true);
    expect(roomFor([att(1), att(1), att(1), att(1)], att(1))).toBe(false);
    expect(roomFor([att(5_000_000), att(5_000_000)], att(4_999_000))).toBe(true);
    expect(roomFor([att(5_000_000), att(5_000_000), att(4_000_000)], att(2_000_000))).toBe(false);
    expect(() => checkSize("big.bin", 5_000_001)).toThrow("larger than 5 MB");
    expect([mimeOf("Notes.MD"), mimeOf("a.docx").slice(-8), mimeOf("x.bin", "image/png"), mimeOf("x.bin")]).toEqual(["text/markdown", "document", "image/png", "application/octet-stream"]);
  });
});

describe("hotkeys", () => {
  it("turns config specs into accelerators and refuses a bare key", async () => {
    const { toAccelerator } = await import("../src/shared/hotkeys");
    expect(toAccelerator("ctrl+alt+k")).toBe("Control+Alt+K");
    expect(toAccelerator("Ctrl+Shift+F5")).toBe("Control+Shift+F5");
    expect(toAccelerator("win+space")).toBe("Super+Space");
    expect(toAccelerator(" control + ctrl + esc ")).toBe("Control+Escape");
    for (const bad of ["k", "ctrl+", "ctrl+alt+nope", "hyper+k", "", "ctrl+f99"]) expect(() => toAccelerator(bad)).toThrow(/hotkey|modifier|key/);
  });
});
