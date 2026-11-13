/** The conversation list beside the chat: new chat, search (text, with an approval, with a file), pin, and the groups by recency. */
import { useEffect, useRef, useState } from "react";
import { tr } from "../../shared/i18n";
import { Icon } from "./Icon";
import { store, type Thread, useStore } from "./store";

type Hit = { id: number; thread_id: string; title: string; kind: string; text: string };

function group(t: Thread, now: number): string {
  if (t.pinned) return "pinned";
  const midnight = new Date(now * 1000).setHours(0, 0, 0, 0) / 1000;
  return t.updated >= midnight ? "today" : t.updated >= midnight - 6 * 86400 ? "week" : "earlier";
}

const GROUPS: [string, string, string][] = [["pinned", "Pinned", "Đã ghim"], ["today", "Today", "Hôm nay"], ["week", "This week", "Tuần này"], ["earlier", "Earlier", "Cũ hơn"]];

export function Threads({ overlay = false, onPick = () => undefined }: { overlay?: boolean; onPick?: () => void }): React.JSX.Element {
  const { session, threads } = useStore();
  const [q, setQ] = useState("");
  const [has, setHas] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const latest = useRef(q);
  latest.current = q;

  useEffect(() => void window.agent.send({ type: "threads_list" }), []);
  useEffect(() => store.onDaemonEvent((ev) => void (ev.type === "thread_results" && ev.query === latest.current && setHits(ev.items))), []);
  useEffect(() => {
    if (!q.trim() && !has) return setHits(null);
    const t = setTimeout(() => void window.agent.send({ type: "thread_search", query: q, has }), 250);
    return () => clearTimeout(t);
  }, [q, has]);

  const go = (id: string): void => {
    void window.agent.ui("thread", id);
    onPick();
  };
  const now = Date.now() / 1000;
  const unsaved = !threads.some((t) => t.id === session.threadId);
  const row = (t: Pick<Thread, "id" | "title"> & Partial<Thread>): React.JSX.Element => (
    <div key={t.id} className={"thread" + (t.id === session.threadId ? " current" : "")}>
      <button className="pick" onClick={() => go(t.id)} aria-current={t.id === session.threadId ? "true" : undefined}>
        {(t.running || t.pending) && <i className={"mark " + (t.pending ? "wait" : "run")} aria-label={t.pending ? tr("Waiting for your approval", "Đang chờ bạn duyệt") : tr("Working", "Đang làm việc")} />}
        <span>{t.title || tr("New conversation", "Cuộc trò chuyện mới")}</span>
      </button>
      {"pinned" in t && <button className={"ghost sm pin" + (t.pinned ? " on" : "")} aria-pressed={t.pinned} title={t.pinned ? tr("Unpin", "Bỏ ghim") : tr("Pin", "Ghim")}
        aria-label={t.pinned ? tr("Unpin", "Bỏ ghim") : tr("Pin", "Ghim")} onClick={() => void window.agent.send({ type: "thread_pin", thread_id: t.id, pinned: !t.pinned })}><Icon name="pin" /></button>}
    </div>
  );

  return (
    <aside className={"threads" + (overlay ? " over" : "")} aria-label={tr("Conversations", "Các cuộc trò chuyện")}>
      <button className="primary" onClick={() => {
        void window.agent.ui("thread_new");
        onPick();
      }}><Icon name="plus" />{tr("New chat", "Chat mới")}</button>
      <div className="find">
        <Icon name="search" />
        <input type="text" value={q} onChange={(e) => setQ(e.target.value)} placeholder={tr("Search conversations", "Tìm cuộc trò chuyện")} aria-label={tr("Search conversations", "Tìm cuộc trò chuyện")} />
      </div>
      <select value={has} onChange={(e) => setHas(e.target.value)} aria-label={tr("Filter", "Lọc")}>
        <option value="">{tr("All messages", "Mọi tin nhắn")}</option>
        <option value="approval">{tr("With an approval", "Có yêu cầu duyệt")}</option>
        <option value="attachment">{tr("With a file", "Có tệp đính kèm")}</option>
      </select>
      <div className="tlist">
        {hits !== null ? (
          hits.length ? hits.map((h) => (
            <button key={h.id} className="hit" onClick={() => go(h.thread_id)}><b>{h.title || h.thread_id}</b><span>{h.text}</span></button>
          )) : <p className="muted">{tr("Nothing found.", "Không tìm thấy gì.")}</p>
        ) : (
          <>
            {unsaved && row({ id: session.threadId, title: "" })}
            {GROUPS.map(([key, en, vi]) => {
              const items = threads.filter((t) => group(t, now) === key);
              return items.length ? <div key={key}><div className="grp">{tr(en, vi)}</div>{items.map(row)}</div> : null;
            })}
            {!threads.length && !unsaved && <p className="muted">{tr("Your conversations appear here.", "Các cuộc trò chuyện của bạn sẽ hiện ở đây.")}</p>}
          </>
        )}
      </div>
      <p className="muted note">{tr("Messages are kept here in full. The agent itself remembers through summaries and the Memory page.",
        "Tin nhắn được giữ đầy đủ ở đây. Bản thân agent nhớ qua các bản tóm tắt và trang Bộ nhớ.")}</p>
    </aside>
  );
}
