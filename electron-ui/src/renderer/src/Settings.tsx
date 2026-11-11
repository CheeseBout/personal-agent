/** Settings (General, Models, API keys, Voice, Budgets, Screen, About) and the first-run wizard. Everything goes through the daemon. */
import { useEffect, useState } from "react";
import { tr } from "../../shared/i18n";
import type { Ev } from "../../shared/session";
import { type Form, formFrom, hasProviderKey, PROVIDER_KEYS, settingsPatch } from "../../shared/settings";
import { applyTheme, DEFAULT_HIGHLIGHT } from "../../shared/theme";
import { store, useStore } from "./store";

const HIGHLIGHTS: [string, string, string][] = [["#E8742F", "Orange", "Cam"], ["#3340D9", "Ultramarine", "Xanh chàm"], ["#0F9D8A", "Teal", "Xanh ngọc"],
  ["#2C9A5B", "Green", "Xanh lá"], ["#E0407B", "Pink", "Hồng"], ["#7A4DE0", "Violet", "Tím"], ["#E0A21F", "Amber", "Hổ phách"], ["#D64545", "Red", "Đỏ"]];
const KEY_HELP: Record<string, string> = { ANTHROPIC_API_KEY: "Anthropic (Claude)", GOOGLE_API_KEY: "Google (Gemini)", OPENAI_API_KEY: "OpenAI",
  OPENROUTER_API_KEY: "OpenRouter", LANGFUSE_PUBLIC_KEY: "Langfuse public key", LANGFUSE_SECRET_KEY: "Langfuse secret key", LANGFUSE_BASE_URL: "Langfuse URL" };
const TABS: [string, string][] = [["General", "Chung"], ["Models", "Mô hình"], ["API keys", "Khóa API"], ["Voice", "Giọng nói"], ["Budgets", "Ngân sách"],
  ["Screen", "Màn hình"], ["About", "Giới thiệu"]];

const addFolder = async (): Promise<string | null> => {
  const d = await window.agent.pick("folder", tr("Choose a folder the agent may read and write", "Chọn thư mục agent được đọc/ghi"));
  if (d) void window.agent.send({ type: "policy_add_path", path: d });
  return d;
};

/** One connection test per role; the result comes back as a `test_role` event. */
function useRoleTests(): [Record<string, Ev | "…">, (role: string) => void] {
  const [results, setResults] = useState<Record<string, Ev | "…">>({});
  useEffect(() => store.onDaemonEvent((ev) => {
    if (ev.type === "test_role") setResults((r) => ({ ...r, [String(ev.role)]: ev }));
  }), []);
  return [results, (role) => {
    setResults((r) => ({ ...r, [role]: "…" }));
    void window.agent.send({ type: "test_role", role });
  }];
}

function KeyRow({ name, isSet }: { name: string; isSet: boolean }): React.JSX.Element {
  const [value, setValue] = useState("");
  return (
    <>
      <label>{KEY_HELP[name] ?? name}</label>
      <div className="row">
        <span className="keystate">{isSet ? "✔ " + tr("set", "đã có") : "✖ " + tr("not set", "chưa có")}</span>
        <input type="password" className="grow" value={value} onChange={(e) => setValue(e.target.value)} aria-label={KEY_HELP[name] ?? name}
          placeholder={tr("paste a new value to replace it", "dán giá trị mới để thay")} />
        <button className="primary" onClick={() => {
          if (!value.trim()) return;
          void window.agent.send({ type: "secret_set", name, value });
          setValue(""); // the value leaves the UI as soon as it is sent
        }}>{tr("Save key", "Lưu khóa")}</button>
      </div>
    </>
  );
}

function About({ data }: { data: Ev }): React.JSX.Element {
  const [s, setS] = useState<Awaited<ReturnType<typeof window.agent.stats>> | null>(null);
  useEffect(() => { // live CPU / RAM, polled only while this tab is on screen
    const poll = (): void => void window.agent.stats().then(setS);
    poll();
    const t = setInterval(poll, 2000);
    return () => clearInterval(t);
  }, []);
  const line = (x?: { cpu: number; rss_mb: number } | null): string => (x ? `CPU ${x.cpu.toFixed(1)}% · RAM ${x.rss_mb.toFixed(0)} MB` : tr("n/a", "không có"));
  return (
    <div className="form">
      <label>{tr("This window (UI)", "Cửa sổ này (UI)")}</label><span>{line(s?.ui)}</span>
      <label>Daemon</label><span>{line(s?.daemon)}</span>
      <label>Langfuse</label>
      <span>{data.observability?.langfuse_enabled && data.secrets?.LANGFUSE_PUBLIC_KEY ? tr("on", "bật") : tr("off (no keys or disabled)", "tắt (thiếu khóa hoặc bị tắt)")}</span>
    </div>
  );
}

export function Settings(): React.JSX.Element {
  const { settings: data, ui } = useStore();
  const [tab, setTab] = useState(0);
  const [f, setF] = useState<Form | null>(data ? formFrom(data) : null);
  const [autostart, setAutostart] = useState(false);
  const [skins, setSkins] = useState<string[]>(["default"]);
  const [status, setStatus] = useState("");
  const [tests, test] = useRoleTests();

  useEffect(() => { // fresh settings from the daemon replace the form (after a save, a new key, or opening the page)
    if (!data) return;
    setF(formFrom(data));
    void window.agent.autostart().then(setAutostart);
    void window.agent.skins().then(setSkins);
  }, [data]);
  useEffect(() => {
    void window.agent.send({ type: "settings_get" });
    return () => applyTheme(document, store.ui.dark, store.ui.highlight); // left without saving: drop the colour preview
  }, []);

  if (!data || !f) return <p className="muted pad">{tr("Waiting for the agent…", "Đang chờ agent…")}</p>;
  const set = (patch: Partial<Form>): void => setF({ ...f, ...patch });
  const setUi = (p: Partial<Form["ui"]>): void => set({ ui: { ...f.ui, ...p } });
  const setScreen = (p: Partial<NonNullable<Form["screen"]>>): void => set({ screen: { ...f.screen!, ...p } });
  const highlight = (color: string): void => {
    setUi({ highlight: color });
    applyTheme(document, ui.dark, color); // preview now; Save makes it permanent
  };
  const check = (on: boolean, change: (v: boolean) => void, label: string): React.JSX.Element =>
    <input type="checkbox" checked={on} onChange={(e) => change(e.target.checked)} aria-label={label} />;
  const number = (v: number, change: (v: number) => void, min: number, max: number, step: number, label: string): React.JSX.Element =>
    <input type="number" value={v} min={min} max={max} step={step} aria-label={label} onChange={(e) => change(Number(e.target.value))} />;

  async function save(): Promise<void> {
    const { patch, name } = settingsPatch(data!, f!);
    let note = "";
    try {
      if (autostart !== (await window.agent.autostart())) await window.agent.autostart(autostart);
    } catch (e) {
      note = (e as Error).message;
    }
    if (name) void window.agent.send({ type: "set_agent_name", name });
    if (patch) {
      void window.agent.send({ type: "settings_set", patch });
      note ||= tr("Saved.", "Đã lưu.");
    }
    setStatus(note);
  }

  const L = (en: string, vi: string): React.JSX.Element => <label>{tr(en, vi)}</label>;
  const langs = ["auto", "vi", "en"].map((l) => <option key={l}>{l}</option>);
  const problems = Object.entries(ui.hotkeyProblems);
  return (
    <div className="settings">
      <div className="tabs" role="tablist">
        {TABS.map(([en, vi], i) => <button key={i} role="tab" aria-selected={tab === i} className={tab === i ? "tab current" : "tab"} onClick={() => setTab(i)}>{tr(en, vi)}</button>)}
      </div>
      <div className="tabbody">
        {tab === 0 && (
          <div className="form">
            {L("Agent name", "Tên agent")}<input type="text" value={f.name} onChange={(e) => set({ name: e.target.value })} aria-label={tr("Agent name", "Tên agent")} />
            {L("Interface language", "Ngôn ngữ giao diện")}<select value={f.uiLang} onChange={(e) => set({ uiLang: e.target.value })}>{langs}</select>
            {L("Agent reply language", "Ngôn ngữ agent trả lời")}<select value={f.agentLang} onChange={(e) => set({ agentLang: e.target.value })}>{langs}</select>
            {L("Start with Windows", "Khởi động cùng Windows")}{check(autostart, setAutostart, tr("Start with Windows", "Khởi động cùng Windows"))}
            {L("Show avatar", "Hiện avatar")}{check(f.ui.avatar_enabled, (v) => setUi({ avatar_enabled: v }), tr("Show avatar", "Hiện avatar"))}
            {L("Avatar always on top", "Avatar luôn ở trên")}{check(f.ui.avatar_always_on_top, (v) => setUi({ avatar_always_on_top: v }), tr("Avatar always on top", "Avatar luôn ở trên"))}
            {L("Avatar wanders when idle", "Avatar đi dạo khi rảnh")}{check(f.ui.avatar_wander, (v) => setUi({ avatar_wander: v }), tr("Avatar wanders when idle", "Avatar đi dạo khi rảnh"))}
            {L("Avatar size", "Kích thước avatar")}{number(f.ui.avatar_scale, (v) => setUi({ avatar_scale: v }), 0.5, 3, 0.1, tr("Avatar size", "Kích thước avatar"))}
            {L("Skin", "Skin")}
            <select value={f.ui.skin} onChange={(e) => setUi({ skin: e.target.value })}>{[...new Set([...skins, f.ui.skin])].map((s) => <option key={s}>{s}</option>)}</select>
            {L("Highlight color", "Màu nổi bật")}
            <div className="row">
              {HIGHLIGHTS.map(([col, en, vi]) => ( // a colour name, not the hex code, is what a screen reader should say
                <button key={col} className={"swatch" + (f.ui.highlight.toLowerCase() === col.toLowerCase() ? " current" : "")} style={{ background: col }}
                  title={`${tr(en, vi)} (${col})`} aria-label={tr(en, vi)} aria-pressed={f.ui.highlight.toLowerCase() === col.toLowerCase()} onClick={() => highlight(col)} />
              ))}
              <label className="btn">{tr("Custom…", "Tùy chọn…")}
                <input type="color" hidden value={f.ui.highlight} onChange={(e) => highlight(e.target.value.toUpperCase())} /></label>
              <button onClick={() => highlight(DEFAULT_HIGHLIGHT)}>{tr("Reset", "Đặt lại")}</button>
            </div>
            {L("Folders the agent may use", "Thư mục agent được dùng")}<div><button onClick={() => void addFolder()}>{tr("Add a folder…", "Thêm thư mục…")}</button></div>
            {L("Hotkeys", "Phím tắt")}
            <div>
              <span>{Object.entries(data.ui.hotkeys ?? {}).map(([k, v]) => `${k}: ${v}`).join("   ")}</span>
              {problems.map(([n, why]) => <p key={n} className="warn">{tr("Hotkey '{n}' unavailable: {w}", "Phím tắt '{n}' không dùng được: {w}", { n, w: why })}</p>)}
            </div>
          </div>
        )}
        {tab === 1 && (
          <div className="form">
            {Object.entries(f.models).map(([role, m]) => {
              const res = tests[role];
              return [
                <label key={role + "l"}>{role}</label>,
                <div key={role} className="row">
                  <select value={m.provider} aria-label={`${role} provider`} onChange={(e) => set({ models: { ...f.models, [role]: { ...m, provider: e.target.value } } })}>
                    {(data.providers as string[]).map((p) => <option key={p}>{p}</option>)}</select>
                  <input type="text" className="grow" value={m.model} aria-label={`${role} model`} onChange={(e) => set({ models: { ...f.models, [role]: { ...m, model: e.target.value } } })} />
                  <button onClick={() => test(role)}>{tr("Test", "Thử")}</button>
                  <span className="testres" title={res && res !== "…" ? String(res.message ?? "") : ""}>{!res ? "" : res === "…" ? "…" : res.ok ? "✔" : `✖ ${res.code}`}</span>
                </div>];
            })}
          </div>
        )}
        {tab === 2 && <div className="form">{Object.entries(data.secrets as Record<string, boolean>).map(([name, ok]) => <KeyRow key={name} name={name} isSet={ok} />)}</div>}
        {tab === 3 && (
          <div className="form">
            {L("Speech recognition", "Nhận dạng giọng nói")}
            <select value={f.stt.provider} onChange={(e) => set({ stt: { ...f.stt, provider: e.target.value } })}>{["google", "openai", "local"].map((p) => <option key={p}>{p}</option>)}</select>
            {L("Recognition model", "Mô hình nhận dạng")}<input type="text" value={f.stt.model} onChange={(e) => set({ stt: { ...f.stt, model: e.target.value } })} />
            {L("Read answers aloud", "Đọc câu trả lời")}{check(f.tts.enabled, (v) => set({ tts: { ...f.tts, enabled: v } }), tr("Read answers aloud", "Đọc câu trả lời"))}
            {L("Vietnamese voice", "Giọng tiếng Việt")}<input type="text" value={f.tts.voice_vi} onChange={(e) => set({ tts: { ...f.tts, voice_vi: e.target.value } })} />
            {L("English voice", "Giọng tiếng Anh")}<input type="text" value={f.tts.voice_en} onChange={(e) => set({ tts: { ...f.tts, voice_en: e.target.value } })} />
            {L("Speech rate (+10%, -20%)", "Tốc độ (+10%, -20%)")}<input type="text" value={f.tts.rate} onChange={(e) => set({ tts: { ...f.tts, rate: e.target.value } })} />
          </div>
        )}
        {tab === 4 && (
          <div className="form">
            {L("Daily token cap", "Giới hạn token mỗi ngày")}
            {number(f.budgets.daily_max_tokens, (v) => set({ budgets: { ...f.budgets, daily_max_tokens: v } }), 10_000, 1_000_000_000, 100_000, tr("Daily token cap", "Giới hạn token mỗi ngày"))}
            {L("Daily cost cap (USD)", "Giới hạn chi phí mỗi ngày (USD)")}
            {number(f.budgets.daily_max_cost_usd, (v) => set({ budgets: { ...f.budgets, daily_max_cost_usd: v } }), 0.01, 10_000, 0.01, tr("Daily cost cap (USD)", "Giới hạn chi phí mỗi ngày (USD)"))}
          </div>
        )}
        {tab === 5 && f.screen && (
          <div className="form">
            {L("Let the agent look at the screen", "Cho agent xem màn hình")}{check(f.screen.agent_view, (v) => setScreen({ agent_view: v }), tr("Let the agent look at the screen", "Cho agent xem màn hình"))}
            {L("Let the agent use mouse/keyboard (each use needs approval)", "Cho agent dùng chuột/bàn phím (mỗi lần cần duyệt)")}
            {check(f.screen.control, (v) => setScreen({ control: v }), tr("Let the agent use mouse/keyboard", "Cho agent dùng chuột/bàn phím"))}
            {L("Private window titles (one per line, never captured or controlled)", "Tiêu đề cửa sổ riêng tư (mỗi dòng một mục, không bao giờ chụp/điều khiển)")}
            <textarea rows={4} value={f.screen.block_titles} onChange={(e) => setScreen({ block_titles: e.target.value })} />
            {L("Private programs (exe names)", "Chương trình riêng tư (tên exe)")}
            <textarea rows={3} value={f.screen.block_processes} onChange={(e) => setScreen({ block_processes: e.target.value })} />
            {L("Watching: seconds between captures", "Theo dõi: số giây giữa các lần chụp")}{number(f.screen.watch_interval_s, (v) => setScreen({ watch_interval_s: v }), 0.5, 60, 0.5, "interval")}
            {L("Watching: change sensitivity (1-64, lower = more sensitive)", "Theo dõi: độ nhạy thay đổi (1-64, thấp = nhạy hơn)")}
            {number(f.screen.watch_change_threshold, (v) => setScreen({ watch_change_threshold: v }), 1, 64, 1, "threshold")}
            {L("Watching: switches itself off after (minutes)", "Theo dõi: tự tắt sau (phút)")}{number(f.screen.watch_max_minutes, (v) => setScreen({ watch_max_minutes: v }), 1, 480, 1, "minutes")}
            {L("Steps one approval may cover", "Số bước tối đa mỗi lần duyệt")}{number(f.screen.max_actions, (v) => setScreen({ max_actions: v }), 1, 20, 1, "actions")}
          </div>
        )}
        {tab === 6 && <About data={data} />}
      </div>
      <div className="row savebar">
        <span className="status">{status}</span>
        <button className="primary" onClick={() => void save()}>{tr("Save changes", "Lưu thay đổi")}</button>
      </div>
    </div>
  );
}

/** First run: language -> provider key (+ connection test) -> workspace folder. Shown while no model provider key is set. */
export function Wizard(): React.JSX.Element | null {
  const { settings: data, ui } = useStore();
  const [closed, setClosed] = useState(false);
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [provider, setProvider] = useState("anthropic");
  const [key, setKey] = useState("");
  const [result, setResult] = useState("");
  const [folder, setFolder] = useState("");
  const [tests, test] = useRoleTests();
  useEffect(() => {
    if (data && !hasProviderKey(data)) setOpen(true); // once open it stays until finished, even after a key was saved
  }, [data]);
  const role = Object.entries((data?.models ?? {}) as Record<string, any>).find(([, m]) => m.provider === provider)?.[0];
  const res = role ? tests[role] : undefined;
  useEffect(() => {
    if (res && res !== "…") setResult(res.ok ? "✔ " + tr("Connected", "Kết nối được") : `✖ ${res.code}: ${res.message}`);
  }, [res]);
  if (!open || closed || !data) return null;

  const saveAndTest = (): void => {
    if (key.trim()) {
      void window.agent.send({ type: "secret_set", name: PROVIDER_KEYS[provider], value: key });
      setKey("");
    }
    if (!role) return setResult(tr("Key saved. No model uses this provider yet; pick it under Settings -> Models.",
      "Đã lưu khóa. Chưa có mô hình nào dùng nhà cung cấp này; chọn trong Cài đặt -> Mô hình."));
    setResult("…");
    test(role);
  };
  const titles: [string, string][] = [["Language", "Ngôn ngữ"], ["Connect a model provider", "Kết nối nhà cung cấp mô hình"], ["Workspace", "Thư mục làm việc"]];
  return (
    <div className="modal-back">
      <div className="modal wizard" role="dialog" aria-modal="true" aria-label={tr("Welcome", "Chào mừng")}>
        <div className="title">{tr("Welcome", "Chào mừng")} · {tr(...titles[step]!)}</div>
        {step === 0 && (
          <>
            <p>{tr("Choose the interface language.", "Chọn ngôn ngữ giao diện.")}</p>
            <select value={ui.lang} aria-label={tr("Language", "Ngôn ngữ")}
              onChange={(e) => void window.agent.send({ type: "settings_set", patch: { ui: { language: e.target.value }, agent: { language: e.target.value } } })}>
              <option value="vi">Tiếng Việt</option><option value="en">English</option>
            </select>
          </>
        )}
        {step === 1 && (
          <>
            <p>{tr("The key is stored in Windows Credential Manager, never in a file.", "Khóa được lưu trong Windows Credential Manager, không lưu vào tệp.")}</p>
            <select value={provider} onChange={(e) => setProvider(e.target.value)} aria-label="provider">{Object.keys(PROVIDER_KEYS).map((p) => <option key={p}>{p}</option>)}</select>
            <input type="password" value={key} onChange={(e) => setKey(e.target.value)} aria-label="API key" />
            <button className="primary" onClick={saveAndTest}>{tr("Save key and test", "Lưu khóa và thử")}</button>
            <p>{result}</p>
          </>
        )}
        {step === 2 && (
          <>
            <p>{tr("The agent may only touch the folders you allow. Default: Documents\\agent-workspace.", "Agent chỉ được đụng vào thư mục bạn cho phép. Mặc định: Documents\\agent-workspace.")}</p>
            <button onClick={() => void addFolder().then((d) => d && setFolder(d))}>{tr("Add a folder…", "Thêm thư mục…")}</button>
            <p>{folder && `✔ ${folder}`}</p>
          </>
        )}
        <div className="buttons">
          {step > 0 && <button onClick={() => setStep(step - 1)}>{tr("Back", "Quay lại")}</button>}
          {step < 2 ? <button className="primary" onClick={() => setStep(step + 1)}>{tr("Next", "Tiếp")}</button>
            : <button className="primary" onClick={() => setClosed(true)}>{tr("Finish", "Xong")}</button>}
          <button onClick={() => setClosed(true)}>{tr("Close", "Đóng")}</button>
        </div>
      </div>
    </div>
  );
}
