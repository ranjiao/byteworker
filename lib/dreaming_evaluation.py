"""Private shadow evaluation using IDs and labels only."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dreaming_state import DreamingError, atomic_write_json


GOLDEN_FIELDS = {"sample_id", "priority", "slices", "expected"}
PREDICTION_FIELDS = {"sample_id", "selected"}
REQUIRED_SLICES = {
    "decision",
    "assignment",
    "risk",
    "short_reply",
    "p2p",
    "muted",
    "low_activity",
    "unreadable_attachment",
    "partial_coverage",
}


def _private_directory(path: Path, *, kb: Path, skill_root: Path) -> Path:
    resolved = path.expanduser().resolve()
    roots = (kb.resolve(), skill_root.resolve())
    if any(resolved == root or root in resolved.parents for root in roots):
        raise DreamingError(
            "DREAMING_EVALUATION_PATH_INVALID",
            "评估目录必须位于 KB 和 skill 仓库之外。",
        )
    if not resolved.is_dir():
        raise DreamingError(
            "DREAMING_EVALUATION_PATH_INVALID",
            "评估目录不存在。",
        )
    return resolved


def _load(path: Path, key: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_EVALUATION_INVALID",
            f"无法读取评估文件: {path.name}",
        ) from exc
    items = value.get(key) if isinstance(value, Mapping) else None
    if not isinstance(items, list):
        raise DreamingError(
            "DREAMING_EVALUATION_INVALID",
            f"{path.name} 缺少 {key} 数组。",
        )
    return items


def _golden(path: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for value in _load(path, "samples"):
        if not isinstance(value, Mapping) or set(value) - GOLDEN_FIELDS:
            raise DreamingError(
                "DREAMING_EVALUATION_INVALID",
                "golden sample 只允许 ID、priority、slices、expected。",
            )
        sample_id = str(value.get("sample_id", "")).strip()
        priority = str(value.get("priority", "")).strip()
        slices = value.get("slices")
        if (
            not sample_id
            or priority not in {"P0", "P1", "P2"}
            or not isinstance(slices, list)
            or not isinstance(value.get("expected"), bool)
            or sample_id in result
        ):
            raise DreamingError(
                "DREAMING_EVALUATION_INVALID",
                "golden sample 字段无效或 sample_id 重复。",
            )
        result[sample_id] = {
            "priority": priority,
            "slices": sorted({str(item) for item in slices if str(item)}),
            "expected": value["expected"],
        }
    return result


def _predictions(path: Path, valid_ids: set[str]) -> set[str]:
    selected = set()
    seen = set()
    for value in _load(path, "predictions"):
        if not isinstance(value, Mapping) or set(value) - PREDICTION_FIELDS:
            raise DreamingError(
                "DREAMING_EVALUATION_INVALID",
                "prediction 只允许 sample_id 和 selected。",
            )
        sample_id = str(value.get("sample_id", "")).strip()
        if (
            sample_id not in valid_ids
            or sample_id in seen
            or not isinstance(value.get("selected"), bool)
        ):
            raise DreamingError(
                "DREAMING_EVALUATION_INVALID",
                "prediction sample_id 无效、重复或 selected 非布尔值。",
            )
        seen.add(sample_id)
        if value["selected"]:
            selected.add(sample_id)
    return selected


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def evaluate_shadow(
    *,
    kb: Path,
    skill_root: Path,
    evaluation_dir: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    root = _private_directory(evaluation_dir, kb=kb, skill_root=skill_root)
    golden = _golden(root / "golden.json")
    valid_ids = set(golden)
    legacy = _predictions(root / "legacy.json", valid_ids)
    dreaming = _predictions(root / "dreaming.json", valid_ids)
    expected = {sample_id for sample_id, value in golden.items() if value["expected"]}
    p0p1 = {
        sample_id
        for sample_id in expected
        if golden[sample_id]["priority"] in {"P0", "P1"}
    }
    true_positive = expected & dreaming
    false_positive = dreaming - expected
    slices = sorted(
        {
            slice_name
            for value in golden.values()
            for slice_name in value["slices"]
        }
    )
    slice_recall = {}
    slice_counts = {}
    for slice_name in slices:
        population = {
            sample_id
            for sample_id in expected
            if slice_name in golden[sample_id]["slices"]
        }
        slice_recall[slice_name] = _ratio(
            len(population & dreaming),
            len(population),
        )
        slice_counts[slice_name] = len(population)
    overall_recall = _ratio(len(true_positive), len(expected))
    p0p1_recall = _ratio(len(p0p1 & dreaming), len(p0p1))
    precision = _ratio(len(true_positive), len(true_positive) + len(false_positive))
    dataset_ready = len(golden) >= 200 and all(
        slice_counts.get(slice_name, 0) >= 20
        for slice_name in REQUIRED_SLICES
    )
    gate_passed = (
        dataset_ready
        and
        overall_recall >= 0.90
        and p0p1_recall >= 0.95
        and all(value >= overall_recall - 0.05 for value in slice_recall.values())
        and not ((p0p1 & legacy) - dreaming)
    )
    result = {
        "schema_version": "byteworker-shadow-evaluation/v1",
        "sample_count": len(golden),
        "expected_count": len(expected),
        "metrics": {
            "recall": overall_recall,
            "p0_p1_recall": p0p1_recall,
            "precision": precision,
            "slice_recall": slice_recall,
            "slice_positive_counts": slice_counts,
        },
        "dataset_ready": dataset_ready,
        "missed_sample_ids": sorted(expected - dreaming),
        "false_positive_sample_ids": sorted(false_positive),
        "legacy_p0_p1_regression_ids": sorted((p0p1 & legacy) - dreaming),
        "gate_passed": gate_passed,
        "evaluated_at": current.isoformat(),
    }
    history_path = root / "metrics-history.jsonl"
    descriptor = os.open(
        history_path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    os.chmod(history_path, 0o600)
    with os.fdopen(descriptor, "ab") as handle:
        handle.write(
            json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
    history = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        evaluated = value.get("evaluated_at") if isinstance(value, Mapping) else None
        try:
            day = datetime.fromisoformat(str(evaluated)).date()
        except ValueError:
            continue
        if day.weekday() < 5:
            history.append((day, bool(value.get("gate_passed"))))
    latest_by_day = {}
    for day, passed in history:
        latest_by_day[day] = passed
    latest = sorted(latest_by_day.items())[-10:]
    eligible = (
        len(latest) == 10
        and all(passed for _, passed in latest)
        and (latest[-1][0] - latest[0][0]).days >= 11
    )
    result["product_gate"] = {
        "passing_workdays": sum(1 for _, passed in latest if passed),
        "window_workdays": len(latest),
        "eligible_for_inbox_removal": eligible,
    }
    atomic_write_json(root / "metrics.json", result)
    return result
