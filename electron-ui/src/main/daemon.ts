/** Find, start and talk to the daemon. It is started detached, so closing the UI never stops running tasks. */
import { execFile, spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import { existsSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { app } from "electron";
import WebSocket from "ws";

export type Connection = { host: string; port: number; token: string };
const BACKOFF_S = [0.3, 0.6, 1, 2, 4]; // short first steps: the UI often starts the daemon and it is ready ~4 s later

/** The daemon executable and its leading arguments: PERSONAL_AGENT_CORE, the bundled agent-core.exe, or the repo's virtualenv. */
export function coreCommand(windowless = false): [string, string[]] {
  const custom = process.env.PERSONAL_AGENT_CORE;
  if (custom) {
    const cmd: string[] = custom.startsWith("[") ? JSON.parse(custom) : [custom]; // a path, or a JSON array: [exe, ...args]
    return [cmd[0]!, cmd.slice(1)];
  }
  if (app.isPackaged) return [path.join(path.dirname(process.execPath), "..", "agent-core.exe"), []]; // <bundle>\ui\agent-ui.exe, <bundle>\agent-core.exe
  const scripts = path.resolve(app.getAppPath(), "..", ".venv", "Scripts");
  const pyw = path.join(scripts, "pythonw.exe");
  return [windowless && existsSync(pyw) ? pyw : path.join(scripts, "python.exe"), ["-m", "personal_agent.core.main"]];
}

const repoRoot = (): string | undefined => (app.isPackaged ? undefined : path.resolve(app.getAppPath(), ".."));

/** Host, port and the per-install token, asked from the daemon's own code (env > .env > Windows Credential Manager). */
export function readConnection(): Promise<Connection> {
  const [exe, args] = coreCommand();
  return new Promise((resolve, reject) => {
    execFile(exe, [...args, "--connection"], { cwd: repoRoot(), windowsHide: true, timeout: 30_000 }, (err, stdout) => {
      if (err) return reject(new Error(`cannot ask the daemon for its connection details: ${err.message}`));
      try {
        const c = JSON.parse(stdout.trim().split(/\r?\n/).at(-1) ?? "");
        resolve({ host: String(c.host), port: Number(c.port), token: String(c.token) });
      } catch {
        reject(new Error("the daemon returned unreadable connection details"));
      }
    });
  });
}

export function isUp(host: string, port: number, timeoutMs = 300): Promise<boolean> {
  return new Promise((resolve) => {
    const s = net.connect({ host, port });
    const done = (ok: boolean): void => {
      s.destroy();
      resolve(ok);
    };
    s.setTimeout(timeoutMs, () => done(false));
    s.once("connect", () => done(true));
    s.once("error", () => done(false));
  });
}

export function startDaemon(): void {
  const [exe, args] = coreCommand(true);
  spawn(exe, args, { cwd: repoRoot(), detached: true, stdio: "ignore", windowsHide: true }).unref();
}

/** WebSocket link to the daemon. Reconnects by itself; pending approvals are re-pushed by the daemon on every connect.
 * Emits: "message" (event object), "connected", "disconnected". */
export class DaemonClient extends EventEmitter {
  private ws: WebSocket | null = null;
  private retry: NodeJS.Timeout | null = null;
  private attempt = 0;
  private wanted = false;

  constructor(private conn: Connection) {
    super();
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  start(): void {
    this.wanted = true;
    this.open();
  }

  stop(): void {
    this.wanted = false;
    if (this.retry) clearTimeout(this.retry);
    this.ws?.close();
  }

  private open(): void {
    if (this.ws) return;
    // No Origin header (the daemon only rejects a foreign one); the token never reaches a renderer.
    const ws = (this.ws = new WebSocket(`ws://${this.conn.host}:${this.conn.port}/ws`, { headers: { Authorization: `Bearer ${this.conn.token}` } }));
    ws.on("open", () => {
      this.attempt = 0;
      this.emit("connected");
    });
    ws.on("message", (data) => {
      try {
        const ev = JSON.parse(String(data));
        if (ev && typeof ev === "object" && "type" in ev) this.emit("message", ev);
      } catch {
        /* not JSON: ignore */
      }
    });
    ws.on("error", () => undefined); // "close" follows and schedules the retry
    ws.on("close", () => {
      const was = this.ws === ws;
      this.ws = null;
      if (was) this.emit("disconnected");
      if (this.wanted) {
        this.retry = setTimeout(() => this.open(), BACKOFF_S[Math.min(this.attempt, BACKOFF_S.length - 1)]! * 1000);
        this.attempt += 1;
      }
    });
  }

  send(payload: Record<string, unknown>): boolean {
    if (!this.connected) return false;
    this.ws!.send(JSON.stringify(payload));
    return true;
  }
}
