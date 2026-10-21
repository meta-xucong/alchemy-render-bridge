"""Source HTTP adapter with explicitly optional development node/dispatch routes.

The caller must inject a BridgeStore and an explicit bearer-to-SourceScope map.
There is deliberately no module-global app, server startup, credential creation,
default node API, scheduler, or execution trigger. An HTTP read or replay cannot start
generation. The fixture harness owns any separately injected engine.

Authentication below is an isolated-test configuration seam, not production
credential management, TLS termination, revocation, or an Internet deployment.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping
from types import MappingProxyType
from typing import TypeVar

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException

from .domain import MAX_INPUT_BYTES, BridgeStore, DomainError, SourceScope
from .schemas import InputCreate, ReceiptAck, TaskCreate, WithdrawalRequest


_RequestModel = TypeVar("_RequestModel", bound=BaseModel)
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._~+/-]+=*$")
_API_PREFIX = "/bridge/v1"


class _APIError(Exception):
    def __init__(self, code: str, status: int):
        self.code = code
        self.status = status


def _error_response(code: str, status: int) -> JSONResponse:
    """Never reflect input values, exception messages, credentials, or paths."""
    if not _ERROR_CODE.fullmatch(code):
        code = "request_failed"
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if status in (429, 503):
        headers["Retry-After"] = "1"
    return JSONResponse(
        {"error": {"code": code, "message": "Request could not be completed."}},
        status_code=status,
        headers=headers,
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON number.")


def _declared_length(request: Request) -> int | None:
    values = request.headers.getlist("content-length")
    if not values:
        return None
    # Ambiguous framing should be rejected even in the in-process test harness.
    if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
        raise _APIError("invalid_content_length", 400)
    try:
        return int(values[0])
    except ValueError:
        raise _APIError("invalid_content_length", 400) from None


async def _bounded_body(request: Request, limit: int) -> bytes:
    length = _declared_length(request)
    if length is not None and length > limit:
        raise _APIError("payload_too_large", 413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise _APIError("payload_too_large", 413)
        body.extend(chunk)
    if length is not None and length != len(body):
        raise _APIError("invalid_content_length", 400)
    return bytes(body)


async def _json_request(
    request: Request, model: type[_RequestModel], limit: int
) -> dict[str, object]:
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1:
        raise _APIError("unsupported_media_type", 415)
    if content_types[0].split(";", 1)[0].strip().lower() != "application/json":
        raise _APIError("unsupported_media_type", 415)
    raw = await _bounded_body(request, limit)
    try:
        # Decode explicitly so JSON encodings other than UTF-8 are not guessed.
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        parsed = model.model_validate(payload)
    except (UnicodeError, ValueError, RecursionError, ValidationError):
        raise _APIError("invalid_request", 422) from None
    # Preserve omission: the domain owns parameter defaults and canonical digest.
    return parsed.model_dump(exclude_unset=True)


def create_app(
    *,
    store: BridgeStore,
    source_tokens: Mapping[str, SourceScope],
    max_json_bytes: int = 65_536,
    max_upload_bytes: int = MAX_INPUT_BYTES,
    node_tokens=None,
    dispatch_tokens=None,
) -> FastAPI:
    """Create an isolated fixture API with no implicit authority or executor."""
    if not isinstance(source_tokens, Mapping) or not source_tokens:
        raise ValueError("An explicit source token map is required.")
    for token, scope in source_tokens.items():
        if (
            not isinstance(token, str)
            or len(token) > 4096
            or not _TOKEN.fullmatch(token)
            or not isinstance(scope, SourceScope)
        ):
            raise ValueError("Invalid source authentication configuration.")
    for limit, maximum in (
        (max_json_bytes, 1_048_576),
        (max_upload_bytes, MAX_INPUT_BYTES),
    ):
        if type(limit) is not int or limit < 1 or limit > maximum:
            raise ValueError("Invalid request size limit.")
    # Snapshot configuration: a caller cannot accidentally mutate auth mid-read.
    tokens = MappingProxyType(dict(source_tokens))
    # A single admitted store operation runs off the event loop. The nonblocking
    # gate rejects extra work before the thread pool, rather than accumulating an
    # unbounded queue. This is bounded fixture admission, not production capacity.
    store_gate = threading.BoundedSemaphore(1)
    store_thread_limiter = anyio.CapacityLimiter(1)

    async def call_store(method, *args):
        if not store_gate.acquire(blocking=False):
            raise _APIError("store_busy", 503)
        state_lock = threading.Lock()
        state = {"started": False, "canceled": False}

        def invoke():
            with state_lock:
                if state["canceled"]:
                    return None
                state["started"] = True
            try:
                return method(*args)
            finally:
                # Even an externally canceled HTTP task cannot free admission
                # while the synchronous transaction is still running.
                store_gate.release()

        try:
            return await anyio.to_thread.run_sync(
                invoke, abandon_on_cancel=False, limiter=store_thread_limiter
            )
        except BaseException:
            with state_lock:
                if not state["started"]:
                    # A cancellation before worker startup must neither leak the
                    # gate nor permit delayed execution after admission is free.
                    state["canceled"] = True
                    store_gate.release()
            raise

    app = FastAPI(
        title="Render Bridge experimental source API",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def source_scope(request: Request) -> SourceScope:
        values = request.headers.getlist("authorization")
        if len(values) != 1:
            raise _APIError("unauthorized", 401)
        parts = values[0].split(" ")
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise _APIError("unauthorized", 401)
        token = parts[1]
        if not _TOKEN.fullmatch(token) or len(token) > 4096:
            raise _APIError("unauthorized", 401)
        scope = tokens.get(token)
        if scope is None:
            raise _APIError("unauthorized", 401)
        return scope

    @app.exception_handler(_APIError)
    async def api_error_handler(_request: Request, exc: _APIError):
        return _error_response(exc.code, exc.status)

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError):
        return _error_response(exc.code, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, _exc: RequestValidationError):
        return _error_response("invalid_request", 422)

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException):
        code = "not_found" if exc.status_code == 404 else "request_failed"
        return _error_response(code, exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, _exc: Exception):
        return _error_response("internal_error", 500)

    @app.get(f"{_API_PREFIX}/capabilities")
    async def capabilities(scope: SourceScope = Depends(source_scope)):
        return await call_store(store.capabilities, scope)

    @app.post(f"{_API_PREFIX}/inputs")
    async def create_input(request: Request, scope: SourceScope = Depends(source_scope)):
        return await call_store(
            store.create_input,
            scope, await _json_request(request, InputCreate, max_json_bytes)
        )

    @app.put(f"{_API_PREFIX}/inputs/{{input_id}}/content")
    async def put_input_content(
        input_id: str, request: Request, scope: SourceScope = Depends(source_scope)
    ):
        # Check scope before consuming bytes or revealing the object's MIME/size.
        reserved = await call_store(store.get_input, scope, input_id)
        content_types = request.headers.getlist("content-type")
        if len(content_types) != 1 or content_types[0] != reserved["mime_type"]:
            raise _APIError("unsupported_media_type", 415)
        content = await _bounded_body(
            request, min(max_upload_bytes, reserved["size"])
        )
        return await call_store(store.put_input_content, scope, input_id, content)

    @app.get(f"{_API_PREFIX}/inputs/{{input_id}}")
    async def get_input(input_id: str, scope: SourceScope = Depends(source_scope)):
        return await call_store(store.get_input, scope, input_id)

    @app.post(f"{_API_PREFIX}/tasks")
    async def create_task(request: Request, scope: SourceScope = Depends(source_scope)):
        return await call_store(
            store.create_task,
            scope, await _json_request(request, TaskCreate, max_json_bytes)
        )

    # Keep this route before /tasks/{task_id}.
    @app.get(f"{_API_PREFIX}/tasks/by-origin/{{origin_key}}")
    async def get_task_by_origin(
        origin_key: str, scope: SourceScope = Depends(source_scope)
    ):
        return await call_store(store.get_task_by_origin, scope, origin_key)

    @app.get(f"{_API_PREFIX}/tasks/{{task_id}}")
    async def get_task(task_id: str, scope: SourceScope = Depends(source_scope)):
        return await call_store(store.get_task, scope, task_id)

    @app.post(f"{_API_PREFIX}/tasks/{{task_id}}/withdraw-before-start")
    async def withdraw(task_id: str, request: Request, scope: SourceScope = Depends(source_scope)):
        data = await _json_request(request, WithdrawalRequest, 4096)
        return await call_store(store.withdraw_before_start, scope, task_id, data)

    @app.get(f"{_API_PREFIX}/tasks/{{task_id}}/withdraw-before-start/{{withdrawal_key}}")
    async def withdrawal(task_id: str, withdrawal_key: str, scope: SourceScope = Depends(source_scope)):
        return await call_store(store.get_withdrawal, scope, task_id, withdrawal_key)

    @app.post(f"{_API_PREFIX}/tasks/{{task_id}}/cancel")
    async def cancel_task(task_id: str, scope: SourceScope = Depends(source_scope)):
        return await call_store(store.cancel_task, scope, task_id)

    @app.get(f"{_API_PREFIX}/receipts/{{receipt_id}}")
    async def get_receipt(receipt_id: str, scope: SourceScope = Depends(source_scope)):
        return await call_store(store.get_receipt, scope, receipt_id)

    @app.get(f"{_API_PREFIX}/artifacts/{{artifact_id}}/content")
    async def get_artifact_content(
        artifact_id: str, scope: SourceScope = Depends(source_scope)
    ):
        content, mime_type = await call_store(store.get_artifact_content, scope, artifact_id)

        def chunks():
            # The domain enforces the experimental 1 MiB artifact limit.
            for offset in range(0, len(content), 65_536):
                yield content[offset : offset + 65_536]

        return StreamingResponse(
            chunks(),
            media_type=mime_type,
            headers={
                "Content-Length": str(len(content)),
                "Content-Disposition": "attachment",
            },
        )

    @app.post(f"{_API_PREFIX}/receipts/{{receipt_id}}/ack")
    async def ack_receipt(
        receipt_id: str, request: Request, scope: SourceScope = Depends(source_scope)
    ):
        return await call_store(
            store.ack_receipt,
            scope, receipt_id, await _json_request(request, ReceiptAck, max_json_bytes)
        )

    if node_tokens is not None or dispatch_tokens is not None:
        # Keep credentials disjoint: source callers cannot acquire node authority.
        node_keys, dispatch_keys = set(node_tokens or {}), set(dispatch_tokens or {})
        if set(tokens) & (node_keys | dispatch_keys) or node_keys & dispatch_keys:
            raise ValueError("Source, node and dispatcher credentials must differ")
        from .node_api import install_node_routes, install_dispatch_routes
        if node_tokens is not None:
            install_node_routes(app, store, node_tokens, call_store)
        if dispatch_tokens is not None:
            install_dispatch_routes(app, store, dispatch_tokens, call_store)
    return app
