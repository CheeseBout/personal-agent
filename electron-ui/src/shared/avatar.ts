/** The avatar and its satellites without windows: where things go on the screen, the radial ring's geometry, the headpat gesture,
 * skin manifests. Pure, tested. Rectangles are {x, y, width, height} in screen points (Electron's DIP). */
import { STATES } from "./avatarState";

export type Rect = { x: number; y: number; width: number; height: number };
export type Point = { x: number; y: number };

export const SNAP_PX = 24;
export const DRAG_PX = 4;
export const DOUBLE_CLICK_MS = 500; // a single click waits this long, so a double click does not also fire it

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(v, hi));

/** Position for `r` after a drag: stick to a screen edge when within `margin` of it, always stay on screen. */
export function snapToEdges(r: Rect, area: Rect, margin = SNAP_PX): Point {
  let { x, y } = r;
  const right = area.x + area.width - r.width, bottom = area.y + area.height - r.height;
  if (Math.abs(x - area.x) <= margin) x = area.x;
  else if (Math.abs(x - right) <= margin) x = right;
  if (Math.abs(y - area.y) <= margin) y = area.y;
  else if (Math.abs(y - bottom) <= margin) y = bottom;
  return { x: clamp(x, area.x, Math.max(area.x, right)), y: clamp(y, area.y, Math.max(area.y, bottom)) };
}

export const defaultPlace = (size: { width: number; height: number }, area: Rect): Point =>
  ({ x: area.x + area.width - size.width - 24, y: area.y + area.height - size.height - 24 });

export const intersects = (a: Rect, b: Rect): boolean => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

/** The speech bubble: centred above the avatar, kept on screen; below it when there is no room above. */
export function bubblePlace(size: { width: number; height: number }, near: Rect, area: Rect): Point {
  const x = clamp(near.x + Math.floor(near.width / 2) - Math.floor(size.width / 2), area.x, Math.max(area.x, area.x + area.width - size.width));
  const y = near.y - size.height - 4;
  return { x, y: y >= area.y ? y : near.y + near.height + 4 };
}

/** The quick-input box: right-aligned with the avatar, above it when there is room, else below. */
export function quickPlace(size: { width: number; height: number }, near: Rect, area: Rect): Point {
  const x = clamp(near.x - size.width + near.width, area.x, Math.max(area.x, area.x + area.width - size.width));
  return { x, y: near.y - area.y > size.height + 10 ? near.y - size.height - 6 : near.y + near.height + 6 };
}

// ---- radial menu ---------------------------------------------------------------------------------------------------------------
export const RING_THICK = 70;
export const RING_MARGIN = 6;

/** Which of `n` slots lies at offset (dx, dy) from the ring centre. Slot 0 is centred at the top, the others follow clockwise. */
export function slotAt(dx: number, dy: number, rIn: number, rOut: number, n: number): number | null {
  const r = Math.hypot(dx, dy);
  if (n <= 0 || r < rIn || r > rOut) return null;
  const cw = ((Math.atan2(dx, -dy) * 180) / Math.PI + 360) % 360; // degrees clockwise from the top
  const step = 360 / n;
  return Math.floor(((cw + step / 2) % 360) / step);
}

/** The ring around a target of `near` size: inner radius, window side, and its place (shifted inward at a screen edge). */
export function ringPlace(near: Rect, area: Rect): { rIn: number; side: number; x: number; y: number } {
  const rIn = clamp(Math.floor(Math.max(near.width, near.height) / 2) + 10, 56, 150);
  const side = Math.round(2 * (rIn + RING_THICK + RING_MARGIN));
  const cx = near.x + Math.floor(near.width / 2), cy = near.y + Math.floor(near.height / 2);
  return { rIn, side, x: clamp(cx - side / 2, area.x, Math.max(area.x, area.x + area.width - side)),
    y: clamp(cy - side / 2, area.y, Math.max(area.y, area.y + area.height - side)) };
}

/** An item of the ring as the window sees it; `id` is what it reports back when chosen (actions live in the main process). */
export type RadialItem = { id?: string; label: string; glyph: string; children?: RadialItem[]; badge?: number; active?: boolean; danger?: boolean };

// ---- headpat: stroke the top of the head back and forth with no button pressed ---------------------------------------------------
export const PAT = { zone: 0.4, minPx: 6, strokes: 3, idleMs: 700, ms: 2500 };

export class Stroke {
  private x: number | null = null;
  private dir = 0;
  private n = 0;

  reset(): void {
    this.x = null;
    this.dir = this.n = 0;
  }

  /** Feed a pointer position (inside a `height`-tall avatar); true when the gesture is complete. Call reset() after ~700 ms of no movement. */
  move(x: number, y: number, height: number): boolean {
    if (y > height * PAT.zone) return this.reset(), false;
    if (this.x === null) this.x = x;
    else if (Math.abs(x - this.x) >= PAT.minPx) {
      const d = x > this.x ? 1 : -1;
      if (this.dir !== 0 && d !== this.dir) this.n += 1;
      this.dir = d;
      this.x = x;
      if (this.n >= PAT.strokes) return this.reset(), true;
    }
    return false;
  }
}

// ---- click-through shape ---------------------------------------------------------------------------------------------------------
/** The opaque area of an RGBA image as rectangles (runs per row, equal runs on consecutive rows merged): the window's hit region. */
export function opaqueRects(rgba: Uint8ClampedArray, width: number, height: number): Rect[] {
  const out: Rect[] = [];
  let open = new Map<string, Rect>(); // runs of the previous row that can still grow downwards
  for (let y = 0; y < height; y++) {
    const next = new Map<string, Rect>();
    for (let x = 0; x < width; x++) {
      if (!rgba[(y * width + x) * 4 + 3]) continue;
      const start = x;
      while (x + 1 < width && rgba[(y * width + x + 1) * 4 + 3]) x++;
      const key = `${start},${x}`, above = open.get(key);
      if (above) above.height += 1;
      const r = above ?? { x: start, y, width: x - start + 1, height: 1 };
      if (!above) out.push(r);
      next.set(key, r);
    }
    open = next;
  }
  return out;
}

// ---- skins ---------------------------------------------------------------------------------------------------------------------
export const SKIN_BASE = 128;
export const SKIN_MAX = 512;
export const MAX_FRAMES = 240;
export type AnimSpec = { fps?: number; loop: boolean } & ({ builtin: string } | { gif: string } | { frames: string[] });
export type Manifest = { name: string; size: number; fallback: string; states: Record<string, AnimSpec>; problems: string[] };

/** Read a skin's manifest.json. Problems never throw: they are collected and that state falls back (fallback -> idle -> built-in). */
export function parseManifest(data: any, folderName: string): Manifest {
  const size = clamp(Number((data?.size ?? [SKIN_BASE])[0]) || SKIN_BASE, 32, SKIN_MAX);
  const m: Manifest = { name: String(data?.name ?? folderName), size: Math.floor(size), fallback: String(data?.fallback ?? "idle"), states: {}, problems: [] };
  for (const [state, spec] of Object.entries((data?.states ?? {}) as Record<string, any>)) {
    if (!(STATES as readonly string[]).includes(state) && state !== "custom") {
      m.problems.push(`unknown state '${state}'`);
      continue;
    }
    if (!spec) continue;
    const fps = spec.fps === undefined ? undefined : Number(spec.fps);
    if (fps !== undefined && !(fps >= 0.2 && fps <= 30)) {
      m.problems.push(`${state}: fps must be between 0.2 and 30`);
      continue;
    }
    const base = { fps, loop: spec.loop === undefined ? true : Boolean(spec.loop) };
    if ("builtin" in spec) m.states[state] = { ...base, builtin: String(spec.builtin) };
    else if ("gif" in spec) m.states[state] = { ...base, gif: String(spec.gif) };
    else if (Array.isArray(spec.frames) && spec.frames.length) m.states[state] = { ...base, frames: spec.frames.slice(0, MAX_FRAMES).map(String) };
    else m.problems.push(`${state}: no frames`);
  }
  return m;
}

/** The animation to show for `state`: its own, else the fallback state's, else idle's; undefined = draw the built-in look. */
export function pickState<T>(anims: Record<string, T | undefined>, state: string, fallback: string): T | undefined {
  return anims[state] ?? anims[fallback] ?? anims.idle;
}

export const easeInOutQuad = (t: number): number => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
