"""Deterministic (no LLM call) stop reports in Vietnamese / English."""

from __future__ import annotations

from typing import Any

REASONS = {
    "steps": ("đã dùng hết số bước tối đa", "the step limit was reached"),
    "tool_calls": ("đã dùng hết số lần gọi công cụ", "the tool-call limit was reached"),
    "seconds": ("đã hết thời gian cho phép", "the time limit was reached"),
    "tokens": ("đã hết ngân sách token của tác vụ", "the task token budget was reached"),
    "cost": ("đã hết ngân sách chi phí của tác vụ", "the task cost budget was reached"),
    "failures": ("công cụ liên tục thất bại", "tools kept failing"),
    "verifications": ("đã kiểm tra nhiều lần mà chưa đạt", "verification kept failing"),
    "no_progress": ("không có tiến triển (lặp lại cùng một thao tác)", "no progress (the same action kept repeating)"),
    "replans": ("đã lập lại kế hoạch quá số lần cho phép", "the replan limit was reached"),
    "daily_tokens": ("đã chạm hạn mức token trong ngày (bảo vệ quota)", "the daily token cap was reached (quota protection)"),
    "daily_cost": ("đã chạm hạn mức chi phí trong ngày (bảo vệ quota)", "the daily cost cap was reached (quota protection)"),
}


def build_report(state: dict[str, Any]) -> str:
    vi = state.get("lang") == "vi"
    reason = state.get("stop_reason", "")
    why = REASONS.get(reason, (reason, reason))[0 if vi else 1]
    u = state["used"]
    notes = state.get("notes") or ""
    goal = state.get("goal") or {}
    crit = "; ".join(goal.get("success_criteria", []))
    if vi:
        parts = [f"Tôi đã dừng vì {why}.",
                 f"Đã dùng: {u['steps']} bước, {u['tool_calls']} lần gọi công cụ, ~{u['tokens']} token (~${u['cost']:.3f}).",
                 f"Tiêu chí hoàn thành: {crit}." if crit else "",
                 f"Ghi chú: {notes}" if notes else "",
                 "Công việc CHƯA được xác nhận hoàn thành. Nói \"tiếp tục\" để chạy tiếp (ngân sách mới), hoặc chỉnh lại yêu cầu."]
    else:
        parts = [f"I stopped because {why}.",
                 f"Used: {u['steps']} steps, {u['tool_calls']} tool calls, ~{u['tokens']} tokens (~${u['cost']:.3f}).",
                 f"Success criteria: {crit}." if crit else "",
                 f"Notes: {notes}" if notes else "",
                 "The task is NOT confirmed complete. Say \"continue\" to resume with a fresh budget, or adjust the request."]
    return "\n".join(p for p in parts if p)
