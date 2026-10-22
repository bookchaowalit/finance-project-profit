"""Opt-in Sentry error reporting for the API.

Off unless ``SENTRY_DSN`` is set. Errors only: no tracing, no profiling, no breadcrumbs,
no local variables, no request bodies, headers, cookies or query strings. Every event
passes through :func:`scrub_event`, an allowlist: anything not named there is dropped,
so a new SDK field can never leak financial records or user identifiers.
"""

from __future__ import annotations

import os
from typing import Any

import sentry_sdk

# Top-level event keys that may leave the process. Everything else is removed.
_ALLOWED_EVENT_KEYS = frozenset(
    {
        "event_id",
        "timestamp",
        "level",
        "logger",
        "platform",
        "release",
        "environment",
        "exception",
        "tags",
        "transaction",
        "fingerprint",
        "sdk",
    }
)
_ALLOWED_FRAME_KEYS = frozenset(
    {"filename", "abs_path", "function", "module", "lineno", "colno", "in_app"}
)
# Tags we set ourselves or the SDK sets from configuration (never from request data).
_ALLOWED_TAGS = frozenset({"service", "request_id"})


def _scrub_frame(frame: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in frame.items() if k in _ALLOWED_FRAME_KEYS}


def _scrub_exception(exception: dict[str, Any]) -> dict[str, Any]:
    values = []
    for value in exception.get("values") or []:
        # The exception message (``value``) is dropped: it can embed SQL parameters,
        # URLs or amounts. The class name and stack are enough to find the bug.
        kept: dict[str, Any] = {k: value[k] for k in ("type", "module") if k in value}
        mechanism = value.get("mechanism")
        if isinstance(mechanism, dict):
            kept["mechanism"] = {k: mechanism[k] for k in ("type", "handled") if k in mechanism}
        frames = (value.get("stacktrace") or {}).get("frames") or []
        if frames:
            kept["stacktrace"] = {"frames": [_scrub_frame(f) for f in frames]}
        values.append(kept)
    return {"values": values}


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a copy of ``event`` that keeps only allowlisted, non-personal fields."""
    out = {k: v for k, v in event.items() if k in _ALLOWED_EVENT_KEYS}
    if isinstance(out.get("exception"), dict):
        out["exception"] = _scrub_exception(out["exception"])
    tags = out.get("tags")
    if isinstance(tags, dict):
        out["tags"] = {k: v for k, v in tags.items() if k in _ALLOWED_TAGS}
    elif "tags" in out:
        del out["tags"]
    return out


def init_sentry(env: dict[str, str] | None = None) -> bool:
    """Initialise Sentry when ``SENTRY_DSN`` is set; return whether it is on."""
    source = os.environ if env is None else env
    dsn = (source.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=source.get("SENTRY_ENVIRONMENT", "production"),
        release=source.get("SENTRY_RELEASE") or None,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        max_breadcrumbs=0,
        traces_sample_rate=0.0,
        before_send=scrub_event,  # type: ignore[arg-type]
        before_send_transaction=lambda _event, _hint: None,
    )
    sentry_sdk.set_tag("service", "project-profit-api")
    return True


def report_exception(exc: BaseException, request_id: str | None = None) -> None:
    """Send one error event; a no-op when Sentry is not initialised."""
    if not sentry_sdk.get_client().is_active():
        return
    with sentry_sdk.new_scope() as scope:
        if request_id:
            scope.set_tag("request_id", request_id)
        sentry_sdk.capture_exception(exc)
