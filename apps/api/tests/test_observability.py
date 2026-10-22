"""Sentry stays off without a DSN and never sends request data, users or amounts."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from starlette.testclient import TestClient

from app.errors import RequestContextMiddleware
from app.observability import init_sentry, report_exception, scrub_event


def _raw_event() -> dict[str, Any]:
    return {
        "event_id": "abc",
        "level": "error",
        "environment": "production",
        "request": {
            "url": "https://api.example.test/workspaces/w1/projects?token=secret",
            "headers": {"Authorization": "Bearer secret-token", "Cookie": "sid=1"},
            "data": {"amount_minor": 123456},
        },
        "user": {"id": "user-1", "email": "owner@example.test", "ip_address": "10.0.0.1"},
        "breadcrumbs": {"values": [{"message": "SELECT * FROM payments WHERE amount=123456"}]},
        "extra": {"payload": "client contract 50,000 THB"},
        "contexts": {"trace": {"trace_id": "t"}, "response": {"status_code": 500}},
        "server_name": "host-123",
        "tags": {"service": "project-profit-api", "request_id": "r1", "email": "x@example.test"},
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "module": "builtins",
                    "value": "amount 123456 for client Acme is invalid",
                    "mechanism": {"type": "generic", "handled": False, "data": {"x": 1}},
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "app/service.py",
                                "function": "record_payment",
                                "lineno": 42,
                                "in_app": True,
                                "vars": {"amount": 123456, "email": "owner@example.test"},
                                "context_line": "raise ValueError(f'amount {amount}')",
                                "pre_context": ["amount = body.amount"],
                            }
                        ]
                    },
                }
            ]
        },
    }


def test_scrubber_keeps_only_allowlisted_fields() -> None:
    out = scrub_event(_raw_event())

    for dropped in ("request", "user", "breadcrumbs", "extra", "contexts", "server_name"):
        assert dropped not in out
    assert out["level"] == "error"
    assert out["tags"] == {"service": "project-profit-api", "request_id": "r1"}
    value = out["exception"]["values"][0]
    assert value["type"] == "ValueError"
    assert "value" not in value  # the message can embed amounts or names
    assert value["mechanism"] == {"type": "generic", "handled": False}
    frame = value["stacktrace"]["frames"][0]
    assert frame == {
        "filename": "app/service.py",
        "function": "record_payment",
        "lineno": 42,
        "in_app": True,
    }


def test_scrubbed_event_contains_no_sensitive_strings() -> None:
    text = repr(scrub_event(_raw_event()))
    for secret in ("secret-token", "owner@example.test", "123456", "Acme", "10.0.0.1", "sid=1"):
        assert secret not in text


def test_init_is_off_without_dsn() -> None:
    assert init_sentry({}) is False
    assert init_sentry({"SENTRY_DSN": "   "}) is False


class _Capture(Transport):
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(dict(event))


@pytest.fixture
def sentry_capture() -> Iterator[_Capture]:
    transport = _Capture()
    sentry_sdk.init(
        dsn="https://public@o0.ingest.sentry.io/0",
        transport=transport,
        default_integrations=False,
        send_default_pii=False,
        include_local_variables=False,
        before_send=scrub_event,  # type: ignore[arg-type]
    )
    yield transport
    sentry_sdk.get_client().close()
    sentry_sdk.init(dsn=None)


def test_report_exception_is_a_noop_when_sentry_is_off() -> None:
    sentry_sdk.init(dsn=None)
    report_exception(RuntimeError("boom"), "req-12345678")  # must not raise


def test_unhandled_error_is_reported_scrubbed(sentry_capture: _Capture) -> None:
    async def explode(request: Request) -> None:
        raise RuntimeError("payment 987654 for owner@example.test failed")

    app = Starlette(routes=[Route("/boom", explode)])
    client = TestClient(RequestContextMiddleware(app), raise_server_exceptions=False)

    response = client.get("/boom?token=abc", headers={"x-request-id": "req-12345678"})

    assert response.status_code == 500
    assert len(sentry_capture.events) == 1
    event = sentry_capture.events[0]
    assert event["exception"]["values"][0]["type"] == "RuntimeError"
    assert event["tags"]["request_id"] == "req-12345678"
    text = repr(event)
    assert "987654" not in text
    assert "owner@example.test" not in text
    assert "token=abc" not in text
