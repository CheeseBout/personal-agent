"""Staging area: copy host files in -> the sandbox works on the copies -> diff against the originals -> apply.

Pure Python (no Docker needed), so the safety-critical logic is testable everywhere.
Nothing here trusts the contents of the work directory: the container can create anything in it.
The manifest lives OUTSIDE the work directory, so the container cannot tamper with it.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personal_agent.policy.engine import PolicyEngine
from personal_agent.sandbox.backup import delete_with_backup, write_with_backup

MAX_DIFF_BYTES = 200_000


@dataclass
class Change:
    rel: str
    status: str  # added | modified | deleted | conflict | blocked
    target: str
    size: int
    diff: str
    note: str = ""
    sha: str = ""  # sha256 of the staged content that was reviewed; apply refuses anything else


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Staging:
    def __init__(self, root: Path, policy: PolicyEngine, workspace: Path) -> None:
        self.root, self.policy, self.workspace = root, policy, workspace
        self.work = root / "work"
        self._manifest = root / "manifest.json"
        self.work.mkdir(parents=True, exist_ok=True)

    # ---- config helpers ------------------------------------------------------------------------------
    @property
    def _cfg(self) -> dict[str, Any]:
        cfg: dict[str, Any] = self.policy.cfg.get("sandbox", {})
        return cfg

    @property
    def _max_bytes(self) -> int:
        return int(self._cfg.get("max_file_mb", 5)) * 1_000_000

    @property
    def output_dir(self) -> Path:
        return self.workspace / str(self._cfg.get("output_dir", "sandbox-output"))

    def _manifest_data(self) -> dict[str, dict[str, str]]:
        return json.loads(self._manifest.read_text(encoding="utf-8")) if self._manifest.exists() else {}

    def _save(self, m: dict[str, dict[str, str]]) -> None:
        self._manifest.write_text(json.dumps(m), encoding="utf-8")

    # ---- stage -----------------------------------------------------------------------------------------
    def stage(self, paths: list[str]) -> list[str]:
        max_files = int(self._cfg.get("max_files", 200))
        manifest = self._manifest_data()
        staged: list[str] = []
        for raw in paths:
            p = Path(os.path.expandvars(raw))
            p = p if p.is_absolute() else self.workspace / p
            if not (d := self.policy.check_path(p)).allowed:
                raise ValueError(f"'{raw}': {d.reason}")
            files = [p] if p.is_file() else sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else []
            if not files:
                raise ValueError(f"'{raw}': not found")
            for f in files:
                real = Path(os.path.realpath(f))
                if f.is_symlink() or not self.policy.check_path(real).allowed:
                    continue  # never follow links out of the allowed folders
                if real.stat().st_size > self._max_bytes:
                    raise ValueError(f"'{f.name}' is larger than {self._max_bytes // 1_000_000} MB")
                rel = self._rel(real)
                if rel in manifest and manifest[rel]["origin"] != str(real):
                    raise ValueError(f"name collision for '{rel}'")
                if len(manifest) + (rel not in manifest) > max_files:
                    raise ValueError(f"more than {max_files} files staged")
                dest = self.work / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(real, dest)
                manifest[rel] = {"origin": str(real), "sha": _sha(real)}
                staged.append(rel)
        self._save(manifest)
        return staged

    def _rel(self, real: Path) -> str:
        allow = [Path(os.path.expandvars(a)) for a in self.policy.cfg.get("paths", {}).get("allow", [])]
        for root in [self.workspace, *allow]:
            try:
                return Path(real).relative_to(Path(os.path.realpath(root))).as_posix()
            except ValueError:
                continue
        return real.name

    # ---- diff --------------------------------------------------------------------------------------------
    def changes(self) -> list[Change]:
        manifest = self._manifest_data()
        limit = int(self._cfg.get("max_files", 200)) * 2
        seen: set[str] = set()
        out: list[Change] = []
        work_real = os.path.realpath(self.work)
        visited = 0
        for f in sorted(self.work.rglob("*")):
            if f.is_symlink() or not f.is_file() or not os.path.realpath(f).startswith(work_real + os.sep):
                continue  # the container may have made links/devices: ignore anything not a plain file
            visited += 1
            if visited > limit:
                raise ValueError(f"the sandbox produced more than {limit} files; refusing to review them")
            rel = f.relative_to(self.work).as_posix()
            if rel.startswith(".scratch/"):
                continue  # run_python's temporary scripts are not part of the reviewable result
            seen.add(rel)
            if rel in manifest:
                if f.stat().st_size <= self._max_bytes and _sha(f) == manifest[rel]["sha"]:
                    continue
                out.append(self._classify(rel, "modified", Path(manifest[rel]["origin"]), f, manifest[rel]["sha"]))
            else:
                out.append(self._classify(rel, "added", self.output_dir / rel, f, None))
        for rel, m in manifest.items():
            if rel not in seen:
                out.append(self._classify(rel, "deleted", Path(m["origin"]), None, m["sha"]))
        return out

    def _in_output_dir(self, target: str | Path) -> bool:
        out = os.path.normcase(os.path.realpath(self.output_dir))
        return os.path.normcase(os.path.realpath(target)).startswith(out + os.sep)

    def _classify(self, rel: str, status: str, target: Path, staged: Path | None, base_sha: str | None) -> Change:
        size = staged.stat().st_size if staged else 0
        if size > self._max_bytes:
            return Change(rel, "blocked", str(target), size, "", f"file is larger than {self._max_bytes // 1_000_000} MB")
        if not (d := self.policy.check_path(target)).allowed:
            return Change(rel, "blocked", str(target), size, "", d.reason)
        if status == "added" and not self._in_output_dir(target):
            return Change(rel, "blocked", str(target), size, "", "new files may only be created in the output folder")
        if status != "added" and (not target.is_file() or _sha(target) != base_sha):
            return Change(rel, "conflict", str(target), size, "", "the file on disk changed since it was staged")
        if status == "added" and target.exists():
            return Change(rel, "conflict", str(target), size, "", "a file already exists at the target")
        sha = _sha(staged) if staged else ""
        return Change(rel, status, str(target), size, self._diff(target if status != "added" else None, staged), sha=sha)

    @staticmethod
    def _diff(old: Path | None, new: Path | None) -> str:
        try:
            if any(p is not None and p.stat().st_size > MAX_DIFF_BYTES for p in (old, new)):
                return "(large file - diff not shown)"
            a = old.read_text(encoding="utf-8").splitlines(keepends=True) if old else []
            b = new.read_text(encoding="utf-8").splitlines(keepends=True) if new else []
        except (UnicodeDecodeError, OSError):
            return "(binary or unreadable file - content diff not shown)"
        return "".join(difflib.unified_diff(a, b, "before", "after"))[:4000]

    # ---- apply -------------------------------------------------------------------------------------------
    def apply(self, rels: list[str] | None, apply_id: str) -> list[str]:
        return self.apply_changes([c for c in self.changes() if rels is None or c.rel in rels], apply_id)

    def apply_changes(self, changes: list[Change], apply_id: str) -> list[str]:
        """Apply exactly the reviewed changes: the staged bytes must still hash to what was reviewed."""
        manifest = self._manifest_data()
        done: list[str] = []
        work_real = os.path.realpath(self.work)
        for c in changes:
            if c.status in ("conflict", "blocked"):
                done.append(f"skipped {c.rel}: {c.note}")
                continue
            target = Path(c.target)
            if not self.policy.check_path(target).allowed:  # re-check at write time, not only at review time
                done.append(f"skipped {c.rel}: target is not allowed")
                continue
            if c.status == "added" and not self._in_output_dir(target):  # e.g. a folder swapped for a junction
                done.append(f"skipped {c.rel}: new files may only be created in the output folder")
                continue
            if c.status == "deleted":
                if not target.is_file() or _sha(target) != manifest.get(c.rel, {}).get("sha"):
                    done.append(f"skipped {c.rel}: the file on disk changed")
                    continue
                delete_with_backup(target, apply_id)
                manifest.pop(c.rel, None)
            else:
                staged = self.work / c.rel
                real = os.path.realpath(staged)
                if staged.is_symlink() or not real.startswith(work_real + os.sep):
                    done.append(f"skipped {c.rel}: staged file is not a plain file")
                    continue
                data = Path(real).read_bytes()
                if hashlib.sha256(data).hexdigest() != c.sha:
                    done.append(f"skipped {c.rel}: the staged file changed after it was reviewed")
                    continue
                if c.status == "modified" and (not target.is_file() or _sha(target) != manifest[c.rel]["sha"]):
                    done.append(f"skipped {c.rel}: the file on disk changed")
                    continue
                write_with_backup(target, data, apply_id)
                manifest[c.rel] = {"origin": str(target), "sha": c.sha}
            done.append(f"{c.status} {c.rel} -> {c.target}")
        self._save(manifest)
        return done

    def clear(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)
        self._manifest.unlink(missing_ok=True)
        self.work.mkdir(parents=True, exist_ok=True)


def describe(changes: list[Change], with_diff: bool = True) -> str:
    if not changes:
        return "No changes."
    mark = {"added": "+ added   ", "modified": "~ modified", "deleted": "- DELETED ", "conflict": "! conflict", "blocked": "x blocked "}
    lines = [f"{len(changes)} change(s):"]
    lines += [f"{mark[c.status]} {c.rel} -> {c.target} ({c.size} B){' ['+c.note+']' if c.note else ''}" for c in changes]
    if with_diff:
        for c in changes:
            if c.diff:
                lines += ["", f"--- {c.rel} ---", c.diff]
    return "\n".join(lines)
