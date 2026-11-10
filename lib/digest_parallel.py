"""Deterministic shard planning and result reduction for digest analysis."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from operation_trace import DIGEST_RUN_ID
from workflow_budget import estimate_tokens, inspect_budget, load_manifest, resolve_route


PARALLEL_PLAN_SCHEMA = "byteworker-digest-parallel-plan/v1"
PARALLEL_SHARD_SCHEMA = "byteworker-digest-parallel-shard/v1"
PARALLEL_RESULT_SCHEMA = "byteworker-digest-parallel-result/v1"
REDUCE_PACKET_SCHEMA = "byteworker-digest-reduce-packet/v1"
ANALYSIS_PACKET_SCHEMA = "byteworker-digest-analysis-packet/v1"
CONFLICT_CANDIDATES_SCHEMA = "byteworker-conflict-candidates/v1"
MAX_WORKERS = 4
MAX_BUDGET_SHARDS = 128
MIN_PARALLEL_ITEMS = {"dependency": 32, "semantic": 200, "conflict": 16}
SOURCE_TOKEN_FLOOR = {"dependency": 12_000, "semantic": 64_000, "conflict": 24_000}
TARGET_SHARD_TOKENS = {"dependency": 16_000, "semantic": 64_000, "conflict": 24_000}
PARALLEL_TOTAL_TOKEN_BUDGET = {
    "dependency": 128_000,
    "semantic": 512_000,
    "conflict": 192_000,
}
OUTPUT_TOKENS_PER_ITEM = {"dependency": 40, "semantic": 120, "conflict": 80}
INPUT_TOKENS_PER_SECOND = {"dependency": 1_000, "semantic": 800, "conflict": 800}
OUTPUT_TOKENS_PER_SECOND = 50
WORKER_STARTUP_MS = 8_000
REDUCER_OVERHEAD_MS = 10_000
MIN_WALL_SAVINGS_MS = 30_000
MIN_WALL_SAVINGS_RATIO = 0.25
WORKER_WORKFLOWS = {
    "dependency": "digest_dependency_worker",
    "semantic": "digest_semantic_worker",
    "conflict": "digest_conflict_worker",
}
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
    return {
        "items": items,
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
    return {
        "items": items,
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
    return {
        "items": items,
        "shared": {"source_match": value.get("source_match", {})},
        "input_bytes": input_bytes,
        "candidate_count": total_candidates,
    }


def _route_tokens(
    workflow: str,
    *,
    skill_root: Path,
    source_type: str = "",
    features: Iterable[str] = (),
) -> tuple[int, int]:
    receipt = inspect_budget(
        workflow=workflow,
        source_type=source_type,
        features=features,
        root=skill_root,
        manifest_path=skill_root / "references" / "workflow-routes.json",
    )
    measurement = receipt["measurement"]
    tokens = int(measurement["static_rules_tokens"]) + int(
        measurement["worker_prompt_tokens"]
    )
    source_budget = int(receipt["budget"]["source_packet_tokens"])
    return tokens, source_budget


def _wall_time_ms(
    *, stage: str, source_tokens: int, output_tokens: int, workers: int
) -> int:
    input_ms = math.ceil(
        source_tokens / INPUT_TOKENS_PER_SECOND[stage] * 1000 / max(1, workers)
    )
    output_ms = math.ceil(
        output_tokens / OUTPUT_TOKENS_PER_SECOND * 1000 / max(1, workers)
    )
    return input_ms + output_ms


def _plan_mode(
    *,
    stage: str,
    value: Mapping[str, Any],
    source_text: str,
    item_count: int,
    max_workers: int,
    skill_root: Path,
) -> dict[str, Any]:
    source_estimate = estimate_tokens(source_text)
    source_tokens = int(source_estimate["tokens"])
    output_tokens = item_count * OUTPUT_TOKENS_PER_ITEM[stage]
    identity = value.get("identity", {})
    source_type = str(identity.get("source_type", "")) if isinstance(identity, Mapping) else ""
    section_kinds = {
        str(section.get("kind", ""))
        for section in value.get("sections", [])
        if isinstance(section, Mapping)
    }
    features = []
    if "comment" in section_kinds or "comments" in section_kinds:
        features.append("comments")
    if "whiteboard" in section_kinds:
        features.append("whiteboard")
    if source_tokens >= SOURCE_TOKEN_FLOOR[stage]:
        features.append("large_input")
    inline_rules, inline_source_budget = _route_tokens(
        "digest",
        skill_root=skill_root,
        source_type=source_type,
        features=features,
    )
    worker_rules, worker_source_budget = _route_tokens(
        WORKER_WORKFLOWS[stage], skill_root=skill_root
    )
    reducer_rules, reducer_source_budget = _route_tokens(
        "digest_final_reducer", skill_root=skill_root
    )
    proposed_workers = min(
        max_workers,
        MAX_WORKERS,
        max(1, item_count),
        max(2, math.ceil(source_tokens / TARGET_SHARD_TOKENS[stage])),
    )
    if max_workers == 1 or item_count < 2:
        proposed_workers = 1
    result_tokens = output_tokens
    inline_total = inline_rules + source_tokens + output_tokens
    parallel_total = (
        proposed_workers * worker_rules
        + source_tokens
        + output_tokens
        + reducer_rules
        + result_tokens
        + output_tokens
    )
    inline_wall = _wall_time_ms(
        stage=stage, source_tokens=source_tokens, output_tokens=output_tokens, workers=1
    )
    parallel_wall = (
        WORKER_STARTUP_MS
        + _wall_time_ms(
            stage=stage,
            source_tokens=source_tokens,
            output_tokens=output_tokens,
            workers=proposed_workers,
        )
        + REDUCER_OVERHEAD_MS
        + math.ceil(result_tokens / INPUT_TOKENS_PER_SECOND[stage] * 1000)
    )
    wall_savings = inline_wall - parallel_wall
    savings_ratio = wall_savings / inline_wall if inline_wall else 0.0
    max_shard_source_tokens = math.ceil(source_tokens / max(1, proposed_workers))
    parallel_budget = PARALLEL_TOTAL_TOKEN_BUDGET[stage]
    if max_workers == 1:
        reason = "MAX_WORKERS_ONE"
    elif item_count < MIN_PARALLEL_ITEMS[stage]:
        reason = "INSUFFICIENT_WORK_ITEMS"
    elif source_tokens < SOURCE_TOKEN_FLOOR[stage]:
        reason = "SOURCE_TOKENS_BELOW_PARALLEL_FLOOR"
    elif max_shard_source_tokens > worker_source_budget:
        reason = "WORKER_SOURCE_BUDGET_EXCEEDED"
    elif result_tokens > reducer_source_budget:
        reason = "REDUCER_SOURCE_BUDGET_EXCEEDED"
    elif parallel_total > parallel_budget:
        reason = "PARALLEL_TOTAL_TOKEN_BUDGET_EXCEEDED"
    elif wall_savings < MIN_WALL_SAVINGS_MS or savings_ratio < MIN_WALL_SAVINGS_RATIO:
        reason = "WALL_TIME_SAVINGS_BELOW_MINIMUM"
    else:
        reason = "PARALLEL_WALL_TIME_JUSTIFIES_TOKEN_PREMIUM"
    parallel = reason == "PARALLEL_WALL_TIME_JUSTIFIES_TOKEN_PREMIUM"
    return {
        "use_parallel": parallel,
        "workers": proposed_workers if parallel else 1,
        "selection_reason_code": reason,
        "estimator_method": source_estimate["method"],
        "estimator_exact": source_estimate["exact"],
        "source_tokens": source_tokens,
        "inline_source_budget": inline_source_budget,
        "worker_source_budget": worker_source_budget,
        "worker_rule_tokens": worker_rules,
        "inline": {
            "estimated_total_tokens": inline_total,
            "estimated_wall_time_ms": inline_wall,
        },
        "parallel_estimate": {
            "estimated_total_tokens": parallel_total,
            "estimated_wall_time_ms": parallel_wall,
            "proposed_workers": proposed_workers,
            "estimated_max_shard_source_tokens": max_shard_source_tokens,
        },
        "parallel_token_budget": parallel_budget,
        "parallel_budget_remaining_tokens": max(0, parallel_budget - parallel_total),
        "estimated_wall_savings_ms": wall_savings,
        "estimated_wall_savings_ratio_millis": round(savings_ratio * 1000),
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
        **({"anchors_deferred": True} if shared.get("anchors_deferred") else {}),
    }


def _shard_payload(stage: str, bucket: Sequence[dict[str, Any]], shared: Mapping[str, Any]) -> dict[str, Any]:
    if stage == "semantic":
        return _semantic_shard(bucket, shared)
    identity_key = "identity" if stage == "dependency" else "source_match"
    return {identity_key: shared.get(identity_key, {}), "items": [item["value"] for item in bucket]}


def _json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def _shard_document(
    stage: str, bucket: Sequence[dict[str, Any]], shared: Mapping[str, Any],
    input_hash: str, index: int, count: int,
) -> dict[str, Any]:
    return {
        "schema_version": PARALLEL_SHARD_SCHEMA,
        "stage": stage,
        "input_hash": input_hash,
        "shard_id": f"{stage}-{index:03d}",
        "shard_index": index,
        "shard_count": count,
        **_shard_payload(stage, bucket, shared),
    }


def _split_text_item(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    value = item["value"]
    text = value["text"]
    if len(text) < 2:
        return None
    middle = len(text) // 2
    split_at = text.rfind("\n", middle // 2, middle + 1) + 1
    if split_at == 0:
        split_at = max(text.rfind(marker, middle // 2, middle + 1) for marker in ("。", "！", "？", " ")) + 1
    if split_at == 0 or split_at >= len(text):
        split_at = middle
    start = value.get("text_range", {}).get("start", 0)
    return (
        {**item, "_weight": split_at, "value": {
            **value, "text": text[:split_at], "text_range": {"start": start, "end": start + split_at},
        }},
        {**item, "_weight": len(text) - split_at, "value": {
            **value, "text": text[split_at:], "text_range": {"start": start + split_at, "end": start + len(text)},
        }},
    )


def _budget_buckets(stage, items, shared, input_hash, limit):
    # Bisection keeps the source order and checks the serialized model input,
    # including repeated shard metadata, rather than trusting an average size.
    pending = [items]
    result = []

    def size(bucket):
        sample = _shard_document(stage, bucket, shared, input_hash, MAX_BUDGET_SHARDS, MAX_BUDGET_SHARDS)
        return estimate_tokens(_json_text(sample))["tokens"]

    while pending:
        bucket = pending.pop()
        count = size(bucket)
        if count <= limit:
            if result and size(result[-1] + bucket) <= limit:
                result[-1] = result[-1] + bucket
            else:
                result.append(bucket)
            continue
        parts = None
        if len(bucket) > 1:
            middle = len(bucket) // 2
            parts = (bucket[:middle], bucket[middle:])
        elif bucket and stage == "semantic":
            split = _split_text_item(bucket[0])
            if split:
                parts = ([split[0]], [split[1]])
        if not parts or len(result) + len(pending) + 2 > MAX_BUDGET_SHARDS:
            raise DigestParallelError(
                "DIGEST_SOURCE_SHARDING_UNAVAILABLE", "在当前预算内无法生成完整的有界分片。",
                details={"stage": stage, "input_tokens": count, "source_packet_limit": limit},
            )
        pending.extend(reversed(parts))
    return result


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
    input_hash = _canonical_hash(value)
    shared = work["shared"]
    inline_document = _shard_document(stage, work["items"], shared, input_hash, 1, 1)
    decision = _plan_mode(
        stage=stage,
        value=value,
        source_text=_json_text(inline_document),
        item_count=item_count,
        max_workers=max_workers,
        skill_root=skill_root,
    )
    budget_required = decision["source_tokens"] > decision["inline_source_budget"]
    buckets = _balanced(work["items"], decision["workers"]) if item_count else [[]]
    if decision["use_parallel"] and not budget_required:
        budget_required = any(
            estimate_tokens(_json_text(_shard_document(stage, bucket, shared, input_hash, index, len(buckets))))["tokens"]
            > decision["worker_source_budget"]
            for index, bucket in enumerate(buckets, start=1)
        )
    if budget_required:
        if stage == "semantic":
            shared = {**shared, "anchors": [], "anchors_deferred": True}
        buckets = _budget_buckets(stage, work["items"], shared, input_hash, decision["worker_source_budget"])
    shard_count = len(buckets)
    shard_entries = []
    for index, bucket in enumerate(buckets, start=1):
        shard_id = f"{stage}-{index:03d}"
        shard_path = out_dir / f"{shard_id}.json"
        shard = _shard_document(stage, bucket, shared, input_hash, index, shard_count)
        shard_tokens = estimate_tokens(_json_text(shard))["tokens"] if budget_required else None
        if shard_tokens is not None and shard_tokens > decision["worker_source_budget"]:
            raise DigestParallelError(
                "DIGEST_SOURCE_SHARDING_UNAVAILABLE", "分片序列化后超过 worker 输入上限。",
                details={"stage": stage, "input_tokens": shard_tokens, "source_packet_limit": decision["worker_source_budget"]},
            )
        _atomic_json(shard_path, shard)
        shard_entries.append(
            {
                "shard_id": shard_id,
                "path": str(shard_path),
                "item_count": len(bucket),
                "weight": sum(int(item["_weight"]) for item in bucket),
                **({"estimated_source_tokens": shard_tokens} if shard_tokens is not None else {}),
            }
        )
    plan_path = out_dir / f"{stage}-plan.json"
    selection_reason = "INPUT_REQUIRES_BUDGET_SHARDS" if budget_required else decision["selection_reason_code"]
    estimates = {
        key: value for key, value in decision.items()
        if key not in {"use_parallel", "workers", "selection_reason_code"}
    }
    if budget_required:
        estimates["budget_shards_total_input_tokens"] = sum(
            entry["estimated_source_tokens"] + decision["worker_rule_tokens"]
            for entry in shard_entries
        )
    plan = {
        "schema_version": PARALLEL_PLAN_SCHEMA,
        "stage": stage,
        "input_hash": input_hash,
        "mode": "parallel" if shard_count > 1 or budget_required else "inline",
        "reason_codes": list(dict.fromkeys((selection_reason, decision["selection_reason_code"]))),
        "selection_reason_code": selection_reason,
        "budget_required": budget_required,
        "estimates": estimates,
        "max_workers": max_workers,
        "shard_count": shard_count,
        "work_item_count": item_count,
        "input_bytes": input_bytes,
        "shards": shard_entries,
    }
    if stage == "semantic":
        plan["content_fingerprint"] = _canonical_hash({
            "identity": value.get("identity"), "components": value.get("component_hashes"),
            "algorithm": value.get("analysis_algorithm_version"),
        })
    _atomic_json(plan_path, plan)
    return {
        "schema_version": PARALLEL_PLAN_SCHEMA,
        "stage": stage,
        "mode": plan["mode"],
        "reason_codes": plan["reason_codes"],
        "selection_reason_code": plan["selection_reason_code"],
        "budget_required": budget_required,
        "max_workers": max_workers,
        "estimates": plan["estimates"],
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
        "todo_candidate",  # Accepted for in-flight shards, then dropped on merge.
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


def _reducer_batches(packet: dict[str, Any], output: Path, *, skill_root: Path) -> list[dict[str, Any]]:
    _, limit = _route_tokens("digest_final_reducer", skill_root=skill_root)
    if estimate_tokens(_json_text(packet))["tokens"] <= limit:
        return []

    duplicates = set(packet["duplicate_dedupe_keys"])
    pending = [packet["records"]]
    batches = []
    while pending:
        records = pending.pop()
        keys = {str(record.get("dedupe_key", "")) for record in records} & duplicates
        batch = {
            **packet,
            "records": records,
            "duplicate_dedupe_keys": sorted(keys),
            "stats": {
                "shard_count": len({record["origin_shard_id"] for record in records}),
                "record_count": len(records),
                "duplicate_dedupe_key_count": len(keys),
            },
        }
        tokens = estimate_tokens(_json_text(batch))["tokens"]
        if tokens <= limit:
            batches.append((batch, tokens))
            continue
        if len(records) < 2 or len(batches) + len(pending) + 2 > MAX_BUDGET_SHARDS:
            raise DigestParallelError(
                "DIGEST_SOURCE_SHARDING_UNAVAILABLE", "归并结果无法在当前预算内完整分批。",
                details={"stage": "reduce", "input_tokens": tokens, "source_packet_limit": limit},
            )
        middle = len(records) // 2
        pending.extend((records[middle:], records[:middle]))

    receipts = []
    for index, (batch, tokens) in enumerate(batches, start=1):
        path = output.with_name(f"{output.name}.batch-{index:03d}.json")
        _atomic_json(path, batch)
        receipts.append({"path": str(path), "record_count": len(batch["records"]), "estimated_source_tokens": tokens})
    return receipts


def _plan_shards(plan: Mapping[str, Any]) -> tuple[str, dict[str, Path]]:
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
    return stage, expected


def _validate_shard_result(shard, result, stage, shard_id, input_hash):
    if (
        shard.get("schema_version") != PARALLEL_SHARD_SCHEMA
        or shard.get("stage") != stage or shard.get("shard_id") != shard_id
        or shard.get("input_hash") != input_hash
    ):
        raise DigestParallelError("DIGEST_PARALLEL_INPUT_INVALID", "shard 与 plan 不一致。")
    records = _result_records(result, stage=stage, shard_id=shard_id, input_hash=input_hash)
    {
        "dependency": _validate_dependency_records,
        "semantic": _validate_semantic_records,
        "conflict": _validate_conflict_records,
    }[stage](shard, records)
    return records


def checkpoint_parallel_results(
    kb: Path, plan_path: Path, *, result_path: Path | None = None, skill_root: Path = DEFAULT_SKILL_ROOT,
) -> dict[str, Any]:
    """Save one validated shard or return the completed and missing shards."""
    resolved, plan = _load_json(plan_path, skill_root=skill_root)
    stage, expected = _plan_shards(plan)
    root = kb.resolve() / "state" / "digest"
    relative = resolved.relative_to(root) if resolved.is_relative_to(root) else Path()
    if (
        len(relative.parts) != 4 or relative.parts[0] != "flows"
        or not DIGEST_RUN_ID.fullmatch(relative.parts[1]) or relative.parts[2] != stage
    ):
        raise DigestParallelError("DIGEST_PARALLEL_PATH_UNSAFE", "分片进度必须位于 Digest run 私有目录。")
    route = resolve_route(load_manifest(skill_root / "references/workflow-routes.json"), workflow=WORKER_WORKFLOWS[stage])
    rules = {
        name: hashlib.sha256((skill_root / name).read_bytes()).hexdigest()
        for name in route["static_rule_paths"] + route["worker_prompt_paths"]
    }
    supplied = _load_json(result_path, skill_root=skill_root)[1] if result_path else None
    if supplied is not None and supplied.get("shard_id") not in expected:
        raise DigestParallelError("DIGEST_PARALLEL_RESULT_INVALID", "worker result 的 shard_id 不在计划内。")
    cache = root / "shard-results"
    cutoff = time.time() - 30 * 86400
    if cache.is_symlink():
        raise DigestParallelError("DIGEST_PARALLEL_PATH_UNSAFE", "分片缓存目录不能是符号链接。")
    if supplied is None and stage == "semantic":
        for path in cache.glob("*.json"):
            try:
                if not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
    completed, missing = [], []
    input_hash = str(plan.get("input_hash", ""))
    selected = expected if supplied is None else {supplied["shard_id"]: expected[supplied["shard_id"]]}
    for shard_id, shard_path in selected.items():
        resolved_shard, shard = _load_json(shard_path, skill_root=skill_root)
        if not MACHINE_ID_RE.fullmatch(shard_id) or resolved_shard.parent != resolved.parent:
            raise DigestParallelError("DIGEST_PARALLEL_PATH_UNSAFE", "分片不属于当前计划目录。")
        fingerprint = _canonical_hash({
            "rules": rules, "content": plan.get("content_fingerprint", input_hash),
            "shard": {key: value for key, value in shard.items() if key != "input_hash"},
        })
        output = resolved.parent / "results" / f"{shard_id}.json"
        shared = cache / f"{fingerprint.removeprefix('sha256:')}.json"
        if supplied is not None and supplied.get("shard_id") == shard_id:
            _validate_shard_result(shard, supplied, stage, shard_id, input_hash)
            result = {**supplied, "checkpoint_fingerprint": fingerprint}
            _atomic_json(output, result)
            if stage == "semantic":
                _atomic_json(shared, result)
        else:
            result = None
            for candidate in (output, shared) if stage == "semantic" else (output,):
                try:
                    if candidate.is_symlink() or candidate.stat().st_mtime < cutoff:
                        continue
                    _, previous = _load_json(candidate, skill_root=skill_root)
                    if previous.get("checkpoint_fingerprint") != fingerprint:
                        continue
                    rebound = {**previous, "input_hash": input_hash}
                    _validate_shard_result(shard, rebound, stage, shard_id, input_hash)
                except (DigestParallelError, OSError):
                    continue
                result = rebound
                if candidate != output:
                    _atomic_json(output, result)
                break
        if result is None:
            missing.append(shard_id)
        else:
            completed.append({"shard_id": shard_id, "result_path": str(output)})
    return {"stage": stage, "completed": completed, "missing_shard_ids": missing}


def merge_parallel_results(
    plan_path: Path,
    result_paths: Iterable[Path],
    output_path: Path,
    *,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> dict[str, Any]:
    """Validate complete worker coverage and create one reducer input packet."""
    resolved_plan, plan = _load_json(plan_path, skill_root=skill_root)
    stage, expected = _plan_shards(plan)
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
        records = _validate_shard_result(shard, supplied[shard_id], stage, shard_id, str(plan.get("input_hash", "")))
        for record in records:
            if stage == "semantic" and record.get("record_type") == "todo_candidate":
                continue
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
    batches = _reducer_batches(packet, output, skill_root=skill_root)
    _atomic_json(output, packet)
    return {
        "schema_version": REDUCE_PACKET_SCHEMA,
        "stage": stage,
        "output_path": str(output),
        **({"reduction_batches": batches} if batches else {}),
        **packet["stats"],
    }
