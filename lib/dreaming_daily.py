"""Publish a deadline-triggered Markdown revision from accepted local evidence."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from time import monotonic
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from dreaming_consolidation import accepted_findings_unlocked
from dreaming_report_bundle import _existing_manual_notes, _write_archive, _write_private
from dreaming_scheduler import _queue_report_periods
from dreaming_state import (
    DreamingError, atomic_write_json, load_state_unlocked, parse_time, save_state_unlocked,
    secure_path, state_lock, utc_iso,
)
from frontmatter import parse_frontmatter
from report_owner import report_owner_lock
from sources.adapters.text import chat_window


def _line(value: object) -> str:
    # Preserve evidence as plain text even when a source contains Markdown.
    line = " ".join(str(value or "").split())
    return re.sub(r"([\\`*_[\]<>])", r"\\\1", line)


def _committed_events(kb: Path, since: datetime) -> list[tuple[datetime, str]]:
    """Select dated Digest events and their primary raw from one Git snapshot."""
    deadline = monotonic() + 2

    def git(*args: str) -> str:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("daily Git snapshot budget exceeded")
        result = subprocess.run(["git", "-C", str(kb), *args], capture_output=True, text=True,
                                timeout=remaining)
        return result.stdout if result.returncode == 0 else ""

    head = git("rev-parse", "--verify", "HEAD").strip()
    if not head:
        return []
    paths = git("log", head, f"--since={utc_iso(since)}", "--format=", "--name-only",
                 "--diff-filter=AM", "--", "knowledge/events").splitlines()
    events = []
    for path in list(dict.fromkeys(filter(None, paths)))[:40]:
        node, body = parse_frontmatter(git("show", f"{head}:{path}"))
        raw_id = str(node.get("primary_source") or "")
        if not re.fullmatch(r"raw-[A-Za-z0-9_.-]+", raw_id):
            continue
        raws = git("grep", "-l", "-F", "-e", f"raw_id: {raw_id}", head, "--", "raw_data").splitlines()
        if len(raws) != 1:
            continue
        raw, _ = parse_frontmatter(git("show", raws[0]))
        if raw.get("raw_id") != raw_id or raw.get("digest_status") != "digested":
            continue
        window = chat_window(raw.get("source_window"))
        occurred = parse_time(raw.get("event_time")) or (window[1] if window else None)
        fact = re.search(r"^>\s*\*\*TL;DR:\*\*\s*(.+)$", body, re.MULTILINE)
        locator = raw.get("source_url") or raw.get("recording_url")
        if occurred and fact and locator:
            events.append((occurred, f"- {_line(fact[1])} （{_line(locator)}，{raw_id}）"))
    return events


def _markdown(period: str, cutoff: datetime, findings: Mapping[str, Any], notes: str,
              zone: ZoneInfo, coverage_notes: list[str], events: list[tuple[datetime, str]]) -> str:
    start = cutoff.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    items = [(occurred, text) for occurred, text in events if start <= occurred <= cutoff]
    for finding in findings.values():
        occurred = parse_time(finding.get("occurred_at"))
        refs = finding.get("evidence_refs") or []
        if not occurred or not start <= occurred < cutoff or not refs or not finding.get("summary"):
            continue
        refs = "、".join(_line(ref) for ref in refs[:5])
        items.append((occurred, f"- {_line(finding['summary'])} （{occurred.astimezone(zone):%H:%M}，原始消息 {refs}）"))
    lines = [f"# {period} 日报", "", f"统计截至 {cutoff.astimezone(zone):%H:%M %Z}。",
             "", "## 已验收事项", ""]
    lines.extend(text for _, text in sorted(items, reverse=True)[:50])
    if not items:
        lines.append("截至发布时，暂无可核实的事项可列入。")
    lines.extend(["", "## 覆盖说明", "", *coverage_notes,
                  "", "## 手动补充 / 备注", "", notes or "- 暂无", ""])
    return "\n".join(lines)


def _publish_revision(kb: Path, period: str, cutoff: datetime, current: datetime,
                      findings: Mapping[str, Any], zone: ZoneInfo, coverage_notes: list[str],
                      events: list[tuple[datetime, str]], *, supplement: bool) -> dict[str, Any] | None:
    directory = secure_path(kb, "reports", f"daily-{period}", "deadline")
    receipt_path = directory / "publication.json"
    archive = kb / "reports" / "daily" / f"{period}.md"
    prior = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {}
    if not prior and archive.is_file():
        # An existing report belongs to its original publisher.
        return None
    if prior:
        digest = prior.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise DreamingError("DREAMING_DAILY_RECEIPT_INVALID", "日报发布凭证校验值无效。")
        if prior["status"] == "staged" or not archive.is_file():
            version = (directory / f"{digest}.md").read_text(encoding="utf-8")
            if hashlib.sha256(version.encode()).hexdigest() != digest:
                raise DreamingError("DREAMING_DAILY_RECEIPT_INVALID", "待发布日报与发布凭证不一致。")
            archive_text = archive.read_text(encoding="utf-8") if archive.is_file() else ""
            if archive_text and archive_text != version:
                notes = _existing_manual_notes(archive)
                if notes:
                    version = version.split("\n## 手动补充 / 备注\n", 1)[0] + f"\n## 手动补充 / 备注\n\n{notes}\n"
                    digest = hashlib.sha256(version.encode()).hexdigest()
                    _write_private(directory / f"{digest}.md", version)
                    prior["sha256"] = digest
                    atomic_write_json(receipt_path, prior)
            if archive_text != version:
                _write_archive(archive, version)
                if not prior.get("published_at") and current >= cutoff + timedelta(minutes=1):
                    prior["missed_deadline"] = True
            prior.update(status="published", published_at=prior.get("published_at") or utc_iso(current))
            atomic_write_json(receipt_path, prior)
            return prior
        if not supplement:
            return prior
        prior_text = (directory / f"{digest}.md").read_text(encoding="utf-8")
        archive_text = archive.read_text(encoding="utf-8")
        heading = "\n## 手动补充 / 备注\n"
        if prior_text.split(heading, 1)[0] != archive_text.split(heading, 1)[0]:
            return None
    notes = _existing_manual_notes(archive)
    version = _markdown(period, cutoff, findings, notes, zone, coverage_notes, events)
    digest = hashlib.sha256(version.encode()).hexdigest()
    if prior.get("sha256") == digest:
        return None
    _write_private(directory / f"{digest}.md", version)
    receipt = {
        "schema_version": "byteworker-deadline-daily/v1", "period": period,
        "cutoff": utc_iso(cutoff), "sha256": digest, "status": "staged",
        "revision": int(prior.get("revision", 0)) + 1,
        "published_at": prior.get("published_at"), "archive": str(archive.relative_to(kb)),
        "missed_deadline": prior.get("missed_deadline", current >= cutoff + timedelta(minutes=1)),
    }
    atomic_write_json(receipt_path, receipt)
    _write_archive(archive, version)
    receipt.update(status="published", published_at=receipt["published_at"] or utc_iso(current))
    atomic_write_json(receipt_path, receipt)
    return receipt


def publish_due_daily(kb: Path, *, supplement: bool = False,
                      now: datetime | None = None) -> dict[str, Any]:
    """Independent local entry point. No model, provider call, or process lease."""
    current = now or datetime.now(timezone.utc)
    published = []
    with report_owner_lock(kb), state_lock(kb):
        state = load_state_unlocked(kb, current)
        job = state["jobs"]["daily"]
        owner = state["report_owner"]
        if not (state["enabled"] and job["enabled"] and owner.get("owner") == "dreaming"):
            return {"status": "disabled", "published": []}
        zone = ZoneInfo(state["timezone"])
        enabled_at = parse_time(state["enabled_at"])
        if enabled_at is None:
            raise DreamingError("DREAMING_STATE_INVALID", "缺少日报调度的启用时间。")
        start = max(enabled_at, parse_time(owner.get("migrated_at")) or enabled_at)
        _queue_report_periods(kb, "daily", job, current.astimezone(zone), start)
        deadlines = {
            period: cutoff for period, cutoff in job["pending_periods"].items()
            if not (kb / "reports" / "daily" / f"{period}.md").is_file()
        }
        directory = secure_path(kb, "reports")
        age_limit = current - timedelta(days=14)
        for path in directory.glob("daily-*/deadline/publication.json"):
            period = path.parent.parent.name.removeprefix("daily-")
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", period):
                continue
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
                cutoff = parse_time(receipt.get("cutoff"))
                if cutoff is None or receipt.get("period") != period:
                    raise ValueError("daily receipt has an invalid period")
            except (AttributeError, OSError, ValueError):
                job.setdefault("publication_incidents", {}).setdefault(period, {
                    "severity": "critical", "first_seen_at": utc_iso(current),
                    "code": "DREAMING_DAILY_RECEIPT_INVALID",
                })
                save_state_unlocked(kb, state)
                continue
            archive = kb / "reports" / "daily" / f"{period}.md"
            if (path.parent.parent / "artifacts" / "manifest.json").is_file():
                deadlines.pop(period, None)
                continue
            if receipt.get("status") == "staged" or not archive.is_file() or (supplement and cutoff >= age_limit):
                deadlines.setdefault(period, receipt["cutoff"])
        if not deadlines:
            return {"status": "idle", "published": []}
        coverage_notes = ["本版列出已验收且可按原始时间归期的消息与归档事件。待验收资料留待补充。"]
        try:
            findings = accepted_findings_unlocked(kb, state)
        except (DreamingError, OSError):
            findings = {}
            coverage_notes.append("消息验收记录暂不可读，本次未纳入消息事项。")
        try:
            events = _committed_events(kb, min(parse_time(value) for value in deadlines.values()) - timedelta(days=1)) if deadlines else []
        except (OSError, subprocess.SubprocessError):
            events = []
            coverage_notes.append("本次未能读取已归档事件，仍可列出已验收的消息。")
        if state["grants"]["im"]["mode"] == "all_visible":
            coverage_notes.append("全可见会话仅尽力覆盖。")
        for period, value in sorted(deadlines.items(), reverse=True):
            cutoff = parse_time(value)
            if not cutoff or cutoff > current:
                continue
            # A manual lease controls its own archive; automatic leases can use a base snapshot.
            active = state.get("active_lease") or {}
            active_until = parse_time(active.get("expires_at")) or current
            if (
                active.get("manual_report")
                and (active.get("job"), active.get("period")) == ("daily", period)
                and active_until > current
            ):
                continue
            try:
                if state["jobs"]["process"].get("waiting_for_user") in {
                    "DIGEST_TXN_ROLLBACK_FAILED", "KB_MUTATION_ROLLBACK_ERROR",
                }:
                    raise DreamingError("DREAMING_DAILY_UNSAFE_SNAPSHOT", "知识库写入回滚失败，无法核验快照。")
                receipt = _publish_revision(kb, period, cutoff, current, findings, zone, coverage_notes, events,
                                            supplement=supplement)
            except (OSError, ValueError, DreamingError) as exc:
                job.setdefault("publication_incidents", {}).setdefault(period, {
                    "severity": "critical", "first_seen_at": utc_iso(current),
                    "code": getattr(exc, "code", "DREAMING_DAILY_PUBLICATION_FAILED"),
                })
                save_state_unlocked(kb, state)
                raise
            if receipt:
                published.append(receipt)
                previous = job.get("last_deadline_publication") or {}
                if receipt["period"] >= previous.get("period", ""):
                    job["last_deadline_publication"] = receipt
                if receipt["missed_deadline"]:
                    job.setdefault("publication_incidents", {}).setdefault(period, {
                        "severity": "critical", "first_seen_at": utc_iso(current), "code": "DREAMING_DAILY_LATE",
                    })
                incident = job.get("publication_incidents", {}).get(period)
                if incident is not None:
                    incident["recovered_at"] = receipt["published_at"]
            save_state_unlocked(kb, state)
    return {"status": "published" if published else "idle", "published": published}
