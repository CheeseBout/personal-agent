/** `agent-ui`: tray + chat + approval center, all talking to the daemon over one WebSocket held here in the main process.
 * The UI holds no agent state: closing it never stops a running task, and on reconnect the daemon re-sends pending approvals. */
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { app, BrowserWindow, dialog, ipcMain, type IpcMainInvokeEvent, Menu, nativeTheme, Notification, Tray } from "electron";
import { AvatarState } from "../shared/avatarState";
import { setLanguage, tr } from "../shared/i18n";
import { type Ev, Session } from "../shared/session";
import { DEFAULT_HIGHLIGHT } from "../shared/theme";
import type { Attachment } from "../shared/attach";
import { Companion } from "./companion";
import { DaemonClient, isUp, readConnection, startDaemon } from "./daemon";
import { autostartEnabled, Hotkeys, setAutostart } from "./system";
import { createWindow, orbIcon, type Role, roleOf } from "./windows";

const THREAD = "default";
// What a window may send straight to the daemon. Turns and approvals have their own checked channels below.
const FORBIDDEN_FROM_WINDOWS = new Set(["user", "approve", "about"]);

const session = new Session(THREAD);
const avatar = new AvatarState();
let client: DaemonClient | null = null;
let mainWin: BrowserWindow | null = null;
let approvalsWin: BrowserWindow | null = null;
let tray: Tray | null = null;
let settings: Ev | null = null; // the daemon's last `settings` event
let about: Ev | null = null; // pid, data_dir, config_dir
let railCollapsed = false;
let page = "chat";
let quitting = false;
let seq = 0; // every broadcast is numbered, so a window that starts from a snapshot knows which events it already contains
const hotkeys = new Hotkeys();
// Daemon events only the main process needs (large payloads, or not for a page that renders untrusted text).
const NOT_FOR_WINDOWS = new Set(["captured", "tts_audio", "tts_done", "transcript"]);

// ---- windows ---------------------------------------------------------------------------------------------------------------
/** To the chat and approval windows; UI state (`_ui`: theme, language) goes to every window, the companion ones included. */
function broadcast(ev: Ev): void {
  const numbered = { ...ev, _seq: ++seq };
  for (const w of BrowserWindow.getAllWindows()) {
    if (w.isDestroyed()) continue;
    const role = roleOf(w.webContents);
    if (role === "main" || role === "approvals" || ev.type === "_ui") w.webContents.send("agent:event", numbered);
  }
}

function showMain(key = "chat"): void {
  page = key;
  broadcast({ type: "_page", page });
  if (!mainWin) return;
  mainWin.show();
  if (mainWin.isMinimized()) mainWin.restore();
  mainWin.focus();
}

function showApprovals(): void {
  if (!approvalsWin) return;
  if (approvalsWin.isMinimized()) approvalsWin.restore();
  approvalsWin.show();
  approvalsWin.moveTop();
  approvalsWin.flashFrame(true); // Windows may refuse to hand over the focus: at least the taskbar button blinks
  approvalsWin.once("focus", () => approvalsWin?.flashFrame(false));
}

function hideInsteadOfClosing(w: BrowserWindow): void {
  w.on("close", (e) => {
    if (quitting) return;
    e.preventDefault(); // closing only hides it: the app lives in the tray
    w.hide();
  });
}

const highlight = (): string => settings?.ui?.highlight || DEFAULT_HIGHLIGHT;

function buildTray(): void {
  const n = Object.keys(session.approvals).length;
  const item = (en: string, vi: string, click: () => void): Electron.MenuItemConstructorOptions => ({ label: tr(en, vi), click });
  const pages: [string, string, string][] = [["memory", "Memory", "Bộ nhớ"], ["rag", "Documents", "Tài liệu"], ["mcp", "Tools / MCP", "Công cụ / MCP"],
    ["schedules", "Schedules", "Lịch chạy"], ["rules", "Remembered approvals", "Quy tắc đã nhớ"], ["audit", "Audit log", "Nhật ký"],
    ["usage", "Usage", "Mức dùng"], ["policy", "Policy", "Chính sách"]];
  tray?.setContextMenu(Menu.buildFromTemplate([
    item("Open chat", "Mở chat", () => showMain("chat")),
    { label: n ? tr("Approvals ({n})", "Phê duyệt ({n})", { n }) : tr("Approvals", "Phê duyệt"), click: showApprovals },
    { label: tr("Manage", "Quản lý"), submenu: pages.map(([k, en, vi]) => item(en, vi, () => showMain(k))) },
    item("Settings", "Cài đặt", () => showMain("settings")),
    { type: "separator" },
    ...companion.trayItems(),
    item("Pause agent (kill switch)", "Tạm dừng agent (công tắc khẩn)", kill),
    item("Resume agent", "Cho agent chạy lại", () => client?.send({ type: "release_kill" })),
    { type: "separator" },
    item("Show / hide avatar", "Hiện / ẩn avatar", () => companion.toggleAvatar()),
    item("Quit the UI (the agent keeps running)", "Thoát giao diện (agent vẫn chạy)", quit),
  ]));
  tray?.setToolTip(client?.connected ? session.agentName : `${session.agentName} — ${tr("offline", "mất kết nối")}`);
}

function notify(body: string): void {
  if (Notification.isSupported()) new Notification({ title: session.agentName, body, icon: orbIcon(highlight(), 64) }).show();
}

// ---- daemon events ---------------------------------------------------------------------------------------------------------
function onEvent(ev: Ev): void {
  // A speech failure is about the loudspeaker, not about the task: it must not end up in the transcript or stop the "busy" state.
  if (ev.type === "error" && ev.code === "tts") return companion.onEvent({ ...ev, type: "tts_error" }, [], false);
  const before = new Set(Object.keys(session.approvals));
  const changed = session.apply(ev);
  avatar.pendingApprovals = Object.keys(session.approvals).length;
  avatar.onEvent(ev);
  if (!NOT_FOR_WINDOWS.has(ev.type ?? "")) broadcast(ev);
  const fresh = Object.keys(session.approvals).filter((id) => !before.has(id)).sort();
  if (changed.has("approvals")) {
    if (fresh.length) {
      notify(tr("Needs your approval: {t}", "Cần bạn duyệt: {t}", { t: session.approvals[fresh[0]!]!.tool }));
      showApprovals(); // a risky action must be able to interrupt whatever page is open
    }
    buildTray();
  }
  if (changed.has("name")) applyName();
  switch (ev.type) {
    case "settings":
      onSettings(ev);
      break;
    case "secrets":
      client?.send({ type: "settings_get" });
      break;
    case "about":
      about = ev;
      loadRail();
      if (ev.killed && !session.killed) onEvent({ type: "killed", threads: 0 }); // the daemon was paused before this UI connected
      break;
    case "final":
      client?.send({ type: "usage" }); // keep the budget meters current after every answer
      break;
    case "schedule_run":
      notify(String(ev.status ?? ""));
      break;
    case "control_aborted":
      notify(String(ev.reason ?? ""));
      break;
    case "screen_change":
      tray?.setToolTip(`${session.agentName} — ` + tr("screen changed ×{n}", "màn hình đổi ×{n}", { n: ev.changes ?? 0 }));
      break;
  }
  companion.onEvent(ev, fresh, changed.has("approvals"));
}

function onSettings(ev: Ev): void {
  settings = ev;
  setLanguage(ev.ui?.language ?? "auto", app.getLocale());
  avatar.sleepAfterS = ev.ui?.sleep_after_s ?? avatar.sleepAfterS;
  if (ev.agent?.name && ev.agent.name !== session.agentName) onEvent({ type: "agent_name", name: ev.agent.name });
  const icon = orbIcon(highlight());
  tray?.setImage(icon);
  for (const w of [mainWin, approvalsWin]) w?.setIcon(orbIcon(highlight(), 64));
  buildTray();
  companion.applySettings(ev.ui ?? {});
  const keys = { kill, chat: () => showMain("chat"), capture: () => companion.capture("screen"), watch: () => companion.toggleWatch(),
    talk: () => companion.toggleTalk() };
  if (hotkeys.apply(ev.ui?.hotkeys ?? {}, keys)) {
    for (const [name, why] of Object.entries(hotkeys.failed)) notify(tr("Hotkey '{n}' unavailable: {w}", "Phím tắt '{n}' không dùng được: {w}", { n: name, w: why }));
  }
  broadcast({ type: "_ui", ...uiState() });
}

function applyName(): void {
  companion.postAvatar();
  mainWin?.setTitle(session.agentName);
  approvalsWin?.setTitle(`${session.agentName} — ${tr("Approvals", "Phê duyệt")}`);
  buildTray();
}

const uiState = (): Record<string, unknown> => ({ connected: Boolean(client?.connected), dark: nativeTheme.shouldUseDarkColors, highlight: highlight(),
  lang: setLanguage(settings?.ui?.language ?? "auto", app.getLocale()), railCollapsed, page, hotkeyProblems: hotkeys.failed,
  recording: companion.recording, speaking: avatar.speaking });

// ---- small UI state kept next to the daemon's data (survives a restart) ---------------------------------------------------------
const railFile = (): string | null => (about?.data_dir ? path.join(String(about.data_dir), "ui_state.json") : null);

function loadRail(): void {
  try {
    railCollapsed = Boolean(JSON.parse(readFileSync(railFile() ?? "", "utf8")).rail_collapsed);
  } catch {
    railCollapsed = false;
  }
  broadcast({ type: "_ui", ...uiState() });
}

function setRail(collapsed: boolean): void {
  railCollapsed = collapsed;
  try {
    const f = railFile();
    if (f) writeFileSync(f, JSON.stringify({ rail_collapsed: collapsed }), "utf8");
  } catch {
    /* a read-only data folder only costs the remembered state */
  }
  broadcast({ type: "_ui", ...uiState() });
}

/** Skins on disk: a folder with a manifest.json under <data>/skins or <config>/avatar. 'default' is built in. */
function skins(): string[] {
  const found = new Set<string>();
  for (const root of [about?.data_dir && path.join(String(about.data_dir), "skins"), about?.config_dir && path.join(String(about.config_dir), "avatar")]) {
    try {
      for (const d of readdirSync(root || "", { withFileTypes: true })) if (d.isDirectory() && existsSync(path.join(root!, d.name, "manifest.json"))) found.add(d.name);
    } catch {
      /* no such folder */
    }
  }
  found.delete("default");
  return ["default", ...[...found].sort()];
}

/** CPU % and memory of this UI (every Electron process together) and of the daemon (from its last `about`). */
function stats(): Record<string, unknown> {
  const m = app.getAppMetrics();
  client?.send({ type: "about", ui_pid: process.pid }); // refreshes the daemon's numbers for the next poll
  return { ui: { cpu: m.reduce((n, p) => n + p.cpu.percentCPUUsage, 0), rss_mb: m.reduce((n, p) => n + p.memory.workingSetSize, 0) / 1024 },
    daemon: client?.connected ? (about?.stats ?? null) : null };
}

const companion: Companion = new Companion({ session, state: avatar, settings: () => settings, about: () => about,
  send: (msg) => client?.send(msg) ?? false, showMain, showApprovals, kill, quit, sendUser,
  changed: () => {
    buildTray();
    broadcast({ type: "_ui", ...uiState() });
  },
  insertText: (text) => mainWin?.webContents.send("agent:event", { type: "_insert", text, _seq: ++seq }),
  attach: (a) => mainWin?.webContents.send("agent:event", { type: "_attach", attachment: a, _seq: ++seq }) });

// ---- user actions ----------------------------------------------------------------------------------------------------------
function local(ev: Ev): void {
  session.apply(ev);
  broadcast(ev);
}

function sendUser(text: string, files: Attachment[]): void {
  companion.stopSpeaking();
  local({ type: "_user", text: text || tr("(voice message)", "(tin nhắn thoại)"), attachments: files.map((f) => f.name) });
  avatar.userSent();
  companion.refreshState();
  if (!client?.send({ type: "user", thread_id: THREAD, text, attachments: files })) {
    onEvent({ type: "error", code: "offline", message: tr("The agent is not running or not reachable yet.", "Agent chưa chạy hoặc chưa kết nối được.") });
  }
}

function decide(threadId: string, decisions: Record<string, unknown>): boolean {
  if (!client?.send({ type: "approve", thread_id: threadId, decisions })) return false;
  local({ type: "_decided", call_ids: Object.keys(decisions) });
  avatar.pendingApprovals = Object.keys(session.approvals).length;
  avatar.busy = true;
  companion.refreshState();
  buildTray();
  return true;
}

function kill(): void {
  companion.stopSpeaking();
  client?.send({ type: "kill" });
}

function quit(): void {
  quitting = true;
  companion.stopSpeaking();
  client?.send({ type: "screen_mode", action: "off" }); // never leave watching on behind a closed UI
  hotkeys.clear();
  client?.stop();
  app.quit();
}

// ---- IPC: every handler checks which window is asking ------------------------------------------------------------------------
function handle(channel: string, allowed: Role[], fn: (e: IpcMainInvokeEvent, ...args: any[]) => unknown): void {
  ipcMain.handle(channel, (e, ...args) => {
    const role = roleOf(e.sender);
    if (!role || !allowed.includes(role) || e.senderFrame !== e.sender.mainFrame) throw new Error("not allowed");
    return fn(e, ...args);
  });
}

function registerIpc(): void {
  handle("agent:init", ["main", "approvals"], () => ({ snapshot: session.snapshot(), seq, settings, ...uiState() }));
  // The companion windows: no transcript, no settings, only what each needs to draw itself.
  const satellites: Role[] = ["avatar", "bubble", "quick", "radial", "highlight", "badge", "region", "voice"];
  handle("overlay:init", satellites, () => uiState());
  handle("overlay", satellites, (e, name: unknown, ...args: unknown[]) => companion.handle(roleOf(e.sender)!, String(name), args));
  handle("agent:send", ["main"], (_e, msg: Ev) => {
    if (!msg || typeof msg !== "object" || typeof msg.type !== "string" || FORBIDDEN_FROM_WINDOWS.has(msg.type)) throw new Error("not allowed");
    return client?.send(msg) ?? false;
  });
  handle("agent:user", ["main"], (_e, text: unknown, files: unknown) => sendUser(String(text ?? ""), Array.isArray(files) ? files : []));
  // Only the approval window may answer an approval request: a page that renders untrusted text must not be able to approve.
  handle("agent:decide", ["approvals"], (_e, threadId: unknown, decisions: unknown) =>
    decisions && typeof decisions === "object" ? decide(String(threadId), decisions as Record<string, unknown>) : false);
  handle("agent:ui", ["main", "approvals"], (e, action: string, arg: unknown) => {
    const fromMain = roleOf(e.sender) === "main";
    if (action === "approvals") showApprovals();
    else if (action === "page" && fromMain) showMain(String(arg));
    else if (action === "rail" && fromMain) setRail(Boolean(arg));
    else if (action === "quit" && fromMain) quit();
    else if (action === "record" && fromMain) arg ? companion.startRecording("chat") : companion.stopRecording(); // the chat's Talk button
  });
  // The user picks a file or folder in the system dialog; only that one path goes back to the page.
  handle("agent:pick", ["main"], async (e, kind: unknown, title: unknown, extensions: unknown) => {
    const win = BrowserWindow.fromWebContents(e.sender)!;
    const res = await dialog.showOpenDialog(win, { title: String(title ?? ""), defaultPath: app.getPath("documents"),
      properties: [kind === "folder" ? "openDirectory" : "openFile"],
      filters: Array.isArray(extensions) && kind !== "folder" ? [{ name: tr("Documents", "Tài liệu"), extensions: extensions.map(String) }] : [] });
    return res.canceled ? null : (res.filePaths[0] ?? null);
  });
  handle("agent:skins", ["main"], skins);
  handle("agent:stats", ["main"], stats);
  handle("agent:autostart", ["main"], (_e, enabled?: unknown) => {
    if (typeof enabled === "boolean") setAutostart(enabled);
    return autostartEnabled();
  });
}

// ---- lifecycle -------------------------------------------------------------------------------------------------------------
async function start(): Promise<void> {
  registerIpc();
  tray = new Tray(orbIcon(DEFAULT_HIGHLIGHT));
  tray.on("click", () => showMain("chat"));
  mainWin = createWindow("main", { width: 1120, height: 700, minWidth: 720, minHeight: 480, title: session.agentName, icon: orbIcon(DEFAULT_HIGHLIGHT, 64) });
  approvalsWin = createWindow("approvals", { width: 720, height: 760, minWidth: 480, minHeight: 360, icon: orbIcon(DEFAULT_HIGHLIGHT, 64) });
  hideInsteadOfClosing(mainWin);
  hideInsteadOfClosing(approvalsWin);
  mainWin.once("ready-to-show", () => showMain("chat"));
  nativeTheme.on("updated", () => broadcast({ type: "_ui", ...uiState() }));
  buildTray();
  applyName();

  const conn = await readConnection();
  if (!process.env.PERSONAL_AGENT_NO_DAEMON && !(await isUp(conn.host, conn.port))) startDaemon();
  client = new DaemonClient(conn);
  client.on("message", onEvent);
  client.on("connected", () => {
    avatar.connected = true;
    companion.refreshState();
    // ui_pid keeps the agent off our own windows (it could approve itself): every BrowserWindow belongs to this process.
    for (const m of [{ type: "settings_get" }, { type: "usage" }, { type: "about", ui_pid: process.pid }, { type: "screen_mode", action: "status" }]) client!.send(m);
    broadcast({ type: "_ui", ...uiState() });
    buildTray();
  });
  client.on("disconnected", () => {
    avatar.connected = false;
    companion.refreshState();
    broadcast({ type: "_ui", ...uiState() });
    buildTray();
  });
  client.start();
}

if (!app.requestSingleInstanceLock()) {
  app.quit(); // already running: look for the tray icon
} else {
  app.on("second-instance", () => showMain(page));
  app.on("window-all-closed", () => undefined); // lives in the tray
  app.on("before-quit", () => (quitting = true));
  app.on("will-quit", () => hotkeys.clear());
  void app.whenReady().then(start).catch((e) => {
    console.error(e);
    notify(String(e?.message ?? e));
  });
}
