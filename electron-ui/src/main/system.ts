/** Windows integration: global hotkeys and "start with Windows". */
import { app, globalShortcut } from "electron";
import { toAccelerator } from "../shared/hotkeys";

const REPEAT_MS = 400; // a held key repeats: one press must be one action (the talk hotkey is a toggle)
export const AUTOSTART_NAME = "personal-agent-ui"; // the same Run value the installer writes, so there is never a second entry

/** Registers global hotkeys. A key taken by another program (or a bad spec) is reported in `failed` instead of throwing:
 * the app keeps working and the user can pick another key in the config. */
export class Hotkeys {
  failed: Record<string, string> = {};
  private specs = "";

  /** (Re)register everything; does nothing when the specs did not change. Returns true if it re-registered. */
  apply(specs: Record<string, string>, actions: Record<string, () => void>): boolean {
    const wanted = Object.keys(actions).map((n) => `${n}=${specs[n] ?? ""}`).join(";");
    if (wanted === this.specs) return false;
    this.clear();
    this.specs = wanted;
    for (const [name, run] of Object.entries(actions)) {
      const spec = specs[name] ?? "";
      if (!spec) continue;
      let last = 0;
      try {
        const ok = globalShortcut.register(toAccelerator(spec), () => {
          const now = Date.now();
          if (now - last >= REPEAT_MS) run();
          last = now;
        });
        if (!ok) this.failed[name] = `${spec} is already used by another program`;
      } catch (e) {
        this.failed[name] = (e as Error).message;
      }
    }
    return true;
  }

  clear(): void {
    globalShortcut.unregisterAll();
    this.failed = {};
    this.specs = "";
  }
}

// A development run is `electron.exe <app folder>`: the folder must be part of the command Windows runs at sign-in.
const loginItem = (): { name: string; path: string; args: string[] } =>
  ({ name: AUTOSTART_NAME, path: process.execPath, args: app.isPackaged ? [] : [app.getAppPath()] });

export const autostartEnabled = (): boolean => app.getLoginItemSettings(loginItem()).openAtLogin;

export function setAutostart(enabled: boolean): void {
  app.setLoginItemSettings({ ...loginItem(), openAtLogin: enabled });
}
