from __future__ import annotations

import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .database import DatabaseTarget, database_health
from . import __version__
from .exporter import acron_export, provider_export
from .service import (
    RevisionConflictError,
    bulk_review_datapoints,
    get_datapoint,
    list_datapoints,
    project_summary,
    recent_audit,
    update_datapoint,
)
from .upload import MAX_UPLOAD_BYTES, import_uploaded_dpl
from .pcs7 import import_pcs7_export
from .modules import list_modules
from .runtime import (
    APP_NAME,
    BUNDLED_PATHS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    RuntimePaths,
    data_directory as resolve_data_directory,
    request_timeout_seconds,
)


WEB_ROOT = Path(__file__).with_name("web")
MAX_REQUEST_BYTES = 512 * 1024
LOGGER = logging.getLogger("dpl_acron.http")


class DPLHTTPServer(ThreadingHTTPServer):
    """Thread-per-request server with bounded shutdown behavior for systemd."""

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False
    request_queue_size = 64


class DPLRequestHandler(BaseHTTPRequestHandler):
    database: DatabaseTarget
    data_directory: Path
    acron_template = BUNDLED_PATHS.acron_template
    provider_template = BUNDLED_PATHS.provider_template
    request_timeout = DEFAULT_REQUEST_TIMEOUT_SECONDS
    server_version = f"DPLAcron/{__version__}"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.request_timeout)

    def log_message(self, format: str, *args) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def _headers(self, content_type: str, length: int, status: int = 200, **extra: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "no-cache")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        for name, value in extra.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()

    def _send_json(self, payload, status: int = 200, **headers: str) -> None:
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        self._headers("application/json; charset=utf-8", len(content), status, **headers)
        self.wfile.write(content)

    def _error(self, status: int, message: str, **payload) -> None:
        self._send_json({"error": message, **payload}, status)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Ungültige Content-Length.") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("Leerer oder zu großer Request.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Ungültiges JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON-Objekt erwartet.")
        return payload

    def _read_upload(self) -> tuple[str, bytes]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Ungültige Dateigröße.") from exc
        if length <= 0:
            raise ValueError("Es wurde keine DPL übertragen.")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("Die DPL ist größer als 25 MB.")

        encoded_filename = self.headers.get("X-Filename", "")
        if not encoded_filename or len(encoded_filename) > 512:
            raise ValueError("Der Dateiname fehlt oder ist zu lang.")
        filename = unquote(encoded_filename, encoding="utf-8", errors="strict")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        allowed_types = {
            "application/octet-stream",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        if content_type not in allowed_types:
            raise ValueError("Der Dateityp wird für diesen Upload nicht unterstützt.")
        return filename, self.rfile.read(length)

    def _discard_request_body(self) -> None:
        try:
            remaining = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if remaining <= 0 or remaining > MAX_UPLOAD_BYTES:
            return
        while remaining:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)

    def _static(self, path: str) -> None:
        filename = "index.html" if path in {"", "/"} else path.lstrip("/")
        if filename not in {"index.html", "app.js", "styles.css", "assets/agw-logo.png"}:
            self._error(HTTPStatus.NOT_FOUND, "Datei nicht gefunden.")
            return
        file_path = WEB_ROOT / filename
        content = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self._headers(content_type, len(content))
        self.wfile.write(content)

    def _unexpected(self, context: str) -> None:
        LOGGER.exception(context)
        self._error(HTTPStatus.INTERNAL_SERVER_ERROR, context)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                health = database_health(self.database)
                health["version"] = __version__
                self._send_json(health)
                return
            if not parsed.path.startswith("/api/"):
                self._static(parsed.path)
                return

            if parsed.path == "/api/project":
                self._send_json(project_summary(self.database))
                return
            if parsed.path == "/api/modules":
                self._send_json({"items": [
                    module.public_dict() for module in list_modules(self.data_directory / "modules")
                ]})
                return
            if parsed.path == "/api/datapoints":
                query = parse_qs(parsed.query)
                self._send_json(
                    {
                        "items": list_datapoints(
                            self.database,
                            search=query.get("search", [""])[0],
                            value_type=query.get("type", [""])[0],
                            marker=query.get("marker", [""])[0],
                        )
                    }
                )
                return
            if parsed.path.startswith("/api/datapoints/"):
                datapoint_id = int(parsed.path.rsplit("/", 1)[1])
                self._send_json(get_datapoint(self.database, datapoint_id))
                return
            if parsed.path == "/api/audit":
                query = parse_qs(parsed.query)
                self._send_json({"items": recent_audit(self.database, int(query.get("limit", ["30"])[0]))})
                return
            if parsed.path in {"/api/exports/acron", "/api/exports/provider"}:
                export = acron_export if parsed.path.endswith("acron") else provider_export
                filename, content = export(self.database)
                self._headers(
                    "text/tab-separated-values; charset=utf-16le",
                    len(content),
                    Content_Disposition=f'attachment; filename="{filename}"',
                )
                self.wfile.write(content)
                return
            self._error(HTTPStatus.NOT_FOUND, "Route nicht gefunden.")
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "Datensatz nicht gefunden.")
        except (TypeError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            self._unexpected("Interner Fehler beim Laden der Daten.")

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/datapoints/"):
            self._error(HTTPStatus.NOT_FOUND, "Route nicht gefunden.")
            return
        try:
            datapoint_id = int(parsed.path.rsplit("/", 1)[1])
            payload = self._read_json()
            self._send_json(update_datapoint(self.database, datapoint_id, payload))
        except RevisionConflictError as exc:
            current = get_datapoint(self.database, int(parsed.path.rsplit("/", 1)[1]))
            self._error(HTTPStatus.CONFLICT, str(exc), current=current)
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "Datensatz nicht gefunden oder durch einen neuen DPL-Import ersetzt.")
        except (TypeError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            self._unexpected("Interner Fehler beim Speichern des Datensatzes.")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/datapoints/bulk-review":
                payload = self._read_json()
                self._send_json({
                    "items": bulk_review_datapoints(
                        self.database,
                        payload.get("items", []),
                        bool(payload.get("reviewed")),
                    )
                })
                return
            if parsed.path == "/api/import-dpl":
                filename, content = self._read_upload()
                module_code = self.headers.get("X-Anlage-Module", "").strip().casefold()
                if not module_code:
                    raise ValueError("Bitte vor dem Upload einen Anlagentyp auswählen.")
                result = import_uploaded_dpl(
                    self.database,
                    filename,
                    content,
                    self.acron_template,
                    self.provider_template,
                    data_directory=self.data_directory,
                    module_code=module_code,
                )
                self._send_json(result, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/import-pcs7":
                filename, content = self._read_upload()
                result = import_pcs7_export(
                    self.database,
                    filename,
                    content,
                    data_directory=self.data_directory,
                )
                result["summary"] = project_summary(self.database)
                self._send_json(result, HTTPStatus.CREATED)
                return
            self._error(HTTPStatus.NOT_FOUND, "Route nicht gefunden.")
        except RevisionConflictError as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "Mindestens ein Datensatz wurde nicht gefunden.")
        except (UnicodeDecodeError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            self._unexpected("Interner Fehler bei der Verarbeitung.")


def serve(
    database: DatabaseTarget,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    data_directory: Path | None = None,
    runtime_paths: RuntimePaths = BUNDLED_PATHS,
) -> None:
    runtime_paths.require_templates()
    active_data_directory = data_directory if data_directory is not None else resolve_data_directory()
    active_data_directory = active_data_directory.resolve()
    active_data_directory.mkdir(parents=True, exist_ok=True)
    handler = type(
        "ConfiguredDPLRequestHandler",
        (DPLRequestHandler,),
        {
            "database": database,
            "data_directory": active_data_directory,
            "acron_template": runtime_paths.acron_template,
            "provider_template": runtime_paths.provider_template,
            "request_timeout": request_timeout_seconds(),
        },
    )
    server = DPLHTTPServer((host, port), handler)
    LOGGER.info("%s %s läuft auf http://%s:%s", APP_NAME, __version__, host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
