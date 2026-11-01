"""Turn what the user chose on the approval cards into the `decisions` payload the daemon expects (pure, tested)."""

from __future__ import annotations

import json
from typing import Any

CHOICES = ("approve", "remember", "reject")


def build_decisions(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """cards: [{call_id, tier, choice, args_text, args, reason, confirm}] -> {call_id: decision}.

    - approve / remember -> {"action":"approve","remember":bool}; changed arguments become {"action":"edit","args":...}
    - T3 needs the word CONFIRM typed
    - reject carries the user's reason (the agent sees it)
    Raises ValueError (with a user-facing message) on missing choices, bad JSON or a missing T3 confirmation."""
    out: dict[str, Any] = {}
    for c in cards:
        cid, choice = c["call_id"], c.get("choice")
        if choice not in CHOICES:
            raise ValueError(f"choose an answer for {c.get('tool', cid)}")
        if choice == "reject":
            out[cid] = {"action": "reject", "feedback": str(c.get("reason", "")).strip()}
            continue
        try:
            new_args = json.loads(c.get("args_text") or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"arguments of {c.get('tool', cid)} are not valid JSON: {e.msg}") from e
        if not isinstance(new_args, dict):
            raise ValueError("arguments must be a JSON object")
        if c.get("tier") == "T3" and str(c.get("confirm", "")).strip() != "CONFIRM":
            raise ValueError("type CONFIRM to approve a dangerous (T3) action")
        if new_args != c.get("args"):
            out[cid] = {"action": "edit", "args": new_args}  # an edited call is approved as edited (no 'remember')
        else:
            d: dict[str, Any] = {"action": "approve", "remember": choice == "remember"}
            if c.get("tier") == "T3":
                d["confirm"] = True
            out[cid] = d
    return out
