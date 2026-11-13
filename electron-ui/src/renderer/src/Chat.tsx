/** Chat page: transcript (markdown), composer (attachments, pasted images) and the Plan/Progress panel. */
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { type Attachment, checkSize, mimeOf, roomFor } from "../../shared/attach";
import { tr } from "../../shared/i18n";
import type { Entry, Session } from "../../shared/session";
import { Icon } from "./Icon";
import { renderMarkdown } from "./markdown";
import { store, useStore } from "./store";

const MARK: Record<string, string> = { todo: "", done: "✓", failed: "✕", blocked: "!" };

function readFile(f: File, name = f.name): Promise<Attachment> {
  checkSize(name, f.size);
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(new Error(`${name}: ${r.error?.message ?? "cannot read"}`));
    r.onload = () => resolve({ name, mime: mimeOf(name, f.type), data: String(r.result).split(",")[1] ?? "" });
    r.readAsDataURL(f);
  });
}

const Message = memo(function Message({ kind, text, used, name }: { kind: Entry["kind"]; text: string; used?: Record<string, any>; name: string }) {
  const html = useMemo(() => (kind === "user" || kind === "agent" ? renderMarkdown(text) : ""), [kind, text]);
  if (kind === "event") return <div className="msg event">{text.replace(/\n/g, " ")}</div>;
  if (kind === "error") return <div className="msg error">{text}</div>;
  return (
    <div className={`msg ${kind}`}>
      <div className="who">{kind === "user" ? tr("You", "Bạn") : name}</div>
      {/* sanitised by renderMarkdown: no raw HTML, no script, no remote resources */}
      <div className="md" dangerouslySetInnerHTML={{ __html: html }} />
      {used && Object.keys(used).length > 0 && <div className="foot">{used.steps} steps · {used.tokens} tokens · ${Number(used.cost ?? 0).toFixed(4)}</div>}
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

export function Chat(): React.JSX.Element {
  const { session: s, ui } = useStore();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<Attachment[]>([]);
  const [note, setNote] = useState(""); // a message about the last attachment (too big, too many) that must stay visible
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const pending = Object.keys(s.approvals).length;
  const last = s.transcript.at(-1);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" }); // braces matter: newer Chromium returns a promise here, and an effect may only return a cleanup
  }, [s.transcript.length, last?.text]);
  useEffect(() => {
    if (ui.page === "chat") box.current?.focus();
  }, [ui.page]);
  useEffect(() => store.onDaemonEvent((ev) => { // a screenshot taken from the tray, a hotkey or the avatar: you decide whether to send it
    if (ev.type === "_insert") setText((cur) => cur + ev.text); // what was said into the microphone: check it, then send
    if (ev.type !== "_attach") return;
    setFiles((cur) => (roomFor(cur, ev.attachment) ? [...cur, ev.attachment] : cur));
  }), []);

  async function add(list: Iterable<File>, pastedAs?: (i: number) => string): Promise<void> {
    let cur = files, msg = "";
    for (const f of list) {
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
    if ((!t && !files.length) || pending) return; // a paused task must be answered first: a new request would orphan its tool calls
    void window.agent.sendUser(t, files);
    setText("");
    setFiles([]);
    setNote("");
  }

  const status = note || (pending ? tr("Waiting for your approval ({n})", "Đang chờ bạn duyệt ({n})", { n: pending }) : s.busy ? tr("Working…", "Đang xử lý…") : "");
  return (
    <div className="chat" onDragOver={(e) => e.preventDefault()} onDrop={(e) => {
      e.preventDefault();
      void add(e.dataTransfer.files);
    }}>
      <section className="left">
        {s.killed && (
          <div className="paused">
            <span>{tr("The agent is paused (kill switch). It will refuse every request until you resume it.",
              "Agent đang tạm dừng (công tắc khẩn). Agent sẽ từ chối mọi yêu cầu cho tới khi bạn cho chạy lại.")}</span>
            <button onClick={() => void window.agent.send({ type: "release_kill" })}>{tr("Resume agent", "Cho agent chạy lại")}</button>
          </div>
        )}
        <div className="transcript">
          {s.transcript.map((e, i) => <Message key={i} kind={e.kind} text={e.text} used={e.meta.used} name={s.agentName} />)}
          <div ref={end} />
        </div>
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
          <span className={"status" + (pending ? " wait" : "")}>{status}</span>
          <button className="primary" disabled={pending > 0} onClick={send}><Icon name="send" />{tr("Send", "Gửi")}</button>
        </div>
        </div>
      </section>
      <PlanPanel s={s} />
    </div>
  );
}
