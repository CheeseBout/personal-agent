/** Window creation. Every renderer is sandboxed, isolated, cannot navigate and cannot open windows; links go to the system browser. */
import path from "node:path";
import { BrowserWindow, type BrowserWindowConstructorOptions, nativeImage, shell, type WebContents } from "electron";

export type Role = "main" | "approvals" | "avatar" | "bubble" | "quick" | "radial" | "highlight" | "badge" | "region" | "voice";
const roles = new Map<number, Role>(); // webContents id -> what that window is allowed to ask for

export const roleOf = (wc: WebContents): Role | undefined => roles.get(wc.id);

export function openExternal(url: string): void {
  if (/^https?:\/\//i.test(url)) void shell.openExternal(url); // never file:, javascript: or custom schemes
}

/** Options shared by the avatar and its satellites: no frame, no taskbar button, see-through, above other windows. */
export const FLOATING: BrowserWindowConstructorOptions = { frame: false, transparent: true, skipTaskbar: true, resizable: false, hasShadow: false,
  alwaysOnTop: true, minimizable: false, maximizable: false, fullscreenable: false, thickFrame: false };

export function createWindow(role: Role, opts: BrowserWindowConstructorOptions): BrowserWindow {
  const win = new BrowserWindow({
    show: false,
    autoHideMenuBar: true,
    ...opts,
    webPreferences: { preload: path.join(__dirname, "../preload/index.js"), sandbox: true, contextIsolation: true, nodeIntegration: false,
      webSecurity: true, spellcheck: false,
      // The voice window is never shown: it must keep running in the background and may play answers without a click.
      ...(role === "voice" ? { backgroundThrottling: false, autoplayPolicy: "no-user-gesture-required" as const } : {}) },
  });
  const wc = win.webContents;
  roles.set(wc.id, role);
  win.on("closed", () => roles.delete(wc.id));
  win.removeMenu();
  wc.setWindowOpenHandler(({ url }) => {
    openExternal(url);
    return { action: "deny" };
  });
  wc.on("will-navigate", (e, url) => {
    e.preventDefault(); // the transcript holds untrusted text: a link must never replace the app's own page
    openExternal(url);
  });
  // The only permission any window gets: the microphone (audio only), and only for the hidden voice window.
  const micOnly = (asker: WebContents | null, permission: string, details: { mediaTypes?: string[]; mediaType?: string }): boolean =>
    Boolean(asker) && roleOf(asker!) === "voice" && permission === "media" && !details.mediaTypes?.includes("video") && details.mediaType !== "video";
  wc.session.setPermissionRequestHandler((asker, permission, cb, details) => cb(micOnly(asker, permission, details as { mediaTypes?: string[] })));
  wc.session.setPermissionCheckHandler((asker, permission, _origin, details) => micOnly(asker, permission, details));
  const dev = process.env.ELECTRON_RENDERER_URL;
  if (dev) void win.loadURL(`${dev}#/${role}`);
  else void win.loadFile(path.join(__dirname, "../renderer/index.html"), { hash: `/${role}` });
  return win;
}

/** The logo as an icon: a filled disc in the highlight colour with a soft halo (no image file shipped). */
export function orbIcon(color: string, size = 32): Electron.NativeImage {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16)) as [number, number, number];
  const buf = Buffer.alloc(size * size * 4);
  const c = (size - 1) / 2;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const d = Math.hypot(x - c, y - c) / (size / 2);
      const a = d <= 0.78 ? 255 : d <= 1 ? 70 : 0; // disc, then the halo ring
      const lift = d <= 0.78 ? Math.max(0, 0.45 - Math.hypot(x - size * 0.4, y - size * 0.36) / size) : 0; // highlight towards the top left
      const o = (y * size + x) * 4;
      const pm = a / 255; // BGRA, premultiplied
      buf[o] = Math.round(Math.min(255, b + 255 * lift) * pm);
      buf[o + 1] = Math.round(Math.min(255, g + 255 * lift) * pm);
      buf[o + 2] = Math.round(Math.min(255, r + 255 * lift) * pm);
      buf[o + 3] = a;
    }
  }
  return nativeImage.createFromBitmap(buf, { width: size, height: size });
}
