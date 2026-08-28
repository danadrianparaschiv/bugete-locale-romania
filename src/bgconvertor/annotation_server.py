"""Loopback-only HTTP interface for the offline annotation workspace."""

from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from . import annotation

log = logging.getLogger("bgc.annotation")
_MAX_REQUEST_BYTES = 8 * 1024 * 1024
_ASSETS = Path(__file__).parent / "annotation_ui"


class AnnotationHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, workspace: Path, token: str):
        super().__init__(address, AnnotationRequestHandler)
        self.workspace = workspace
        self.token = token


class AnnotationRequestHandler(BaseHTTPRequestHandler):
    server: AnnotationHTTPServer

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        log.debug("annotation HTTP: " + format, *args)

    def _host_is_loopback(self) -> bool:
        host = (self.headers.get("Host") or "").split(":", 1)[0].lower()
        return host in {"127.0.0.1", "localhost"}

    def _authorized(self, query: dict[str, list[str]]) -> bool:
        supplied = self.headers.get("X-Annotation-Token") or (query.get("token") or [""])[0]
        return bool(supplied) and secrets.compare_digest(supplied, self.server.token)

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _bytes(self, payload: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
        self._headers(status, content_type, len(payload))
        self.wfile.write(payload)

    def _json(self, payload, status: int = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._bytes(encoded, "application/json; charset=utf-8", status)

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _asset(self, name: str) -> None:
        if name not in {"index.html", "app.css", "app.js"}:
            self._error(HTTPStatus.NOT_FOUND, "resursă inexistentă")
            return
        path = _ASSETS / name
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in {".html", ".css", ".js"}:
            content_type += "; charset=utf-8"
        self._bytes(path.read_bytes(), content_type)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._host_is_loopback():
            self._error(HTTPStatus.FORBIDDEN, "interfața acceptă numai acces local")
            return
        # Static code contains no source data.  Serving it token-free lets the
        # authorized root page load ordinary relative CSS/JS URLs; every API,
        # page image and workbook value remains token-gated.
        if parsed.path in {"/app.css", "/app.js"}:
            self._asset(parsed.path.removeprefix("/"))
            return
        if not self._authorized(query):
            self._error(HTTPStatus.UNAUTHORIZED, "token local invalid")
            return
        try:
            if parsed.path == "/":
                self._asset("index.html")
            elif parsed.path == "/api/workspace":
                self._json(annotation.workspace_summary(self.server.workspace))
            elif parsed.path == "/api/page":
                document = (query.get("document") or [""])[0]
                page = int((query.get("page") or ["0"])[0])
                self._json(annotation.page_payload(self.server.workspace, document, page))
            elif parsed.path == "/api/render":
                document = (query.get("document") or [""])[0]
                page = int((query.get("page") or ["0"])[0])
                path = annotation.render_pdf_page(self.server.workspace, document, page)
                self._bytes(path.read_bytes(), "image/png")
            elif parsed.path == "/api/sheet":
                document = (query.get("document") or [""])[0]
                page = int((query.get("page") or ["0"])[0])
                start = int((query.get("start") or ["1"])[0])
                self._json(annotation.workbook_window(
                    self.server.workspace, document, page, start_row=start
                ))
            else:
                self._error(HTTPStatus.NOT_FOUND, "rută inexistentă")
        except (KeyError, ValueError, RuntimeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            log.exception("annotation GET failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "eroare internă; consultați terminalul")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise ValueError("Content-Length invalid") from exc
        if length <= 0 or length > _MAX_REQUEST_BYTES:
            raise ValueError("dimensiune cerere invalidă")
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValueError("JSON invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("corpul cererii trebuie să fie obiect JSON")
        return payload

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._host_is_loopback():
            self._error(HTTPStatus.FORBIDDEN, "interfața acceptă numai acces local")
            return
        if not self._authorized(query):
            self._error(HTTPStatus.UNAUTHORIZED, "token local invalid")
            return
        try:
            payload = self._body()
            if parsed.path == "/api/page":
                action = payload.get("action", "save")
                review = annotation.save_review(
                    self.server.workspace,
                    str(payload["document"]),
                    int(payload["page"]),
                    dict(payload.get("review") or {}),
                    freeze=action == "freeze",
                    unfreeze=action == "unfreeze",
                )
                self._json({"review": review.model_dump(mode="json")})
            elif parsed.path == "/api/scope":
                document = annotation.set_benchmark_scope(
                    self.server.workspace,
                    str(payload["document"]),
                    str(payload["scope"]),
                )
                self._json({"document": document.id, "benchmark_scope": document.benchmark_scope})
            elif parsed.path == "/api/second-review":
                review = annotation.complete_second_review(
                    self.server.workspace,
                    str(payload["document"]),
                    int(payload["page"]),
                    expected_revision=int(payload["expected_revision"]),
                    reviewer=str(payload["reviewer"]),
                )
                self._json({"review": review.model_dump(mode="json")})
            else:
                self._error(HTTPStatus.NOT_FOUND, "rută inexistentă")
        except (KeyError, ValueError, RuntimeError) as exc:
            self._error(HTTPStatus.CONFLICT if "revizie conflictuală" in str(exc) else HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            log.exception("annotation POST failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "eroare internă; consultați terminalul")


def serve(
    workspace: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> str:
    """Run the local UI until interrupted and return its URL on shutdown."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("serverul de annotare poate asculta numai pe loopback")
    annotation.load_workspace(workspace)
    token = secrets.token_urlsafe(24)
    server = AnnotationHTTPServer((host, port), workspace, token)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/?{urlencode({'token': token})}"
    print(f"Annotation UI: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return url
