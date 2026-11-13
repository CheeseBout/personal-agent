/** The one window: a sidebar plus the chat, the manager pages and the settings. Approvals stay a separate window on purpose:
 * a risky action must be able to interrupt whatever page is open. */
import { useEffect } from "react";
import { tr } from "../../shared/i18n";
import { Chat } from "./Chat";
import { DialogHost } from "./dialogs";
import { Icon } from "./Icon";
import { ManagerPage, ManagerStatus } from "./Manager";
import { Settings, Wizard } from "./Settings";
import { useStore } from "./store";

// Groups of [key, English, Vietnamese]. Keys other than chat / approvals / settings are manager pages.
const GROUPS: [string, string, [string, string, string][]][] = [
  ["Work", "Làm việc", [["chat", "Chat", "Trò chuyện"], ["approvals", "Approvals", "Chờ duyệt"]]],
  ["Knowledge", "Kiến thức", [["memory", "Memory", "Bộ nhớ"], ["rag", "Documents", "Tài liệu"]]],
  ["Control", "Kiểm soát", [["mcp", "Tools / MCP", "Công cụ / MCP"], ["schedules", "Schedules", "Lịch chạy"], ["rules", "Remembered approvals", "Quy tắc đã nhớ"],
    ["policy", "Policy", "Chính sách"], ["audit", "Audit log", "Nhật ký kiểm toán"]]],
  ["System", "Hệ thống", [["usage", "Usage", "Mức dùng"], ["settings", "Settings", "Cài đặt"]]]];
const ITEMS = GROUPS.flatMap((g) => g[2]);

const STATE_TEXT: Record<string, [string, string]> = { idle: ["Ready", "Sẵn sàng"], working: ["Working", "Đang làm việc"],
  awaiting: ["Waiting for your decision", "Đang chờ bạn quyết định"], sleeping: ["Paused", "Tạm dừng"] };

export function MainWindow(): React.JSX.Element {
  const { session, ui } = useStore();
  const pending = Object.keys(session.approvals).length;
  const toggleRail = (): void => void window.agent.ui("rail", !ui.railCollapsed);
  const go = (key: string): void => void (key === "approvals" ? window.agent.ui("approvals") : window.agent.ui("page", key));
  const setLang = (language: string): void => void window.agent.send({ type: "settings_set", patch: { ui: { language }, agent: { language } } });

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.ctrlKey && e.key.toLowerCase() === "b") toggleRail();
      if (e.ctrlKey && e.key.toLowerCase() === "q") void window.agent.ui("quit");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const title = ITEMS.find((it) => it[0] === ui.page);
  const railTip = ui.railCollapsed ? tr("Show sidebar (Ctrl+B)", "Hiện thanh bên (Ctrl+B)") : tr("Hide sidebar (Ctrl+B)", "Ẩn thanh bên (Ctrl+B)");
  const state = session.killed ? "sleeping" : pending > 0 ? "awaiting" : session.busy ? "working" : "idle";
  const { today = {}, caps = {} } = session.usage as { today?: { tokens?: number }; caps?: { tokens?: number } };
  const used = caps.tokens ? Math.min(100, (100 * (today.tokens ?? 0)) / caps.tokens) : 0;
  return (
    <div className="shell">
      {!ui.railCollapsed && (
        <nav className="rail">
          <div className="brand"><span className="orb" /><span className="title">{session.agentName}</span></div>
          {GROUPS.map(([en, vi, items]) => (
            <div key={en}>
              <div className="grp">{tr(en, vi)}</div>
              {items.map((it) => (
                <button key={it[0]} className={"nav" + (ui.page === it[0] ? " current" : "")} onClick={() => go(it[0])}>
                  <Icon name={it[0]} /><span>{tr(it[1], it[2])}</span>
                  {it[0] === "approvals" && pending > 0 && <span className="badge">{pending}</span>}
                </button>
              ))}
            </div>
          ))}
          <div className="spacer" />
          <button className="danger killbtn" onClick={() => void window.agent.send({ type: "kill" })}
            title={tr("Pause the agent and stop what it is doing", "Tạm dừng agent và dừng việc đang làm")}><Icon name="power" />{tr("Stop everything", "Dừng tất cả")}</button>
          <div className="railtools">
            <div className="seg" role="group" aria-label={tr("Language", "Ngôn ngữ")}>
              <button aria-pressed={ui.lang === "vi"} onClick={() => setLang("vi")}>VI</button>
              <button aria-pressed={ui.lang !== "vi"} onClick={() => setLang("en")}>EN</button>
            </div>
            <span className="grow" />
            <button className="ghost" onClick={() => void window.agent.ui("quit")}
              title={tr("Close the app (Ctrl+Q). The background agent keeps running.", "Đóng ứng dụng (Ctrl+Q). Agent nền vẫn chạy.")}><Icon name="exit" />{tr("Quit", "Thoát")}</button>
          </div>
        </nav>
      )}
      <div className="content">
        <header className="topbar">
          <button className="burger" onClick={toggleRail} title={railTip} aria-label={railTip}><Icon name="menu" /></button>
          <span className="title">{title ? tr(title[1], title[2]) : ""}</span>
          {!ui.connected && <span className="warn">{tr("offline", "mất kết nối")}</span>}
          <span className="grow" />
          {ui.page !== "chat" && ui.page !== "settings" && <ManagerStatus />}
          <div className="state" style={{ "--sc": `var(--st-${state})` } as React.CSSProperties} role="status">
            <span className="dot" />{tr(...STATE_TEXT[state]!)}
          </div>
          {caps.tokens ? <div className="mini" title={tr("Today's token budget", "Ngân sách token hôm nay")}><i style={{ width: `${used}%` }} /></div> : null}
        </header>
        {/* The chat stays mounted so a half-typed message and its attachments survive a visit to another page. */}
        <div className="page" hidden={ui.page !== "chat"}><Chat /></div>
        {ui.page !== "chat" && <div className="page">{ui.page === "settings" ? <Settings /> : <ManagerPage page={ui.page} />}</div>}
      </div>
      <Wizard />
      <DialogHost />
    </div>
  );
}
