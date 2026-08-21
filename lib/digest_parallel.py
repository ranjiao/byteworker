"""Deterministic shard planning and result reduction for digest analysis."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PARALLEL_PLAN_SCHEMA = "byteworker-digest-parallel-plan/v1"
PARALLEL_SHARD_SCHEMA = "byteworker-digest-parallel-shard/v1"
PARALLEL_RESULT_SCHEMA = "byteworker-digest-parallel-result/v1"
REDUCE_PACKET_SCHEMA = "byteworker-digest-reduce-packet/v1"
ANALYSIS_PACKET_SCHEMA = "byteworker-digest-analysis-packet/v1"
CONFLICT_CANDIDATES_SCHEMA = "byteworker-conflict-candidates/v1"
MAX_WORKERS = 4
DEPENDENCY_PARALLEL_THRESHOLD = 12
SEMANTIC_ITEM_THRESHOLD = 500
SEMANTIC_BYTE_THRESHOLD = 1024 * 1024
CONFLICT_QUERY_THRESHOLD = 8
CONFLICT_CANDIDATE_THRESHOLD = 20
MACHINE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[1]


class DigestParallelError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.details:
            result["details"] = self.details
        return result


def _outside_skill(path: Path, *, skill_root: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    root = skill_root.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return resolved
    raise DigestParallelError(
        "DIGEST_PARALLEL_PATH_IN_SKILL_REPO",
        "并发分片、结果和归并包不得位于 byteworker skill 仓库。",
        details={"path": str(resolved)},
    )


def _load_json(path: Path, *, skill_root: Path) -> tuple[Path, Mapping[str, Any]]:
    resolved = _outside_skill(path, skill_root=skill_root)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DigestParallelError(
            "DIGEST_PARALLEL_INPUT_INVALID", "并发输入无法读取或不是合法 JSON。"
        ) from exc
    if not isinstance(value, Mapping):
        raise DigestParallelError(
            "DIGEST_PARALLEL_INPUT_INVALID", "并发输入必须为 JSON object。"
        )
    return resolved, value


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise DigestParallelError(
            "DIGEST_PARALLEL_PATH_UNSAFE", "并发产物或父目录不能是符号链接。"
        )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".digest-parallel-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _balanced(items: Sequence[dict[str, Any]], count: int) -> list[list[dict[str, Any]]]:
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(count)]
    weights = [0] * count
    for item in sorted(items, key=lambda value: (-int(value["_weight"]), int(value["_order"]))):
        index = min(range(count), key=lambda candidate: (weights[candidate], candidate))
        buckets[index].append(item)
        weights[index] += int(item["_weight"])
    for bucket in buckets:
        bucket.sort(key=lambda value: int(value["_order"]))
    return buckets


def _dependency_work(value: Mapping[str, Any], input_bytes: int) -> dict[str, Any]:
    if value.get("schema_version") != ANALYSIS_PACKET_SCHEMA:
        raise DigestParallelError(
            "DIGEST_PARALLEL_INPUT_INVALID", "dependency stage 需要 analysis packet v1。"
        )
    candidates = value.get("dependency_candidates")
    if not isinstance(candidates, list):
        raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "依赖候选必须为数组。")
    items = [
        {"_order": index, "_weight": max(1, len(json.dumps(item, ensure_ascii=False))), "value": item}
        for index, item in enumerate(candidates)
    ]
    parallel = len(items) >= DEPENDENCY_PARALLEL_THRESHOLD
    workers = max(2, math.ceil(len(items) / 8)) if parallel else 1
    return {
        "items": items,
        "parallel": parallel,
        "workers": workers,
        "reason_codes": [
            "DEPENDENCY_CANDIDATE_THRESHOLD"
            if parallel
            else "DEPENDENCY_INLINE_BELOW_THRESHOLD"
        ],
        "shared": {"identity": value.get("identity", {})},
        "input_bytes": input_bytes,
    }


def _semantic_work(value: Mapping[str, Any], input_bytes: int) -> dict[str, Any]:
    if value.get("schema_version") != ANALYSIS_PACKET_SCHEMA:
        raise DigestParallelError(
            "DIGEST_PARALLEL_INPUT_INVALID", "semantic stage 需要 analysis packet v1。"
        )
    sections = value.get("sections")
    if not isinstance(sections, list):
        raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "sections 必须为数组。")
    items: list[dict[str, Any]] = []
    order = 0
    for section in sections:
        if not isinstance(section, Mapping) or not isinstance(section.get("text_items"), list):
            raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "section 结构非法。")
        for text_item in section["text_items"]:
            if not isinstance(text_item, Mapping) or not isinstance(text_item.get("text"), str):
                raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "text_item 结构非法。")
            items.append(
                {
                    "_order": order,
                    "_weight": max(1, len(text_item["text"])),
                    "section": {
                        key: section.get(key)
                        for key in ("name", "kind", "heading", "source_bytes")
                    },
                    "value": dict(text_item),
                }
            )
            order += 1
    reasons = []
    if len(items) >= SEMANTIC_ITEM_THRESHOLD:
        reasons.append("SEMANTIC_ITEM_THRESHOLD")
    if input_bytes >= SEMANTIC_BYTE_THRESHOLD:
        reasons.append("SEMANTIC_BYTE_THRESHOLD")
    parallel = bool(reasons) and len(items) > 1
    workers = (
        max(2, math.ceil(max(len(items) / 250, input_bytes / SEMANTIC_BYTE_THRESHOLD)))
        if parallel
        else 1
    )
    return {
        "items": items,
        "parallel": parallel,
        "workers": workers,
        "reason_codes": reasons or ["SEMANTIC_INLINE_BELOW_THRESHOLD"],
        "shared": {
            "identity": value.get("identity", {}),
            "outline": value.get("outline", []),
            "anchors": value.get("anchors", []),
        },
        "input_bytes": input_bytes,
    }


def _conflict_work(value: Mapping[str, Any], input_bytes: int) -> dict[str, Any]:
    if value.get("schema_version") != CONFLICT_CANDIDATES_SCHEMA:
        raise DigestParallelError(
            "DIGEST_PARALLEL_INPUT_INVALID", "conflict stage 需要 conflict candidates v1。"
        )
    queries = value.get("queries")
    if not isinstance(queries, list):
        raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "queries 必须为数组。")
    total_candidates = 0
    items = []
    for index, query in enumerate(queries):
        if not isinstance(query, Mapping) or not isinstance(query.get("candidates"), list):
            raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "conflict query 结构非法。")
        total_candidates += len(query["candidates"])
        items.append(
            {
                "_order": index,
                "_weight": max(1, len(json.dumps(query, ensure_ascii=False))),
                "value": query,
            }
        )
    reasons = []
    if len(items) >= CONFLICT_QUERY_THRESHOLD:
        reasons.append("CONFLICT_QUERY_THRESHOLD")
    if total_candidates > CONFLICT_CANDIDATE_THRESHOLD:
        reasons.append("CONFLICT_CANDIDATE_THRESHOLD")
    parallel = bool(reasons) and len(items) > 1
    workers = (
        max(2, math.ceil(max(len(items) / 4, total_candidates / 10)))
        if parallel
        else 1
    )
    return {
        "items": items,
        "parallel": parallel,
        "workers": workers,
        "reason_codes": reasons or ["CONFLICT_INLINE_BELOW_THRESHOLD"],
        "shared": {"source_match": value.get("source_match", {})},
        "input_bytes": input_bytes,
    }


def _semantic_shard(bucket: Sequence[dict[str, Any]], shared: Mapping[str, Any]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    participant_ids: set[str] = set()
    for item in bucket:
        section = item["section"]
        name = str(section.get("name", ""))
        target = grouped.setdefault(name, {**section, "text_items": []})
        target["text_items"].append(item["value"])
        participant_ids.update(re.findall(r"ou_[A-Za-z0-9]+", item["value"]["text"]))
    components = set(grouped)
    anchors = [
        anchor
        for anchor in shared.get("anchors", [])
        if isinstance(anchor, Mapping) and anchor.get("component") in components
    ]
    return {
        "identity": shared.get("identity", {}),
        "outline": shared.get("outline", []),
        "participant_ids": sorted(participant_ids),
        "sections": list(grouped.values()),
        "anchors": anchors,
    }


def plan_parallel_work(
    input_path: Path,
    output_dir: Path,
    *,
    stage: str,
    max_workers: int = MAX_WORKERS,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> dict[str, Any]:
    """Create private, balanced shards and a coordinator plan."""

    if stage not in {"dependency", "semantic", "conflict"}:
        raise DigestParallelError("DIGEST_PARALLEL_STAGE_INVALID", "stage 非法。")
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or not 1 <= max_workers <= MAX_WORKERS:
        raise DigestParallelError(
            "DIGEST_PARALLEL_WORKERS_INVALID", f"max_workers 必须为 1-{MAX_WORKERS}。"
        )
    resolved_input, value = _load_json(input_path, skill_root=skill_root)
    out_dir = _outside_skill(output_dir, skill_root=skill_root)
    if out_dir.is_symlink():
        raise DigestParallelError("DIGEST_PARALLEL_PATH_UNSAFE", "输出目录不能是符号链接。")
    input_bytes = resolved_input.stat().st_size
    work = {
        "dependency": _dependency_work,
        "semantic": _semantic_work,
        "conflict": _conflict_work,
    }[stage](value, input_bytes)
    item_count = len(work["items"])
    requested = min(max_workers, int(work["workers"]), max(1, item_count))
    shard_count = requested if work["parallel"] else 1
    buckets = _balanced(work["items"], shard_count) if item_count else [[]]
    input_hash = _canonical_hash(value)
    shard_entries = []
    for index, bucket in enumerate(buckets, start=1):
        shard_id = f"{stage}-{index:03d}"
        shard_path = out_dir / f"{shard_id}.json"
        if stage == "semantic":
            payload = _semantic_shard(bucket, work["shared"])
        elif stage == "dependency":
            payload = {
                "identity": work["shared"].get("identity", {}),
                "items": [item["value"] for item in bucket],
            }
        else:
            payload = {
                "source_match": work["shared"].get("source_match", {}),
                "items": [item["value"] for item in bucket],
            }
        shard = {
            "schema_version": PARALLEL_SHARD_SCHEMA,
            "stage": stage,
            "input_hash": input_hash,
            "shard_id": shard_id,
            "shard_index": index,
            "shard_count": shard_count,
            **payload,
        }
        _atomic_json(shard_path, shard)
        shard_entries.append(
            {
                "shard_id": shard_id,
                "path": str(shard_path),
                "item_count": len(bucket),
                "weight": sum(int(item["_weight"]) for item in bucket),
            }
        )
    plan_path = out_dir / f"{stage}-plan.json"
    plan = {
        "schema_version": PARALLEL_PLAN_SCHEMA,
        "stage": stage,
        "input_hash": input_hash,
        "mode": "parallel" if shard_count > 1 else "inline",
        "reason_codes": work["reason_codes"],
        "max_workers": max_workers,
        "shard_count": shard_count,
        "work_item_count": item_count,
        "input_bytes": input_bytes,
        "shards": shard_entries,
    }
    _atomic_json(plan_path, plan)
    return {
        "schema_version": PARALLEL_PLAN_SCHEMA,
        "stage": stage,
        "mode": plan["mode"],
        "reason_codes": plan["reason_codes"],
        "plan_path": str(plan_path),
        "shard_count": shard_count,
        "work_item_count": item_count,
        "input_bytes": input_bytes,
    }


def _result_records(
    result: Mapping[str, Any], *, stage: str, shard_id: str, input_hash: str
) -> list[Mapping[str, Any]]:
    if (
        result.get("schema_version") != PARALLEL_RESULT_SCHEMA
        or result.get("stage") != stage
        or result.get("shard_id") != shard_id
        or result.get("input_hash") != input_hash
        or not isinstance(result.get("records"), list)
        or any(not isinstance(item, Mapping) for item in result["records"])
    ):
        raise DigestParallelError(
            "DIGEST_PARALLEL_RESULT_INVALID", f"worker result 与 shard 不匹配: {shard_id}"
        )
    return result["records"]


def _validate_dependency_records(shard: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> None:
    expected = [str(item.get("candidate_id", "")) for item in shard.get("items", [])]
    actual = [str(item.get("candidate_id", "")) for item in records]
    allowed = {"important", "not_important", "uncertain"}
    if sorted(expected) != sorted(actual) or any(item.get("disposition") not in allowed for item in records):
        raise DigestParallelError(
            "DIGEST_PARALLEL_RESULT_INVALID", "dependency result 必须逐项覆盖当前 shard。"
        )
    if any(not REASON_CODE_RE.fullmatch(str(item.get("reason_code", ""))) for item in records):
        raise DigestParallelError(
            "DIGEST_PARALLEL_RESULT_INVALID", "dependency reason_code 非法。"
        )


def _validate_semantic_records(shard: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> None:
    allowed_types = {
        "fact",
        "entity",
        "decision",
        "stakeholder_position",
        "evidence",
        "conflict_query",
        "todo_candidate",
        "warning",
    }
    allowed_refs = set()
    for section in shard.get("sections", []):
        if not isinstance(section, Mapping):
            raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "semantic shard 非法。")
        for item in section.get("text_items", []):
            if not isinstance(item, Mapping):
                raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "semantic shard 非法。")
            allowed_refs.add((str(section.get("name", "")), str(item.get("path", ""))))
    seen: set[str] = set()
    for record in records:
        record_id = str(record.get("record_id", ""))
        refs = record.get("source_refs")
        if (
            not MACHINE_ID_RE.fullmatch(record_id)
            or record_id in seen
            or record.get("record_type") not in allowed_types
            or not isinstance(record.get("dedupe_key"), str)
            or not str(record.get("dedupe_key", "")).strip()
            or len(str(record.get("dedupe_key"))) > 256
            or not isinstance(record.get("payload"), Mapping)
            or not isinstance(refs, list)
            or not refs
        ):
            raise DigestParallelError(
                "DIGEST_PARALLEL_RESULT_INVALID", "semantic record 结构非法。"
            )
        for ref in refs:
            if not isinstance(ref, Mapping) or (
                str(ref.get("component", "")), str(ref.get("path", ""))
            ) not in allowed_refs:
                raise DigestParallelError(
                    "DIGEST_PARALLEL_RESULT_INVALID", "semantic source_ref 不属于当前 shard。"
                )
        seen.add(record_id)


def _validate_conflict_records(shard: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> None:
    queries = {str(item.get("id", "")): item for item in shard.get("items", [])}
    actual = [str(item.get("query_id", "")) for item in records]
    allowed = {"no_conflict", "revision", "supersede", "independent_conflict", "uncertain"}
    if sorted(queries) != sorted(actual) or any(item.get("disposition") not in allowed for item in records):
        raise DigestParallelError(
            "DIGEST_PARALLEL_RESULT_INVALID", "conflict result 必须逐 query 覆盖当前 shard。"
        )
    for record in records:
        candidates = record.get("candidate_ids")
        if not isinstance(candidates, list) or any(not isinstance(item, str) for item in candidates):
            raise DigestParallelError("DIGEST_PARALLEL_RESULT_INVALID", "candidate_ids 非法。")
        allowed_ids = {
            str(item.get("id", "")) for item in queries[str(record["query_id"])].get("candidates", [])
        }
        if not set(candidates).issubset(allowed_ids):
            raise DigestParallelError(
                "DIGEST_PARALLEL_RESULT_INVALID", "conflict result 引用了 shard 外候选。"
            )


def merge_parallel_results(
    plan_path: Path,
    result_paths: Iterable[Path],
    output_path: Path,
    *,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> dict[str, Any]:
    """Validate complete worker coverage and create one reducer input packet."""

    resolved_plan, plan = _load_json(plan_path, skill_root=skill_root)
    if plan.get("schema_version") != PARALLEL_PLAN_SCHEMA:
        raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "并发 plan schema 非法。")
    stage = str(plan.get("stage", ""))
    if stage not in {"dependency", "semantic", "conflict"} or not isinstance(
        plan.get("shards"), list
    ):
        raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "并发 plan stage/shards 非法。")
    expected = {
        str(item.get("shard_id", "")): Path(str(item.get("path", "")))
        for item in plan.get("shards", [])
        if isinstance(item, Mapping)
    }
    if not expected or len(expected) != len(plan["shards"]):
        raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "并发 plan shards 非法或重复。")
    supplied: dict[str, Mapping[str, Any]] = {}
    for path in result_paths:
        _, result = _load_json(path, skill_root=skill_root)
        shard_id = str(result.get("shard_id", ""))
        if shard_id not in expected or shard_id in supplied:
            raise DigestParallelError(
                "DIGEST_PARALLEL_RESULT_INVALID", "worker result shard_id 缺失、重复或未知。"
            )
        supplied[shard_id] = result
    if set(supplied) != set(expected):
        raise DigestParallelError(
            "DIGEST_PARALLEL_RESULT_INCOMPLETE",
            "worker results 未覆盖全部 shards。",
            details={"missing_shard_ids": sorted(set(expected) - set(supplied))},
        )
    merged_records: list[dict[str, Any]] = []
    dedupe_counts: dict[str, int] = {}
    for shard_id in sorted(expected):
        _, shard = _load_json(expected[shard_id], skill_root=skill_root)
        if (
            shard.get("schema_version") != PARALLEL_SHARD_SCHEMA
            or shard.get("stage") != stage
            or shard.get("input_hash") != plan.get("input_hash")
        ):
            raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "shard 与 plan 不一致。")
        records = _result_records(
            supplied[shard_id],
            stage=stage,
            shard_id=shard_id,
            input_hash=str(plan.get("input_hash", "")),
        )
        {
            "dependency": _validate_dependency_records,
            "semantic": _validate_semantic_records,
            "conflict": _validate_conflict_records,
        }[stage](shard, records)
        for record in records:
            value = {**record, "origin_shard_id": shard_id}
            merged_records.append(value)
            dedupe_key = str(record.get("dedupe_key", ""))
            if dedupe_key:
                dedupe_counts[dedupe_key] = dedupe_counts.get(dedupe_key, 0) + 1
    duplicates = sorted(key for key, count in dedupe_counts.items() if count > 1)
    output = _outside_skill(output_path, skill_root=skill_root)
    packet = {
        "schema_version": REDUCE_PACKET_SCHEMA,
        "stage": stage,
        "input_hash": plan.get("input_hash"),
        "plan_path": str(resolved_plan),
        "records": merged_records,
        "duplicate_dedupe_keys": duplicates,
        "stats": {
            "shard_count": len(expected),
            "record_count": len(merged_records),
            "duplicate_dedupe_key_count": len(duplicates),
        },
    }
    _atomic_json(output, packet)
    return {
        "schema_version": REDUCE_PACKET_SCHEMA,
        "stage": stage,
        "output_path": str(output),
        **packet["stats"],
    }
