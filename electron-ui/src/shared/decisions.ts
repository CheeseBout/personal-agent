/** Turn what the user chose on the approval cards into the `decisions` payload the daemon expects (pure, tested). */

export type CardState = { call_id: string; tool?: string; tier?: string; choice: string | null; args_text: string;
  args: Record<string, any>; reason: string; confirm: string };
const CHOICES = ["approve", "remember", "reject"];

// Same structure and values, whatever the key order (the user may reformat the JSON without changing the call).
function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null || Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => k in b && same((a as any)[k], (b as any)[k]));
}

/** approve / remember -> {action:"approve", remember}; changed arguments -> {action:"edit", args}; T3 needs the word CONFIRM typed;
 * reject carries the user's reason. Throws an Error with a user-facing message on a missing choice, bad JSON or a missing confirmation. */
export function buildDecisions(cards: CardState[]): Record<string, any> {
  const out: Record<string, any> = {};
  for (const c of cards) {
    const name = c.tool ?? c.call_id;
    if (!c.choice || !CHOICES.includes(c.choice)) throw new Error(`choose an answer for ${name}`);
    if (c.choice === "reject") {
      out[c.call_id] = { action: "reject", feedback: String(c.reason ?? "").trim() };
      continue;
    }
    let args: unknown;
    try {
      args = JSON.parse(c.args_text || "{}");
    } catch (e) {
      throw new Error(`arguments of ${name} are not valid JSON: ${(e as Error).message}`);
    }
    if (args === null || typeof args !== "object" || Array.isArray(args)) throw new Error("arguments must be a JSON object");
    if (c.tier === "T3" && String(c.confirm ?? "").trim() !== "CONFIRM") throw new Error("type CONFIRM to approve a dangerous (T3) action");
    if (!same(args, c.args)) out[c.call_id] = { action: "edit", args }; // an edited call is approved as edited (no 'remember')
    else out[c.call_id] = { action: "approve", remember: c.choice === "remember", ...(c.tier === "T3" ? { confirm: true } : {}) };
  }
  return out;
}
