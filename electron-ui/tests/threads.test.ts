// Conversations in the UI model: saved history, paging, routing of events to the right thread, the compaction marker.
import { describe, expect, it } from "vitest";
import { GLOBAL_EVENTS, Session } from "../src/shared/session";

const rows = (from: number, to: number, kind = "user"): any[] =>
  Array.from({ length: to - from + 1 }, (_, i) => ({ id: from + i, ts: 0, kind, text: `m${from + i}`, meta: {} }));

describe("saved history", () => {
  it("fills an empty conversation and puts it before anything said since", () => {
    const s = new Session("c-a");
    s.apply({ type: "_user", thread_id: "c-a", text: "live" });
    s.apply({ type: "thread_history", thread_id: "c-a", entries: rows(1, 3), more: true });
    expect(s.transcript.map((e) => e.text)).toEqual(["m1", "m2", "m3", "live"]);
    expect(s.historyLoaded).toBe(true);
    expect(s.hasMore).toBe(true);
    expect(s.firstId).toBe(1);
  });

  it("does not load twice (a reconnect asks again)", () => {
    const s = new Session("c-a");
    s.apply({ type: "thread_history", thread_id: "c-a", entries: rows(1, 2), more: false });
    s.apply({ type: "thread_history", thread_id: "c-a", entries: rows(1, 2), more: false });
    expect(s.transcript).toHaveLength(2);
  });

  it("prepends older pages and never repeats a message", () => {
    const s = new Session("c-a");
    s.apply({ type: "thread_history", thread_id: "c-a", entries: rows(6, 8), more: true });
    s.apply({ type: "thread_history", thread_id: "c-a", before: 6, entries: rows(3, 7), more: true }); // 6 and 7 overlap
    expect(s.transcript.map((e) => e.id)).toEqual([3, 4, 5, 6, 7, 8]);
    expect(s.firstId).toBe(3);
    s.apply({ type: "thread_history", thread_id: "c-a", before: 3, entries: rows(1, 2), more: false });
    expect(s.transcript.map((e) => e.id)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(s.hasMore).toBe(false);
  });

  it("survives a snapshot round trip (a window opened later)", () => {
    const s = new Session("c-a");
    s.apply({ type: "thread_history", thread_id: "c-a", entries: rows(1, 2), more: true });
    const copy = Session.from(s.snapshot());
    expect([copy.historyLoaded, copy.hasMore, copy.firstId, copy.transcript.length]).toEqual([true, true, 1, 2]);
  });
});

describe("events and threads", () => {
  it("a conversation ignores another conversation's events", () => {
    const a = new Session("c-a"), b = new Session("c-b");
    for (const s of [a, b]) {
      s.apply({ type: "final", thread_id: "c-a", text: "for a" });
      s.apply({ type: "plan", thread_id: "c-b", objective: "for b", success_criteria: [], steps: [] });
    }
    expect(a.transcript.map((e) => e.text)).toEqual(["for a"]);
    expect(a.plan).toBeNull();
    expect(b.transcript.map((e) => e.text)).toEqual(["plan: for b"]);
  });

  it("an event without a thread belongs to the conversation on screen", () => {
    const s = new Session("c-a");
    s.apply({ type: "error", code: "offline", message: "not reachable" });
    expect(s.transcript[0]?.kind).toBe("error");
  });

  it("approvals, the budget and the kill switch reach every conversation", () => {
    const sessions = [new Session("c-a"), new Session("c-b")];
    const evs = [{ type: "approval_request", thread_id: "c-a", approvals: [{ call_id: "x", tool: "file_write", args: {} }] },
      { type: "usage", today: { tokens: 5 } }, { type: "killed", threads: 1 }];
    for (const ev of evs) for (const s of sessions) if (ev.type === "killed" || GLOBAL_EVENTS.has(ev.type)) s.apply(ev);
    expect(sessions.map((s) => Object.keys(s.approvals))).toEqual([["x"], ["x"]]);
    expect(sessions.every((s) => s.killed && s.usage.today?.tokens === 5)).toBe(true);
    expect(sessions[1]!.pendingForThread()).toEqual({ "c-a": [expect.objectContaining({ call_id: "x" })] }); // blocks only its own thread
  });

  it("marks where the agent's context was summarised", () => {
    const s = new Session("c-a");
    s.apply({ type: "compacted", thread_id: "c-a" });
    s.apply({ type: "compacted", thread_id: "c-other" });
    expect(s.transcript.map((e) => e.kind)).toEqual(["note"]);
  });

  it("streams an answer into the right thread only", () => {
    const a = new Session("c-a");
    a.apply({ type: "token", thread_id: "c-b", text: "x" });
    a.apply({ type: "token", thread_id: "c-a", text: "hel" });
    a.apply({ type: "token", thread_id: "c-a", text: "lo" });
    expect(a.transcript.map((e) => e.text)).toEqual(["hello"]);
  });
});
