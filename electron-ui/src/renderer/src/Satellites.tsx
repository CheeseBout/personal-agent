/** The avatar's small companion windows: speech bubble, quick-input box, radial menu, target highlight, "watching" badge and the
 * region selector. Each page shows what the main process posts to it and reports back what the user did; none of them holds agent state. */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { type Attachment, roomFor } from "../../shared/attach";
import { type RadialItem, RING_MARGIN, RING_THICK, slotAt } from "../../shared/avatar";
import { tr } from "../../shared/i18n";
import type { Ev } from "../../shared/session";
import { onColor } from "../../shared/theme";
import { Icon } from "./Icon";
import { store, useStore } from "./store";

const post = (name: string, ...args: unknown[]): Promise<any> => window.agent.overlay(name, ...args);

/** The latest event of `type` posted to this window (null until the first one). Tells the main process when the page is ready. */
function usePosted(type: string): Ev | null {
  const [ev, setEv] = useState<Ev | null>(null);
  useEffect(() => {
    const off = window.agent.onEvent((e) => void (e.type === type && setEv(e)));
    void post("ready");
    return off;
  }, [type]);
  return ev;
}

/** After the content was drawn: tell the main process how big it is, so the window can be sized, placed and shown. */
function useLaidOut(ev: Ev | null, size: () => { width: number; height: number }): void {
  useLayoutEffect(() => {
    if (ev) void post("laidOut", ev._n, size());
  });
}

const measure = (el: HTMLElement | null): { width: number; height: number } => {
  const r = el?.getBoundingClientRect();
  return { width: Math.ceil(r?.width ?? 0), height: Math.ceil(r?.height ?? 0) };
};

export function Bubble(): React.JSX.Element {
  const ev = usePosted("_bubble"), el = useRef<HTMLDivElement>(null);
  useLaidOut(ev, () => measure(el.current));
  return <div ref={el} className="bubble" onMouseDown={() => void post("click")}>{ev?.text ?? ""}</div>;
}

const IDLE_CLOSE_MS = 5000; // the quick box closes itself after this long if nothing is typed

/** Small always-on-top box opened by clicking the avatar. */
export function Quick(): React.JSX.Element {
  const ev = usePosted("_quick");
  const [text, setText] = useState("");
  const [files, setFiles] = useState<Attachment[]>([]);
  const box = useRef<HTMLTextAreaElement>(null);
  const live = useRef({ text, files });
  live.current = { text, files };
  useLaidOut(ev, () => ({ width: 320, height: 96 }));
  useEffect(() => { // opened (again), perhaps with files dropped on the avatar
    if (!ev) return;
    setFiles((cur) => (ev.files as Attachment[]).reduce((all, f) => (roomFor(all, f) ? [...all, f] : all), cur));
    box.current?.focus();
  }, [ev]);
  useEffect(() => { // nothing typed and nothing attached: close by itself
    if (!ev || text || files.length) return;
    const t = setTimeout(() => void post("hide"), IDLE_CLOSE_MS);
    return () => clearTimeout(t);
  }, [ev, text, files.length]);
  useEffect(() => window.agent.onEvent((e) => { // the avatar is being dragged: get out of the way unless the user has started something
    if (e.type === "_quickCloseIfEmpty" && !live.current.text.trim() && !live.current.files.length) void post("hide");
  }), []);

  const send = (): void => {
    const t = text.trim();
    if (!t && !files.length) return;
    void post("submit", t, files);
    setText("");
    setFiles([]);
  };
  return (
    <div className="quick">
      <div className="hint">{files.map((f) => f.name).join("  ")}</div>
      <textarea ref={box} value={text} onChange={(e) => setText(e.target.value)}
        placeholder={tr("Ask anything… (Enter to send, Esc to close)", "Hỏi gì cũng được… (Enter gửi, Esc đóng)")}
        onKeyDown={(e) => {
          if (e.key === "Escape") void post("hide");
          else if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }} />
    </div>
  );
}

const GAP_DEG = 2; // visual gap between slots

/** A ring of slots around the avatar. A slot runs an action, opens a sub-ring (its last slot goes back) or, for a dangerous
 * action, needs a second click. Mouse, number keys 1-9 and the arrow keys + Enter all work. */
export function Radial(): React.JSX.Element | null {
  const ev = usePosted("_radial");
  useStore(); // theme tokens
  const [stack, setStack] = useState<RadialItem[][]>([]);
  const [armed, setArmed] = useState<RadialItem | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const back: RadialItem = { label: tr("Back", "Quay lại"), glyph: "◀" };
  useEffect(() => {
    setStack(ev ? [ev.items] : []);
    setArmed(null);
    setHover(null);
  }, [ev]);
  useLaidOut(ev, () => ({ width: ev!.side, height: ev!.side }));

  const items = stack.length > 1 ? [...stack.at(-1)!, back] : (stack.at(-1) ?? []);
  const n = items.length, rIn: number = ev?.rIn ?? 60, rOut = rIn + RING_THICK, c = rOut + RING_MARGIN;
  const choose = (i: number): void => {
    const item = items[i];
    if (!item) return;
    if (item !== armed) setArmed(null);
    setHover(null);
    if (item === back) setStack(stack.slice(0, -1));
    else if (item.children) setStack([...stack, item.children]);
    else if (item.danger && armed !== item) setArmed(item); // the first click only arms it
    else void post("choose", item.id);
  };
  const live = useRef({ n, hover, choose, depth: stack.length });
  live.current = { n, hover, choose, depth: stack.length };
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const s = live.current;
      if (e.key === "Escape") s.depth > 1 ? s.choose(s.n - 1) : void post("close");
      else if (/^[1-9]$/.test(e.key) && Number(e.key) <= s.n) s.choose(Number(e.key) - 1); // number keys pick a slot, clockwise from the top
      else if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(e.key) && s.n) {
        const fwd = e.key === "ArrowRight" || e.key === "ArrowDown";
        setHover(s.hover === null ? (fwd ? 0 : s.n - 1) : (s.hover + (fwd ? 1 : -1) + s.n) % s.n);
      } else if (["Enter", " "].includes(e.key) && s.hover !== null) s.choose(s.hover);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  if (!ev || !n) return null;

  const at = (r: number, deg: number): [number, number] => [c + r * Math.sin((deg * Math.PI) / 180), c - r * Math.cos((deg * Math.PI) / 180)];
  const step = 360 / n;
  const sector = (i: number): string => {
    const a0 = i * step - step / 2 + GAP_DEG / 2, a1 = i * step + step / 2 - GAP_DEG / 2, big = a1 - a0 > 180 ? 1 : 0;
    const [o0, o1, i1, i0] = [at(rOut, a0), at(rOut, a1), at(rIn, a1), at(rIn, a0)];
    return `M${o0} A${rOut} ${rOut} 0 ${big} 1 ${o1} L${i1} A${rIn} ${rIn} 0 ${big} 0 ${i0} Z`;
  };
  const slotOf = (e: React.MouseEvent): number | null => {
    const r = e.currentTarget.getBoundingClientRect();
    return slotAt(e.clientX - r.left - c, e.clientY - r.top - c, rIn, rOut, n);
  };
  const t = (name: string): string => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
  return (
    <div className="radial" style={{ width: 2 * c, height: 2 * c }} onMouseMove={(e) => setHover(slotOf(e))}
      onMouseDown={(e) => { // right click, or a click in the hole / outside the ring, closes it
        const i = e.button === 0 ? slotOf(e) : null;
        if (i === null) void post("close");
        else choose(i);
      }} onContextMenu={(e) => e.preventDefault()}>
      <svg width={2 * c} height={2 * c}>
        {items.map((item, i) => {
          const isArmed = item === armed, danger = item.danger || isArmed;
          // the window palette, so the ring matches the app: surface, accent on hover, soft accent when on, red for the dangerous one
          const fill = isArmed ? t("critical") : i === hover ? t("accent") : item.active ? t("accent-soft") : t("surface");
          const edge = isArmed || i === hover ? "none" : item.active ? t("accent") : danger ? t("critical") : t("muted");
          return <path key={i} d={sector(i)} fill={fill} fillOpacity={0.965} stroke={edge} strokeWidth={item.active ? 2 : 1} strokeOpacity={item.active || danger ? 1 : 0.35} />;
        })}
        <circle cx={c} cy={c} r={rIn - 4} fill="none" stroke={t("accent")} strokeOpacity={0.67} strokeWidth={2} />
      </svg>
      {items.map((item, i) => {
        const isArmed = item === armed;
        const color = isArmed ? onColor(t("critical")) : i === hover ? t("on-accent") : item.danger ? t("critical") : t("ink");
        const [x, y] = at(rIn + RING_THICK / 2, i * step), [nx, ny] = at(rOut - 9, i * step);
        const [bx, by] = [x + 30 * Math.sin((i * step * Math.PI) / 180 + 0.5), y - 30 * Math.cos((i * step * Math.PI) / 180 + 0.5)];
        return (
          <div key={i}>
            <div className="slot" style={{ left: x - 40, top: y - 27, color }}>
              <div className="glyph">{item.glyph.startsWith("i:") ? <Icon name={item.glyph.slice(2)} /> : item.glyph}</div>
              <div className="label">{isArmed ? tr("Click again", "Bấm lần nữa") : item.label + (item.children ? " ›" : "")}</div>
            </div>
            {i < 9 && <div className="key" style={{ left: nx - 8, top: ny - 8, color }}>{i + 1}</div>}
            {Boolean(item.badge) && <div className="ringbadge" style={{ left: bx - 10, top: by - 10, background: i === hover ? t("on-accent") : t("accent"),
              color: i === hover ? t("accent") : t("on-accent") }}>{Math.min(item.badge!, 99)}</div>}
          </div>
        );
      })}
    </div>
  );
}

/** Outlines the element the agent is about to touch (the window is click-through and hides itself). */
export function Highlight(): React.JSX.Element {
  const ev = usePosted("_highlight");
  useLaidOut(ev, () => ({ width: 0, height: 0 }));
  return <div className="highlight"><div className="tag">{ev?.label ?? ""}</div><div className="frame" /></div>;
}

/** Always-visible badge while continuous screen watching runs. Click it to stop watching. */
export function Badge(): React.JSX.Element {
  const ev = usePosted("_badge"), el = useRef<HTMLDivElement>(null);
  useLaidOut(ev, () => ({ width: Math.max(180, measure(el.current).width), height: 28 }));
  return (
    <div ref={el} className="watchbadge" title={ev?.tip ?? ""} style={{ borderColor: ev?.color }} onMouseDown={() => void post("click")}>
      <span className="dot" style={{ background: ev?.color }} />{ev?.text ?? ""}
    </div>
  );
}

/** Full-screen dim layer: drag a rectangle, release to capture, Esc to cancel. */
export function Region(): React.JSX.Element {
  const ev = usePosted("_region");
  const [a, setA] = useState<[number, number] | null>(null);
  const [b, setB] = useState<[number, number] | null>(null);
  useLaidOut(ev, () => ({ width: 0, height: 0 }));
  useEffect(() => {
    setA(null);
    setB(null);
  }, [ev]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => void (e.key === "Escape" && post("done", null));
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const rect = a && b ? { x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]), width: Math.abs(a[0] - b[0]), height: Math.abs(a[1] - b[1]) } : null;
  return (
    <div className={"region" + (rect ? " selecting" : "")} onMouseDown={(e) => {
      setA([e.clientX, e.clientY]);
      setB([e.clientX, e.clientY]);
    }} onMouseMove={(e) => a && setB([e.clientX, e.clientY])} onMouseUp={() => {
      void post("done", rect);
      setA(null);
      setB(null);
    }}>
      <div className="tip">{tr("Drag to select an area · Esc to cancel", "Kéo để chọn vùng · Esc để hủy")}</div>
      {rect && <div className="sel" style={{ left: rect.x, top: rect.y, width: rect.width, height: rect.height }} />}
    </div>
  );
}

/** Theme and language for a companion window (it has no session). */
export async function startSatellite(): Promise<void> {
  store.applyUi(await window.agent.overlayInit());
  window.agent.onEvent((ev) => void (ev.type === "_ui" && store.applyUi(ev)));
}
