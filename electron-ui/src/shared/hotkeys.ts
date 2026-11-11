/** Hotkey specs as written in the config ('ctrl+alt+k') -> Electron accelerators ('Control+Alt+K'). Pure, tested. */

const MOD: Record<string, string> = { alt: "Alt", ctrl: "Control", control: "Control", shift: "Shift", win: "Super" };
const NAMED: Record<string, string> = { space: "Space", enter: "Return", esc: "Escape", tab: "Tab", home: "Home", end: "End", pause: "Pause" };

/** At least one modifier is required (a bare key would steal typing). Throws an Error with a user-facing message. */
export function toAccelerator(spec: string): string {
  const parts = spec.split("+").map((p) => p.trim().toLowerCase()).filter(Boolean);
  if (parts.length < 2) throw new Error(`hotkey '${spec}' needs a modifier and a key`);
  const key = parts.pop()!;
  if (parts.some((m) => !(m in MOD))) throw new Error(`unknown modifier in '${spec}'`);
  let k: string;
  if (key in NAMED) k = NAMED[key]!;
  else if (/^[a-z0-9]$/.test(key)) k = key.toUpperCase();
  else if (/^f\d+$/.test(key) && Number(key.slice(1)) >= 1 && Number(key.slice(1)) <= 24) k = key.toUpperCase();
  else throw new Error(`unknown key in '${spec}'`);
  return [...new Set(parts.map((m) => MOD[m]!)), k].join("+");
}
