"""Versioned opt-in shared-pool APIs; v1 API adapters remain unchanged."""
from __future__ import annotations

import hmac
from typing import Annotated, Literal

from fastapi import Depends, Query, Request
from fastapi.responses import Response
from pydantic import Field, StrictBool, StrictStr

from .api import _APIError, _TOKEN, _bounded_body, _json_request
from .domain import SourceScope
from .node_api import (AttentionRequest, ClaimIdentity, ClaimRequest,
                       HeartbeatRequest, UploadRequest)
from .profiles import MAX_PROFILE_OUTPUT_BYTES
from .schemas import CancelCompletedRequest, Key, StrictRequest, TerminalFailureRequest
from .shared_pool import SharedNodeIdentity
from .shared_pool_console import CONSOLE_HTML

_KEY = Annotated[StrictStr, Field(min_length=1, max_length=128,
                                  pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]


class PoolCreate(StrictRequest):
    name: Annotated[StrictStr, Field(min_length=1, max_length=80)]


class InviteCreate(StrictRequest):
    kind: Literal["workspace", "node"]
    ttl_seconds: Annotated[int, Field(strict=True, ge=60, le=86400)] = 3600
    profiles: list[Literal["fake-image", "codex-image", "midjourney-manual"]] | None = None


class SourceCredentialCreate(StrictRequest):
    source_id: Key
    workspace_id: Key


class InviteCodeRequest(StrictRequest):
    invite_code: Annotated[StrictStr, Field(min_length=16, max_length=256)]


class WorkspaceInviteAccept(InviteCodeRequest):
    accept_shared_data: StrictBool


class NodeEnrollRequest(InviteCodeRequest):
    node_id: _KEY
    executor_instance: _KEY
    profiles: Annotated[list[Literal["fake-image", "codex-image", "midjourney-manual"]], Field(min_length=1, max_length=3)]
    accept_shared_data: StrictBool


class AssignmentUploadRequest(ClaimIdentity):
    payload: dict


class NodeStateRequest(StrictRequest):
    pass


def _bearer(request: Request):
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        raise _APIError("unauthorized", 401)
    parts = values[0].split(" ")
    if (len(parts) != 2 or parts[0].lower() != "bearer"
            or len(parts[1]) > 4096 or not _TOKEN.fullmatch(parts[1])):
        raise _APIError("unauthorized", 401)
    return parts[1]


def install_shared_pool_routes(app, store, control_token, call_store, *, max_json_bytes, max_upload_bytes):
    control_prefix = "/bridge/control/v2"
    source_prefix = "/bridge/source/v2"
    node_prefix = "/bridge/node/v2"

    async def operator(request: Request):
        supplied = _bearer(request)
        if not hmac.compare_digest(supplied, control_token):
            raise _APIError("unauthorized", 401)
        return True

    async def source(request: Request):
        supplied = _bearer(request)
        scope = await call_store(store.shared_source_scope, supplied)
        if scope is None:
            raise _APIError("unauthorized", 401)
        return scope

    async def node(request: Request):
        supplied = _bearer(request)
        identity = await call_store(store.shared_node_from_token, supplied)
        if identity is None:
            raise _APIError("unauthorized", 401)
        return identity

    @app.get("/bridge/console")
    async def console():
        return Response(CONSOLE_HTML, media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            "Referrer-Policy": "no-referrer",
        })

    @app.post(control_prefix + "/pools")
    async def create_pool(request: Request, _auth=Depends(operator)):
        data = await _json_request(request, PoolCreate, max_json_bytes)
        return await call_store(store.create_shared_pool, data["name"])

    @app.get(control_prefix + "/pools")
    async def list_pools(_auth=Depends(operator)):
        return {"pools": await call_store(store.list_shared_pools)}

    @app.post(control_prefix + "/pools/{pool_id}/invites")
    async def create_invite(pool_id: str, request: Request, _auth=Depends(operator)):
        data = await _json_request(request, InviteCreate, max_json_bytes)
        return await call_store(store.create_shared_invite, pool_id, data["kind"],
                                ttl_seconds=data.get("ttl_seconds", 3600),
                                profiles=data.get("profiles"))

    @app.post(control_prefix + "/source-credentials")
    async def create_source_credential(request: Request, _auth=Depends(operator)):
        data = await _json_request(request, SourceCredentialCreate, max_json_bytes)
        return await call_store(store.create_shared_source_credential,
                                data["source_id"], data["workspace_id"])

    @app.post(control_prefix + "/source-credentials/{source_id}/{workspace_id}/revoke")
    async def revoke_source_credentials(source_id: str, workspace_id: str, _auth=Depends(operator)):
        return await call_store(store.revoke_shared_source_credentials, source_id, workspace_id)

    @app.get(control_prefix + "/summary")
    async def summary(_auth=Depends(operator)):
        return await call_store(store.summary_shared_pool)

    @app.get(control_prefix + "/nodes")
    async def nodes(_auth=Depends(operator)):
        return {"nodes": await call_store(store.list_shared_nodes)}

    @app.post(control_prefix + "/pools/{pool_id}/nodes/{node_id}/{executor}/drain")
    async def drain_node(pool_id: str, node_id: str, executor: str, _auth=Depends(operator)):
        return await call_store(store.set_shared_node_state, pool_id, node_id, executor, "draining")

    @app.post(control_prefix + "/pools/{pool_id}/nodes/{node_id}/{executor}/revoke")
    async def revoke_node(pool_id: str, node_id: str, executor: str, _auth=Depends(operator)):
        return await call_store(store.set_shared_node_state, pool_id, node_id, executor, "revoked")

    @app.post(control_prefix + "/pools/{pool_id}/nodes/{node_id}/{executor}/reactivate")
    async def reactivate_node(pool_id: str, node_id: str, executor: str, _auth=Depends(operator)):
        return await call_store(store.set_shared_node_state, pool_id, node_id, executor, "active")

    @app.get(control_prefix + "/tasks")
    async def operator_history(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        status: Annotated[str | None, Query(max_length=32)] = None,
        node_id: Annotated[str | None, Query(max_length=128)] = None,
        workspace_id: Annotated[str | None, Query(max_length=128)] = None,
        _auth=Depends(operator),
    ):
        return await call_store(store.shared_task_history, limit=limit, cursor=cursor,
                                status=status, node_id=node_id, workspace_id=workspace_id)

    @app.get(control_prefix + "/tasks/{task_id}")
    async def operator_task_detail(task_id: str, _auth=Depends(operator)):
        return await call_store(store.shared_task_detail, task_id)

    @app.post(source_prefix + "/pool-invites/inspect")
    async def inspect_invite(request: Request, scope: SourceScope = Depends(source)):
        data = await _json_request(request, InviteCodeRequest, max_json_bytes)
        return await call_store(store.inspect_shared_invite, scope, data["invite_code"])

    @app.post(source_prefix + "/pool-invites/accept")
    async def accept_invite(request: Request, scope: SourceScope = Depends(source)):
        data = await _json_request(request, WorkspaceInviteAccept, max_json_bytes)
        if not data["accept_shared_data"]:
            raise _APIError("sharing_consent_required", 403)
        return await call_store(store.accept_shared_invite, scope, data["invite_code"])

    @app.get(source_prefix + "/pools")
    async def source_pools(scope: SourceScope = Depends(source)):
        return {"pools": await call_store(store.shared_workspace_pools, scope)}

    @app.post(source_prefix + "/pools/{pool_id}/leave")
    async def leave_pool(pool_id: str, scope: SourceScope = Depends(source)):
        return await call_store(store.leave_shared_pool, scope, pool_id)

    @app.get(source_prefix + "/tasks")
    async def source_history(
        scope: SourceScope = Depends(source),
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        status: Annotated[str | None, Query(max_length=32)] = None,
    ):
        return await call_store(store.shared_task_history, scope=scope, limit=limit,
                                cursor=cursor, status=status)

    @app.post(node_prefix + "/enroll")
    async def enroll_node(request: Request):
        data = await _json_request(request, NodeEnrollRequest, max_json_bytes)
        return await call_store(store.enroll_shared_node, data["invite_code"],
                                data["node_id"], data["executor_instance"], data["profiles"],
                                accept_shared_data=data["accept_shared_data"])

    @app.post(node_prefix + "/heartbeat")
    async def heartbeat(request: Request, identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, HeartbeatRequest, max_json_bytes)
        reports = data.get("profile_health") or []
        return await call_store(store.report_shared_heartbeat, identity, data["state"],
                                data["profiles"], reports if data.get("profile_health") is not None else None)

    @app.get(node_prefix + "/tasks/next")
    async def next_task(identity: SharedNodeIdentity = Depends(node)):
        return await call_store(store.next_shared_assignment, identity)

    @app.get(node_prefix + "/assignments/by-task/{task_id}")
    async def assigned_task_by_id(task_id: str, identity: SharedNodeIdentity = Depends(node)):
        return await call_store(store.get_assigned_task_for_task, identity, task_id)

    @app.get(node_prefix + "/assignments/{assignment_id}/tasks/{task_id}")
    async def assigned_task(assignment_id: str, task_id: str,
                            identity: SharedNodeIdentity = Depends(node)):
        return await call_store(store.get_assigned_task, identity, assignment_id, task_id)

    @app.post(node_prefix + "/assignments/{assignment_id}/claim")
    async def claim(assignment_id: str, request: Request,
                    identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, ClaimRequest, max_json_bytes)
        return await call_store(store.claim_shared_assignment, identity, assignment_id,
                                mode=data.get("mode", "execute"), lease_seconds=3600)

    @app.post(node_prefix + "/assignments/{assignment_id}/start")
    async def start(assignment_id: str, request: Request,
                    identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, ClaimIdentity, max_json_bytes)
        return await call_store(store.start_shared_execution, identity, assignment_id, data["claim_id"])

    @app.post(node_prefix + "/assignments/{assignment_id}/manual-ready")
    async def manual_ready(assignment_id: str, request: Request,
                          identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, ClaimIdentity, max_json_bytes)
        return await call_store(store.manual_ready_shared, identity, assignment_id, data["claim_id"])

    @app.post(node_prefix + "/assignments/{assignment_id}/unknown")
    async def unknown(assignment_id: str, request: Request,
                      identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, ClaimIdentity, max_json_bytes)
        return await call_store(store.mark_shared_execution_unknown, identity, assignment_id, data["claim_id"])

    @app.post(node_prefix + "/assignments/{assignment_id}/attention")
    async def attention(assignment_id: str, request: Request,
                        identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, AttentionRequest, max_json_bytes)
        return await call_store(store.report_shared_attention, identity, assignment_id,
                                data["attempt_id"], data["code"])

    @app.post(node_prefix + "/assignments/{assignment_id}/terminal-failure")
    async def terminal_failure(assignment_id: str, request: Request,
                               identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, TerminalFailureRequest, max_json_bytes)
        return await call_store(store.confirm_shared_terminal_failure, identity, assignment_id, data)

    @app.post(node_prefix + "/assignments/{assignment_id}/cancel-completed")
    async def cancel_completed(assignment_id: str, request: Request,
                               identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, CancelCompletedRequest, max_json_bytes)
        return await call_store(store.confirm_shared_cancelled_completion, identity, assignment_id, data)

    @app.get(node_prefix + "/assignments/{assignment_id}/tasks/{task_id}/inputs/{input_id}/content")
    async def input_content(assignment_id: str, task_id: str, input_id: str,
                           request: Request, identity: SharedNodeIdentity = Depends(node)):
        claims = request.headers.getlist("x-bridge-claim")
        if len(claims) != 1:
            raise _APIError("invalid_request", 400)
        content, mime = await call_store(store.assigned_input_content, identity,
                                         assignment_id, task_id, input_id, claims[0])
        return Response(content, media_type=mime)

    @app.post(node_prefix + "/assignments/{assignment_id}/tasks/{task_id}/uploads")
    async def create_upload(assignment_id: str, task_id: str, request: Request,
                            identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, UploadRequest, max_json_bytes)
        return await call_store(store.create_assigned_upload, identity, assignment_id,
                                task_id, data["claim_id"], data["payload"])

    @app.put(node_prefix + "/assignments/{assignment_id}/uploads/{upload_id}/content")
    async def upload_content(assignment_id: str, upload_id: str, request: Request,
                             identity: SharedNodeIdentity = Depends(node)):
        claims = request.headers.getlist("x-bridge-claim")
        if len(claims) != 1:
            raise _APIError("invalid_request", 400)
        reserved = await call_store(store.authorize_assigned_upload, identity,
                                    assignment_id, upload_id, claims[0])
        if request.headers.getlist("content-type") != [reserved["mime_type"]]:
            raise _APIError("unsupported_media_type", 415)
        if reserved["size"] > max_upload_bytes or reserved["size"] > MAX_PROFILE_OUTPUT_BYTES:
            raise _APIError("payload_too_large", 413)
        content = await _bounded_body(request, reserved["size"])
        return await call_store(store.put_assigned_upload_content, identity,
                                assignment_id, upload_id, claims[0], content)

    @app.post(node_prefix + "/assignments/{assignment_id}/uploads/{upload_id}/commit")
    async def commit_upload(assignment_id: str, upload_id: str, request: Request,
                            identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, ClaimIdentity, max_json_bytes)
        return await call_store(store.commit_assigned_upload, identity, assignment_id,
                                upload_id, data["claim_id"])
