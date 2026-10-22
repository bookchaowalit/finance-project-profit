"""Error envelope, request-id middleware and sanitized exception handling."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability import report_exception

log = logging.getLogger("profit_api")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
MAX_BODY_BYTES = 64 * 1024

_STATUS_CODES = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "idempotency_conflict",
    413: "validation_error",
    422: "validation_error",
    503: "service_unavailable",
}


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


def unauthorized(message: str = "Authentication required") -> AppError:
    return AppError(401, "unauthorized", message)


def not_found(message: str = "Resource not found") -> AppError:
    return AppError(404, "not_found", message)


def error_body(code: str, message: str, request_id: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": request_id}}


def _rid(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _json(
    status: int, code: str, message: str, rid: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    h = {"X-Request-ID": rid, **(headers or {})}
    return JSONResponse(error_body(code, message, rid), status_code=status, headers=h)


class RequestContextMiddleware:
    """Assigns a request id and converts any escaped exception into a sanitized 500."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inbound = ""
        for k, v in scope.get("headers", []):
            if k == b"x-request-id":
                inbound = v.decode("latin-1")
        rid = inbound if _REQUEST_ID_RE.match(inbound) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", rid.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            # Only the exception class is logged: messages may embed URLs or payloads.
            log.error("unhandled_error request_id=%s type=%s", rid, type(exc).__name__)
            report_exception(exc, rid)
            if started:
                return
            body = json.dumps(error_body("internal_error", "Internal server error", rid)).encode()
            await send_wrapper(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})


class BodyLimitMiddleware:
    """Bound bodies before FastAPI buffers JSON, including chunked requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                length = int(raw_length)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                status = 400 if length < 0 else 413
                message = "Invalid content length" if length < 0 else "Request body too large"
                await _json(status, "validation_error", message, _rid(request))(
                    scope, receive, send
                )
                return
        consumed = 0

        async def bounded_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > MAX_BODY_BYTES:
                    raise StarletteHTTPException(status_code=413)
            return message

        await self.app(scope, bounded_receive, send)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return _json(exc.status, exc.code, exc.message, _rid(request), headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Only field locations are reported; submitted values are never echoed.
        fields = sorted({".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()})
        message = ("Invalid request: " + ", ".join(fields[:10])) if fields else "Invalid request"
        return _json(422, "validation_error", message[:300], _rid(request))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "http_error")
        msg = {
            404: "Resource not found",
            405: "Method not allowed",
            413: "Request body too large",
        }.get(exc.status_code, "Request failed")
        return _json(exc.status_code, code, msg, _rid(request))

    @app.exception_handler(OperationalError)
    @app.exception_handler(PoolTimeoutError)
    async def _db_unavailable(request: Request, exc: Exception) -> JSONResponse:
        log.error("database_unavailable request_id=%s type=%s", _rid(request), type(exc).__name__)
        return _json(503, "service_unavailable", "Service temporarily unavailable", _rid(request))

    @app.exception_handler(DBAPIError)
    async def _db_error(request: Request, exc: Exception) -> JSONResponse:
        log.error("database_error request_id=%s type=%s", _rid(request), type(exc).__name__)
        report_exception(exc, _rid(request))
        return _json(500, "internal_error", "Internal server error", _rid(request))
