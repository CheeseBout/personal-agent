/** The desktop companion: the avatar (transparent, draggable, always on top), its speech bubble, quick-input box and radial menu, and
 * the screen overlays (target highlight, "watching" badge, region selector). Each is its own small window, created on first use. */
import { existsSync, readFileSync, statSync, watch, type FSWatcher } from "node:fs";
import path from "node:path";
import { app, type BrowserWindow, type BrowserWindowConstructorOptions, screen } from "electron";
import type { Attachment } from "../shared/attach";
import { bubblePlace, defaultPlace, easeInOutQuad, intersects, type Manifest, parseManifest, quickPlace, type RadialItem, type Rect, ringPlace,
  snapToEdges } from "../shared/avatar";
import type { AvatarState } from "../shared/avatarState";
import { tr } from "../shared/i18n";
import type { Ev, Session } from "../shared/session";
import { STATE } from "../shared/theme";
import { createWindow, FLOATING, type Role } from "./windows";

export type Hub = { session: Session; state: AvatarState; settings: () => Ev | null; about: () => Ev | null; send: (msg: Ev) => boolean;
  showMain: (key?: string) => void; showApprovals: () => void; kill: () => void; quit: () => void;
  sendUser: (text: string, files: Attachment[]) => void; attach: (a: Attachment) => void; insertText: (text: string) => void; changed: () => void };

const MAX_SKIN_FILE = 5_000_000;
const MIME: Record<string, string> = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp" };

/** A window that is created the first time something is shown in it. The page says when it has laid the content out; only then is it placed and shown. */
class Overlay {
  win: BrowserWindow | null = null;
  private ready = false;
  private last: Ev | null = null;
  private n = 0;
  private laidOut: (size: { width: number; height: number }) => void = () => undefined;
  private waiting: Ev[] = []; // commands posted before the page was ready (only kept when `commands` is on)

  /** `commands`: every post is a command that must arrive exactly once and in order (voice), not a picture where only the latest counts. */
  constructor(private role: Role, private opts: BrowserWindowConstructorOptions, private setup: (w: BrowserWindow) => void = () => undefined,
              private commands = false) {}

  post(ev: Ev, laidOut: (size: { width: number; height: number }) => void = () => undefined): void {
    this.last = { ...ev, _n: ++this.n };
    this.laidOut = laidOut;
    if (this.commands && !this.ready) this.waiting.push(this.last);
    if (!this.win || this.win.isDestroyed()) {
      this.ready = false;
      this.win = createWindow(this.role, { ...FLOATING, ...this.opts });
      this.win.setAlwaysOnTop(true, "screen-saver"); // above full-screen-ish windows and the taskbar
      this.setup(this.win);
    } else if (this.ready) this.win.webContents.send("agent:event", this.last);
  }

  /** Messages from the page: "ready" (send me the content) and "laidOut" (n, size). Returns true when it was one of those. */
  handle(name: string, args: unknown[]): boolean {
    if (name === "ready") {
      const first = !this.ready;
      this.ready = true;
      if (this.commands) {
        if (first) for (const ev of this.waiting.splice(0)) this.win?.webContents.send("agent:event", ev);
      } else if (this.last) this.win?.webContents.send("agent:event", this.last);
    } else if (name === "laidOut") {
      if (args[0] === this.n && this.visibleWanted) this.laidOut((args[1] ?? { width: 0, height: 0 }) as { width: number; height: number });
    } else return false;
    return true;
  }

  visibleWanted = true; // false after hide(): a late "laidOut" must not bring the window back

  hide(): void {
    this.visibleWanted = false;
    if (this.win && !this.win.isDestroyed()) this.win.hide();
  }

  get visible(): boolean {
    return Boolean(this.win && !this.win.isDestroyed() && this.win.isVisible());
  }

  bounds(): Rect {
    return this.win!.getBounds();
  }
}

export class Companion {
  screenState = "off";
  private avatar = new Overlay("avatar", { width: 128, height: 128, focusable: false, title: "" });
  private bubble = new Overlay("bubble", { width: 280, height: 60, focusable: false });
  private quick = new Overlay("quick", { width: 320, height: 96 });
  private radial = new Overlay("radial", { width: 300, height: 300 }, (w) => w.on("blur", () => this.closeRadial()));
  private highlight = new Overlay("highlight", { width: 100, height: 100, focusable: false }, (w) => w.setIgnoreMouseEvents(true));
  private badge = new Overlay("badge", { width: 240, height: 28, focusable: false });
  private region = new Overlay("region", { width: 400, height: 300 });
  private voice = new Overlay("voice", { width: 200, height: 100, focusable: false }, undefined, true); // never shown
  recording = false;
  private voiceMode: "chat" | "send" = "chat"; // where a transcript goes: into the chat box, or straight to the agent
  private ttsId = 0;
  private timers: Record<string, NodeJS.Timeout | undefined> = {};
  private cfg = { enabled: false, scale: 1, skin: "default", onTop: true, wander: false };
  private skinRev = 0;
  private skinWatch: FSWatcher | null = null;
  private placed = false;
  private pose: string | null = null; // "walking" while the avatar wanders
  private right = false; // skin art faces left; mirrored while moving right
  private drag: { dx: number; dy: number } | null = null;
  private wanderBefore = false;
  private actions: Record<string, () => void> = {};

  constructor(private hub: Hub) {
    this.avatar.visibleWanted = false; // until the settings say the avatar is enabled
    void app.whenReady().then(() => { // the screen module does not exist before that
      const back = (): void => this.keepOnScreen();
      screen.on("display-removed", back);
      screen.on("display-metrics-changed", back);
    });
  }

  // ---- timers ----------------------------------------------------------------------------------------------------------------
  private after(name: string, ms: number, fn: () => void): void {
    this.cancel(name);
    this.timers[name] = setTimeout(fn, ms);
  }

  private cancel(name: string): void {
    clearTimeout(this.timers[name]);
    clearInterval(this.timers[name]);
    this.timers[name] = undefined;
  }

  // ---- avatar ----------------------------------------------------------------------------------------------------------------
  get avatarVisible(): boolean {
    return this.avatar.visible;
  }

  private area(r?: Rect): Rect {
    return (r ? screen.getDisplayMatching(r) : screen.getPrimaryDisplay()).workArea;
  }

  /** Apply the daemon's UI settings (avatar on/off, size, skin, always on top, wandering). */
  applySettings(ui: Ev): void {
    const before = this.cfg;
    this.cfg = { enabled: Boolean(ui.avatar_enabled), scale: Math.max(0.5, Math.min(Number(ui.avatar_scale) || 1, 3)), skin: String(ui.skin || "default"),
      onTop: Boolean(ui.avatar_always_on_top), wander: Boolean(ui.avatar_wander) };
    if (before.skin !== this.cfg.skin) this.watchSkin();
    if (!this.cfg.enabled) return this.hideAvatar();
    this.avatar.visibleWanted = true;
    this.postAvatar();
    this.avatar.win?.setAlwaysOnTop(this.cfg.onTop, "screen-saver");
    this.scheduleWander();
  }

  toggleAvatar(): void {
    if (this.avatar.visible) this.hideAvatar();
    else {
      this.avatar.visibleWanted = true;
      this.postAvatar();
    }
  }

  private hideAvatar(): void {
    this.stopWalk();
    this.cancel("wander");
    this.avatar.hide();
    this.bubble.hide();
  }

  /** Tell the avatar page what to draw; it answers with its size and click-through shape (see `shape`). */
  postAvatar(): void {
    if (!this.avatar.visibleWanted) return;
    this.avatar.post({ type: "_avatar", state: this.hub.state.current(), watching: this.screenState === "on", scale: this.cfg.scale, skin: this.cfg.skin,
      skinRev: this.skinRev, pose: this.pose, right: this.right, label: this.hub.session.agentName });
    this.avatar.win?.setTitle(this.hub.session.agentName);
  }

  /** The agent's state changed (or may change by itself later: one timer, no polling). */
  refreshState(): void {
    const s = this.hub.state;
    if (s.current() !== "idle") this.stopWalk();
    this.postAvatar();
    this.scheduleWander();
    const wait = s.nextChangeIn();
    if (wait !== null) this.after("state", wait * 1000 + 50, () => this.refreshState());
  }

  // The page drew a frame: resize to it and let clicks through wherever the picture is transparent.
  private shape(width: number, height: number, rects: Rect[]): void {
    const w = this.avatar.win;
    if (!w || w.isDestroyed() || !this.avatar.visibleWanted) return;
    const b = w.getBounds();
    if (!this.placed) {
      this.placed = true;
      w.setBounds({ ...defaultPlace({ width, height }, this.area()), width, height });
    } else if (b.width !== width || b.height !== height) w.setBounds({ x: b.x, y: b.y, width, height });
    w.setShape(rects);
    if (!w.isVisible()) {
      w.showInactive();
      this.keepOnScreen();
      this.scheduleWander();
    }
  }

  /** Pull the avatar back into a visible screen (monitor unplugged, resolution changed). */
  private keepOnScreen(): void {
    if (!this.avatar.visible) return;
    const b = this.avatar.bounds();
    if (!screen.getAllDisplays().some((d) => intersects(d.workArea, b))) this.avatar.win!.setBounds({ ...defaultPlace(b, this.area()), width: b.width, height: b.height });
  }

  private moveAvatar(x: number, y: number): void {
    const w = this.avatar.win!, b = w.getBounds();
    w.setBounds({ x: Math.round(x), y: Math.round(y), width: b.width, height: b.height }); // size restated: moving across monitors must not rescale it
  }

  // ---- optional idle wandering ---------------------------------------------------------------------------------------------------
  private scheduleWander(): void {
    this.cancel("wander");
    if (this.cfg.wander && this.avatar.visible && this.hub.state.current() === "idle" && !this.pose) {
      this.after("wander", 20_000 + Math.random() * 40_000, () => this.wanderStep());
    }
  }

  private wanderStep(): void {
    if (!this.cfg.wander || this.hub.state.current() !== "idle" || this.drag || !this.avatar.visible) return;
    const b = this.avatar.bounds(), a = this.area(b);
    const target = a.x + Math.random() * Math.max(0, a.width - b.width), from = b.x;
    const ms = Math.max(800, Math.abs(target - from) * 8), t0 = Date.now();
    this.pose = "walking";
    this.right = target > from;
    this.postAvatar();
    this.timers.walk = setInterval(() => {
      const t = Math.min(1, (Date.now() - t0) / ms);
      if (this.avatar.visible) this.moveAvatar(from + (target - from) * easeInOutQuad(t), b.y);
      if (t >= 1) this.stopWalk();
    }, 16);
  }

  private stopWalk(): void {
    this.cancel("walk");
    if (this.pose !== "walking") return;
    this.pose = null;
    this.postAvatar();
    this.scheduleWander();
  }

  // ---- skins -----------------------------------------------------------------------------------------------------------------
  private skinFolder(name: string): string | null {
    const a = this.hub.about();
    for (const root of [a?.data_dir && path.join(String(a.data_dir), "skins"), a?.config_dir && path.join(String(a.config_dir), "avatar")]) {
      if (root && existsSync(path.join(root, name, "manifest.json"))) return path.join(root, name);
    }
    return null;
  }

  /** A skin with its picture files inlined, for the avatar page. Problems are reported, never thrown: that state falls back. */
  private loadSkin(name: string): Record<string, unknown> {
    const builtin = { name: "default", size: 128, fallback: "idle", states: {}, problems: [] as string[] };
    const folder = name === "default" ? null : this.skinFolder(name);
    if (!folder) return name === "default" ? builtin : { ...builtin, problems: [`skin '${name}' not found`] };
    let m: Manifest;
    try {
      m = parseManifest(JSON.parse(readFileSync(path.join(folder, "manifest.json"), "utf8")), path.basename(folder));
    } catch (e) {
      return { ...builtin, problems: [`skin '${name}' failed to load: ${(e as Error).message}`] };
    }
    const root = path.resolve(folder) + path.sep;
    const file = (rel: string): string => {
      const p = path.resolve(folder, rel);
      if (!p.startsWith(root)) throw new Error(`'${rel}' is outside the skin folder`); // a manifest must not be able to read arbitrary paths
      const mime = MIME[path.extname(p).toLowerCase()];
      if (!mime) throw new Error(`'${rel}' is not a picture`);
      if (statSync(p).size > MAX_SKIN_FILE) throw new Error(`'${rel}' is larger than 5 MB`);
      return `data:${mime};base64,${readFileSync(p).toString("base64")}`;
    };
    const states: Record<string, unknown> = {};
    for (const [state, spec] of Object.entries(m.states)) {
      try {
        if ("frames" in spec) states[state] = { ...spec, frames: spec.frames.map(file) };
        else if ("gif" in spec) states[state] = { ...spec, gif: file(spec.gif) };
        else states[state] = spec;
      } catch (e) {
        m.problems.push(`${state}: ${(e as NodeJS.ErrnoException).code === "ENOENT" ? "cannot read " + ("frames" in spec ? spec.frames.join(", ") : "its file") : (e as Error).message}`);
      }
    }
    if (!Object.keys(states).length) m.problems.push("no usable animation; using the built-in look");
    return { ...m, states };
  }

  /** Reload the skin when its folder changes (a skin author sees edits at once). */
  private watchSkin(): void {
    this.skinWatch?.close();
    this.skinWatch = null;
    const folder = this.cfg.skin === "default" ? null : this.skinFolder(this.cfg.skin);
    if (!folder) return;
    try {
      this.skinWatch = watch(folder, () => this.after("skin", 300, () => {
        this.skinRev += 1;
        this.postAvatar();
      }));
    } catch {
      /* no live reload for this folder */
    }
  }

  // ---- speech bubble -----------------------------------------------------------------------------------------------------------
  /** Short status text next to the avatar. `sticky` bubbles (approval prompts) stay until replaced or clicked. */
  say(text: string, ms = 4000, sticky = false): void {
    if (!text || !this.avatar.visible) return;
    this.cancel("bubble");
    this.bubble.visibleWanted = true;
    this.bubble.post({ type: "_bubble", text: text.slice(0, 240) }, (size) => {
      if (!this.avatar.visible) return;
      const near = this.avatar.bounds();
      this.bubble.win!.setBounds({ ...bubblePlace(size, near, this.area(near)), ...size });
      this.bubble.win!.showInactive();
      if (!sticky) this.after("bubble", ms, () => this.bubble.hide());
    });
  }

  hideBubble(): void {
    this.cancel("bubble");
    this.bubble.hide();
  }

  // ---- quick input -------------------------------------------------------------------------------------------------------------
  openQuick(files: Attachment[] = []): void {
    if (!this.avatar.visible) return this.hub.showMain("chat");
    this.quick.visibleWanted = true;
    this.quick.post({ type: "_quick", files }, (size) => {
      const near = this.avatar.bounds();
      this.quick.win!.setBounds({ ...quickPlace(size, near, this.area(near)), ...size });
      this.quick.win!.show();
      this.quick.win!.focus();
    });
  }

  // ---- radial menu -------------------------------------------------------------------------------------------------------------
  private radialItems(): RadialItem[] {
    const h = this.hub, watching = this.screenState !== "off";
    this.actions = {};
    const item = (en: string, vi: string, glyph: string, run: () => void, extra: Partial<RadialItem> = {}): RadialItem => {
      const id = `a${Object.keys(this.actions).length}`;
      this.actions[id] = run;
      return { id, label: tr(en, vi), glyph, ...extra };
    };
    const pages: [string, string, string, string][] = [["memory", "Memory", "Bộ nhớ", "M"], ["rag", "Documents", "Tài liệu", "D"], ["mcp", "Tools / MCP", "Công cụ", "T"],
      ["schedules", "Schedules", "Lịch chạy", "S"], ["rules", "Remembered", "Đã nhớ", "R"], ["audit", "Audit log", "Nhật ký", "A"],
      ["usage", "Usage", "Mức dùng", "U"], ["policy", "Policy", "Chính sách", "P"]];
    return [
      item("Chat", "Chat", "C", () => h.showMain("chat")),
      item("Talk", "Nói", "T", () => this.toggleTalk(), { active: this.recording }),
      { label: tr("Capture", "Chụp"), glyph: "C", children: [
        item("Whole screen", "Toàn màn hình", "S", () => this.capture("screen")),
        item("Window (2 s)", "Cửa sổ (2 giây)", "W", () => this.captureWindowSoon()),
        item("Select area", "Chọn vùng", "A", () => this.captureRegion())] },
      item(watching ? "Stop watching" : "Watch screen", watching ? "Dừng theo dõi" : "Theo dõi màn hình", "W", () => this.toggleWatch(), { active: watching }),
      item("Approvals", "Phê duyệt", "A", () => h.showApprovals(), { badge: Object.keys(h.session.approvals).length }),
      { label: tr("Manage", "Quản lý"), glyph: "M", children: pages.map(([k, en, vi, g]) => item(en, vi, g, () => h.showMain(k))) },
      { label: tr("More", "Thêm"), glyph: "⋯", children: [
        item("Settings", "Cài đặt", "S", () => h.showMain("settings")),
        item("Stop speaking", "Ngừng đọc", "S", () => this.stopSpeaking()),
        item("Resume agent", "Cho chạy lại", "R", () => void h.send({ type: "release_kill" })),
        item("Hide avatar", "Ẩn avatar", "H", () => this.toggleAvatar()),
        item("Quit the UI", "Thoát giao diện", "Q", () => h.quit())] },
      item("Pause agent", "Dừng agent", "!", () => h.kill(), { danger: true }), // the kill switch: needs a second click
    ];
  }

  private openRadial(): void {
    if (!this.avatar.visible) return;
    this.stopWalk(); // the ring is centred on the avatar: keep it still while the menu is open
    this.wanderBefore = this.cfg.wander;
    this.cfg.wander = false;
    this.cancel("wander");
    this.hideBubble();
    const near = this.avatar.bounds(), p = ringPlace(near, this.area(near));
    this.radial.visibleWanted = true;
    this.radial.post({ type: "_radial", items: this.radialItems(), rIn: p.rIn, side: p.side }, () => {
      this.radial.win!.setBounds({ x: Math.round(p.x), y: Math.round(p.y), width: p.side, height: p.side });
      this.radial.win!.show();
      this.radial.win!.focus();
    });
  }

  private closeRadial(): void {
    if (!this.radial.visibleWanted) return;
    this.radial.hide();
    this.cfg.wander = this.wanderBefore;
    this.scheduleWander();
  }

  // ---- screen: capture, highlight, watching --------------------------------------------------------------------------------------
  /** Ask the daemon for a (privacy-masked) screenshot; it comes back as a chat attachment and you decide whether to send it. */
  capture(kind: string, rect?: [number, number, number, number]): void {
    if (!this.hub.send({ type: "capture", kind, rect })) this.say(tr("Not connected.", "Chưa kết nối."), 3000);
  }

  captureWindowSoon(): void {
    this.say(tr("Click the window you want in 2 s…", "Bấm vào cửa sổ cần chụp trong 2 giây…"), 2000);
    this.after("captureSoon", 2200, () => this.capture("active_window"));
  }

  /** Dim the screen the pointer is on and let the user drag a rectangle. */
  captureRegion(): void {
    const d = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
    this.region.visibleWanted = true;
    this.region.post({ type: "_region" }, () => {
      const w = this.region.win!;
      w.setBounds(d.bounds);
      w.show();
      w.focus();
    });
  }

  private regionDone(r: Rect | null): void {
    const w = this.region.win;
    if (!w || !this.region.visibleWanted) return;
    const origin = w.getBounds();
    const phys = r && r.width >= 8 && r.height >= 8 ? screen.dipToScreenRect(w, { x: origin.x + r.x, y: origin.y + r.y, width: r.width, height: r.height }) : null;
    this.region.hide();
    if (!phys) return this.say(tr("Capture cancelled.", "Đã hủy chụp."), 2000);
    // The dim layer must be off the screen before the daemon grabs it; hiding a window takes the compositor a frame or two.
    this.after("regionShot", 150, () => this.capture("region", [phys.x, phys.y, phys.x + phys.width, phys.y + phys.height]));
  }

  toggleWatch(): void {
    this.hub.send({ type: "screen_mode", action: this.screenState === "off" ? "on" : "off" });
  }

  private onScreenMode(ev: Ev): void {
    this.screenState = String(ev.state ?? "off");
    if (this.screenState === "off") this.badge.hide();
    else {
      const note = ev.private ? tr(" · private window skipped", " · bỏ qua cửa sổ riêng tư") : "";
      const text = (this.screenState === "on"
        ? tr("Watching the screen ({m} min left)", "Đang theo dõi màn hình (còn {m} phút)", { m: Math.round(Number(ev.minutes_left ?? 0)) })
        : tr("Screen watching paused", "Tạm dừng theo dõi màn hình")) + note + "  ✕";
      this.badge.visibleWanted = true;
      this.badge.post({ type: "_badge", text, color: this.screenState === "on" ? STATE.error : STATE.working,
        tip: tr("Click to stop watching. Nothing is saved to disk.", "Bấm để dừng theo dõi. Không lưu gì vào đĩa.") }, (size) => {
        const a = screen.getPrimaryDisplay().workArea;
        this.badge.win!.setBounds({ x: Math.round(a.x + a.width / 2 - size.width / 2), y: a.y + 6, ...size });
        this.badge.win!.showInactive();
      });
    }
    this.postAvatar();
    this.hub.changed();
    if (ev.reason === "timeout") this.say(tr("Screen watching switched itself off.", "Đã tự tắt theo dõi màn hình."), 5000);
    else if (this.screenState === "on" && !ev.private) {
      this.say(tr("Watching the screen (nothing is saved). Click the red badge to stop.", "Đang theo dõi màn hình (không lưu gì). Bấm huy hiệu đỏ để dừng."), 4500);
    }
  }

  /** Outline the element the agent is about to touch. `rect` is in physical pixels (what the daemon uses). */
  private showHighlight(rect: number[], label: string, ms: number): void {
    const [l, t, r, b] = rect as [number, number, number, number];
    const dip = screen.screenToDipRect(null, { x: l, y: t, width: r - l, height: b - t });
    this.highlight.visibleWanted = true;
    this.highlight.post({ type: "_highlight", label: label.slice(0, 90) }, () => {
      this.highlight.win!.setBounds({ x: dip.x - 6, y: dip.y - 28, width: dip.width + 12, height: dip.height + 34 }); // 22 px above the box for the label
      this.highlight.win!.showInactive();
      this.highlight.win!.moveTop();
      this.after("highlight", Math.max(200, ms + 250), () => this.highlight.hide());
    });
  }

  // ---- voice -------------------------------------------------------------------------------------------------------------------
  /** Hotkey / menu: the first press starts listening, the second (or silence) stops and sends what was said. */
  toggleTalk(): void {
    if (this.recording) this.stopRecording();
    else this.startRecording("send");
  }

  startRecording(mode: "chat" | "send"): void {
    if (this.recording) return;
    this.stopSpeaking(); // talking over the agent interrupts it
    this.voiceMode = mode;
    this.voice.post({ type: "_voice", cmd: "record", maxSeconds: Number(this.hub.settings()?.voice?.stt?.max_seconds) || 120 });
  }

  stopRecording(): void {
    if (this.recording) this.voice.post({ type: "_voice", cmd: "stopRecord" });
  }

  private setRecording(on: boolean): void {
    this.recording = this.hub.state.listening = on;
    this.hub.changed();
    this.refreshState();
  }

  /** Read an answer aloud (when enabled in Settings): the daemon synthesises it sentence by sentence, the voice window plays it. */
  private speak(text: string): void {
    if (!this.hub.settings()?.voice?.tts?.enabled || !text.trim()) return;
    const id = ++this.ttsId;
    this.voice.post({ type: "_voice", cmd: "speak", id });
    this.hub.send({ type: "tts", id, text });
  }

  /** Interrupt now (the user pressed stop, started talking, sent a message, or the kill switch fired). */
  stopSpeaking(): void {
    if (!this.voice.win) return; // nothing was ever spoken
    this.ttsId += 1;
    this.voice.post({ type: "_voice", cmd: "stop" });
    this.hub.send({ type: "tts_stop" });
  }

  private onTranscript(text: string): void {
    this.hideBubble();
    if (!text) return this.say(tr("I did not catch that.", "Mình chưa nghe rõ."), 3000);
    if (this.voiceMode === "send") return this.hub.sendUser(text, []);
    this.hub.insertText(text);
    this.hub.showMain("chat");
  }

  // ---- events ------------------------------------------------------------------------------------------------------------------
  /** What the companion does about a daemon event (after the session and the avatar state were updated). */
  onEvent(ev: Ev, freshApprovals: string[], approvalsChanged: boolean): void {
    const name = this.hub.session.agentName;
    if (freshApprovals.length) {
      this.say(tr("Needs your approval: {t}", "Cần bạn duyệt: {t}", { t: this.hub.session.approvals[freshApprovals[0]!]!.tool }), 0, true);
    } else if (approvalsChanged && !Object.keys(this.hub.session.approvals).length) this.hideBubble();
    switch (ev.type) {
      case "final":
        this.say(ev.text ? String(ev.text).trim().split("\n")[0]!.slice(0, 120) : "", 5000);
        this.speak(String(ev.text ?? ""));
        break;
      case "killed":
        this.stopSpeaking();
        break;
      case "transcript":
        this.onTranscript(String(ev.text ?? ""));
        break;
      case "tts_audio":
        if (ev.id === this.ttsId) this.voice.post({ type: "_voice", cmd: "clip", id: ev.id, data: ev.data, mime: ev.mime });
        break;
      case "tts_done":
        if (ev.id === this.ttsId) this.voice.post({ type: "_voice", cmd: "done", id: ev.id });
        break;
      case "tts_error":
        this.say(tr("Cannot speak: {m}", "Không đọc được: {m}", { m: ev.message ?? "" }), 4000);
        break;
      case "error":
        if (ev.code !== "approval_pending") this.say(`! ${ev.message ?? ""}`, 5000);
        break;
      case "screen_mode":
        this.onScreenMode(ev);
        break;
      case "screen_viewed":
        this.say(tr("{n} looked at your screen ({k})", "{n} đã xem màn hình ({k})", { n: name, k: ev.kind ?? "" }), 3500);
        break;
      case "highlight":
        if (Array.isArray(ev.rect) && ev.rect.length === 4) this.showHighlight(ev.rect, String(ev.label ?? ""), Number(ev.ms ?? 700));
        break;
      case "control_aborted":
        this.say(String(ev.reason ?? ""), 6000);
        break;
      case "captured": {
        this.hub.showMain("chat");
        this.hub.attach(ev.attachment);
        const note = ev.masked ? tr(" ({n} private window hidden)", " (đã ẩn {n} cửa sổ riêng tư)", { n: ev.masked }) : "";
        this.say(tr("Captured: {t}", "Đã chụp: {t}", { t: String(ev.title || tr("screen", "màn hình")).slice(0, 40) }) + note, 3500);
        break;
      }
    }
    this.refreshState();
  }

  // ---- messages from the companion windows ------------------------------------------------------------------------------------------
  /** One entry point for every satellite window; `role` is what the window is (checked by the caller), so each can only do its own things. */
  handle(role: Role, name: string, args: any[]): unknown {
    const overlay = { avatar: this.avatar, bubble: this.bubble, quick: this.quick, radial: this.radial, highlight: this.highlight, badge: this.badge,
      region: this.region, voice: this.voice }[role as string];
    if (!overlay || overlay.handle(name, args)) return;
    const key = `${role}:${name}`;
    if (key === "avatar:skin") return this.loadSkin(this.cfg.skin);
    if (key === "avatar:shape") return this.shape(Math.round(args[0]), Math.round(args[1]), Array.isArray(args[2]) ? args[2] : []);
    if (key === "avatar:click") return this.openQuick();
    if (key === "avatar:dblclick") return this.hub.showMain("chat");
    if (key === "avatar:menu") return this.openRadial();
    if (key === "avatar:drop") return this.openQuick(Array.isArray(args[0]) ? args[0] : []);
    if (key === "avatar:press" || key === "avatar:headpat") return this.stopWalk(); // stop() of a walk never fires its own end
    if (key === "avatar:headpatEnd") return this.scheduleWander();
    if (key === "avatar:dragStart") {
      const c = screen.getCursorScreenPoint(), b = this.avatar.bounds();
      this.drag = { dx: c.x - b.x, dy: c.y - b.y };
      if (this.quick.visible) this.quick.win!.webContents.send("agent:event", { type: "_quickCloseIfEmpty" });
      return;
    }
    if (key === "avatar:dragMove" && this.drag) {
      const c = screen.getCursorScreenPoint();
      return this.moveAvatar(c.x - this.drag.dx, c.y - this.drag.dy);
    }
    if (key === "avatar:dragEnd") {
      this.drag = null;
      const b = this.avatar.bounds(), p = snapToEdges(b, this.area(b));
      this.moveAvatar(p.x, p.y);
      return this.scheduleWander();
    }
    if (key === "bubble:click") {
      this.hideBubble();
      return Object.keys(this.hub.session.approvals).length ? this.hub.showApprovals() : this.hub.showMain("chat");
    }
    if (key === "quick:hide") return this.quick.hide();
    if (key === "quick:submit") {
      this.quick.hide();
      return this.hub.sendUser(String(args[0] ?? ""), Array.isArray(args[1]) ? args[1] : []);
    }
    if (key === "radial:close") return this.closeRadial();
    if (key === "radial:choose") {
      const run = this.actions[String(args[0])];
      this.closeRadial();
      if (run) setImmediate(run); // after the ring is gone, so a window it opens is not hidden behind it
      return;
    }
    if (key === "voice:speaking") {
      this.hub.state.speaking = Boolean(args[0]);
      this.hub.changed();
      return this.refreshState();
    }
    if (key === "voice:recording") {
      this.setRecording(true);
      return this.say(tr("Listening…", "Đang nghe…"), 0, true);
    }
    if (key === "voice:failed") {
      this.setRecording(false);
      return this.say(String(args[0] ?? ""), 3000);
    }
    if (key === "voice:recorded") {
      this.setRecording(false);
      this.hideBubble();
      this.say(tr("Transcribing…", "Đang chuyển giọng nói thành chữ…"), 15000);
      if (!this.hub.send({ type: "transcribe", data: String(args[0] ?? ""), mime: "audio/wav" })) this.say(tr("Not connected.", "Chưa kết nối."), 3000);
      return;
    }
    if (key === "badge:click") return void this.hub.send({ type: "screen_mode", action: "off" });
    if (key === "region:done") return this.regionDone((args[0] ?? null) as Rect | null);
  }

  /** Extra tray menu entries for the screen and the avatar. */
  trayItems(): Electron.MenuItemConstructorOptions[] {
    return [
      { label: tr("Capture", "Chụp màn hình"), submenu: [
        { label: tr("Whole screen", "Toàn màn hình"), click: () => this.capture("screen") },
        { label: tr("Active window (in 2 s)", "Cửa sổ đang dùng (sau 2 giây)"), click: () => this.captureWindowSoon() },
        { label: tr("Select an area…", "Chọn một vùng…"), click: () => this.captureRegion() }] },
      { label: this.screenState !== "off" ? tr("Stop watching the screen", "Dừng theo dõi màn hình") : tr("Watch the screen", "Theo dõi màn hình"), click: () => this.toggleWatch() },
      { label: tr("Stop speaking", "Ngừng đọc"), click: () => this.stopSpeaking() },
    ];
  }
}
