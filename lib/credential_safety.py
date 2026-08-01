"""Credential-shape detection shared by persisted source contracts."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit


_SENSITIVE_NAMES = {
    "accesstoken",
    "apikey",
    "authtoken",
    "authorization",
    "bearertoken",
    "bytecloudjwt",
    "clientsecret",
    "cookie",
    "credential",
    "credentials",
    "disposablelogintoken",
    "jwt",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "sessiontoken",
    "sign",
    "signature",
    "titanpassport",
    "token",
}
_CREDENTIAL_SUFFIXES = (
    "accesstoken",
    "apikey",
    "authtoken",
    "bearertoken",
    "clientsecret",
    "credential",
    "jwt",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "sessiontoken",
    "signature",
)


def normalize_field_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", unquote(str(value))).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def is_credential_field(value: object) -> bool:
    normalized = normalize_field_name(value)
    return bool(normalized) and (
        normalized in _SENSITIVE_NAMES
        or any(normalized.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES)
    )


def credential_url_fields(value: str) -> list[str]:
    """Return credential locations in an HTTP(S) URL, including fragment data."""

    parsed = urlsplit(value)
    findings: set[str] = set()
    if parsed.username is not None or parsed.password is not None:
        findings.add("userinfo")
    for location, encoded in (("query", parsed.query), ("fragment", parsed.fragment)):
        for key, _item in parse_qsl(encoded.replace(";", "&"), keep_blank_values=True):
            if is_credential_field(key):
                findings.add(f"{location}:{key}")
    return sorted(findings)
