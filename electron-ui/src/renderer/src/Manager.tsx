/** The management pages. All the rules (reply matching, locks, notes) live in shared/manager.ts; this file only draws and asks. */
import { useEffect, useState, useSyncExternalStore } from "react";
import { tr } from "../../shared/i18n";
import { Manager, RAG_EXTENSIONS, type Row, TABLES, viewRows } from "../../shared/manager";
import type { Ev } from "../../shared/session";
import { alertBox, confirmBox, formBox, promptText } from "./dialogs";
import { store } from "./store";

let version = 0;
const listeners = new Set<() => void>();
export const manager = new Manager((msg) => void window.agent.send(msg), () => {
  version += 1;
  listeners.forEach((l) => l());
});
store.onDaemonEvent((ev) => void manager.onEvent(ev));
const useManager = (): Manager => (useSyncExternalStore((l) => (listeners.add(l), () => listeners.delete(l)), () => version), manager);

// A row action: returns the message to send, or null to send nothing (cancelled, or it only shows something).
type Action = { en: string; vi: string; make: (row: Row) => Promise<Ev | null> | Ev | null; applies?: (row: Row) => boolean };
const yes = (title: [string, string], text: [string, string]): Promise<boolean> => confirmBox(tr(...title), tr(...text));

function mcpWhat(row: Row): string {
  const verb = row.kind === "http" ? tr("connect to", "kết nối tới") : tr("run", "chạy");
  const env: string[] = row.env ?? [];
  return `${verb}:\n\n${row.target ?? ""}` + (env.length ? tr("\nEnvironment variables passed: ", "\nBiến môi trường được truyền: ") + env.join(", ") : "");
}

const ACTIONS: Record<string, Action[]> = {
  memory: [
    { en: "Edit", vi: "Sửa", make: async (row) => {
      // The daemon re-checks length and sensitivity.
      const text = (await promptText(tr("Edit memory", "Sửa bộ nhớ"), tr("Text", "Nội dung"), String(row.text)))?.trim();
      if (!text || text === row.text) return null;
      if (text.length > 2000) return alertBox(tr("Edit memory", "Sửa bộ nhớ"), tr("At most 2000 characters.", "Tối đa 2000 ký tự.")).then(() => null);
      return { type: "memory_update", id: row.id, text };
    } },
    { en: "Approve", vi: "Duyệt", make: async (row) => {
      // Approving activates the memory. A sensitive one (password, key, personal data) needs an explicit second yes.
      if (row.sensitive && !(await yes(["Approve memory", "Duyệt bộ nhớ"], [
        "This memory looks sensitive (password, key, personal data). Approving lets the agent recall and use it. Approve anyway?",
        "Mục này có vẻ nhạy cảm (mật khẩu, khóa, dữ liệu cá nhân). Duyệt sẽ cho agent nhớ và dùng nó. Vẫn duyệt?"]))) return null;
      return { type: "memory_update", id: row.id, status: "active" };
    } },
    { en: "Delete", vi: "Xóa", make: async (row) => {
      const snippet = String(row.text).slice(0, 200);
      return (await yes(["Delete memory", "Xóa bộ nhớ"], ["Delete this memory permanently?\n\n" + snippet, "Xóa vĩnh viễn mục này?\n\n" + snippet]))
        ? { type: "memory_delete", id: row.id } : null;
    } },
  ],
  rag: [{ en: "Remove from index", vi: "Xóa khỏi chỉ mục", make: async (row) =>
    (await yes(["Remove from index", "Xóa khỏi chỉ mục"], [`Remove this document from the index?\nThe file itself is not deleted.\n\n${row.source}`,
      `Xóa tài liệu này khỏi chỉ mục?\nTệp gốc không bị xóa.\n\n${row.source}`])) ? { type: "rag_remove", source: row.source } : null }],
  mcp: [
    { en: "Enable", vi: "Bật", applies: (r) => !r.enabled, make: async (row) =>
      (await yes(["Enable server", "Bật máy chủ"], [
        `Enable '${row.name}'? After the next reload the agent will ${mcpWhat(row)}\n\nIts tools ask for your approval unless mcp.yaml says otherwise.`,
        `Bật '${row.name}'? Sau lần tải lại tiếp theo, agent sẽ ${mcpWhat(row)}\n\nCông cụ của nó sẽ hỏi bạn duyệt trừ khi mcp.yaml quy định khác.`]))
        ? { type: "mcp_set", name: row.name, enabled: true } : null },
    { en: "Disable", vi: "Tắt", applies: (r) => Boolean(r.enabled), make: async (row) => {
      const n = (row.tools ?? []).length;
      return (await yes(["Disable server", "Tắt máy chủ"], [`Disable '${row.name}'? Its ${n} tool(s) are removed from the agent after the next reload.`,
        `Tắt '${row.name}'? ${n} công cụ của nó sẽ bị gỡ khỏi agent sau lần tải lại tiếp theo.`])) ? { type: "mcp_set", name: row.name, enabled: false } : null;
    } },
    { en: "Tools", vi: "Công cụ", make: (row) => (manager.mcpShowTools(row), null) },
    { en: "Remove", vi: "Xóa", make: async (row) =>
      (await yes(["Remove server", "Xóa máy chủ"], [`Remove '${row.name}' from mcp.yaml? Its tools disappear after the next reload.`,
        `Xóa '${row.name}' khỏi mcp.yaml? Công cụ của nó sẽ biến mất sau lần tải lại tiếp theo.`])) ? { type: "mcp_remove", name: row.name } : null },
  ],
  schedules: [{ en: "Cancel", vi: "Hủy", make: (r) => ({ type: "schedule_cancel", id: r.id }) }],
  rules: [{ en: "Forget", vi: "Quên", make: (r) => ({ type: "forget_rule", id: r.id }) }],
};

function Table({ page, children, below }: { page: string; children?: React.ReactNode; below?: React.ReactNode }): React.JSX.Element {
  const m = useManager();
  const spec = TABLES[page]!, t = m.tables[page]!, actions = ACTIONS[page] ?? [];
  const [needle, setNeedle] = useState("");
  const [facet, setFacet] = useState("");
  const [sort, setSort] = useState<[number, boolean] | null>(null);
  const facets = spec.facet ? [...new Set(t.rows.map((r) => String(spec.facet![2](r))))].sort() : [];
  const view = viewRows(t.rows, spec, { facet: facets.includes(facet) ? facet : "", needle, sort });

  async function run(row: Row, i: number): Promise<void> {
    const msg = await actions[i]!.make(row);
    if (msg) m.act(page, row, i, msg);
  }

  return (
    <div className="mgr">
      <div className="row">
        <button onClick={() => m.refresh(page)}>{tr("Refresh", "Làm mới")}</button>
        {spec.searchable && <input type="text" className="grow" value={needle} onChange={(e) => setNeedle(e.target.value)} placeholder={tr("Search...", "Tìm kiếm...")} />}
        {spec.facet && (
          <select className="facet" value={facets.includes(facet) ? facet : ""} onChange={(e) => setFacet(e.target.value)} aria-label={tr(spec.facet[0], spec.facet[1])}>
            <option value="">{tr("All", "Tất cả")}</option>
            {facets.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        )}
      </div>
      {children}
      {view.length > 0 ? (
        <div className="tablebox">
          <table>
            <thead>
              <tr>
                {spec.columns.map(([en, vi], c) => (
                  <th key={c} className={c === (spec.wide ?? spec.columns.length - 1) ? "wide" : ""} onClick={() => setSort([c, sort?.[0] === c ? !sort[1] : false])}>
                    {tr(en, vi)}{sort?.[0] === c ? (sort[1] ? " ▼" : " ▲") : ""}
                  </th>
                ))}
                {actions.length > 0 && <th>{tr("Actions", "Thao tác")}</th>}
              </tr>
            </thead>
            <tbody>
              {view.map((row, r) => {
                const busy = t.busy(row);
                return (
                  <tr key={r}>
                    {spec.columns.map(([, , get], c) => <td key={c} className={c === (spec.wide ?? spec.columns.length - 1) ? "wide" : ""} title={String(get(row))}>{String(get(row))}</td>)}
                    {actions.length > 0 && (
                      <td className="acts">
                        {actions.map((a, i) => (a.applies && !a.applies(row) ? null : (
                          <button key={i} disabled={busy !== null} title={`${tr(a.en, a.vi)}: ${spec.columns[0]![2](row)}`} onClick={() => void run(row, i)}>
                            {busy === i ? tr("Working…", "Đang xử lý…") : tr(a.en, a.vi)}
                          </button>
                        )))}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty muted">{t.rows.length ? tr("No matches.", "Không có kết quả phù hợp.") : tr(spec.empty[0], spec.empty[1])}</p>
      )}
      {below}
    </div>
  );
}

function Documents(): React.JSX.Element {
  const m = useManager(), g = m.rag, busy = g.job !== null;
  const [query, setQuery] = useState("");
  const add = async (kind: "file" | "folder"): Promise<void> => {
    const path = await window.agent.pick(kind, kind === "file" ? tr("Add file", "Thêm tệp") : tr("Add folder", "Thêm thư mục"), RAG_EXTENSIONS);
    if (path) m.ragStart({ type: "rag_add", path });
  };
  const sync = async (): Promise<void> => {
    if (await yes(["Sync folders", "Đồng bộ thư mục"], [
      "Re-scan the watched folders? New and changed files are indexed, and entries for files that no longer exist are removed from the index.",
      "Quét lại các thư mục theo dõi? Tệp mới và tệp đã đổi sẽ được lập chỉ mục; mục của tệp không còn tồn tại sẽ bị gỡ khỏi chỉ mục."])) m.ragStart({ type: "rag_sync" });
  };
  const state = busy ? tr("Indexing… this can take a while.", "Đang lập chỉ mục… có thể mất một lúc.")
    : g.watching && g.paused ? tr("Watching folders: paused.", "Theo dõi thư mục: đang tạm dừng.")
    : g.watching ? tr("Watching folders: on.", "Theo dõi thư mục: đang bật.")
    : tr("No watched folders (set rag.watch in the config).", "Chưa theo dõi thư mục nào (đặt rag.watch trong cấu hình).");
  return (
    <Table page="rag" below={
      <>
        <div className="row">
          <input type="text" className="grow" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && m.ragSearch(query)}
            placeholder={tr("Try a question against the indexed documents…", "Thử hỏi trên các tài liệu đã lập chỉ mục…")} />
          <button disabled={g.search !== null} onClick={() => m.ragSearch(query)}>{tr("Search", "Tìm")}</button>
        </div>
        <textarea className="output" readOnly value={g.output} placeholder={tr("Search results and sync reports appear here.", "Kết quả tìm kiếm và báo cáo đồng bộ hiện ở đây.")} />
      </>
    }>
      <div className="row">
        <button disabled={busy} onClick={() => void add("file")} title={tr("Index one file (md, docx, pdf, txt)", "Lập chỉ mục một tệp (md, docx, pdf, txt)")}>{tr("Add file…", "Thêm tệp…")}</button>
        <button disabled={busy} onClick={() => void add("folder")} title={tr("Index every supported file in a folder", "Lập chỉ mục mọi tệp được hỗ trợ trong thư mục")}>{tr("Add folder…", "Thêm thư mục…")}</button>
        <button disabled={busy || !g.watching} onClick={() => void sync()}
          title={tr("Re-scan the watched folders; entries for deleted files are removed", "Quét lại thư mục theo dõi; mục của tệp đã xóa sẽ bị gỡ")}>{tr("Sync folders", "Đồng bộ thư mục")}</button>
        <button disabled={!g.watching} onClick={() => void window.agent.send({ type: g.paused ? "rag_resume" : "rag_pause" })}>
          {g.paused ? tr("Resume watching", "Tiếp tục theo dõi") : tr("Pause watching", "Tạm dừng theo dõi")}</button>
        <span className="muted grow">{state}</span>
      </div>
    </Table>
  );
}

/** Collects a new MCP server. The server is stored disabled; nothing runs until the user enables it and reloads. */
function AddServer({ close }: { close: (r: Ev | null) => void }): React.JSX.Element {
  const [f, setF] = useState({ name: "", kind: "stdio", command: "", args: "", env: "", url: "" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const stdio = f.kind === "stdio";
  const payload = (): Ev => {
    const out = { type: "mcp_add", name: f.name.trim() };
    if (!stdio) return { ...out, url: f.url.trim() };
    const env = Object.fromEntries(f.env.split("\n").filter((l) => l.trim()).map((l) => {
      const i = l.indexOf("=");
      return i < 0 ? [l.trim(), ""] : [l.slice(0, i).trim(), l.slice(i + 1).trim()];
    }));
    return { ...out, command: f.command.trim(), args: f.args.split("\n").filter((a) => a.trim()), env };
  };
  return (
    <div className="form">
      <label>{tr("Name", "Tên")}</label><input type="text" autoFocus value={f.name} onChange={set("name")} />
      <label>{tr("Type", "Loại")}</label>
      <select value={f.kind} onChange={set("kind")}>
        <option value="stdio">{tr("Local command (stdio)", "Lệnh cục bộ (stdio)")}</option>
        <option value="http">{tr("Remote URL (http)", "URL từ xa (http)")}</option>
      </select>
      <label>{tr("Command", "Lệnh")}</label><input type="text" disabled={!stdio} value={f.command} onChange={set("command")} />
      <label>{tr("Arguments", "Đối số")}</label>
      <textarea rows={3} disabled={!stdio} value={f.args} onChange={set("args")} placeholder={tr("One argument per line", "Mỗi dòng một đối số")} />
      <label>{tr("Environment", "Môi trường")}</label>
      <textarea rows={3} disabled={!stdio} value={f.env} onChange={set("env")}
        placeholder={tr("NAME=value or NAME=${SECRET_NAME}, one per line", "TEN=giá trị hoặc TEN=${TEN_BÍ_MẬT}, mỗi dòng một biến")} />
      <label>URL</label><input type="text" disabled={stdio} value={f.url} onChange={set("url")} />
      <p className="span">{tr("The server is added disabled. Review it, enable it, then reload: only then is anything started or contacted. Its tools will ask for your approval.",
        "Máy chủ được thêm ở trạng thái tắt. Hãy xem lại, bật nó rồi tải lại: chỉ khi đó mới có gì được khởi chạy hay kết nối. Các công cụ của nó sẽ hỏi bạn duyệt.")}</p>
      <div className="buttons span">
        <button className="primary" onClick={() => close(payload())}>OK</button>
        <button onClick={() => close(null)}>{tr("Cancel", "Hủy")}</button>
      </div>
    </div>
  );
}

function Mcp(): React.JSX.Element {
  const m = useManager(), busy = m.mcp.job !== null;
  const add = async (): Promise<void> => {
    const msg = await formBox<Ev>(tr("Add MCP server", "Thêm máy chủ MCP"), (close) => <AddServer close={close} />);
    if (msg) void window.agent.send(msg);
  };
  const reload = async (): Promise<void> => {
    const names = m.tables.mcp!.rows.filter((r) => r.enabled).map((r) => `• ${r.name}: ${r.target ?? ""}`);
    const listing = names.join("\n") || tr("(no enabled servers)", "(không có máy chủ nào đang bật)");
    if (await yes(["Reload MCP", "Tải lại MCP"], ["Reconnect every enabled server? Local commands are started and remote URLs are contacted:\n\n" + listing,
      "Kết nối lại mọi máy chủ đang bật? Các lệnh cục bộ sẽ được chạy và các URL từ xa sẽ được kết nối:\n\n" + listing])) m.mcpReload();
  };
  const state = busy ? tr("Reconnecting to the servers… this can take a while.", "Đang kết nối lại các máy chủ… có thể mất một lúc.")
    : m.mcp.reloadNeeded ? tr("Changes are not applied yet: reload MCP.", "Thay đổi chưa được áp dụng: hãy tải lại MCP.") : tr("Up to date.", "Đã cập nhật.");
  return (
    <Table page="mcp" below={<textarea className="output" readOnly value={m.mcp.output}
      placeholder={tr("Tools of a server and reload results appear here.", "Công cụ của máy chủ và kết quả tải lại hiện ở đây.")} />}>
      <div className="row">
        <button disabled={busy} onClick={() => void add()} title={tr("Add an MCP server (stored disabled)", "Thêm máy chủ MCP (lưu ở trạng thái tắt)")}>{tr("Add server…", "Thêm máy chủ…")}</button>
        <button disabled={busy} onClick={() => void reload()} title={tr("Apply the changes: reconnect every enabled server", "Áp dụng thay đổi: kết nối lại mọi máy chủ đang bật")}>
          {busy ? tr("Reloading…", "Đang tải lại…") : tr("Reload MCP", "Tải lại MCP")}</button>
        <span className="muted grow">{state}</span>
      </div>
    </Table>
  );
}

const num = (v: number, d = 0): string => Number(v ?? 0).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v: number | null | undefined): string => (v == null ? "-" : `${Math.round(v * 100)}%`);

function Grid({ heads, rows }: { heads: string[]; rows: unknown[][] }): React.JSX.Element {
  return (
    <div className="tablebox">
      <table>
        <thead><tr>{heads.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{String(v)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function Usage(): React.JSX.Element {
  const m = useManager(), ev = m.usage ?? {}, today = ev.today ?? {}, caps = ev.caps ?? {}, rep = ev.report ?? {};
  const meter = (used: number, cap: number | undefined, unit: "tokens" | "$"): React.JSX.Element => (
    <div className="meter"><div className="fill" style={{ width: `${cap ? Math.min(100, (100 * used) / cap) : 0}%` }} />
      <span>{!cap ? "-" : unit === "$" ? `$${num(used, 4)} / $${num(cap, 2)}` : `${num(used)} / ${num(cap)} ${unit}`}</span></div>
  );
  return (
    <div className="mgr scroll">
      <div className="row"><button onClick={() => m.refresh("usage")}>{tr("Refresh", "Làm mới")}</button></div>
      <p>{tr("Today: {tokens} tokens, ${cost}, {calls} calls · Langfuse: {lf}", "Hôm nay: {tokens} token, ${cost}, {calls} lần gọi · Langfuse: {lf}",
        { tokens: num(today.tokens), cost: num(today.cost_usd, 4), calls: today.calls ?? 0, lf: ev.langfuse ? tr("on", "bật") : tr("off", "tắt") })}</p>
      {meter(today.tokens ?? 0, caps.tokens, "tokens")}
      {meter(today.cost_usd ?? 0, caps.cost_usd, "$")}
      <Grid heads={[tr("Day", "Ngày"), tr("Tokens", "Token"), tr("Cost $", "Chi phí $"), tr("LLM calls", "Gọi LLM"), tr("LLM error rate", "Tỷ lệ lỗi LLM"),
        tr("Avg ms", "ms TB"), tr("Tool calls", "Gọi công cụ"), tr("Tool success", "Công cụ thành công")]}
        rows={(rep.days ?? []).map((d: Row) => [d.day, d.tokens, num(d.cost_usd, 4), d.llm_calls, pct(d.llm_error_rate), d.llm_avg_ms || "-", d.tool_calls, pct(d.tool_success_rate)])} />
      <Grid heads={[tr("Model", "Mô hình"), tr("Calls", "Số lần"), tr("Errors", "Lỗi"), "ms"]} rows={(rep.models ?? []).map((x: Row) => [x.model, x.calls, x.errors, x.avg_ms])} />
      <Grid heads={[tr("Tool", "Công cụ"), tr("Calls", "Số lần"), tr("OK", "Thành công"), "ms"]} rows={(rep.tools ?? []).map((x: Row) => [x.tool, x.calls, x.ok, x.avg_ms])} />
    </div>
  );
}

/** Raw YAML editor for policy.yaml. The daemon validates before saving and keeps a .bak copy. */
function Policy(): React.JSX.Element {
  const m = useManager();
  const [text, setText] = useState(m.policy.text);
  useEffect(() => setText(m.policy.text), [m.policy.loads]);
  return (
    <div className="mgr">
      <p>{tr("Safety policy (tiers, allowed folders, commands, domains). It is validated before saving, the old file is kept as policy.yaml.bak, and changes apply immediately. Be careful: this controls what the agent may do.",
        "Chính sách an toàn (mức rủi ro, thư mục, lệnh, tên miền). Được kiểm tra trước khi lưu, bản cũ giữ ở policy.yaml.bak, áp dụng ngay. Hãy cẩn thận: đây là thứ quyết định agent được làm gì.")}</p>
      <textarea className="mono grow policy" wrap="off" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="row">
        <button onClick={() => m.refresh("policy")}>{tr("Reload", "Tải lại")}</button>
        <button className="primary" onClick={() => void window.agent.send({ type: "policy_set", text })}>{tr("Save", "Lưu")}</button>
        <span className="muted">{m.policy.saved ? tr("Saved.", "Đã lưu.") : ""}</span>
      </div>
    </div>
  );
}

/** The page `key`, refreshed from the daemon every time it is opened. */
export function ManagerPage({ page }: { page: string }): React.JSX.Element {
  useEffect(() => manager.refresh(page), [page]);
  if (page === "rag") return <Documents />;
  if (page === "mcp") return <Mcp />;
  if (page === "usage") return <Usage />;
  if (page === "policy") return <Policy />;
  return <Table key={page} page={page} />;
}

export const ManagerStatus = (): React.JSX.Element => <span className="status">{useManager().status}</span>;
