/** Maps what the agent is doing to the avatar's animation state. Pure logic with an injectable clock (no timers).
 * Priority (highest first): awaiting_approval > listening > speaking > error > success > working/thinking > sleeping > idle. */
import type { Ev } from "./session";

export const STATES = ["idle", "listening", "thinking", "working", "speaking", "awaiting_approval", "success", "error", "sleeping",
  "walking", "headpat"] as const; // the last two are shown by the avatar itself, never by AvatarState
export type AvatarStateName = (typeof STATES)[number];
const FLASH_S = { success: 2.5, error: 4.0 }; // how long a one-shot result stays on screen
const WORKING = new Set(["tool_call", "tool_result", "worker"]);
const THINKING = new Set(["intent", "plan", "verdict"]);
const QUIET_ERRORS = new Set(["approval_pending"]); // a normal condition, not a failure animation

export class AvatarState {
  connected = true;
  busy = false;
  listening = false;
  speaking = false;
  pendingApprovals = 0;
  private working = false;
  private flash: [AvatarStateName, number] | null = null;
  private lastActivity: number;

  constructor(public sleepAfterS = 300, private clock: () => number = () => performance.now() / 1000) {
    this.lastActivity = clock();
  }

  touch(): void {
    this.lastActivity = this.clock();
  }

  userSent(): void {
    this.busy = true;
    this.working = false;
    this.flash = null;
    this.touch();
  }

  onEvent(ev: Ev): AvatarStateName {
    const t = ev.type ?? "";
    const now = this.clock();
    if (WORKING.has(t)) this.busy = this.working = true;
    else if (THINKING.has(t)) {
      this.busy = true;
      if (t !== "verdict") this.working = false;
    } else if (t === "approvals") this.pendingApprovals = (ev.items ?? []).length;
    else if (t === "approval_request") this.pendingApprovals += (ev.approvals ?? []).length;
    else if (t === "paused") this.busy = false;
    else if (t === "final") {
      this.busy = this.working = false;
      this.flash = [ev.stopped ? "error" : "success", now + FLASH_S.success];
    } else if (t === "error" && !QUIET_ERRORS.has(ev.code)) {
      this.busy = this.working = false;
      this.flash = ["error", now + FLASH_S.error];
    } else if (t === "killed") {
      this.busy = this.working = false;
      this.pendingApprovals = 0;
      this.flash = ["error", now + FLASH_S.error];
    }
    this.touch();
    return this.current();
  }

  current(): AvatarStateName {
    const now = this.clock();
    if (this.pendingApprovals > 0) return "awaiting_approval";
    if (this.listening) return "listening";
    if (this.speaking) return "speaking";
    if (this.flash && now < this.flash[1]) return this.flash[0];
    if (this.busy) return this.working ? "working" : "thinking";
    if (!this.connected) return "sleeping";
    return now - this.lastActivity >= this.sleepAfterS ? "sleeping" : "idle";
  }

  /** Seconds until current() may change by itself (flash expiry / falling asleep); null = nothing scheduled. */
  nextChangeIn(): number | null {
    const now = this.clock();
    if (this.flash && now < this.flash[1]) return this.flash[1] - now;
    if (!this.busy && this.connected && this.current() === "idle") return Math.max(0, this.sleepAfterS - (now - this.lastActivity));
    return null;
  }
}
