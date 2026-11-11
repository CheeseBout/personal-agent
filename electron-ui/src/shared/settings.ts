/** The Settings form as plain data: built from the daemon's `settings` event, saved as a patch holding only what changed. Pure, tested. */
import type { Ev } from "./session";
import { DEFAULT_HIGHLIGHT } from "./theme";

export type Form = {
  name: string; uiLang: string; agentLang: string;
  ui: { avatar_enabled: boolean; avatar_always_on_top: boolean; avatar_wander: boolean; avatar_scale: number; skin: string; highlight: string };
  models: Record<string, { provider: string; model: string }>;
  stt: { provider: string; model: string };
  tts: { enabled: boolean; voice_vi: string; voice_en: string; rate: string };
  budgets: { daily_max_tokens: number; daily_max_cost_usd: number };
  screen: { agent_view: boolean; control: boolean; block_titles: string; block_processes: string; watch_interval_s: number;
    watch_change_threshold: number; watch_max_minutes: number; max_actions: number } | null;
};

export const PROVIDER_KEYS: Record<string, string> = { anthropic: "ANTHROPIC_API_KEY", google: "GOOGLE_API_KEY", openai: "OPENAI_API_KEY",
  openrouter: "OPENROUTER_API_KEY" };
export const hasProviderKey = (ev: Ev | null): boolean => Object.values(PROVIDER_KEYS).some((k) => ev?.secrets?.[k]);

const lines = (text: string): string[] => text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
const round = (v: number, d: number): number => Math.round(v * 10 ** d) / 10 ** d;

export function formFrom(ev: Ev): Form {
  const { ui, voice, screen: sc } = ev;
  return {
    name: ev.agent.name, uiLang: ui.language, agentLang: ev.agent.language,
    ui: { avatar_enabled: ui.avatar_enabled, avatar_always_on_top: ui.avatar_always_on_top, avatar_wander: ui.avatar_wander, avatar_scale: ui.avatar_scale,
      skin: ui.skin, highlight: ui.highlight ?? DEFAULT_HIGHLIGHT },
    models: Object.fromEntries(Object.entries(ev.models as Record<string, any>).map(([r, m]) => [r, { provider: m.provider, model: m.model }])),
    stt: { provider: voice.stt.provider, model: voice.stt.model },
    tts: { enabled: voice.tts.enabled, voice_vi: voice.tts.voice_vi, voice_en: voice.tts.voice_en, rate: voice.tts.rate },
    budgets: { daily_max_tokens: Number(ev.budgets.daily_max_tokens), daily_max_cost_usd: Number(ev.budgets.daily_max_cost_usd) },
    screen: sc ? { agent_view: sc.agent_view, control: sc.control, block_titles: sc.block_titles.join("\n"), block_processes: sc.block_processes.join("\n"),
      watch_interval_s: sc.watch_interval_s, watch_change_threshold: sc.watch_change_threshold, watch_max_minutes: sc.watch_max_minutes,
      max_actions: sc.max_actions } : null,
  };
}

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

// Only the keys whose value differs from what the daemon has.
function diff(now: Record<string, unknown>, before: Record<string, unknown>): Record<string, unknown> | null {
  const ch = Object.fromEntries(Object.entries(now).filter(([k, v]) => !same(v, before[k])));
  return Object.keys(ch).length ? ch : null;
}

/** What Save sends: `patch` for settings_set (null = nothing changed) and `name` when the agent was renamed. */
export function settingsPatch(ev: Ev, f: Form): { patch: Record<string, any> | null; name: string | null } {
  const patch: Record<string, any> = {};
  const models: Record<string, unknown> = {};
  for (const [role, m] of Object.entries(f.models)) {
    const ch = diff({ provider: m.provider, model: m.model.trim() }, ev.models[role] ?? {});
    if (ch) models[role] = ch;
  }
  if (Object.keys(models).length) patch.models = models;
  if (f.agentLang !== ev.agent.language) patch.agent = { language: f.agentLang };
  const ui = diff({ language: f.uiLang, ...f.ui, avatar_scale: round(f.ui.avatar_scale, 2) }, { highlight: DEFAULT_HIGHLIGHT, ...ev.ui });
  if (ui) patch.ui = ui;
  const voice: Record<string, unknown> = {};
  const stt = diff({ provider: f.stt.provider, model: f.stt.model.trim() }, ev.voice.stt);
  const tts = diff({ enabled: f.tts.enabled, voice_vi: f.tts.voice_vi.trim(), voice_en: f.tts.voice_en.trim(), rate: f.tts.rate.trim() }, ev.voice.tts);
  if (stt) voice.stt = stt;
  if (tts) voice.tts = tts;
  if (Object.keys(voice).length) patch.voice = voice;
  const caps = diff({ daily_max_tokens: Math.round(f.budgets.daily_max_tokens), daily_max_cost_usd: round(f.budgets.daily_max_cost_usd, 2) }, ev.budgets);
  if (caps) patch.budgets = caps;
  if (f.screen && ev.screen) {
    const sc = diff({ ...f.screen, block_titles: lines(f.screen.block_titles), block_processes: lines(f.screen.block_processes),
      watch_interval_s: round(f.screen.watch_interval_s, 1) }, ev.screen);
    if (sc) patch.screen = sc;
  }
  const name = f.name.trim();
  return { patch: Object.keys(patch).length ? patch : null, name: name !== ev.agent.name ? name : null };
}
