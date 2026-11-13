/** Chat page: conversations list, transcript (markdown, saved history), composer (attachments, pasted images) and the Plan/Progress panel. */
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { type Attachment, checkSize, mimeOf, roomFor } from "../../shared/attach";
import { tr } from "../../shared/i18n";
import type { Entry, Session } from "../../shared/session";
import { confirmBox, promptLine } from "./dialogs";
import { Icon } from "./Icon";
import { renderMarkdown } from "./markdown";
import { store, useStore } from "./store";
import { Threads } from "./Threads";

const MARK: Record<string, string> = { todo: "", done: "✓", failed: "✕", blocked: "!" };
const PAGE = 200; // messages drawn at once; "Show earlier" adds a page
const WIDE = 1350; // from this window width the conversation list sits beside the chat; below it, it opens over it

function readFile(f: File, name = f.name): Promise<Attachment> {
  checkSize(name, f.size);
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(new Error(`${name}: ${r.error?.message ?? "cannot read"}`));
    r.onload = () => resolve({ name, mime: mimeOf(name, f.type), data: String(r.result).split(",")[1] ?? "" });
    r.readAsDataURL(f);
  });
}

type Act = { copy: () => void; edit?: () => void; retry?: () => void };

const Message = memo(function Message({ kind, text, used, name, act }: { kind: Entry["kind"]; text: string; used?: Record<string, any>; name: string; act?: Act }) {
  const html = useMemo(() => (kind === "user" || kind === "agent" ? renderMarkdown(text) : ""), [kind, text]);
  if (kind === "event") return <div className="msg event">{text.replace(/\n/g, " ")}</div>;
  if (kind === "note") {
    return <div className="msg note">{tr("Earlier messages were summarised to keep the conversation within the model's context. They stay here in full.",
      "Các tin trước đó đã được tóm tắt để vừa ngữ cảnh của mô hình. Ở đây vẫn giữ đầy đủ.")}</div>;
  }
  if (kind === "error") {
    return <div className="msg error">{text}{act?.retry && <button className="ghost sm" onClick={act.retry}><Icon name="retry" />{tr("Retry", "Thử lại")}</button>}</div>;
  }
  return (
    <div className={`msg ${kind}`}>
      <div className="who">{kind === "user" ? tr("You", "Bạn") : name}</div>
      {/* sanitised by renderMarkdown: no raw HTML, no script, no remote resources */}
      <div className="md" dangerouslySetInnerHTML={{ __html: html }} />
      {used && Object.keys(used).length > 0 && <div className="foot">{used.steps} steps · {used.tokens} tokens · ${Number(used.cost ?? 0).toFixed(4)}</div>}
      {act && (
        <div className="acts">
          <button className="ghost sm" onClick={act.copy} title={tr("Copy", "Sao chép")} aria-label={tr("Copy", "Sao chép")}><Icon name="copy" /></button>
          {act.edit && <button className="ghost sm" onClick={act.edit} title={tr("Edit and send again", "Sửa rồi gửi lại")} aria-label={tr("Edit and send again", "Sửa rồi gửi lại")}><Icon name="edit" /></button>}
          {act.retry && <button className="ghost sm" onClick={act.retry} title={tr("Ask again", "Hỏi lại")} aria-label={tr("Ask again", "Hỏi lại")}><Icon name="retry" /></button>}
        </div>
      )}
    </div>
  );
});

function Meter({ used, cap, unit }: { used: number; cap?: number | null; unit: "tokens" | "$" }): React.JSX.Element {
  const n = (v: number, d = 0): string => v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  const label = !cap ? "-" : unit === "$" ? `$${n(used, 4)} / $${n(cap, 2)}` : `${n(used)} / ${n(cap)} ${unit}`;
  return <div className="meter"><div className="fill" style={{ width: `${cap ? Math.min(100, (100 * used) / cap) : 0}%` }} /><span>{label}</span></div>;
}

function PlanPanel({ s }: { s: Session }): React.JSX.Element {
  const u = s.used, today = s.usage.today ?? {}, caps = s.usage.caps ?? {};
  return (
    <aside className="plan">
      <div>
        <h2>{tr("Task", "Nhiệm vụ")}</h2>
        {s.plan ? <p className="obj">{s.plan.objective}</p>
          : <div className="none">{tr("A simple question needs no plan. Give a bigger task and its steps appear here.", "Câu hỏi đơn giản không cần kế hoạch. Giao việc lớn hơn, các bước sẽ hiện ở đây.")}</div>}
      </div>
      {s.plan && (
        <>
          <div><h2>{tr("Done when", "Hoàn thành khi")}</h2><ul className="crit">{(s.plan.criteria ?? []).map((c, i) => <li key={i}>{c}</li>)}</ul></div>
          <div>
            <h2>{tr("Steps", "Các bước")}</h2>
            <ol className="steps">{s.steps.map((x, i) => (
              <li key={i} className={x.status}><span className="mk" aria-hidden="true">{MARK[x.status] ?? ""}</span><span><span className="role">{x.role}</span>{x.text}</span></li>
            ))}</ol>
          </div>
        </>
      )}
      {Object.keys(u).length > 0 && <p className="muted">{tr("This task: {steps} steps, {tools} tool calls, {tokens} tokens, ${cost}",
        "Tác vụ này: {steps} bước, {tools} lần gọi công cụ, {tokens} token, ${cost}",
        { steps: u.steps ?? 0, tools: u.tool_calls ?? 0, tokens: u.tokens ?? 0, cost: Number(u.cost ?? 0).toFixed(4) })}</p>}
      <div>
        <h2>{tr("Today's budget", "Ngân sách hôm nay")}</h2>
        <Meter used={today.tokens ?? 0} cap={caps.tokens} unit="tokens" />
        <Meter used={today.cost_usd ?? 0} cap={caps.cost_usd} unit="$" />
      </div>
    </aside>
  );
}

type Draft = { text: string; files: Attachment[] };
const drafts = new Map<string, Draft>(); // what was typed in each conversation and not yet sent

export function Chat(): React.JSX.Element {
  const { session: s, ui } = useStore();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<Attachment[]>([]);
  const [note, setNote] = useState(""); // a message about the last attachment (too big, too many) that must stay visible
  const [shown, setShown] = useState(PAGE);
  const [away, setAway] = useState(false); // scrolled up while something new arrived
  const [wide, setWide] = useState(window.innerWidth >= WIDE);
  const [listOpen, setListOpen] = useState<boolean | null>(null); // null: follow the window width
  const open = listOpen ?? wide; // a narrow window shows the list over the chat, only when asked for
  const list = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const stick = useRef(true); // follow new messages only while the reader is at the bottom
  const keep = useRef<number | null>(null); // scrollHeight before older messages were added, to keep the reader's place
  const now = useRef<Draft>({ text, files });
  now.current = { text, files };
  const prevThread = useRef(s.threadId);
  const mine = s.pendingForThread()[s.threadId]?.length ?? 0; // only an approval in THIS conversation blocks it
  const others = Object.keys(s.approvals).length - mine;
  const last = s.transcript.at(-1);
  const rows = s.transcript.slice(-shown);
  const hidden = s.transcript.length - rows.length;
  const info = store.threads.find((t) => t.id === s.threadId);

  useEffect(() => {
    const onResize = (): void => setWide(window.innerWidth >= WIDE);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  useEffect(() => { // another conversation: park this draft, bring back that one's, and start at the bottom
    if (prevThread.current === s.threadId) return;
    drafts.set(prevThread.current, now.current);
    prevThread.current = s.threadId;
    const d = drafts.get(s.threadId);
    setText(d?.text ?? "");
    setFiles(d?.files ?? []);
    setNote("");
    setShown(PAGE);
    stick.current = true;
    setAway(false);
  }, [s.threadId]);
  useLayoutEffect(() => {
    const el = list.current;
    if (!el) return;
    if (keep.current !== null) {
      el.scrollTop += el.scrollHeight - keep.current; // older messages were added above: stay where the reader was
      keep.current = null;
    } else if (stick.current || last?.kind === "user") {
      el.scrollTop = el.scrollHeight;
      stick.current = true;
      setAway(false);
    } else setAway(true);
  }, [s.threadId, s.transcript.length, last?.text]);
  useEffect(() => {
    if (ui.page === "chat") box.current?.focus();
  }, [ui.page, s.threadId]);
  useEffect(() => store.onDaemonEvent((ev) => { // a screenshot taken from the tray, a hotkey or the avatar: you decide whether to send it
    if (ev.type === "_insert") setText((c) => c + ev.text); // what was said into the microphone: check it, then send
    if (ev.type !== "_attach") return;
    setFiles((c) => (roomFor(c, ev.attachment) ? [...c, ev.attachment] : c));
  }), []);

  async function add(picked: Iterable<File>, pastedAs?: (i: number) => string): Promise<void> {
    let cur = files, msg = "";
    for (const f of picked) {
      try {
        const a = await readFile(f, pastedAs ? pastedAs(cur.length + 1) : f.name);
        if (roomFor(cur, a)) cur = [...cur, a];
        else msg = tr("At most 4 files and 15 MB per message.", "Tối đa 4 tệp và 15 MB mỗi tin nhắn.");
      } catch (e) {
        msg = (e as Error).message;
      }
    }
    setFiles(cur);
    setNote(msg);
  }

  function send(): void {
    const t = text.trim();
    if ((!t && !files.length) || mine) return; // a paused task must be answered first: a new request would orphan its tool calls
    void window.agent.sendUser(t, files);
    setText("");
    setFiles([]);
    setNote("");
  }

  const showEarlier = (): void => {
    keep.current = list.current?.scrollHeight ?? null;
    if (hidden <= 0 && s.hasMore) void window.agent.send({ type: "thread_history", thread_id: s.threadId, before: s.firstId, limit: PAGE });
    setShown(shown + PAGE);
  };
  const lastUser = (before: number): string | undefined => s.transcript.slice(0, before).reverse().find((e) => e.kind === "user")?.text;
  const actions = (e: Entry, i: number): Act | undefined => {
    const at = hidden + i;
    const copy = (): void => void navigator.clipboard?.writeText(e.text);
    if (e.kind === "user") return { copy, edit: () => { setText(e.text); box.current?.focus(); } };
    const q = lastUser(at);
    const retry = q && !s.busy && !mine ? () => void window.agent.sendUser(q, []) : undefined;
    if (e.kind === "agent") return { copy, retry };
    if (e.kind === "error" && at === s.transcript.length - 1) return { copy, retry };
    return undefined;
  };
  const title = info?.title || tr("New conversation", "Cuộc trò chuyện mới");
  const rename = async (): Promise<void> => {
    const t = (await promptLine(tr("Rename conversation", "Đổi tên cuộc trò chuyện"), tr("Title", "Tiêu đề"), info?.title ?? ""))?.trim();
    if (t) void window.agent.send({ type: "thread_rename", thread_id: s.threadId, title: t });
  };
  const remove = async (): Promise<void> => {
    if (await confirmBox(tr("Delete conversation", "Xóa cuộc trò chuyện"), tr("Delete “{t}” for good? Its messages and what the agent remembers of it are removed. This cannot be undone.",
      "Xóa vĩnh viễn “{t}”? Tin nhắn và phần agent nhớ về cuộc trò chuyện này sẽ bị xóa. Không thể hoàn tác.", { t: title }))) {
      void window.agent.send({ type: "thread_delete", thread_id: s.threadId });
    }
  };

  const status = note || (mine ? tr("Waiting for your approval ({n})", "Đang chờ bạn duyệt ({n})", { n: mine }) : s.busy ? tr("Working…", "Đang xử lý…")
    : others ? tr("{n} approval(s) wait in other conversations", "{n} yêu cầu duyệt đang chờ ở cuộc trò chuyện khác", { n: others }) : "");
  return (
    <div className="chat" onDragOver={(e) => e.preventDefault()} onDrop={(e) => {
      e.preventDefault();
      void add(e.dataTransfer.files);
    }}>
      {open && <Threads overlay={!wide} onPick={() => !wide && setListOpen(false)} />}
      <section className="left" onMouseDownCapture={(e) => { // clicking away closes the list that opened over the chat
        if (!wide && open && !(e.target as HTMLElement).closest(".thead button")) setListOpen(false);
      }}>
        <div className="thead">
          <button className={"ghost sm" + (open ? " on" : "")} aria-pressed={open} onClick={() => setListOpen(!open)} title={tr("Conversations", "Các cuộc trò chuyện")}
            aria-label={tr("Conversations", "Các cuộc trò chuyện")}><Icon name="menu" /></button>
          <span className="ttitle" title={title}>{title}</span>
          <button className="ghost sm" onClick={() => void rename()} title={tr("Rename", "Đổi tên")} aria-label={tr("Rename", "Đổi tên")} disabled={!info}><Icon name="edit" /></button>
          <button className="ghost sm" onClick={() => void window.agent.send({ type: "thread_export", thread_id: s.threadId })} title={tr("Export as Markdown", "Xuất ra Markdown")}
            aria-label={tr("Export as Markdown", "Xuất ra Markdown")} disabled={!info}><Icon name="download" /></button>
          <button className="ghost sm" onClick={() => void remove()} title={tr("Delete", "Xóa")} aria-label={tr("Delete", "Xóa")} disabled={!info || s.busy || mine > 0}><Icon name="trash" /></button>
        </div>
        {s.killed && (
          <div className="paused">
            <span>{tr("The agent is paused (kill switch). It will refuse every request until you resume it.",
              "Agent đang tạm dừng (công tắc khẩn). Agent sẽ từ chối mọi yêu cầu cho tới khi bạn cho chạy lại.")}</span>
            <button onClick={() => void window.agent.send({ type: "release_kill" })}>{tr("Resume agent", "Cho agent chạy lại")}</button>
          </div>
        )}
        <div className="transcript" ref={list} onScroll={(e) => {
          const el = e.currentTarget, near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          stick.current = near;
          if (near) setAway(false);
        }}>
          {(hidden > 0 || s.hasMore) && <button className="earlier" onClick={showEarlier}>{tr("Show earlier messages", "Hiện các tin trước đó")}</button>}
          {rows.map((e, i) => <Message key={e.id ?? `l${hidden + i}`} kind={e.kind} text={e.text} used={e.meta.used} name={s.agentName} act={actions(e, i)} />)}
        </div>
        {away && (
          <button className="primary sm jump" onClick={() => {
            stick.current = true;
            setAway(false);
            list.current?.scrollTo({ top: list.current.scrollHeight });
          }}><Icon name="down" />{tr("New messages", "Tin nhắn mới")}</button>
        )}
        {files.length > 0 && (
          <div className="chips">{files.map((f, i) => (
            <span key={i} className="chip">{f.name} <button aria-label={tr("Remove", "Bỏ") + " " + f.name} onClick={() => {
              setFiles(files.filter((_, j) => j !== i));
              setNote("");
            }}>✕</button></span>
          ))}</div>
        )}
        <div className="compose">
          <textarea ref={box} className="composer" value={text} onChange={(e) => setText(e.target.value)}
            placeholder={tr("Message… (Enter sends, Shift+Enter = new line; paste or drop images and files)",
              "Tin nhắn… (Enter để gửi, Shift+Enter xuống dòng; dán hoặc kéo thả ảnh, tệp)")}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            onPaste={(e) => {
              const imgs = [...e.clipboardData.files].filter((f) => f.type.startsWith("image/"));
              if (imgs.length) {
                e.preventDefault();
                void add(imgs, (n) => `pasted-${n}.png`);
              }
            }} />
          <div className="row">
            <label className="btn ghost"><Icon name="clip" />{tr("Attach", "Đính kèm")}
              <input type="file" multiple hidden onChange={(e) => {
                void add([...(e.target.files ?? [])]);
                e.target.value = "";
              }} />
            </label>
            <button className={"ghost" + (ui.recording ? " on" : "")} aria-pressed={ui.recording} onClick={() => void window.agent.ui("record", !ui.recording)}>
              <Icon name="mic" />{ui.recording ? tr("Stop", "Dừng") : tr("Talk", "Nói")}</button>
            <span className={"status" + (mine ? " wait" : "")}>{status}</span>
            <button className="primary" disabled={mine > 0} onClick={send}><Icon name="send" />{tr("Send", "Gửi")}</button>
          </div>
        </div>
      </section>
      <PlanPanel s={s} />
    </div>
  );
}
