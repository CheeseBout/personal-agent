/** The only bridge between a (sandboxed) window and the main process. No Node, no token, no raw IPC in the page. */
import { contextBridge, ipcRenderer } from "electron";

const api = {
  init: (): Promise<Record<string, any>> => ipcRenderer.invoke("agent:init"),
  send: (msg: Record<string, unknown>): Promise<boolean> => ipcRenderer.invoke("agent:send", msg),
  sendUser: (text: string, files: unknown[]): Promise<void> => ipcRenderer.invoke("agent:user", text, files),
  decide: (threadId: string, decisions: Record<string, unknown>): Promise<boolean> => ipcRenderer.invoke("agent:decide", threadId, decisions),
  ui: (action: string, arg?: unknown): Promise<void> => ipcRenderer.invoke("agent:ui", action, arg),
  /** Start with Windows: pass a boolean to change it; always resolves to the current state. */
  autostart: (enabled?: boolean): Promise<boolean> => ipcRenderer.invoke("agent:autostart", enabled),
  /** Let the user choose a file or folder; resolves to its path, or null when cancelled. */
  pick: (kind: "file" | "folder", title: string, extensions?: string[]): Promise<string | null> => ipcRenderer.invoke("agent:pick", kind, title, extensions),
  skins: (): Promise<string[]> => ipcRenderer.invoke("agent:skins"),
  stats: (): Promise<{ ui: { cpu: number; rss_mb: number }; daemon: { cpu: number; rss_mb: number } | null }> => ipcRenderer.invoke("agent:stats"),
  /** For the companion windows (avatar, bubble, radial...): what each may ask is decided by the main process from the window's role. */
  overlayInit: (): Promise<Record<string, any>> => ipcRenderer.invoke("overlay:init"),
  overlay: (name: string, ...args: unknown[]): Promise<any> => ipcRenderer.invoke("overlay", name, ...args),
  onEvent: (cb: (ev: Record<string, any>) => void): (() => void) => {
    const h = (_e: unknown, ev: Record<string, any>): void => cb(ev);
    ipcRenderer.on("agent:event", h);
    return () => ipcRenderer.removeListener("agent:event", h);
  },
};

contextBridge.exposeInMainWorld("agent", api);
export type AgentApi = typeof api;
