/** A window's view of the app: the session (same reducer as the main process), UI state and the daemon's settings. */
import { useSyncExternalStore } from "react";
import type { AgentApi } from "../../preload";
import { setLanguage } from "../../shared/i18n";
import { type Ev, Session } from "../../shared/session";
import { applyTheme, DEFAULT_HIGHLIGHT } from "../../shared/theme";

declare global {
  interface Window {
    agent: AgentApi;
  }
}

export type Thread = { id: string; title: string; updated: number; pinned: boolean; count: number; running: boolean; pending: boolean };
export type Ui = { connected: boolean; dark: boolean; highlight: string; lang: string; railCollapsed: boolean; page: string; hotkeyProblems: Record<string, string>; recording: boolean; speaking: boolean };

class Store {
  session = new Session();
  ui: Ui = { connected: false, dark: false, highlight: DEFAULT_HIGHLIGHT, lang: "en", railCollapsed: false, page: "chat", hotkeyProblems: {}, recording: false, speaking: false };
  settings: Ev | null = null;
  threads: Thread[] = []; // the conversation list, newest first, pinned on top
  version = 0;
  private listeners = new Set<() => void>();
  private eventListeners = new Set<(ev: Ev) => void>();

  async start(): Promise<void> {
    const early: Ev[] = [];
    let ready = false;
    window.agent.onEvent((ev) => (ready ? this.onEvent(ev) : early.push(ev)));
    const { snapshot, seq, settings, ...ui } = await window.agent.init();
    this.session = Session.from(snapshot);
    this.settings = settings;
    this.setUi(ui as Ui);
    ready = true;
    for (const ev of early) if (ev._seq > seq) this.onEvent(ev); // older ones are already inside the snapshot
    this.bump();
  }

  /** For windows without a session (the companion ones): take the UI state and re-render. */
  applyUi(ui: Record<string, any>): void {
    const { type: _t, _seq, ...rest } = ui;
    this.setUi(rest);
    this.bump();
  }

  private setUi(ui: Partial<Ui>): void {
    this.ui = { ...this.ui, ...ui };
    setLanguage(this.ui.lang);
    applyTheme(document, this.ui.dark, this.ui.highlight);
  }

  private onEvent(ev: Ev): void {
    if (ev.type === "_ui") {
      const { type: _t, _seq, ...ui } = ev;
      this.setUi(ui);
    } else if (ev.type === "_page") this.setUi({ page: ev.page });
    else if (ev.type === "settings") this.settings = ev;
    else if (ev.type === "threads") this.threads = ev.items ?? [];
    else if (ev.type === "_thread") this.session = Session.from(ev.snapshot); // the user switched conversation
    this.session.apply(ev);
    for (const l of this.eventListeners) l(ev);
    this.bump();
  }

  private bump(): void {
    this.version += 1;
    for (const l of this.listeners) l();
  }

  subscribe = (l: () => void): (() => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };

  /** Raw daemon events, for pages that keep their own lists (manager pages, settings). */
  onDaemonEvent(l: (ev: Ev) => void): () => void {
    this.eventListeners.add(l);
    return () => this.eventListeners.delete(l);
  }
}

export const store = new Store();

/** Re-renders the component on every change; read `store.session`, `store.ui`, `store.settings` directly. */
export function useStore(): Store {
  useSyncExternalStore(store.subscribe, () => store.version);
  return store;
}
