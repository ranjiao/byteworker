"""Persistent, session-resumable queues for confirmed multi-page digests."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from frontmatter import parse_file


JOB_SCHEMA = "byteworker-digest-job/v1"
SELECTION_SCHEMA = "byteworker-wiki-candidate-selection/v1"
JOB_ID_RE = re.compile(r"^WJ-\d{8}-\d{3}$")
ACTIVE_JOB_STATES = {"ready", "running", "waiting_user"}
TERMINAL_JOB_STATES = {"completed", "cancelled", "failed"}
PAGE_STATES = {
    "pending",
    "in_progress",
    "noop",
    "committed",
    "blocked_dependency",
    "blocked_conflict",
    "retryable_error",
    "permanent_error",
    "skipped",
}
SUCCESS_PAGE_STATES = {"noop", "committed", "skipped"}
RETRYABLE_PAGE_STATES = {"pending", "retryable_error"}
TERMINAL_PAGE_STATES = {
    "noop",
    "committed",
    "blocked_dependency",
    "blocked_conflict",
    "permanent_error",
    "skipped",
}


class DigestJobError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.hint:
            value["hint"] = self.hint
        if self.details:
            value["details"] = self.details
        return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_canonical_bytes(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _jobs_root(kb: Path) -> Path:
    return kb.resolve() / "state" / "digest_jobs"


def _ensure_state_ignored(kb: Path) -> None:
    info_exclude = kb.resolve() / ".git" / "info" / "exclude"
    if not info_exclude.parent.is_dir():
        return
    current = info_exclude.read_text(encoding="utf-8") if info_exclude.exists() else ""
    if any(line.strip() == "/state/" for line in current.splitlines()):
        return
    info_exclude.write_text(
        current + ("" if not current or current.endswith("\n") else "\n") + "/state/\n",
        encoding="utf-8",
    )


def _job_path(kb: Path, job_id: str) -> Path:
    if not JOB_ID_RE.fullmatch(job_id):
        raise DigestJobError("DIGEST_JOB_INVALID_ID", "digest job id 格式不合法。")
    return _jobs_root(kb) / f"{job_id}.json"


def _load_path(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise DigestJobError(
            "DIGEST_JOB_NOT_FOUND",
            f"找不到 digest job: {path.stem}",
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DigestJobError(
            "DIGEST_JOB_INVALID",
            f"digest job 文件损坏: {path}",
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != JOB_SCHEMA:
        raise DigestJobError(
            "DIGEST_JOB_INVALID",
            f"digest job schema 不受支持: {path}",
        )
    pages = value.get("pages")
    if not isinstance(pages, list):
        raise DigestJobError("DIGEST_JOB_INVALID", "digest job 缺少 pages 数组。")
    return value


def load_job(kb: Path, job_id: str) -> dict[str, Any]:
    return _load_path(_job_path(kb, job_id))


def _next_job_id(root: Path, now: datetime) -> str:
    prefix = f"WJ-{now:%Y%m%d}-"
    used = {
        int(path.stem.rsplit("-", 1)[-1])
        for path in root.glob(f"{prefix}*.json")
        if JOB_ID_RE.fullmatch(path.stem)
    }
    for serial in range(1, 1000):
        if serial not in used:
            return f"{prefix}{serial:03d}"
    raise DigestJobError(
        "DIGEST_JOB_ID_EXHAUSTED",
        "当天的 digest job 编号已用完。",
    )


def _validate_selection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != SELECTION_SCHEMA:
        raise DigestJobError(
            "DIGEST_JOB_SELECTION_INVALID",
            f"候选列表必须使用 {SELECTION_SCHEMA}。",
        )
    raw_pages = value.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise DigestJobError(
            "DIGEST_JOB_SELECTION_INVALID",
            "候选列表 pages 必须是非空数组。",
        )
    pages = []
    seen: set[str] = set()
    for index, item in enumerate(raw_pages):
        if not isinstance(item, Mapping):
            raise DigestJobError(
                "DIGEST_JOB_SELECTION_INVALID",
                f"pages[{index}] 必须是对象。",
            )
        document_id = str(item.get("document_id", "")).strip()
        node_token = str(item.get("node_token", "")).strip()
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if not document_id or not node_token or not title or not url.startswith("https://"):
            raise DigestJobError(
                "DIGEST_JOB_SELECTION_INVALID",
                f"pages[{index}] 缺少 document_id/node_token/title/HTTPS url。",
            )
        if document_id in seen:
            raise DigestJobError(
                "DIGEST_JOB_SELECTION_INVALID",
                f"候选列表存在重复 document_id: {document_id}",
            )
        seen.add(document_id)
        pages.append(
            {
                "document_id": document_id,
                "node_token": node_token,
                "title": title,
                "url": url,
                "updated_at": str(item.get("updated_at", "")).strip(),
                "path_titles": [
                    str(part).strip()
                    for part in item.get("path_titles", [])
                    if str(part).strip()
                ],
            }
        )
    return {
        "space_id": str(value.get("space_id", "")).strip(),
        "space_url": str(value.get("space_url", "")).strip(),
        "root_node_token": str(value.get("root_node_token", "")).strip(),
        "tree_hash": str(value.get("tree_hash", "")).strip(),
        "pages": pages,
    }


def create_job(
    kb: Path,
    selection: Mapping[str, Any],
    *,
    title: str = "",
    organization_node_id: str = "",
    batch_size: int = 5,
    now: datetime | None = None,
) -> dict[str, Any]:
    if batch_size <= 0 or batch_size > 50:
        raise DigestJobError(
            "DIGEST_JOB_INVALID_ARGUMENT",
            "batch_size 必须在 1..50 之间。",
        )
    normalized = _validate_selection(selection)
    now = now or _utc_now()
    root = _jobs_root(kb)
    _ensure_state_ignored(kb)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".lock"
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        job_id = _next_job_id(root, now)
        selection_hash = "sha256:" + hashlib.sha256(
            _canonical_bytes(normalized)
        ).hexdigest()
        pages = [
            {
                **page,
                "status": "pending",
                "attempts": 0,
                "lease": None,
                "raw_id": "",
                "commit": "",
                "error": "",
            }
            for page in normalized["pages"]
        ]
        page_count = len(pages)
        job = {
            "schema_version": JOB_SCHEMA,
            "job_id": job_id,
            "title": title.strip() or f"Wiki digest ({page_count} pages)",
            "status": "ready",
            "created_at": _iso(now),
            "updated_at": _iso(now),
            "organization_node_id": organization_node_id.strip(),
            "source": {
                "space_id": normalized["space_id"],
                "space_url": normalized["space_url"],
                "root_node_token": normalized["root_node_token"],
                "tree_hash": normalized["tree_hash"],
                "selection_hash": selection_hash,
            },
            "budget": {
                "batch_size": batch_size,
                "estimated_input_tokens_low": page_count * 3_000,
                "estimated_input_tokens_high": page_count * 12_000,
            },
            "pages": pages,
        }
        _atomic_write(root / f"{job_id}.json", job)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return job_summary(job)


def _page_counts(job: Mapping[str, Any]) -> dict[str, int]:
    counts = Counter(str(item.get("status", "")) for item in job.get("pages", []))
    return {state: counts[state] for state in sorted(PAGE_STATES) if counts[state]}


def job_summary(job: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job.get("job_id"),
        "title": job.get("title"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "organization_node_id": job.get("organization_node_id"),
        "space_id": job.get("source", {}).get("space_id"),
        "root_node_token": job.get("source", {}).get("root_node_token"),
        "page_count": len(job.get("pages", [])),
        "page_counts": _page_counts(job),
        "budget": job.get("budget"),
    }


def list_jobs(kb: Path, *, active_only: bool = False) -> list[dict[str, Any]]:
    root = _jobs_root(kb)
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.glob("WJ-*.json"), reverse=True):
        job = _load_path(path)
        if active_only and job.get("status") not in ACTIVE_JOB_STATES:
            continue
        result.append(job_summary(job))
    return result


def job_status(kb: Path, job_id: str, *, limit: int = 20) -> dict[str, Any]:
    if limit <= 0:
        raise DigestJobError("DIGEST_JOB_INVALID_ARGUMENT", "limit 必须是正整数。")
    job = load_job(kb, job_id)
    summary = job_summary(job)
    incomplete = [
        item
        for item in job["pages"]
        if item.get("status") not in SUCCESS_PAGE_STATES
    ]
    summary["preview_truncated"] = len(incomplete) > limit
    summary["incomplete_preview"] = [
        {
            "document_id": item["document_id"],
            "title": item["title"],
            "status": item["status"],
            "attempts": item["attempts"],
            "error": item.get("error", ""),
        }
        for item in incomplete[:limit]
    ]
    return summary


def _recompute_job_status(job: dict[str, Any]) -> None:
    states = {str(item.get("status", "")) for item in job["pages"]}
    if states <= SUCCESS_PAGE_STATES:
        job["status"] = "completed"
    elif "in_progress" in states:
        job["status"] = "running"
    elif states & {"blocked_dependency", "blocked_conflict"}:
        job["status"] = "waiting_user"
    elif states & RETRYABLE_PAGE_STATES:
        job["status"] = "ready"
    elif states & {"permanent_error"}:
        job["status"] = "failed"


def lease_next(
    kb: Path,
    job_id: str,
    *,
    limit: int,
    lease_owner: str,
    lease_seconds: int = 1800,
    now: datetime | None = None,
) -> dict[str, Any]:
    if limit <= 0 or limit > 50:
        raise DigestJobError(
            "DIGEST_JOB_INVALID_ARGUMENT",
            "limit 必须在 1..50 之间。",
        )
    if not lease_owner.strip() or lease_seconds <= 0:
        raise DigestJobError(
            "DIGEST_JOB_INVALID_ARGUMENT",
            "lease_owner 不能为空且 lease_seconds 必须为正数。",
        )
    path = _job_path(kb, job_id)
    root = _jobs_root(kb)
    if not path.is_file():
        _load_path(path)
    now = now or _utc_now()
    with open(root / ".lock", "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        job = _load_path(path)
        if job.get("status") in TERMINAL_JOB_STATES:
            raise DigestJobError(
                "DIGEST_JOB_TERMINAL",
                f"digest job 已处于终态: {job['status']}",
            )
        selected = []
        for page in job["pages"]:
            status = page.get("status")
            lease = page.get("lease")
            expired = False
            if status == "in_progress" and isinstance(lease, Mapping):
                expires_at = _parse_time(lease.get("expires_at"))
                expired = expires_at is None or expires_at <= now
            if status not in RETRYABLE_PAGE_STATES and not expired:
                continue
            page["status"] = "in_progress"
            page["attempts"] = int(page.get("attempts", 0)) + 1
            page["lease"] = {
                "owner": lease_owner.strip(),
                "leased_at": _iso(now),
                "expires_at": _iso(now + timedelta(seconds=lease_seconds)),
            }
            page["error"] = ""
            selected.append(
                {
                    "document_id": page["document_id"],
                    "node_token": page["node_token"],
                    "title": page["title"],
                    "url": page["url"],
                    "updated_at": page.get("updated_at", ""),
                    "attempt": page["attempts"],
                    "lease_expires_at": page["lease"]["expires_at"],
                }
            )
            if len(selected) >= limit:
                break
        _recompute_job_status(job)
        job["updated_at"] = _iso(now)
        _atomic_write(path, job)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {
        "job_id": job_id,
        "leased_count": len(selected),
        "pages": selected,
        "remaining_counts": _page_counts(job),
    }


def mark_page(
    kb: Path,
    job_id: str,
    *,
    document_id: str,
    status: str,
    raw_id: str = "",
    commit: str = "",
    error: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    if status not in PAGE_STATES - {"pending", "in_progress"}:
        raise DigestJobError(
            "DIGEST_JOB_INVALID_ARGUMENT",
            "mark status 不受支持。",
        )
    path = _job_path(kb, job_id)
    root = _jobs_root(kb)
    if not path.is_file():
        _load_path(path)
    now = now or _utc_now()
    with open(root / ".lock", "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        job = _load_path(path)
        page = next(
            (
                item
                for item in job["pages"]
                if item.get("document_id") == document_id
            ),
            None,
        )
        if page is None:
            raise DigestJobError(
                "DIGEST_JOB_PAGE_NOT_FOUND",
                f"任务中没有 document_id={document_id}",
            )
        if page.get("status") in SUCCESS_PAGE_STATES and status != page.get("status"):
            raise DigestJobError(
                "DIGEST_JOB_INVALID_TRANSITION",
                f"已完成页面不能从 {page['status']} 改为 {status}。",
            )
        if status == "committed" and (not raw_id.strip() or not commit.strip()):
            raise DigestJobError(
                "DIGEST_JOB_INVALID_ARGUMENT",
                "mark committed 时必须提供 raw_id 和 commit。",
            )
        page["status"] = status
        page["lease"] = None
        page["raw_id"] = raw_id.strip()
        page["commit"] = commit.strip()
        page["error"] = error.strip()[:2000]
        _recompute_job_status(job)
        job["updated_at"] = _iso(now)
        _atomic_write(path, job)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {
        "job_id": job_id,
        "document_id": document_id,
        "page_status": status,
        "job_status": job["status"],
        "page_counts": _page_counts(job),
    }


def reconcile_job(
    kb: Path,
    job_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    path = _job_path(kb, job_id)
    root = _jobs_root(kb)
    if not path.is_file():
        _load_path(path)
    raw_root = kb.resolve() / "raw_data"
    completed: dict[str, tuple[str, str]] = {}
    if raw_root.is_dir():
        for raw_path in raw_root.rglob("*.md"):
            try:
                frontmatter, _ = parse_file(str(raw_path))
            except OSError:
                continue
            source_uid = str(frontmatter.get("source_uid", "")).strip()
            if (
                source_uid
                and str(frontmatter.get("digest_status", "")).strip() == "digested"
            ):
                completed[source_uid] = (
                    str(frontmatter.get("id", "")).strip() or raw_path.stem,
                    str(frontmatter.get("commit", "")).strip(),
                )
    now = now or _utc_now()
    changed = 0
    with open(root / ".lock", "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        job = _load_path(path)
        for page in job["pages"]:
            match = completed.get(str(page.get("document_id", "")))
            if not match or page.get("status") in SUCCESS_PAGE_STATES:
                continue
            page["status"] = "committed"
            page["lease"] = None
            page["raw_id"], page["commit"] = match
            page["error"] = ""
            changed += 1
        _recompute_job_status(job)
        job["updated_at"] = _iso(now)
        if changed:
            _atomic_write(path, job)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {
        "job_id": job_id,
        "reconciled_count": changed,
        "job_status": job["status"],
        "page_counts": _page_counts(job),
    }


def cancel_job(
    kb: Path,
    job_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    path = _job_path(kb, job_id)
    root = _jobs_root(kb)
    if not path.is_file():
        _load_path(path)
    now = now or _utc_now()
    with open(root / ".lock", "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        job = _load_path(path)
        if job.get("status") == "completed":
            raise DigestJobError(
                "DIGEST_JOB_TERMINAL",
                "已完成的 digest job 不能取消。",
            )
        job["status"] = "cancelled"
        for page in job["pages"]:
            if page.get("status") in {"pending", "in_progress", "retryable_error"}:
                page["status"] = "skipped"
                page["lease"] = None
        job["updated_at"] = _iso(now)
        _atomic_write(path, job)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return job_summary(job)
