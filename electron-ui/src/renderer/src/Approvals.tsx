/** Approval center: every pending action as a card (what, why, risk, editable arguments, diff preview), decided per thread.
 * This is the only window allowed to answer an approval, and it renders everything as plain text. */
import { useEffect, useState } from "react";
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

function Card({ a, form, set, now }: { a: Approval; form: Form; set: (f: Partial<Form>) => void; now: number }): React.JSX.Element {
  const risk = ["low", "medium", "high", "critical"].includes(a.risk) ? a.risk : "";
  const left = a.expires_at ? Math.max(0, Math.floor(Number(a.expires_at) - now)) : null;
  const choices: [string, string][] = [["approve", tr("Approve", "Duyệt")],
    ["remember", tr("Approve and remember for this scope", "Duyệt và nhớ cho phạm vi này")], ["reject", tr("Reject", "Từ chối")]];
  return (
    <fieldset className={`card risk-${risk}`}>
      <div><b>{a.tool}</b> <span className={`risk-text-${risk}`}>{a.tier ?? ""} · {tr("risk", "rủi ro")} {a.risk ?? ""}</span></div>
      <p>{String(a.reason ?? "")}</p>
      {a.tainted && <p className="warn">{tr("This action follows untrusted content (web page, file or tool output). Check it carefully.",
        "Hành động này đến sau nội dung không đáng tin (web, tệp, kết quả công cụ). Hãy kiểm tra kỹ.")}</p>}
      {left !== null && <p>{tr("Expires in {m}:{s} (an unanswered request counts as a rejection)", "Hết hạn sau {m}:{s} (không trả lời được tính là từ chối)",
        { m: Math.floor(left / 60), s: String(left % 60).padStart(2, "0") })}</p>}
      <label>{tr("Arguments (editable; changing them approves the edited call)", "Tham số (sửa được; sửa nghĩa là duyệt bản đã sửa)")}</label>
      <textarea className="mono args" value={form.args_text} onChange={(e) => set({ args_text: e.target.value })} spellCheck={false} />
      <label>{tr("Preview / diff", "Xem trước / khác biệt")}</label>
      <pre className="mono preview">{String(a.preview ?? "").split("\n").map((l, i) => <div key={i} className={diffClass(l)}>{l || " "}</div>)}</pre>
      {choices.map(([key, label]) => (
        <label key={key} className="radio"><input type="radio" name={a.call_id} checked={form.choice === key} onChange={() => set({ choice: key })} /> {label}</label>
      ))}
      <input type="text" value={form.reason} onChange={(e) => set({ reason: e.target.value })}
        placeholder={tr("Reason for rejecting (the agent will see it)", "Lý do từ chối (agent sẽ thấy)")} />
      {a.tier === "T3" && <input type="text" value={form.confirm} onChange={(e) => set({ confirm: e.target.value })}
        placeholder={tr("Type CONFIRM to approve a dangerous action", "Gõ CONFIRM để duyệt hành động nguy hiểm")} />}
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
  const by = session.pendingForThread();
  const ids = Object.keys(session.approvals).sort().join(",");

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
      decisions = buildDecisions(items.map((a): CardState => ({ call_id: a.call_id, tool: a.tool, tier: a.tier, args: a.args, ...(forms[a.call_id] ?? blank(a)) })));
    } catch (e) {
      return setErrors({ ...errors, [threadId]: (e as Error).message });
    }
    setErrors({ ...errors, [threadId]: "" });
    setSent({ ...sent, [threadId]: true });
    if (!(await window.agent.decide(threadId, decisions))) {
      setSent({ ...sent, [threadId]: false });
      setErrors({ ...errors, [threadId]: tr("The agent is not running or not reachable yet.", "Agent chưa chạy hoặc chưa kết nối được.") });
    }
  }

  return (
    <main className="approvals">
      {Object.keys(by).length === 0 && <p>{tr("Nothing is waiting for your approval.", "Không có gì đang chờ bạn duyệt.")}</p>}
      {Object.entries(by).map(([tid, items]) => (
        <section key={tid} className="thread">
          <div className="title">{tr("Thread", "Luồng")}: {tid}</div>
          {items.map((a) => <Card key={a.call_id} a={a} now={now} form={forms[a.call_id] ?? blank(a)}
            set={(f) => setForms((cur) => ({ ...cur, [a.call_id]: { ...(cur[a.call_id] ?? blank(a)), ...f } }))} />)}
          {errors[tid] && <p className="error">{errors[tid]}</p>}
          <button className="primary" disabled={sent[tid]} onClick={() => void submit(tid, items)}>{tr("Send decisions", "Gửi quyết định")}</button>
        </section>
      ))}
    </main>
  );
}
