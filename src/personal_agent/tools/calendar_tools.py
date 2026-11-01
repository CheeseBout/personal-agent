"""Calendar over ICS: read from a local .ics file (read = T0), create events by appending to it (T2, undoable).

shortcut: only ICS files in the workspace (e.g. an export/sync of Google Calendar); no live Google/CalDAV OAuth yet.
Recurring events (RRULE) are expanded with recurring-ical-events when installed, otherwise only the first occurrence shows.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

if TYPE_CHECKING:
    from icalendar import Calendar

from personal_agent.policy.engine import ToolMeta
from personal_agent.sandbox.backup import new_apply_id, write_with_backup
from personal_agent.tools.files import resolve
from personal_agent.tools.registry import cap, policy, register

DEFAULT_FILE = "calendar.ics"
MAX_ICS_BYTES = 5_000_000


def _file(name: str) -> Path | str:
    p = resolve(name or DEFAULT_FILE)
    if not (d := policy().check_path(p)).allowed:
        return f"ERROR: access denied: {d.reason}"
    return Path(os.path.realpath(p))


def _dt(s: str) -> datetime | date:
    s = s.strip()
    d = datetime.fromisoformat(s)
    if len(s) <= 10:
        return d.date()
    return d if d.tzinfo else d.astimezone()  # naive input = the PC's local time zone


def _as_dt(v: datetime | date) -> datetime:
    return v if isinstance(v, datetime) else datetime(v.year, v.month, v.day).astimezone()


def _events(cal: Calendar, start: datetime, end: datetime) -> list[Any]:
    try:
        import recurring_ical_events

        return list(recurring_ical_events.of(cal).between(start, end))
    except ImportError:
        return [e for e in cal.walk("VEVENT") if start <= _as_dt(e.decoded("DTSTART")) <= end]


@tool
def calendar_list(start: str, end: str, file: str = DEFAULT_FILE) -> str:
    """List calendar events between two ISO dates/times (e.g. 2026-11-01 to 2026-11-08), from an .ics file.
    Liệt kê sự kiện lịch trong một khoảng thời gian (từ tệp .ics)."""
    f = _file(file)
    if isinstance(f, str):
        return f
    if not f.is_file():
        return f"No calendar file at {f.name} yet."
    if f.stat().st_size > MAX_ICS_BYTES:
        return f"ERROR: the calendar file is larger than {MAX_ICS_BYTES // 1_000_000} MB"
    try:
        s, e = _as_dt(_dt(start)), _as_dt(_dt(end))
        from icalendar import Calendar  # lazy: ~15 MB and 160 ms the idle daemon should not pay

        cal = Calendar.from_ical(f.read_bytes())
        evs = _events(cal, s, e)
    except (ValueError, OSError) as ex:
        return f"ERROR: cannot read the calendar: {type(ex).__name__}: {str(ex)[:100]}"
    rows = sorted((_as_dt(ev.decoded("DTSTART")), str(ev.get("SUMMARY", "(no title)")), str(ev.get("LOCATION", ""))) for ev in evs)
    return cap("[UNTRUSTED CALENDAR CONTENT]\n" + ("\n".join(f"{t:%Y-%m-%d %H:%M}  {n}" + (f"  @ {loc}" if loc else "") for t, n, loc in rows)
                                                   or "no events"))


def _add_preview(a: dict[str, Any]) -> str:
    return (f"ADD CALENDAR EVENT to {a.get('file') or DEFAULT_FILE}\n{a.get('summary')}\n{a.get('start')} -> {a.get('end')}\n"
            f"Location: {a.get('location') or '-'}\n{a.get('description') or ''}")


@tool
def calendar_add(summary: str, start: str, end: str, location: str = "", description: str = "", file: str = DEFAULT_FILE) -> str:
    """Add an event to the .ics calendar file (needs approval; undoable). start/end are ISO date-times.
    Thêm sự kiện vào tệp lịch .ics (cần duyệt, hoàn tác được)."""
    f = _file(file)
    if isinstance(f, str):
        return f
    try:
        s, e = _dt(start), _dt(end)
        if _as_dt(e) <= _as_dt(s):
            return "ERROR: the end must be after the start"
        from icalendar import Calendar

        cal = Calendar.from_ical(f.read_bytes()) if f.is_file() else Calendar()
    except (ValueError, OSError) as ex:
        return f"ERROR: {type(ex).__name__}: {str(ex)[:100]}"
    if not cal.get("PRODID"):
        cal.add("PRODID", "-//personal-agent//EN")
        cal.add("VERSION", "2.0")
    from icalendar import Event

    ev = Event()
    ev.add("UID", f"{uuid.uuid4()}@personal-agent")
    ev.add("DTSTAMP", datetime.now().astimezone())
    ev.add("SUMMARY", summary[:200])
    ev.add("DTSTART", s)
    ev.add("DTEND", e)
    if location:
        ev.add("LOCATION", location[:200])
    if description:
        ev.add("DESCRIPTION", description[:2000])
    cal.add_component(ev)
    aid = new_apply_id()
    write_with_backup(f, cal.to_ical(), aid)
    return f"added '{summary}' to {f.name}. Undo with undo_change('{aid}')."


register(calendar_list, ToolMeta(tier="T0", untrusted_output=True))
register(calendar_add, ToolMeta(tier="T2", side_effects=True, preview_fn=_add_preview))
