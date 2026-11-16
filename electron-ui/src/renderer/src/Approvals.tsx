/** Approval center: the pending actions as a queue of chips, the focused one as a release slip (what changes, why, risk, editable arguments),
 * and one decision bar. Answers are sent per thread, as the daemon expects. This is the only window allowed to answer an approval,
 * and it renders everything as plain text. */
import { useEffect, useMemo, useRef, useState } from "react";
import { buildDecisions, type CardState } from "../../shared/decisions";
import { tr } from "../../shared/i18n";
import type { Approval } from "../../shared/session";
import { useStore } from "./store";

type Form = { choice: string | null; args_text: string; reason: string; confirm: string };
const blank = (a: Approval): Form => ({ choice: null, args_text: JSON.stringify(a.args, null, 2), reason: "", confirm: "" });

function diffClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "head";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "";
}

const seen = new Map<string, number>(); // seconds left when a request was first drawn: its fuse shrinks from there
const RISK = ["low", "medium", "high", "critical"];
const LEVEL: Record<string, [string, string]> = { low: ["Low risk", "Rủi ro thấp"], medium: ["Medium risk", "Rủi ro vừa"], high: ["High risk", "Rủi ro cao"],
  critical: ["Critical risk", "Rủi ro nghiêm trọng"] };
const riskOf = (a: Approval): string => (RISK.includes(a.risk) ? a.risk : "");
const secondsLeft = (a: Approval, now: number): number | null => {
  const left = a.expires_at ? Math.max(0, Math.floor(Number(a.expires_at) - now)) : null;
  if (left !== null && !seen.has(a.call_id)) seen.set(a.call_id, Math.max(left, 1));
  return left;
};
const clock = (s: number): string => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/** One chip of the queue: what it is, how long it has, and whether it was answered. */
function Chip({ a, form, now, on, pick }: { a: Approval; form: Form; now: number; on: boolean; pick: () => void }): React.JSX.Element {
  const left = secondsLeft(a, now);
  const mark = form.choice === "reject" ? "✕" : form.choice ? "✓" : "";
  return (
    <button className={`q risk-${riskOf(a)}${on ? " on" : ""}${form.choice ? " done" : ""}`} onClick={pick} aria-current={on ? "true" : undefined}>
      <span className="qname">{mark && <i className={"qmark " + (form.choice === "reject" ? "no" : "yes")}>{mark}</i>}{a.tool}</span>
      {left !== null && <span className="qtime">{clock(left)}</span>}
      {left !== null && <span className="qfuse" aria-hidden="true"><i style={{ width: `${(100 * left) / (seen.get(a.call_id) ?? 1)}%` }} /></span>}
    </button>
  );
}

function Card({ a, form, set, now, multi }: { a: Approval; form: Form; set: (f: Partial<Form>) => void; now: number; multi: string | null }): React.JSX.Element {
  const risk = riskOf(a);
  const left = secondsLeft(a, now);
  const tier = Math.min(3, Math.max(0, Number(String(a.tier ?? "").slice(1)) || 0));
  const edited = useMemo(() => form.args_text !== blank(a).args_text, [form.args_text, a]);
  return (
    <fieldset className={`card risk-${risk}`}>
      {left !== null && <div className="fuse" aria-hidden="true"><i style={{ width: `${(100 * left) / (seen.get(a.call_id) ?? 1)}%` }} /></div>}
      <div className="head">
        <div>
          <b>{a.tool}</b>
          <p>{String(a.reason ?? "")}</p>
          {left !== null && <p className="muted">{tr("Expires in {t}. An unanswered request counts as a rejection.", "Hết hạn sau {t}. Không trả lời được tính là từ chối.", { t: clock(left) })}</p>}
          {multi && <p className="muted">{tr("Conversation {t}", "Cuộc trò chuyện {t}", { t: multi })}</p>}
        </div>
        <div className="ruler" role="img" aria-label={`${risk ? tr(...LEVEL[risk]!) : ""} (T${tier})`}>
          <span className="lvl">{risk ? tr(...LEVEL[risk]!) : ""}</span>
          <div className="ticks">{[0, 1, 2, 3].map((i) => <i key={i} className={i <= tier ? "on" : ""} />)}</div>
          <div className="names"><span>T0</span><span>T1</span><span>T2</span><span>T3</span></div>
        </div>
      </div>
      {a.tainted && <div className="taint" role="note">{tr("This action follows untrusted content (web page, file or tool output). Check it carefully.",
        "Hành động này đến sau nội dung không đáng tin (web, tệp, kết quả công cụ). Hãy kiểm tra kỹ.")}</div>}
      <div>
        <label className="cap">{tr("What will change", "Điều sẽ thay đổi")}</label>
        <pre className="mono preview">{String(a.preview ?? "").split("\n").map((l, i) => <div key={i} className={diffClass(l)}>{l || " "}</div>)}</pre>
      </div>
      <details open={edited}>
        <summary>{tr("Arguments", "Tham số")}{edited && <em>{tr("edited: your version will run", "đã sửa: bản của bạn sẽ chạy")}</em>}</summary>
        <textarea className="mono args" value={form.args_text} onChange={(e) => set({ args_text: e.target.value })} spellCheck={false}
          aria-label={tr("Arguments (editable; changing them approves the edited call)", "Tham số (sửa được; sửa nghĩa là duyệt bản đã sửa)")} />
        {edited && <button className="ghost sm" onClick={() => set({ args_text: blank(a).args_text })}>{tr("Undo my edit", "Hoàn tác chỉnh sửa")}</button>}
      </details>
    </fieldset>
  );
}

export function Approvals(): React.JSX.Element {
  const { session } = useStore();
  // Answers are kept per call id, so a half-filled card is never wiped by an unrelated event or by another card arriving.
  const [forms, setForms] = useState<Record<string, Form>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [sent, setSent] = useState<Record<string, boolean>>({});
  const [now, setNow] = useState(Date.now() / 1000);
  const [focus, setFocus] = useState<string | null>(null);
  const by = session.pendingForThread();
  const all = Object.entries(by).flatMap(([tid, items]) => items.map((a) => ({ tid, a })));
  const cur = all.find((x) => x.a.call_id === focus) ?? all[0];
  const ids = Object.keys(session.approvals).sort().join(",");
  const formOf = (a: Approval): Form => forms[a.call_id] ?? blank(a);
  const set = (a: Approval, f: Partial<Form>): void => setForms((c) => ({ ...c, [a.call_id]: { ...(c[a.call_id] ?? blank(a)), ...f } }));
  const live = useRef({ cur, all });
  live.current = { cur, all };

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => setSent({}), [ids]); // one answer per request; a fresh list from the daemon unlocks the buttons
  useEffect(() => {
    document.title = tr("Approval center ({n})", "Trung tâm phê duyệt ({n})", { n: Object.keys(session.approvals).length });
  });

  async function submit(threadId: string, items: Approval[]): Promise<void> {
    let decisions: Record<string, any>;
    try {
      decisions = buildDecisions(items.map((a): CardState => ({ call_id: a.call_id, tool: a.tool, tier: a.tier, args: a.args, ...formOf(a) })));
    } catch (e) {
      const missing = items.find((a) => !formOf(a).choice); // take the reader to the request that still needs an answer
      if (missing) setFocus(missing.call_id);
      return setErrors({ ...errors, [threadId]: (e as Error).message });
    }
    setErrors({ ...errors, [threadId]: "" });
    setSent({ ...sent, [threadId]: true });
    if (!(await window.agent.decide(threadId, decisions))) {
      setSent({ ...sent, [threadId]: false });
      setErrors({ ...errors, [threadId]: tr("The agent is not running or not reachable yet.", "Agent chưa chạy hoặc chưa kết nối được.") });
    }
  }

  useEffect(() => { // keys: A approve, M approve and remember, R reject, Enter send, arrows move between requests (not while typing)
    const onKey = (e: KeyboardEvent): void => {
      const { cur: c, all: list } = live.current;
      const t = e.target as HTMLElement;
      if (!c || e.ctrlKey || e.metaKey || e.altKey || ["INPUT", "TEXTAREA", "SELECT", "SUMMARY"].includes(t.tagName)) return;
      const pick = (choice: string): void => void setForms((f) => ({ ...f, [c.a.call_id]: { ...(f[c.a.call_id] ?? blank(c.a)), choice } }));
      const k = e.key.toLowerCase();
      if (k === "a") pick("approve");
      else if (k === "m") pick("remember");
      else if (k === "r") pick("reject");
      else if (e.key === "Enter" && t.tagName !== "BUTTON") void submit(c.tid, by[c.tid] ?? []);
      else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        const i = list.findIndex((x) => x.a.call_id === c.a.call_id), n = (i + (e.key === "ArrowRight" ? 1 : -1) + list.length) % list.length;
        setFocus(list[n]!.a.call_id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!cur) {
    return (
      <main className="approvals">
        <div className="zero">
          <div className="title">{tr("Nothing is waiting for your approval.", "Không có gì đang chờ bạn duyệt.")}</div>
          <p>{tr("When the agent wants to change something real, like writing a file or sending an email, the request stops here first.",
            "Khi agent muốn thay đổi thứ có thật, như ghi tệp hay gửi email, yêu cầu sẽ dừng ở đây trước.")}</p>
        </div>
      </main>
    );
  }
  const { a, tid } = cur, form = formOf(a), items = by[tid] ?? [];
  const answered = items.filter((x) => formOf(x).choice).length;
  const choices: [string, string, string, string, string][] = [
    ["approve", tr("Approve", "Duyệt"), tr("Run it once", "Chạy một lần"), "A", ""],
    ["remember", tr("Approve and remember", "Duyệt và nhớ"), tr("For this scope from now on", "Cho phạm vi này từ giờ về sau"), "M", ""],
    ["reject", tr("Reject", "Từ chối"), tr("The agent will see why", "Agent sẽ thấy lý do"), "R", " rej"]];
  return (
    <main className="approvals">
      {all.length > 1 && (
        <nav className="queue" aria-label={tr("Waiting requests", "Các yêu cầu đang chờ")}>
          {all.map((x) => <Chip key={x.a.call_id} a={x.a} form={formOf(x.a)} now={now} on={x.a.call_id === a.call_id} pick={() => setFocus(x.a.call_id)} />)}
        </nav>
      )}
      <div className="focus">
        <Card a={a} form={form} set={(f) => set(a, f)} now={now} multi={Object.keys(by).length > 1 ? tid : null} />
      </div>
      <div className="decide">
        <div className="choices" role="radiogroup" aria-label={tr("Your answer", "Câu trả lời của bạn")}>
          {choices.map(([key, label, sub, hot, cls]) => (
            <label key={key} className={"radio" + cls}>
              <input type="radio" name={a.call_id} checked={form.choice === key} onChange={() => set(a, { choice: key })} />
              <span>{label}<small>{sub}</small><kbd>{hot}</kbd></span>
            </label>
          ))}
        </div>
        {form.choice === "reject" && <input type="text" value={form.reason} onChange={(e) => set(a, { reason: e.target.value })}
          placeholder={tr("Reason for rejecting (the agent will see it)", "Lý do từ chối (agent sẽ thấy)")} />}
        {a.tier === "T3" && form.choice !== "reject" && <input type="text" value={form.confirm} onChange={(e) => set(a, { confirm: e.target.value })}
          placeholder={tr("Type CONFIRM to approve a dangerous action", "Gõ CONFIRM để duyệt hành động nguy hiểm")} />}
        <div className="send">
          {errors[tid] ? <p className="error" role="alert">{errors[tid]}</p> : <p className="muted">{items.length > 1 ? tr("{n} of {m} answered", "Đã trả lời {n}/{m}", { n: answered, m: items.length }) : ""}</p>}
          <button className="primary" disabled={sent[tid]} onClick={() => void submit(tid, items)}>{items.length > 1 ? tr("Send {n} decisions", "Gửi {n} quyết định", { n: items.length }) : tr("Send decision", "Gửi quyết định")}</button>
        </div>
      </div>
    </main>
  );
}
