"""Render only already-projected technical metadata for manual sharing."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote, urlencode
from zipfile import ZIP_DEFLATED, ZipFile


DEFAULT_RECIPIENT = "ranjiao@gmail.com"
RECIPIENT_ENV = "BYTEWORKER_FEEDBACK_EMAIL"
EMAIL_RE = re.compile(
    r"[A-Za-z0-9_+-]+(?:\.[A-Za-z0-9_+-]+)*@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
)
BUNDLE_CONTENTS = ("summary.json", "timeline.jsonl")


def make_draft(*, run_id: str, schema: str, subject: str, body: str) -> dict:
    recipient = os.environ.get(RECIPIENT_ENV, DEFAULT_RECIPIENT).strip()
    if len(recipient) > 254 or EMAIL_RE.fullmatch(recipient) is None:
        raise ValueError(f"{RECIPIENT_ENV} 必须是一个有效的收件地址。")
    query = urlencode({"subject": subject, "body": body.replace("\n", "\r\n")}, quote_via=quote)
    return {
        "schema_version": schema,
        "run_id": run_id,
        "to": recipient,
        "subject": subject,
        "body": body,
        "mailto": f"mailto:{quote(recipient, safe='@')}?{query}",
    }

def write_bundle(kb: Path | None, *, skill_root: Path, summary: dict, timeline: list[dict]) -> dict:
    run_id = summary["run_id"]
    export_dir = Path(tempfile.gettempdir()).resolve()
    if any(export_dir.is_relative_to(root.resolve()) for root in (kb, skill_root) if root is not None):
        raise ValueError("系统临时目录必须位于知识库和 Byteworker 仓库之外。")
    descriptor, path = tempfile.mkstemp(
        prefix=f"byteworker-{run_id}-", suffix=".zip", dir=export_dir
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            with ZipFile(handle, "w", ZIP_DEFLATED) as archive:
                archive.writestr(BUNDLE_CONTENTS[0], json.dumps(summary, indent=2) + "\n")
                archive.writestr(
                    BUNDLE_CONTENTS[1],
                    "".join(json.dumps(event) + "\n" for event in timeline),
                )
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise
    return {
        "schema_version": summary["schema_version"],
        "run_id": run_id,
        "path": path,
        "contents": list(BUNDLE_CONTENTS),
        "timeline_event_count": len(timeline),
        "summary": summary,
    }
