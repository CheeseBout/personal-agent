/** The one window: a sidebar plus the chat, the manager pages and the settings. Approvals stay a separate window on purpose:
 * a risky action must be able to interrupt whatever page is open. */
import { useEffect } from "react";
import { tr } from "../../shared/i18n";
import { Chat } from "./Chat";
import { DialogHost } from "./dialogs";
import { ManagerPage, ManagerStatus } from "./Manager";
import { Settings, Wizard } from "./Settings";
import { useStore } from "./store";

// [key, English, Vietnamese]; null draws a divider. Keys other than chat / approvals / settings are manager pages.
const ITEMS: ([string, string, string] | null)[] = [
  ["chat", "Chat", "Trò chuyện"], ["approvals", "Approvals", "Chờ duyệt"], null,
  ["memory", "Memory", "Bộ nhớ"], ["rag", "Documents", "Tài liệu"], null,
  ["mcp", "Tools / MCP", "Công cụ / MCP"], ["schedules", "Schedules", "Lịch chạy"], ["rules", "Remembered approvals", "Quy tắc đã nhớ"],
  ["policy", "Policy", "Chính sách"], ["audit", "Audit log", "Nhật ký kiểm toán"], null,
  ["usage", "Usage", "Mức dùng"], ["settings", "Settings", "Cài đặt"]];

export function MainWindow(): React.JSX.Element {
  const { session, ui } = useStore();
  const pending = Object.keys(session.approvals).length;
  const toggleRail = (): void => void window.agent.ui("rail", !ui.railCollapsed);
  const go = (key: string): void => void (key === "approvals" ? window.agent.ui("approvals") : window.agent.ui("page", key));

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.ctrlKey && e.key.toLowerCase() === "b") toggleRail();
      if (e.ctrlKey && e.key.toLowerCase() === "q") void window.agent.ui("quit");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const title = ITEMS.find((it) => it && it[0] === ui.page);
  const railTip = ui.railCollapsed ? tr("Show sidebar (Ctrl+B)", "Hiện thanh bên (Ctrl+B)") : tr("Hide sidebar (Ctrl+B)", "Ẩn thanh bên (Ctrl+B)");
  return (
    <div className="shell">
      {!ui.railCollapsed && (
        <nav className="rail">
          <div className="brand"><span className="orb" /><span className="title">{session.agentName}</span></div>
          {ITEMS.map((it, i) => it === null ? <hr key={i} /> : (
            <button key={it[0]} className={"nav" + (ui.page === it[0] ? " current" : "")} onClick={() => go(it[0])}>
              <span>{tr(it[1], it[2])}</span>
              {it[0] === "approvals" && pending > 0 && <span className="badge">{pending}</span>}
            </button>
          ))}
        </nav>
      )}
      <div className="content">
        <header className="topbar">
          <button className="burger" onClick={toggleRail} title={railTip} aria-label={railTip}>☰</button>
          <span className="title">{title ? tr(title[1], title[2]) : ""}</span>
          {ui.page !== "chat" && ui.page !== "settings" && <ManagerStatus />}
          {!ui.connected && <span className="warn">{tr("offline", "mất kết nối")}</span>}
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
