/** One visual system for every window: colour tokens (light and dark) and everything derived from the ONE highlight colour the user picks.
 * The risk scale and the avatar state colours keep their own fixed meaning. */

export const DEFAULT_HIGHLIGHT = "#3340D9";
// `signal` is the one colour that means "this needs your decision" (approvals); it never follows the highlight.
const LIGHT = { bg: "#E4E9EE", surface: "#FBFCFD", sunken: "#EEF2F5", ink: "#0F1B3D", muted: "#586583", line: "#CBD3DC", signal: "#E8742F",
  low: "#1F7A4D", medium: "#8F6208", high: "#B94A14", critical: "#B1232B", add: "#1F7A4D", del: "#B1232B", hunk: "#2B5CA8" };
const DARK = { bg: "#0B0E14", surface: "#151A24", sunken: "#10141C", ink: "#E9EDF5", muted: "#97A1B7", line: "#272F3F", signal: "#F08A56",
  low: "#58C282", medium: "#E0B04A", high: "#F08A56", critical: "#F2777D", add: "#58C282", del: "#F2777D", hunk: "#7FA6E8" };
// Avatar and overlays float over any desktop, so they use fixed mid-tone colours instead of the window palette.
export const STATE: Record<string, string> = { idle: "#5B8DEF", listening: "#3FB7C9", thinking: "#9B7FE0", working: "#E3A52F",
  speaking: "#4DB87A", awaiting_approval: "#E8742F", success: "#2FB59A", error: "#E0525B", sleeping: "#7C879B" };
export const AVATAR_INK = "#1B2230";

const rgb = (h: string): number[] => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

// Python's round(): halves go to the even neighbour (kept so both UIs derive identical colours).
const roundEven = (v: number): number => (Math.abs(v % 1) === 0.5 ? 2 * Math.round(v / 2) : Math.round(v));

/** `t` of colour `a` over `b` (both #rrggbb). */
export function mix(a: string, b: string, t: number): string {
  const [x, y] = [rgb(a), rgb(b)];
  return "#" + x.map((v, i) => roundEven(v * t + y[i]! * (1 - t)).toString(16).padStart(2, "0")).join("").toUpperCase();
}

function lum(h: string): number {
  const [r, g, b] = rgb(h).map((v) => v / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

/** Text colour (near-black or white) that contrasts more with `h`. */
export const onColor = (h: string): string => ((lum(h) + 0.05) / 0.05 > 1.05 / (lum(h) + 0.05) ? "#111111" : "#FFFFFF");

/** The base palette plus every accent token derived from the user's highlight colour. */
export function tokens(dark: boolean, highlight = DEFAULT_HIGHLIGHT): Record<string, string> {
  const t: Record<string, string> = { ...(dark ? DARK : LIGHT) };
  return { ...t, accent: highlight, accent_hover: mix(highlight, "#000000", 0.82), on_accent: onColor(highlight),
    accent_soft: mix(highlight, t.surface!, dark ? 0.2 : 0.16), accent_text: mix(highlight, t.ink!, 0.6) };
}

/** Install the tokens as CSS variables (--bg, --accent-soft, ...) on a document. */
export function applyTheme(doc: Document, dark: boolean, highlight?: string): void {
  for (const [k, v] of Object.entries(tokens(dark, highlight || DEFAULT_HIGHLIGHT))) doc.documentElement.style.setProperty(`--${k.replace(/_/g, "-")}`, v);
  doc.documentElement.style.colorScheme = dark ? "dark" : "light";
}
