/** UI-side view of one conversation, built from the daemon's event stream. Pure: the main process and every window run the same reducer. */

export type Ev = { type?: string; [k: string]: any };
export type Entry = { kind: "user" | "agent" | "event" | "error" | "note"; text: string; meta: Record<string, any>; id?: number };
export type Step = { text: string; role: string; status: "todo" | "done" | "failed" | "blocked" };
export type Approval = { call_id: string; tool: string; args: Record<string, any>; tainted: boolean; [k: string]: any };
export type Change = "transcript" | "plan" | "approvals" | "busy" | "usage" | "name" | "killed";

const STEP = /^s(\d+)$/;
/** Events that belong to one conversation: a session ignores them when they name another thread. */
const THREAD_SCOPED = new Set(["intent", "plan", "worker", "tool_call", "tool_result", "verdict", "token", "final", "error", "paused", "compacted", "thread_history", "_user"]);
/** Events that concern every conversation (a pending approval, the budget, the kill switch): the main process hands them to all sessions. */
export const GLOBAL_EVENTS = new Set(["approvals", "approval_request", "_decided", "usage", "agent_name", "killed", "kill_released"]);

/** Approval records arrive with args as a JSON string (DB row) or an object (live request): always return an object. */
export function normalizeApproval(a: Record<string, any>): Approval {
  let args = a.args;
  if (typeof args === "string") {
    try {
      args = JSON.parse(args);
    } catch {
      args = { raw: args };
    }
  }
  const obj = args !== null && typeof args === "object" && !Array.isArray(args);
  return { ...a, args: obj ? args : {}, tainted: Boolean(a.tainted) } as Approval;
}

export class Session {
  transcript: Entry[] = [];
  plan: { objective: string; criteria: string[] } | null = null;
  steps: Step[] = [];
  verdict = "";
  intent: Record<string, any> | null = null;
  approvals: Record<string, Approval> = {}; // call_id -> approval (pending only)
  busy = false;
  used: Record<string, any> = {};
  usage: Record<string, any> = {};
  killed = false;
  historyLoaded = false; // the saved messages of this conversation were fetched from the daemon
  hasMore = false; // older saved messages exist
  firstId: number | null = null; // id of the oldest saved message loaded so far
  private live: Entry | null = null; // the answer being streamed token by token

  constructor(public threadId = "default", public agentName = "personal-agent") {}

  /** A copy of everything a window needs to start from (sent once; events keep it current afterwards). */
  snapshot(): Record<string, any> {
    const { transcript, plan, steps, verdict, intent, approvals, busy, used, usage, killed, threadId, agentName, historyLoaded, hasMore, firstId } = this;
    return structuredClone({ transcript, plan, steps, verdict, intent, approvals, busy, used, usage, killed, threadId, agentName, historyLoaded, hasMore, firstId,
      live: this.live ? transcript.indexOf(this.live) : -1 });
  }

  static from(snap: Record<string, any>): Session {
    const s = new Session(snap.threadId, snap.agentName);
    const { live, ...rest } = structuredClone(snap);
    Object.assign(s, rest);
    s.live = live >= 0 ? (s.transcript[live] ?? null) : null;
    return s;
  }

  private dropLive(): void {
    if (this.live) this.transcript = this.transcript.filter((e) => e !== this.live);
    this.live = null;
  }

  private say(kind: Entry["kind"], text: string, meta: Record<string, any> = {}): void {
    this.transcript.push({ kind, text, meta });
  }

  pendingForThread(): Record<string, Approval[]> {
    const by: Record<string, Approval[]> = {};
    for (const a of Object.values(this.approvals)) (by[a.thread_id ?? this.threadId] ??= []).push(a);
    return by;
  }

  /** Update the model from one event; returns which parts changed. Types starting with "_" are local actions the main process mirrors. */
  apply(ev: Ev): Set<Change> {
    const ch = new Set<Change>();
    const add = (...c: Change[]): void => c.forEach((x) => ch.add(x));
    if (THREAD_SCOPED.has(ev.type ?? "") && ev.thread_id && ev.thread_id !== this.threadId) return ch;
    switch (ev.type) {
      case "thread_history": { // the saved messages of this conversation (the newest page first, then older ones on request)
        const rows: Entry[] = (ev.entries ?? []).map((e: any) => ({ kind: e.kind, text: String(e.text ?? ""), meta: e.meta ?? {}, id: e.id }));
        if (ev.before == null) {
          if (this.historyLoaded) break; // a reconnect must not duplicate what is already on screen
          this.transcript = [...rows, ...this.transcript];
          this.historyLoaded = true;
        } else this.transcript = [...rows.filter((r) => this.firstId === null || (r.id ?? 0) < this.firstId), ...this.transcript];
        this.hasMore = Boolean(ev.more);
        this.firstId = this.transcript.find((e) => e.id !== undefined)?.id ?? this.firstId;
        add("transcript");
        break;
      }
      case "compacted":
        this.say("note", "");
        add("transcript");
        break;
      case "_user": // the user sent a message
        this.say("user", String(ev.text ?? ""), { attachments: ev.attachments ?? [] });
        Object.assign(this, { busy: true, plan: null, steps: [], verdict: "", intent: null, live: null });
        add("transcript", "plan", "busy");
        break;
      case "_decided": // the user answered these approval requests
        for (const id of ev.call_ids ?? []) delete this.approvals[id];
        this.busy = true;
        add("approvals", "busy");
        break;
      case "intent":
        this.intent = Object.fromEntries(["intent", "complexity", "needs_plan", "risk_level", "confidence"].map((k) => [k, ev[k]]));
        this.say("event", `intent: ${ev.intent} (${Math.round(Number(ev.confidence ?? 0) * 100)}%)`);
        add("transcript");
        break;
      case "plan": {
        const roles: string[] = ev.roles ?? [];
        this.plan = { objective: ev.objective ?? "", criteria: [...(ev.success_criteria ?? [])] };
        this.steps = (ev.steps ?? []).map((text: string, i: number) => ({ text, role: roles[i] ?? "main", status: "todo" }));
        this.say("event", `plan: ${ev.objective ?? ""}`);
        add("transcript", "plan");
        break;
      }
      case "worker": {
        const m = STEP.exec(String(ev.step ?? ""));
        const step = m ? this.steps[Number(m[1]) - 1] : undefined;
        if (step) step.status = ev.status === "done" || ev.status === "ok" ? "done" : "failed";
        this.say("event", `[${ev.role}] ${ev.status}: ${ev.summary ?? ""}`);
        add("transcript", "plan");
        break;
      }
      case "tool_call":
        this.dropLive(); // prose streamed before a tool call is not the answer
        this.say("event", `tool: ${ev.name} ${JSON.stringify(ev.args ?? {}).slice(0, 160)}`);
        add("transcript");
        break;
      case "tool_result":
        this.say("event", `  -> ${ev.status}: ${String(ev.content ?? "").slice(0, 200)}`);
        add("transcript");
        break;
      case "verdict":
        this.verdict = String(ev.status ?? "");
        if (this.verdict === "done") for (const s of this.steps) if (s.status === "todo") s.status = "done";
        this.say("event", `verify: ${this.verdict} ${ev.notes ?? ""}`.trim());
        add("transcript", "plan");
        break;
      case "approvals":
        this.approvals = Object.fromEntries((ev.items ?? []).map((a: any) => [a.call_id, normalizeApproval(a)]));
        add("approvals");
        break;
      case "approval_request":
        for (const a of ev.approvals ?? []) this.approvals[a.call_id] = normalizeApproval({ ...a, thread_id: a.thread_id ?? ev.thread_id });
        add("approvals");
        break;
      case "paused":
        this.dropLive();
        this.busy = false;
        add("busy", "approvals");
        break;
      case "token":
        if (!this.live) {
          this.live = { kind: "agent", text: "", meta: {} };
          this.transcript.push(this.live);
        }
        this.live.text += String(ev.text ?? "");
        add("transcript");
        break;
      case "final":
        this.dropLive();
        this.busy = false;
        this.used = { ...(ev.used ?? {}) };
        this.say("agent", String(ev.text ?? "") + (ev.stopped ? ` (stopped: ${ev.stopped})` : ""), { used: this.used });
        add("transcript", "busy", "usage");
        break;
      case "error":
        this.dropLive();
        this.busy = false;
        this.say("error", `${ev.code}: ${ev.message}`);
        add("transcript", "busy");
        break;
      case "usage":
        this.usage = Object.fromEntries(["today", "caps", "report", "langfuse"].map((k) => [k, ev[k]]));
        add("usage");
        break;
      case "agent_name":
        this.agentName = String(ev.name ?? this.agentName);
        add("name");
        break;
      case "killed":
        this.dropLive();
        this.busy = false;
        this.killed = true;
        this.say("error", `kill switch engaged (${ev.threads ?? 0} task(s) stopped)`);
        add("transcript", "busy", "killed");
        break;
      case "kill_released":
        this.killed = false;
        add("killed");
        break;
    }
    return ch;
  }
}
