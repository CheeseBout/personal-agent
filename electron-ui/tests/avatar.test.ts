// Avatar, radial ring and skins without windows (ported from tests/ui/test_ui_radial.py and test_ui_windows.py).
import { describe, expect, it } from "vitest";
import { bubblePlace, opaqueRects, parseManifest, pickState, quickPlace, ringPlace, slotAt, snapToEdges, Stroke } from "../src/shared/avatar";

const AREA = { x: 0, y: 0, width: 1920, height: 1040 };
const box = (x: number, y: number, s = 100): { x: number; y: number; width: number; height: number } => ({ x, y, width: s, height: s });

describe("placement", () => {
  it("snaps to edges and stays on screen", () => {
    expect(snapToEdges(box(10, 500), AREA)).toEqual({ x: 0, y: 500 });
    expect(snapToEdges(box(1815, 935), AREA)).toEqual({ x: 1820, y: 940 });
    expect(snapToEdges(box(500, 500), AREA)).toEqual({ x: 500, y: 500 });
    expect(snapToEdges(box(-80, -80), AREA)).toEqual({ x: 0, y: 0 }); // dragged off screen -> pulled back
    expect(snapToEdges(box(2500, 300), { x: 1920, y: 0, width: 1280, height: 720 })).toEqual({ x: 2500, y: 300 }); // a second monitor has its own origin
  });

  it("puts the bubble above the avatar, below when there is no room, and never off screen", () => {
    expect(bubblePlace({ width: 200, height: 40 }, box(1000, 500), AREA)).toEqual({ x: 950, y: 456 });
    expect(bubblePlace({ width: 200, height: 40 }, box(1000, 10), AREA)).toEqual({ x: 950, y: 114 });
    expect(bubblePlace({ width: 200, height: 40 }, box(1850, 500), AREA).x).toBe(1720);
    expect(quickPlace({ width: 320, height: 92 }, box(1000, 500), AREA)).toEqual({ x: 780, y: 402 });
    expect(quickPlace({ width: 320, height: 92 }, box(1000, 50), AREA)).toEqual({ x: 780, y: 156 });
  });
});

describe("radial ring", () => {
  it("counts slots clockwise from the top and ignores the hole and the outside", () => {
    expect(slotAt(0, -100, 60, 130, 8)).toBe(0); // straight up
    expect(slotAt(100, 0, 60, 130, 8)).toBe(2); // right
    expect(slotAt(0, 100, 60, 130, 8)).toBe(4); // down
    expect(slotAt(-100, 0, 60, 130, 8)).toBe(6); // left
    expect([slotAt(0, -100, 60, 130, 4), slotAt(100, 0, 60, 130, 4)]).toEqual([0, 1]);
    expect([slotAt(0, -10, 60, 130, 8), slotAt(0, -200, 60, 130, 8)]).toEqual([null, null]);
    expect([slotAt(-1, -100, 60, 130, 8), slotAt(1, -100, 60, 130, 8)]).toEqual([0, 0]); // either side of the top line stays in slot 0
    expect(slotAt(0, -100, 60, 130, 0)).toBeNull();
  });

  it("is centred on the avatar and shifted inward at a screen edge", () => {
    const p = ringPlace(box(900, 500, 128), AREA);
    expect(p).toMatchObject({ rIn: 74, side: 300, x: 814, y: 414 });
    expect(ringPlace(box(1800, 950, 128), AREA)).toMatchObject({ x: 1620, y: 740 });
    expect(ringPlace(box(0, 0, 32), AREA).rIn).toBe(56); // never smaller than a usable ring
  });
});

describe("headpat", () => {
  it("needs back-and-forth strokes on the head, not a pass or a rub on the body", () => {
    const s = new Stroke();
    const feed = (xs: number[], y = 10): boolean => xs.some((x) => s.move(x, y, 128));
    expect(feed([10, 30, 50, 70, 90])).toBe(false); // one direction only
    s.reset();
    expect(feed([10, 30, 10, 30, 10])).toBe(true); // three changes of direction
    expect(feed([10, 30, 10, 30, 10], 100)).toBe(false); // below the head
    s.reset();
    expect(feed([10, 12, 10, 12, 10, 12, 10])).toBe(false); // jitter under 6 px is not a stroke
  });
});

describe("click-through shape", () => {
  it("covers exactly the opaque pixels, merging equal runs on consecutive rows", () => {
    const w = 4, h = 3, a = new Uint8ClampedArray(w * h * 4);
    for (const [x, y] of [[1, 0], [2, 0], [1, 1], [2, 1], [0, 2], [3, 2]] as [number, number][]) a[(y * w + x) * 4 + 3] = 255;
    expect(opaqueRects(a, w, h)).toEqual([{ x: 1, y: 0, width: 2, height: 2 }, { x: 0, y: 2, width: 1, height: 1 }, { x: 3, y: 2, width: 1, height: 1 }]);
    expect(opaqueRects(new Uint8ClampedArray(16), 2, 2)).toEqual([]);
  });
});

describe("skins", () => {
  it("reads a manifest, reports problems and never throws", () => {
    const m = parseManifest({ name: "mine", size: [48, 48], fallback: "idle", states: {
      idle: { frames: ["idle0.png", "idle1.png"], fps: 5 }, success: { frames: ["ok.png"], fps: 1, loop: false }, thinking: { gif: "t.gif" },
      working: { frames: ["ok.png"], fps: 999 }, dancing: { frames: ["ok.png"] }, error: { frames: [] }, listening: { builtin: "listening" }, sleeping: null } }, "folder");
    expect(m).toMatchObject({ name: "mine", size: 48, fallback: "idle" });
    expect(m.states.idle).toEqual({ fps: 5, loop: true, frames: ["idle0.png", "idle1.png"] });
    expect(m.states.success).toMatchObject({ loop: false });
    expect(m.states.thinking).toMatchObject({ gif: "t.gif" });
    expect(m.states.listening).toMatchObject({ builtin: "listening" });
    expect(Object.keys(m.states).sort()).toEqual(["idle", "listening", "success", "thinking"]);
    expect(m.problems.join(" | ")).toMatch(/working: fps.*dancing.*error: no frames|dancing/);
    expect(m.problems).toHaveLength(3);
    expect(parseManifest({}, "folder")).toMatchObject({ name: "folder", size: 128, states: {} });
    expect(parseManifest({ size: [9999] }, "x").size).toBe(512);
  });

  it("falls back from a missing state to the fallback state, then idle, then the built-in look", () => {
    expect(pickState({ idle: "I", error: "E" }, "thinking", "error")).toBe("E");
    expect(pickState({ idle: "I" }, "thinking", "error")).toBe("I");
    expect(pickState({}, "thinking", "idle")).toBeUndefined();
  });
});
