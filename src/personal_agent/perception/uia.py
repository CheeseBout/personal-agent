"""Windows UI Automation (pywinauto, UIA backend): a structured view of a window and element lookup.

Prefer this over pixel guessing: names, roles, enabled state and exact rectangles. Password fields are never read.
Walks are bounded by node count and time so a huge app (browser, IDE) cannot stall the daemon.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

MAX_NODES = 250
MAX_SECONDS = 6.0
MAX_DEPTH = 8
VALUE_TYPES = {"Edit", "ComboBox", "Text", "Document"}
OPERABLE = {"Button", "MenuItem", "CheckBox", "RadioButton", "Edit", "ComboBox", "ListItem", "TabItem", "Hyperlink", "SplitButton", "TreeItem", "Slider"}


class UiaError(RuntimeError):
    pass


@dataclass
class Element:
    path: str  # stable-ish id inside this walk, e.g. "0.3.1"
    name: str
    control_type: str
    automation_id: str
    rect: tuple[int, int, int, int]  # physical screen pixels
    enabled: bool
    password: bool = False
    value: str = ""
    depth: int = 0
    ei: Any = field(default=None, repr=False, compare=False)  # live pywinauto element_info (for invoke)

    @property
    def center(self) -> tuple[int, int]:
        return (self.rect[0] + self.rect[2]) // 2, (self.rect[1] + self.rect[3]) // 2

    def describe(self) -> str:
        v = f" value={self.value!r}" if self.value else ""
        a = f" id={self.automation_id}" if self.automation_id else ""
        return (f"{'  ' * self.depth}[{self.path}] {self.control_type} {self.name!r}{a}{v}"
                f" @({self.rect[0]},{self.rect[1]},{self.rect[2]},{self.rect[3]}) center={self.center}{'' if self.enabled else ' DISABLED'}")


def _root(hwnd: int) -> Any:
    try:
        from pywinauto import Desktop

        return Desktop(backend="uia").window(handle=hwnd).wrapper_object()
    except Exception as e:
        raise UiaError(f"cannot attach to that window: {type(e).__name__}") from e


def _is_password(ei: Any) -> bool:
    try:
        return bool(ei.element.CurrentIsPassword)
    except Exception:  # noqa: BLE001 - property not available: assume the worst only for Edit controls (handled by caller)
        return False


def _to_element(ei: Any, path: str, depth: int) -> Element:
    r = ei.rectangle
    ctype = str(ei.control_type or "")
    pw = ctype == "Edit" and _is_password(ei)
    value = ""
    if ctype in VALUE_TYPES and not pw:
        try:
            value = str(ei.element.GetCurrentPropertyValue(30045) or "")[:100]  # UIA_ValueValuePropertyId
        except Exception:  # noqa: BLE001
            value = ""
    return Element(path, str(ei.name or "")[:120], ctype, str(ei.automation_id or "")[:80], (r.left, r.top, r.right, r.bottom),
                   bool(ei.enabled), pw, value, depth, ei)


def walk(hwnd: int, max_nodes: int = MAX_NODES, max_depth: int = MAX_DEPTH, seconds: float = MAX_SECONDS) -> tuple[list[Element], bool]:
    """Breadth-first walk of the window's element tree -> (elements, truncated). Invisible / zero-size elements are skipped."""
    root = _root(hwnd)
    end = time.monotonic() + seconds
    out: list[Element] = []
    queue: list[tuple[Any, str, int]] = [(root.element_info, "0", 0)]
    truncated = False
    while queue:
        ei, path, depth = queue.pop(0)
        if time.monotonic() > end or len(out) >= max_nodes:
            return out, True
        try:
            el = _to_element(ei, path, depth)
            if el.rect[2] > el.rect[0] and el.rect[3] > el.rect[1] and (el.name or el.automation_id or el.control_type not in ("Pane", "Group", "Custom")):
                out.append(el)
            if depth < max_depth:
                queue += [(c, f"{path}.{i}", depth + 1) for i, c in enumerate(ei.children())]
            else:
                truncated = truncated or bool(ei.children())
        except Exception:  # noqa: BLE001 - one flaky element must not abort the walk
            truncated = True  # we skipped something: tell the caller the list is incomplete
    return out, truncated


def find(hwnd: int, name: str | None = None, control_type: str | None = None, automation_id: str | None = None,
         index: int = 0) -> tuple[Element, int]:
    """Locate one element in the window. Returns (element, number of matches). Name match is case-insensitive substring."""
    elements, _ = walk(hwnd, max_nodes=600, seconds=8.0)
    hits = [e for e in elements
            if (name is None or name.casefold() in e.name.casefold())
            and (control_type is None or e.control_type.casefold() == control_type.casefold())
            and (automation_id is None or e.automation_id == automation_id)]
    if not hits:
        raise UiaError("no element matches (use ui_tree to list what is there)")
    # Best match first: an exact name beats a substring ("Save" button over a "Save the report" label), and controls you can
    # operate beat plain text. sorted() is stable, so document order breaks ties.
    hits = sorted(hits, key=lambda e: (0 if name and e.name.casefold() == name.casefold() else 1, 0 if e.control_type in OPERABLE else 1))
    if index >= len(hits):
        raise UiaError(f"only {len(hits)} element(s) match, index {index} is out of range")
    return hits[index], len(hits)


def invoke(el: Element) -> bool:
    """Activate via the UIA Invoke pattern (no mouse needed). Returns False if the element does not support it."""
    try:
        from pywinauto.controls.uiawrapper import UIAWrapper

        UIAWrapper(el.ei).invoke()
        return True
    except Exception:  # noqa: BLE001 - no Invoke pattern / element went away: the caller falls back to a real click
        return False


def focused_is_password() -> bool | None:
    """Does the control that has keyboard focus hold a password? None = cannot tell (no UI Automation info)."""
    try:
        from pywinauto.uia_defines import IUIA

        return bool(IUIA().iuia.GetFocusedElement().CurrentIsPassword)
    except Exception:  # noqa: BLE001 - any failure means "unknown", which the caller treats as unsafe for typing
        return None


def set_focus(hwnd: int) -> None:
    try:
        _root(hwnd).set_focus()
    except Exception as e:
        raise UiaError(f"cannot focus the window: {type(e).__name__}") from e
