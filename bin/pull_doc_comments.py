#!/usr/bin/env python3
"""Fetch a complete, deterministic Feishu document comment snapshot.

The snapshot is written to stdout as JSON. It includes resolved comments,
complete reply chains, and docx relation metadata. Human-readable progress and
errors go to stderr so callers can safely redirect stdout into a temporary file.
"""

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone


MAX_PAGES = 1000


class CommentFetchError(RuntimeError):
    pass


def _unwrap(payload):
    if not isinstance(payload, dict):
        raise CommentFetchError("lark-cli 返回的不是 JSON object")
    if payload.get("ok") is False:
        detail = payload.get("error") or payload.get("message") or payload
        raise CommentFetchError("lark-cli 请求失败: %s" % detail)
    data = payload.get("data", payload)
    if isinstance(data, dict) and set(data) == {"data"}:
        data = data["data"]
    if not isinstance(data, dict):
        raise CommentFetchError("lark-cli 返回缺少 data object")
    return data


def _run_json(args, runner=subprocess.run):
    try:
        completed = runner(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommentFetchError("无法执行 lark-cli 或请求超时: %s" % exc) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        raise CommentFetchError(detail)
    try:
        return _unwrap(json.loads(completed.stdout))
    except json.JSONDecodeError as exc:
        raise CommentFetchError("lark-cli 返回无法解析为 JSON: %s" % exc) from exc


def _page_stream(first_args, token_flag, runner=subprocess.run):
    page_token = None
    for _ in range(MAX_PAGES):
        args = list(first_args)
        if page_token:
            args.extend([token_flag, page_token])
        data = _run_json(args, runner=runner)
        yield data
        if not data.get("has_more"):
            return
        next_token = data.get("page_token")
        if not next_token or next_token == page_token:
            raise CommentFetchError("分页返回 has_more=true 但缺少新的 page_token")
        page_token = next_token
    raise CommentFetchError("分页超过安全上限 %d" % MAX_PAGES)


def _sort_replies(replies):
    return sorted(
        replies,
        key=lambda item: (
            int(item.get("create_time") or 0),
            str(item.get("reply_id") or ""),
        ),
    )


def _fetch_all_replies(comment, file_token, file_type, identity, runner):
    existing = comment.get("reply_list", {}).get("replies", [])
    if not comment.get("has_more"):
        return _sort_replies(existing), False
    if not file_token or not file_type:
        raise CommentFetchError("回复链需要 file_token / file_type,但评论页未返回")

    args = [
        "lark-cli",
        "drive",
        "file.comment.replys",
        "list",
        "--as",
        identity,
        "--file-token",
        file_token,
        "--file-type",
        file_type,
        "--comment-id",
        str(comment["comment_id"]),
        "--page-size",
        "100",
        "--format",
        "json",
    ]
    replies = []
    for page in _page_stream(args, "--page-token", runner=runner):
        replies.extend(page.get("items", []))
    return _sort_replies(replies), True


def _canonical_comment(comment, replies):
    normalized = copy.deepcopy(comment)
    normalized.pop("has_more", None)
    normalized.pop("page_token", None)
    normalized["reply_list"] = {"replies": replies}
    return normalized


def _hash_comments(comments):
    encoded = json.dumps(
        comments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def fetch_snapshot(url, identity="user", runner=subprocess.run, fetched_at=None):
    args = [
        "lark-cli",
        "drive",
        "+list-comments",
        "--as",
        identity,
        "--url",
        url,
        "--solved-status",
        "all",
        "--comment-scope",
        "all",
        "--need-relation",
        "--page-size",
        "100",
        "--format",
        "json",
    ]

    comments = []
    file_token = None
    file_type = None
    expanded_threads = 0
    for page in _page_stream(args, "--page-token", runner=runner):
        page_token_value = page.get("file_token")
        page_type_value = page.get("file_type")
        if page_token_value:
            if file_token and page_token_value != file_token:
                raise CommentFetchError("评论分页返回了不一致的 file_token")
            file_token = page_token_value
        if page_type_value:
            if file_type and page_type_value != file_type:
                raise CommentFetchError("评论分页返回了不一致的 file_type")
            file_type = page_type_value

        for item in page.get("items", []):
            if not item.get("comment_id"):
                raise CommentFetchError("评论记录缺少 comment_id")
            replies, expanded = _fetch_all_replies(
                item,
                file_token or page_token_value,
                file_type or page_type_value,
                identity,
                runner,
            )
            expanded_threads += int(expanded)
            comments.append(_canonical_comment(item, replies))

    comments.sort(
        key=lambda item: (
            int(item.get("create_time") or 0),
            str(item.get("comment_id") or ""),
        )
    )
    timestamp = fetched_at or datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds"
    )
    return {
        "schema_version": 1,
        "source_url": url,
        "file_token": file_token,
        "file_type": file_type,
        "fetched_at": timestamp,
        "coverage": {
            "status": "complete",
            "solved_status": "all",
            "comment_scope": "all",
            "relation_requested": True,
            "expanded_reply_threads": expanded_threads,
        },
        "comment_count": len(comments),
        "comment_hash": _hash_comments(comments),
        "comments": comments,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="抓取飞书文档全部评论、已解决评论、回复链与正文锚点"
    )
    parser.add_argument("--url", required=True, help="飞书 doc/docx/wiki URL")
    parser.add_argument(
        "--as",
        dest="identity",
        choices=("user", "bot"),
        default="user",
        help="lark-cli 身份类型，默认 user",
    )
    parser.add_argument("--pretty", action="store_true", help="缩进输出 JSON")
    args = parser.parse_args(argv)

    try:
        snapshot = fetch_snapshot(args.url, identity=args.identity)
    except CommentFetchError as exc:
        print("评论抓取失败: %s" % exc, file=sys.stderr)
        return 1

    json.dump(
        snapshot,
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if args.pretty else None,
    )
    sys.stdout.write("\n")
    print(
        "comments=%d hash=%s coverage=complete"
        % (snapshot["comment_count"], snapshot["comment_hash"]),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
