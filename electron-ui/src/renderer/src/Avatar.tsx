/** The avatar window's page: draws the current animation frame and reports its size and opaque area, so the window is exactly as big
 * as the picture and lets clicks through wherever it is transparent. Light by design: the frame timer only runs while the
 * current animation has more than one frame; there is no polling. */
import { useEffect, useRef } from "react";
import { type Attachment, checkSize, mimeOf } from "../../shared/attach";
import { DOUBLE_CLICK_MS, DRAG_PX, MAX_FRAMES, opaqueRects, PAT, pickState, SKIN_BASE, Stroke } from "../../shared/avatar";
import type { Ev } from "../../shared/session";
import { AVATAR_INK, STATE } from "../../shared/theme";
import { BUILTIN_FRAMES, builtinFps, drawBuiltin } from "./builtinSkin";

type Frame = HTMLCanvasElement | ImageBitmap | HTMLImageElement;
type Anim = { frames: Frame[]; fps: number; loop: boolean };
type Skin = { name: string; size: number; fallback: string; anims: Record<string, Anim | undefined> };
const post = (name: string, ...args: unknown[]): Promise<any> => window.agent.overlay(name, ...args);
const sizeOf = (f: Frame): [number, number] => ("naturalWidth" in f ? [f.naturalWidth, f.naturalHeight] : [f.width, f.height]);
const bytes = (dataUrl: string): Uint8Array => Uint8Array.from(atob(dataUrl.split(",")[1] ?? ""), (c) => c.charCodeAt(0));

function builtinAnim(state: string): Anim {
  const frames = Array.from({ length: BUILTIN_FRAMES }, (_, i) => {
    const c = document.createElement("canvas");
    c.width = c.height = SKIN_BASE * 2; // drawn at twice the size, so it stays sharp when scaled up
    drawBuiltin(c.getContext("2d")!, state, i, SKIN_BASE * 2);
    return c;
  });
  return { frames, fps: builtinFps(state), loop: true };
}

function image(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("cannot read a frame"));
    img.src = url;
  });
}

async function gifFrames(url: string): Promise<{ frames: Frame[]; fps: number }> {
  const dec = new (window as any).ImageDecoder({ data: bytes(url), type: "image/gif" }); // a plain <img> cannot be stepped frame by frame
  await dec.tracks.ready;
  const n = Math.min(dec.tracks.selectedTrack.frameCount, MAX_FRAMES), frames: Frame[] = [];
  let delayMs = 100;
  for (let i = 0; i < n; i++) {
    const { image: vf } = await dec.decode({ frameIndex: i });
    if (i === 0 && vf.duration) delayMs = vf.duration / 1000;
    frames.push(await createImageBitmap(vf));
    vf.close();
  }
  return { frames, fps: 1000 / Math.max(delayMs, 40) };
}

async function loadSkin(): Promise<Skin> {
  const s = await post("skin");
  const anims: Record<string, Anim | undefined> = {};
  for (const [state, spec] of Object.entries((s.states ?? {}) as Record<string, any>)) {
    try {
      if ("builtin" in spec) anims[state] = builtinAnim(String(spec.builtin));
      else if ("gif" in spec) {
        const g = await gifFrames(spec.gif);
        anims[state] = { frames: g.frames, fps: spec.fps ?? g.fps, loop: spec.loop };
      } else anims[state] = { frames: await Promise.all((spec.frames as string[]).map(image)), fps: spec.fps ?? 6, loop: spec.loop };
    } catch {
      /* an unreadable state falls back like a missing one */
    }
  }
  return { name: s.name, size: s.size ?? SKIN_BASE, fallback: s.fallback ?? "idle", anims };
}

class View {
  private cfg: Ev = { state: "idle", scale: 1 };
  private skin: Skin = { name: "default", size: SKIN_BASE, fallback: "idle", anims: {} };
  private skinKey = "";
  private builtin = new Map<string, Anim>();
  private shown = "";
  private frame = 0;
  private timer: ReturnType<typeof setInterval> | undefined;
  private headpat = false;
  private lastShape = "";
  private mask = document.createElement("canvas");
  private off: Array<() => void> = [];
  private n = 0;

  constructor(private canvas: HTMLCanvasElement) {
    this.off.push(window.agent.onEvent((ev) => void (ev.type === "_avatar" && this.apply(ev))));
    this.pointer();
    void post("ready");
  }

  dispose(): void {
    clearInterval(this.timer);
    this.off.forEach((f) => f());
  }

  private async apply(ev: Ev): Promise<void> {
    this.cfg = ev;
    this.n = ev._n;
    const key = `${ev.skin}#${ev.skinRev}`;
    if (key !== this.skinKey) {
      this.skinKey = key;
      const skin = await loadSkin();
      if (key !== this.skinKey) return; // a newer skin was asked for meanwhile
      this.skin = skin;
      this.shown = "";
    }
    this.show();
  }

  private anim(name: string): Anim {
    const custom = pickState(this.skin.anims, name, this.skin.fallback);
    if (custom?.frames.length) return custom;
    if (!this.builtin.has(name)) this.builtin.set(name, builtinAnim(name));
    return this.builtin.get(name)!;
  }

  // What is on screen: the agent's state, or a short pose (headpat here, walking from the main process).
  private show(): void {
    const name = this.headpat ? "headpat" : (this.cfg.pose ?? this.cfg.state);
    if (name !== this.shown) {
      this.shown = name;
      this.frame = 0;
      clearInterval(this.timer);
      const a = this.anim(name);
      if (a.frames.length > 1) this.timer = setInterval(() => this.tick(), 1000 / a.fps);
    }
    this.draw();
  }

  private tick(): void {
    const a = this.anim(this.shown), last = a.frames.length - 1;
    if (this.frame >= last && !a.loop) return clearInterval(this.timer);
    this.frame = (this.frame + 1) % a.frames.length;
    this.draw();
  }

  private draw(): void {
    const a = this.anim(this.shown), src = a.frames[Math.min(this.frame, a.frames.length - 1)]!;
    const box = Math.max(16, Math.round(this.skin.size * Number(this.cfg.scale || 1)));
    const [sw, sh] = sizeOf(src), fit = Math.min(box / sw, box / sh);
    const w = Math.max(1, Math.round(sw * fit)), h = Math.max(1, Math.round(sh * fit));
    const paint = (c: HTMLCanvasElement, px: number): void => {
      c.width = Math.round(w * px);
      c.height = Math.round(h * px);
      const ctx = c.getContext("2d", { willReadFrequently: c === this.mask })!;
      ctx.scale(px, px);
      ctx.save();
      if (this.cfg.right) { // skin art faces left: mirror it while moving right
        ctx.translate(w, 0);
        ctx.scale(-1, 1);
      }
      ctx.drawImage(src, 0, 0, w, h);
      ctx.restore();
      if (this.cfg.watching) { // red dot while continuous screen watching is on (part of the picture, so also part of the hit area)
        ctx.fillStyle = STATE.error!;
        ctx.strokeStyle = AVATAR_INK;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(w - 13, 13, 7, 0, 2 * Math.PI);
        ctx.fill();
        ctx.stroke();
      }
    };
    paint(this.canvas, window.devicePixelRatio || 1);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    const key = [this.skinKey, this.shown, this.frame, w, h, this.cfg.watching, this.cfg.right].join("|");
    if (key === this.lastShape) return;
    this.lastShape = key;
    paint(this.mask, 1); // the hit area is in window points, whatever the display scaling
    void post("shape", w, h, opaqueRects(this.mask.getContext("2d")!.getImageData(0, 0, w, h).data, w, h));
    void post("laidOut", this.n, { width: w, height: h });
  }

  // ---- mouse: click / double-click / drag / menu / headpat / dropped files ------------------------------------------------------------
  private pointer(): void {
    const c = this.canvas, stroke = new Stroke();
    let press: { x: number; y: number } | null = null, dragging = false, lastX = 0;
    let click: ReturnType<typeof setTimeout> | undefined, strokeIdle: ReturnType<typeof setTimeout> | undefined, patEnd: ReturnType<typeof setTimeout> | undefined;
    c.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      press = { x: e.screenX, y: e.screenY };
      lastX = e.screenX;
      dragging = false;
      c.setPointerCapture(e.pointerId);
      void post("press");
    });
    c.addEventListener("pointermove", (e) => {
      if (!press) {
        if (e.buttons !== 0 || this.headpat || !["idle", "sleeping"].includes(this.cfg.state)) return stroke.reset();
        clearTimeout(strokeIdle);
        if (stroke.move(e.offsetX, e.offsetY, c.clientHeight)) {
          this.headpat = true;
          void post("headpat");
          this.show();
          patEnd = setTimeout(() => {
            this.headpat = false;
            this.show();
            void post("headpatEnd");
          }, PAT.ms);
        } else strokeIdle = setTimeout(() => stroke.reset(), PAT.idleMs); // a pause cancels a half-done stroke
        return;
      }
      if (!dragging && Math.abs(e.screenX - press.x) + Math.abs(e.screenY - press.y) >= DRAG_PX) {
        dragging = true;
        clearTimeout(click);
        void post("dragStart");
      }
      if (dragging) {
        if (e.screenX !== lastX && e.screenX > lastX !== Boolean(this.cfg.right)) {
          this.cfg = { ...this.cfg, right: e.screenX > lastX };
          this.draw();
        }
        lastX = e.screenX;
        void post("dragMove");
      }
    });
    c.addEventListener("pointerup", (e) => {
      if (e.button !== 0 || !press) return;
      press = null;
      c.releasePointerCapture(e.pointerId);
      if (dragging) {
        dragging = false;
        void post("dragEnd");
      } else click = setTimeout(() => void post("click"), DOUBLE_CLICK_MS); // wait out the double-click interval
    });
    c.addEventListener("dblclick", () => {
      clearTimeout(click);
      void post("dblclick");
    });
    c.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      clearTimeout(patEnd);
      this.headpat = false;
      void post("menu");
    });
    const files = (e: DragEvent): boolean => Boolean(e.dataTransfer?.types.includes("Files"));
    c.addEventListener("dragover", (e) => files(e) && e.preventDefault());
    c.addEventListener("drop", (e) => {
      if (!files(e)) return;
      e.preventDefault();
      void Promise.all([...e.dataTransfer!.files].map(readFile)).then((list) => post("drop", list.filter(Boolean)));
    });
  }
}

function readFile(f: File): Promise<Attachment | null> {
  return new Promise((resolve) => {
    try {
      checkSize(f.name, f.size);
    } catch {
      return resolve(null);
    }
    const r = new FileReader();
    r.onerror = () => resolve(null);
    r.onload = () => resolve({ name: f.name, mime: mimeOf(f.name, f.type), data: String(r.result).split(",")[1] ?? "" });
    r.readAsDataURL(f);
  });
}

export function Avatar(): React.JSX.Element {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const view = new View(canvas.current!);
    return () => view.dispose();
  }, []);
  return <canvas ref={canvas} className="avatar" />;
}
