/** The management pages (Memory, Documents, Tools/MCP, Schedules, Remembered approvals, Audit, Usage, Policy) without any drawing:
 * which request fills which list, how replies are matched to requests, which rows are locked while an action is in flight.
 * Pure (send and timers are injected), so the rules that keep the lists truthful are tested without a window. */
import { tr } from "./i18n";
import type { Ev } from "./session";

export type Row = Record<string, any>;
export type Column = [en: string, vi: string, get: (r: Row) => unknown];
export type TableSpec = { request: Ev; event: string; rowsOf: (ev: Ev) => Row[]; columns: Column[]; wide?: number; searchable?: boolean;
  facet?: Column; empty: [string, string] };

export const REPLY_TIMEOUT_MS = 15_000;
export const RAG_JOB_LIMIT_MS = 600_000; // unlock anyway if the daemon never answers (e.g. the connection dropped)
export const MCP_JOB_LIMIT_MS = 180_000;
export const RAG_EXTENSIONS = ["docx", "markdown", "md", "pdf", "txt"];

let rids = 0; // request ids: the daemon echoes them, so replies can be matched and stale ones dropped
export const nextRid = (): number => ++rids;

export function ts(v: unknown): string {
  if (!v) return "";
  const d = new Date(Number(v) * 1000), p = (n: number): string => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// Numbers sort before text and by value; text sorts without regard to case.
function compare(a: unknown, b: unknown): number {
  const na = typeof a === "number", nb = typeof b === "number";
  if (na && nb) return (a as number) - (b as number);
  if (na !== nb) return na ? -1 : 1;
  const [x, y] = [String(a).toLowerCase(), String(b).toLowerCase()];
  return x < y ? -1 : x > y ? 1 : 0;
}

/** What a table shows: the rows that pass the facet and the search box, in the chosen order. */
export function viewRows(rows: Row[], spec: TableSpec, o: { facet?: string; needle?: string; sort?: [col: number, desc: boolean] | null }): Row[] {
  let view = rows;
  if (spec.facet && o.facet) view = view.filter((r) => String(spec.facet![2](r)) === o.facet);
  const needle = (o.needle ?? "").trim().toLowerCase();
  if (needle) view = view.filter((r) => spec.columns.some(([, , get]) => String(get(r)).toLowerCase().includes(needle)));
  if (o.sort) {
    const [col, desc] = o.sort, get = spec.columns[col]![2];
    view = [...view].sort((a, b) => compare(get(a), get(b)) * (desc ? -1 : 1));
  }
  return view;
}

/** One list. A row whose action is in flight stays locked until the daemon answers that request (or the list is rebuilt). */
export class TableState {
  rows: Row[] = [];
  latest = 0; // id of the newest list request or action; older list replies are dropped
  pending = new Map<number, { row: Row; action: number }>();

  load(rid: unknown, rows: Row[]): boolean {
    if (Number.isInteger(rid) && (rid as number) < this.latest) return false; // an older list that crossed a newer request could bring back a deleted row
    this.rows = rows;
    this.pending.clear(); // a list requested after every action is the daemon's truth
    return true;
  }

  busy(row: Row): number | null {
    for (const p of this.pending.values()) if (p.row === row) return p.action;
    return null;
  }
}

/** One short line for a sync / add report: how many files were indexed, unchanged, removed or failed. */
export function ragSummary(report: string[]): string {
  if (!report.length) return tr("No supported files found.", "Không tìm thấy tệp được hỗ trợ.");
  const n = (k: string): number => report.filter((l) => l.startsWith(k)).length;
  const out = [tr("{n} indexed", "{n} đã lập chỉ mục", { n: n("ingested") + n("updated") }), tr("{n} unchanged", "{n} không đổi", { n: n("unchanged") }),
    tr("{n} removed", "{n} đã gỡ", { n: n("removed") })].join(", ");
  if (!n("failed")) return out;
  return `! ${tr("{n} failed", "{n} lỗi", { n: n("failed") })} (${report.find((l) => l.startsWith("failed"))!.slice(0, 80)}); ${out}`;
}

/** The message shown after a change, once the list that follows it has loaded. */
export function resultNote(ev: Ev): string {
  const gone = (en: string, vi: string): string => "! " + tr(en, vi);
  switch (ev.type) {
    case "memory_deleted":
      return ev.ok ? tr("Deleted.", "Đã xóa.") : gone("That memory no longer exists.", "Mục này không còn tồn tại.");
    case "memory_updated":
      if (!ev.ok) return gone("That memory no longer exists.", "Mục này không còn tồn tại.");
      return ev.status === "proposed" ? tr("Saved. It looks sensitive, so it is waiting for your approval.", "Đã lưu. Nội dung có vẻ nhạy cảm nên đang chờ bạn duyệt.")
        : tr("Saved.", "Đã lưu.");
    case "mcp_set":
      return ev.ok ? tr("Saved. Reload MCP to apply the change.", "Đã lưu. Hãy tải lại MCP để áp dụng.") : gone("That server no longer exists.", "Máy chủ này không còn tồn tại.");
    case "mcp_added":
      return tr("Server added (disabled). Review it, enable it, then reload.", "Đã thêm máy chủ (đang tắt). Hãy xem lại, bật rồi tải lại.");
    case "mcp_removed":
      return ev.ok ? tr("Removed. Reload MCP to apply.", "Đã xóa. Hãy tải lại MCP để áp dụng.") : gone("That server no longer exists.", "Máy chủ này không còn tồn tại.");
    case "rag_removed":
      return ev.chunks ? tr("Removed from the index (the file is untouched).", "Đã xóa khỏi chỉ mục (tệp gốc giữ nguyên).")
        : tr("That document was already gone from the index.", "Tài liệu này không còn trong chỉ mục.");
    case "rag_synced":
      if (!ev.folders) return gone("No watched folder is available to sync.", "Không có thư mục theo dõi nào để đồng bộ.");
      return ragSummary(ev.report ?? []);
    case "rag_added":
      return ragSummary(ev.report ?? []);
  }
  return "";
}

const yesNo = (v: unknown): string => (v ? tr("Yes", "Có") : tr("No", "Không"));

export const TABLES: Record<string, TableSpec> = {
  memory: { request: { type: "memory_list" }, event: "memory", rowsOf: (e) => e.items, wide: 3, searchable: true,
    columns: [["ID", "ID", (r) => r.id], ["Kind", "Loại", (r) => r.kind], ["Status", "Trạng thái", (r) => r.status + (r.sensitive ? " !" : "")],
      ["Text", "Nội dung", (r) => r.text]],
    facet: ["Status", "Trạng thái", (r) => r.status],
    empty: ["No memories yet. The agent saves them as you chat.", "Chưa có bộ nhớ nào. Agent sẽ lưu khi bạn trò chuyện."] },
  rag: { request: { type: "rag_list" }, event: "rag", rowsOf: (e) => e.items, wide: 0, searchable: true,
    columns: [["Source", "Nguồn", (r) => r.source], ["Type", "Loại", (r) => r.kind], ["Chunks", "Đoạn", (r) => r.chunks], ["Updated", "Cập nhật", (r) => ts(r.updated)]],
    facet: ["Type", "Loại", (r) => r.kind],
    empty: ["No documents indexed yet. Use Add file / Add folder, or list folders under rag.watch in the config.",
      "Chưa có tài liệu nào. Dùng Thêm tệp / Thêm thư mục, hoặc khai báo thư mục trong rag.watch ở cấu hình."] },
  mcp: { request: { type: "mcp_list" }, event: "mcp", rowsOf: (e) => Object.entries(e.servers ?? {}).map(([name, v]) => ({ name, ...(v as Row) })),
    wide: 2, searchable: true,
    columns: [["Server", "Máy chủ", (r) => r.name], ["Type", "Loại", (r) => (r.kind === "http" ? "URL" : tr("Command", "Lệnh"))],
      ["Runs / connects to", "Chạy / kết nối", (r) => r.target ?? ""], ["Enabled", "Bật", (r) => yesNo(r.enabled)],
      ["Status", "Trạng thái", (r) => (r.status ?? "") + (r.reload_needed ? tr("  ⟳ reload needed", "  ⟳ cần tải lại") : "")],
      ["Tools", "Công cụ", (r) => (r.tools ?? []).length]],
    empty: ["No MCP servers yet. Use Add server…, or edit mcp.yaml in the data folder.", "Chưa có máy chủ MCP nào. Dùng Thêm máy chủ…, hoặc sửa mcp.yaml trong thư mục dữ liệu."] },
  schedules: { request: { type: "schedules" }, event: "schedules", rowsOf: (e) => e.items,
    columns: [["ID", "ID", (r) => r.id], ["Enabled", "Bật", (r) => yesNo(r.enabled)], ["Every (s)", "Mỗi (giây)", (r) => r.every_s],
      ["Last status", "Kết quả gần nhất", (r) => r.last_status ?? ""], ["Task", "Tác vụ", (r) => r.prompt]],
    empty: ["Nothing here yet.", "Chưa có mục nào."] },
  rules: { request: { type: "rules" }, event: "rules", rowsOf: (e) => e.items,
    columns: [["ID", "ID", (r) => r.id], ["Tool", "Công cụ", (r) => r.tool], ["Scope", "Phạm vi", (r) => r.scope]],
    empty: ["Nothing here yet.", "Chưa có mục nào."] },
  audit: { request: { type: "audit", limit: 200 }, event: "audit", rowsOf: (e) => e.items,
    columns: [["Time", "Thời gian", (r) => ts(r.ts)], ["Tool", "Công cụ", (r) => r.tool], ["Tier", "Mức", (r) => r.tier], ["Decision", "Quyết định", (r) => r.decision],
      ["Result", "Kết quả", (r) => r.result]],
    empty: ["Nothing here yet.", "Chưa có mục nào."] },
};

// mutation reply -> the list it changed (re-requested, so the page shows the daemon's truth)
const AFTER: Record<string, string> = { memory_updated: "memory", memory_deleted: "memory", mcp_set: "mcp", schedule_cancel: "schedules",
  rag_synced: "rag", rag_added: "rag", rag_removed: "rag", mcp_added: "mcp", mcp_removed: "mcp" };

type Timer = (fn: () => void, ms: number) => unknown;

export class Manager {
  tables: Record<string, TableState> = Object.fromEntries(Object.keys(TABLES).map((k) => [k, new TableState()]));
  status = ""; // the short message next to the page title
  rag = { job: null as number | null, search: null as number | null, watching: false, paused: false, output: "" };
  mcp = { job: null as number | null, reloadNeeded: false, output: "" };
  usage: Ev | null = null;
  policy = { text: "", saved: false, loads: 0 }; // `loads` lets the editor know a fresh text arrived
  private note = ""; // shown once, after the list that follows a change has loaded

  constructor(private send: (msg: Ev) => void, private changed: () => void = () => undefined, private timer: Timer = (fn, ms) => setTimeout(fn, ms)) {} // wrapped: a browser refuses setTimeout called as a method

  /** Ask the daemon for what page `key` shows. */
  refresh(key: string): void {
    const spec = TABLES[key];
    if (spec) this.send({ ...spec.request, rid: (this.tables[key]!.latest = nextRid()) });
    else if (key === "usage") this.send({ type: "usage", days: 14 });
    else if (key === "policy") this.send({ type: "policy_get" });
  }

  /** Run a row action: the row is locked until the daemon answers this very request. */
  act(key: string, row: Row, action: number, msg: Ev): void {
    const t = this.tables[key]!;
    const rid = (t.latest = nextRid()); // also supersedes any list request still in flight
    t.pending.set(rid, { row, action });
    this.timer(() => {
      // No answer: the outcome is unknown, so keep the row locked and ask the daemon for the truth instead of letting a second click repeat it.
      if (!t.pending.has(rid)) return;
      this.status = "! " + tr("No answer from the agent yet; refreshing the list.", "Agent chưa trả lời; đang làm mới danh sách.");
      this.refresh(key);
      this.changed();
    }, REPLY_TIMEOUT_MS);
    this.send({ ...msg, rid });
    this.changed();
  }

  // ---- documents -----------------------------------------------------------------------------------------------------------
  /** Start an indexing job (rag_add / rag_sync): its buttons stay locked until the daemon answers. */
  ragStart(msg: Ev): void {
    const rid = (this.rag.job = nextRid());
    this.status = tr("Indexing…", "Đang lập chỉ mục…");
    this.timer(() => this.ragFinish(rid) && this.changed(), RAG_JOB_LIMIT_MS);
    this.send({ ...msg, rid });
    this.changed();
  }

  ragSearch(query: string): void {
    const q = query.trim();
    if (!q || this.rag.search !== null) return;
    const rid = (this.rag.search = nextRid());
    this.rag.output = tr("Searching…", "Đang tìm…");
    this.timer(() => this.ragFinish(rid) && this.changed(), REPLY_TIMEOUT_MS * 4);
    this.send({ type: "rag_search", query: q, rid });
    this.changed();
  }

  private ragFinish(rid: unknown): boolean {
    let hit = false;
    if (rid != null && rid === this.rag.job) [this.rag.job, hit] = [null, true];
    if (rid != null && rid === this.rag.search) [this.rag.search, hit] = [null, true];
    return hit;
  }

  // ---- MCP -----------------------------------------------------------------------------------------------------------------
  mcpReload(): void {
    const rid = (this.mcp.job = nextRid());
    this.timer(() => this.mcpFinish(rid) && this.changed(), MCP_JOB_LIMIT_MS);
    this.send({ type: "mcp_reload", rid });
    this.changed();
  }

  mcpShowTools(row: Row): void {
    const lines = [`${row.name}: ${row.status ?? ""}`];
    for (const t of row.tools ?? []) lines.push(`  [${t.tier}] ${t.name}` + (t.tier === "T0" ? "  ! " + tr("runs without approval", "chạy không cần duyệt") : ""));
    for (const t of row.skipped ?? []) lines.push(`  ✗ ${t.name}: ${t.reason}`);
    if (lines.length === 1) lines.push("  " + tr("No tools loaded.", "Chưa có công cụ nào được nạp."));
    this.mcp.output = lines.join("\n");
    this.changed();
  }

  private mcpFinish(rid: unknown): boolean {
    if (rid == null || rid !== this.mcp.job) return false;
    this.mcp.job = null;
    return true;
  }

  // ---- events --------------------------------------------------------------------------------------------------------------
  /** Feed a daemon event; returns true if a page consumed it. */
  onEvent(ev: Ev): boolean {
    const t = ev.type ?? "";
    const done = (used: boolean): boolean => (this.changed(), used);
    if (t === "mcp" && ev.reloaded) {
      const all = Object.values(ev.servers ?? {}) as Row[];
      const bad = all.filter((v) => String(v.status ?? "").startsWith("error")).length;
      this.note = (bad ? "! " : "") + tr("Reloaded: {ok} ok, {bad} failed.", "Đã tải lại: {ok} ổn, {bad} lỗi.", { ok: all.length - bad, bad });
    }
    if (t === "usage") {
      this.usage = ev;
      return done(true);
    }
    if (t === "policy") {
      this.policy = { text: String(ev.text ?? ""), saved: Boolean(ev.saved), loads: this.policy.loads + 1 };
      return done(true);
    }
    const key = Object.keys(TABLES).find((k) => TABLES[k]!.event === t);
    if (key) {
      if (this.tables[key]!.load(ev.rid, TABLES[key]!.rowsOf(ev) ?? [])) {
        if (key === "rag") Object.assign(this.rag, { watching: Boolean(ev.watching), paused: Boolean(ev.paused) });
        if (key === "mcp") {
          this.mcp.reloadNeeded = Boolean(ev.reload_needed);
          if (ev.reloaded) {
            this.mcpFinish(ev.rid);
            this.mcp.output = Object.entries(ev.servers ?? {}).map(([n, v]) => `${n}: ${(v as Row).status ?? ""}`).join("\n");
          }
        }
        this.status = this.note;
        this.note = "";
      }
      return done(true);
    }
    if (t === "rag_results") {
      if (this.ragFinish(ev.rid)) { // otherwise: a reply to a search that was superseded or timed out
        const hits: Row[] = ev.hits ?? [];
        const body = hits.map((h, i) => `[${i + 1}] ${h.cite}  (${h.score})\n${h.text}`).join("\n\n");
        this.rag.output = (ev.note ? `${ev.note}\n\n` : "") + (body || tr("No matching passages.", "Không có đoạn nào phù hợp."));
      }
      return done(true);
    }
    if (t in AFTER) {
      this.note = resultNote(ev);
      if (t === "rag_synced" || t === "rag_added") {
        this.ragFinish(ev.rid);
        if (ev.report?.length) this.rag.output = ev.report.map(String).join("\n");
      } else if (t === "rag_removed") this.ragFinish(ev.rid);
      this.refresh(AFTER[t]!);
      return done(true);
    }
    if (t === "error") {
      for (const table of Object.values(this.tables)) table.pending.delete(ev.rid); // only the request that failed gets its row back
      if (ev.rid != null && ev.rid === this.rag.search) this.rag.output = "";
      this.ragFinish(ev.rid);
      this.mcpFinish(ev.rid);
      this.status = `! ${ev.message ?? ""}`;
      return done(false);
    }
    return false;
  }
}
