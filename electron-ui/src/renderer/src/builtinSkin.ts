/** The default skin: a round blob whose face, colour and accessory depend on the state. Drawn in code (no image files shipped). */
import { SKIN_BASE } from "../../shared/avatar";
import { AVATAR_INK, STATE } from "../../shared/theme";

export const BUILTIN_FRAMES = 6;
const FPS: Record<string, number> = { sleeping: 2, idle: 3, working: 8, thinking: 5, speaking: 8 };
export const builtinFps = (state: string): number => FPS[state] ?? 4;

// Value-scaled colour, like Qt's QColor.lighter()/darker(): factor 130 = 30 % lighter, 115 = 1/1.15 as bright.
function shade(hex: string, factor: number, darker = false): string {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let v = max, s = max ? (max - min) / max : 0;
  v = darker ? v / (factor / 100) : v * (factor / 100);
  if (v > 1) {
    s = Math.max(0, s - (v - 1)); // too bright to scale further: wash the colour out instead
    v = 1;
  }
  const scale = (c: number): number => Math.round(255 * (max ? v * (1 - s * (max === min ? 0 : (max - c) / (max - min))) : v));
  return `rgb(${scale(r)}, ${scale(g)}, ${scale(b)})`;
}

const PT = 96 / 72; // the design is in points

/** Draw frame `frame` (0..5) of `state` into a `size` x `size` square at the context's origin. */
export function drawBuiltin(ctx: CanvasRenderingContext2D, state: string, frame: number, size = SKIN_BASE): void {
  const n = BUILTIN_FRAMES, wave = Math.sin((frame / n) * 2 * Math.PI);
  const bob = wave * (state !== "sleeping" ? 1.5 : 3.0);
  const col = STATE[state] ?? STATE.idle!;
  // Qt measures angles counter-clockwise from 3 o'clock; a canvas measures them clockwise with y pointing down.
  const arc = (x: number, y: number, w: number, h: number, start: number, span: number): void => {
    ctx.beginPath();
    ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, (-start * Math.PI) / 180, (-(start + span) * Math.PI) / 180, span > 0);
    ctx.stroke();
  };
  const line = (x1: number, y1: number, x2: number, y2: number): void => {
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
  };
  const dot = (cx: number, cy: number, rx: number, ry = rx, fill = true, stroke = true): void => {
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, 0, 0, 2 * Math.PI);
    if (fill) ctx.fill();
    if (stroke) ctx.stroke();
  };
  ctx.save();
  ctx.scale(size / SKIN_BASE, size / SKIN_BASE);
  ctx.lineCap = "round";

  const g = ctx.createRadialGradient(54, 52 + bob, 0, 54, 52 + bob, 70);
  g.addColorStop(0, shade(col, 130));
  g.addColorStop(1, shade(col, 115, true));
  ctx.fillStyle = g;
  ctx.strokeStyle = shade(col, 160, true);
  ctx.lineWidth = 2.5;
  dot(64, 71 + bob, 40, 37);

  ctx.strokeStyle = ctx.fillStyle = AVATAR_INK;
  ctx.lineWidth = 3.2;
  const eyeY = 64 + bob;
  const blink = frame === n - 1 && ["idle", "listening", "speaking"].includes(state);
  for (const ex of [46, 82]) {
    if (state === "sleeping" || blink) line(ex - 6, eyeY, ex + 6, eyeY);
    else if (state === "error") {
      line(ex - 5, eyeY - 5, ex + 5, eyeY + 5);
      line(ex - 5, eyeY + 5, ex + 5, eyeY - 5);
    } else if (state === "success") arc(ex - 6, eyeY - 4, 12, 10, 0, 180);
    else {
      const r = state === "listening" || state === "awaiting_approval" ? 6.5 : 5.0;
      dot(ex + (state === "thinking" || state === "working" ? wave * 2 : 0), eyeY, r);
    }
  }
  const my = 86 + bob;
  if (state === "speaking") dot(64, my + 2, 7, 3 + (frame % 3) * 3, false);
  else if (["success", "idle", "listening"].includes(state)) arc(52, my - 6, 24, 14, 200, 140);
  else if (state === "error" || state === "awaiting_approval") arc(54, my, 20, 10, 20, 140);
  else if (state === "sleeping") line(58, my + 2, 70, my + 2);
  else line(56, my + 1, 72, my + 1);

  const accent = shade(col, 130, true);
  ctx.strokeStyle = ctx.fillStyle = accent;
  ctx.lineWidth = 3;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  if (state === "thinking") {
    for (let i = 0; i < 3; i++) dot(50 + i * 14, 20, 4, 4, Math.floor(frame / 2) % 3 === i);
  } else if (state === "working") arc(50, 4, 28, 28, (frame / n) * 360, 270);
  else if (state === "awaiting_approval") {
    ctx.font = `bold ${20 * PT}px Bahnschrift, "Segoe UI", sans-serif`;
    ctx.fillText("!", 65, 15 + (frame % 2) * 2);
  } else if (state === "listening") {
    for (const k of [0, 1]) arc(100 + k * 6, 56 - k * 4, 12 + k * 4, 24 + k * 8, -60, 120);
  } else if (state === "sleeping") {
    ctx.font = `bold ${(12 + (frame % 3) * 2) * PT}px Bahnschrift, "Segoe UI", sans-serif`;
    ctx.fillText("z", 101, 19);
  } else if (state === "success") {
    for (const dx of [-34, 34]) dot(64 + dx, 28 + (frame % 2) * 3, 3);
  }
  ctx.restore();
}
