"""Read-only document aliases for the routine index and execution inventory."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from itertools import chain
from urllib.parse import urlsplit


IdentityKey = tuple[str, str, str]


def _document_keys(record: Mapping) -> list[IdentityKey]:
    if record.get("source_type") != "feishu_doc":
        return []
    keys = []
    uid = str(record.get("source_uid") or "").strip()
    if uid:
        keys.append(("feishu_doc", "uid", uid))
    try:
        url = urlsplit(str(record.get("source_url") or "").strip())
        path = url.path.rstrip("/")
        if (
            url.scheme == "https"
            and url.hostname
            and url.username is None
            and re.fullmatch(r"/(?:wiki|docx)/[A-Za-z0-9_-]+", path)
        ):
            keys.append(("feishu_doc", "url", url.netloc.lower() + path))
    except ValueError:
        pass
    return keys


class RoutineDocumentAliases:
    """A raw or Profile with both UID and URL links those two identities."""

    def __init__(self, records: Iterable[Mapping], profiles: Iterable[Mapping]):
        self._parents: dict[IdentityKey, IdentityKey] = {}
        profiles = list(profiles)
        for record in chain(records, profiles):
            keys = _document_keys(record)
            if len(keys) == 2:
                first, second = sorted(self._root(key) for key in keys)
                if first != second:
                    self._parents[second] = first
        profile_keys = [key for profile in profiles if (key := self.key(profile))]
        if len(profile_keys) != len(set(profile_keys)):
            raise ValueError("同一飞书文档存在多个来源 Profile，无法唯一确定定期摄取配置。")

    def _root(self, key: IdentityKey) -> IdentityKey:
        while key in self._parents:
            key = self._parents[key]
        return key

    def key(self, record: Mapping) -> IdentityKey | None:
        keys = _document_keys(record)
        return self._root(keys[0]) if keys else None
