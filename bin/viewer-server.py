#!/usr/bin/env python3
"""Local Byteworker viewer server with a narrow settings API.

The server binds to 127.0.0.1 only.  Static files remain regular read-only
viewer/KB content.  The write API is explicitly token-gated and routes all
changes through lib/settings.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from settings import (  # noqa: E402
    normalize_settings_error,
    settings_view,
    update_settings,
)


MAX_BODY_BYTES = 128 * 1024


class ViewerHandler(SimpleHTTPRequestHandler):
    kb: Path
    api_token: str

    def _send_json(self, value: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        return self.headers.get("X-Byteworker-Token", "") == self.api_token

    def _reject_unauthorized(self) -> None:
        self._send_json(
            {
                "error": {
                    "code": "VIEWER_API_UNAUTHORIZED",
                    "message": "设置接口需要本次 viewer 会话 token。",
                }
            },
            status=HTTPStatus.FORBIDDEN,
        )

    def _read_json_body(self) -> Any:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length 无效。") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("请求体过大。")
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _handle_api_get(self) -> None:
        if not self._authorized():
            self._reject_unauthorized()
            return
        try:
            self._send_json(settings_view(self.kb))
        except Exception as exc:  # pragma: no cover - defensive boundary
            normalized = normalize_settings_error(exc)
            self._send_json(
                {"error": normalized.as_dict()},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_api_patch(self) -> None:
        if not self._authorized():
            self._reject_unauthorized()
            return
        try:
            patch = self._read_json_body()
            self._send_json(update_settings(self.kb, patch))
        except json.JSONDecodeError as exc:
            self._send_json(
                {
                    "error": {
                        "code": "VIEWER_API_JSON_INVALID",
                        "message": f"请求 JSON 无效: {exc}",
                    }
                },
                status=HTTPStatus.BAD_REQUEST,
            )
        except ValueError as exc:
            self._send_json(
                {
                    "error": {
                        "code": "VIEWER_API_REQUEST_INVALID",
                        "message": str(exc),
                    }
                },
                status=HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:
            normalized = normalize_settings_error(exc)
            self._send_json(
                {"error": normalized.as_dict()},
                status=HTTPStatus.BAD_REQUEST,
            )

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/api/settings":
            self._handle_api_get()
            return
        super().do_GET()

    def do_PATCH(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/api/settings":
            self._handle_api_patch()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/api/settings":
            self._handle_api_patch()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", required=True, type=Path)
    result.add_argument("--kb", required=True, type=Path)
    result.add_argument("--port", required=True, type=int)
    result.add_argument("--token", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.port < 1 or args.port > 65535:
        raise SystemExit("port must be 1..65535")
    root = args.root.expanduser().resolve()
    kb = args.kb.expanduser().resolve()
    if not root.is_dir() or not kb.is_dir():
        raise SystemExit("viewer root and KB must exist")
    if not args.token.strip():
        raise SystemExit("token must not be empty")
    class BoundViewerHandler(ViewerHandler):
        pass

    BoundViewerHandler.kb = kb
    BoundViewerHandler.api_token = args.token

    def factory(*handler_args: Any, **handler_kwargs: Any) -> BoundViewerHandler:
        return BoundViewerHandler(
            *handler_args,
            directory=str(root),
            **handler_kwargs,
        )

    server = ThreadingHTTPServer(("127.0.0.1", args.port), factory)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
