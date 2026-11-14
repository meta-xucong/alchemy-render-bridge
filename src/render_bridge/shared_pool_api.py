"""Versioned opt-in shared-pool APIs; v1 API adapters remain unchanged."""
from __future__ import annotations

import hmac
import os
import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import Field, StrictBool, StrictStr

from .api import _APIError, _TOKEN, _bounded_body, _json_request
from .domain import SourceScope
from .node_api import (AttentionRequest, ClaimIdentity, ClaimRequest,
                       HeartbeatRequest, UploadRequest)
from .profiles import MAX_PROFILE_OUTPUT_BYTES
from .schemas import CancelCompletedRequest, Key, StrictRequest, TerminalFailureRequest
from .shared_pool import SharedNodeIdentity
from .shared_pool_console import CONSOLE_HTML
from .shared_pool_dashboard import STATUS_DASHBOARD_HTML

_KEY = Annotated[StrictStr, Field(min_length=1, max_length=128,
                                  pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
_COMMIT_REVISION = re.compile(r"^[0-9a-f]{40}$")


def _release_revision() -> str:
    value = os.environ.get("RENDER_BRIDGE_BUILD_REVISION", "")
    return value if _COMMIT_REVISION.fullmatch(value) else "unknown"


class PoolCreate(StrictRequest):
    name: Annotated[StrictStr, Field(min_length=1, max_length=80)]


class InviteCreate(StrictRequest):
    kind: Literal["workspace", "node"]
    ttl_seconds: Annotated[int, Field(strict=True, ge=60, le=86400)] = 3600
    profiles: list[Literal["fake-image", "codex-image", "midjourney-manual"]] | None = None
    provider_ref: Annotated[StrictStr, Field(min_length=1, max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")] | None = None


class SourceCredentialCreate(StrictRequest):
    source_id: Key
    workspace_id: Key


class ProviderRefUpdate(StrictRequest):
    provider_ref: Annotated[StrictStr, Field(min_length=1, max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")] | None


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


class RewardClaimConfirmRequest(StrictRequest):
    challenge: Annotated[StrictStr, Field(min_length=32, max_length=256,
        pattern=r"^[A-Za-z0-9_-]{32,256}$")]


def _bearer(request: Request):
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        raise _APIError("unauthorized", 401)
    parts = values[0].split(" ")
    if (len(parts) != 2 or parts[0].lower() != "bearer"
            or len(parts[1]) > 4096 or not _TOKEN.fullmatch(parts[1])):
        raise _APIError("unauthorized", 401)
    return parts[1]


def install_shared_pool_routes(app, store, control_token, call_store, *, max_json_bytes,
                               max_upload_bytes, reward_read_token=None):
    control_prefix = "/bridge/control/v2"
    source_prefix = "/bridge/source/v2"
    node_prefix = "/bridge/node/v2"

    async def operator(request: Request):
        supplied = _bearer(request)
        if not hmac.compare_digest(supplied, control_token):
            raise _APIError("unauthorized", 401)
        return True

    async def reward_reader(request: Request):
        supplied = _bearer(request)
        if (not isinstance(reward_read_token, str)
                or not hmac.compare_digest(supplied, reward_read_token)):
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

    @app.get("/bridge/status/v1")
    async def public_fleet_status():
        """Return a deliberately small, unauthenticated fleet-status projection."""
        source_nodes = await call_store(store.list_shared_nodes)
        public_nodes = []
        allowed_profiles = {"fake-image", "codex-image", "midjourney-manual"}
        for item in source_nodes if source_nodes is not None else ():
            if not isinstance(item, dict) or item.get("state") not in {"active", "draining"}:
                continue
            state = item["state"]
            liveness = item.get("liveness")
            has_assignment = isinstance(item.get("current_assignment"), dict)
            if liveness == "offline":
                availability = "offline"
            elif liveness == "needs_attention":
                availability = "attention"
            elif liveness == "busy" or has_assignment:
                availability = "busy"
            elif state == "draining":
                availability = "draining"
            elif state == "active" and liveness == "online":
                availability = "available"
            else:
                availability = "unknown"

            seen_at = item.get("last_seen_at")
            heartbeat_age = None
            if isinstance(seen_at, str):
                try:
                    observed = datetime.fromisoformat(seen_at.replace("Z", "+00:00"))
                    if observed.tzinfo is None:
                        observed = observed.replace(tzinfo=timezone.utc)
                    heartbeat_age = max(0, int((datetime.now(timezone.utc) - observed).total_seconds()))
                except ValueError:
                    pass
            raw_profiles = item.get("profiles")
            profiles = sorted({profile for profile in raw_profiles
                               if isinstance(profile, str) and profile in allowed_profiles}) \
                if isinstance(raw_profiles, list) else []
            public_nodes.append({
                "label": f"Node {len(public_nodes) + 1:02d}",
                "state": state,
                "availability": availability,
                "heartbeat_age_seconds": heartbeat_age,
                "profiles": profiles,
            })

        statuses = [item["availability"] for item in public_nodes]
        summary = {
            "nodes_total": len(public_nodes),
            "nodes_online": sum(status not in {"offline", "unknown"} for status in statuses),
            "nodes_available": statuses.count("available"),
            "nodes_busy": statuses.count("busy"),
            "nodes_attention": statuses.count("attention"),
            "nodes_draining": statuses.count("draining"),
            "nodes_offline": statuses.count("offline"),
            "nodes_unknown": statuses.count("unknown"),
        }
        return JSONResponse({"release_revision": _release_revision(),
                             "summary": summary, "nodes": public_nodes}, headers={
            "Cache-Control": "no-store",
        })

    @app.get("/bridge/console")
    async def console():
        return Response(STATUS_DASHBOARD_HTML, media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        })

    @app.get("/bridge/operator")
    async def operator_console():
        return Response(CONSOLE_HTML, media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            "Referrer-Policy": "no-referrer",
        })

    @app.get("/bridge/rewards/v1/nodes")
    async def reward_node_metrics(limit: Annotated[int, Query(ge=1, le=1000)] = 500,
                                  _auth=Depends(reward_reader)):
        return JSONResponse(await call_store(store.shared_node_reward_metrics, limit=limit), headers={
            "Cache-Control": "no-store", "Vary": "Authorization",
        })

    @app.get("/bridge/rewards/v1/claim-approval")
    async def reward_claim_approval(request: Request, _auth=Depends(reward_reader)):
        values = request.headers.getlist("x-reward-claim-digest")
        if len(values) != 1 or not re.fullmatch(r"[0-9a-f]{64}", values[0]):
            return JSONResponse({"available": False}, status_code=404, headers={
                "Cache-Control": "no-store", "Vary": "Authorization, X-Reward-Claim-Digest",
            })
        approval = await call_store(store.shared_reward_claim_approval, values[0])
        if approval is None:
            return JSONResponse({"available": False}, status_code=404, headers={
                "Cache-Control": "no-store", "Vary": "Authorization, X-Reward-Claim-Digest",
            })
        return JSONResponse(approval, headers={
            "Cache-Control": "no-store", "Vary": "Authorization, X-Reward-Claim-Digest",
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
                                profiles=data.get("profiles"),
                                provider_ref=data.get("provider_ref"))

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

    @app.put(control_prefix + "/pools/{pool_id}/nodes/{node_id}/{executor}/provider-ref")
    async def update_node_provider_ref(pool_id: str, node_id: str, executor: str,
                                       request: Request, _auth=Depends(operator)):
        data = await _json_request(request, ProviderRefUpdate, max_json_bytes)
        return await call_store(store.set_shared_node_provider_ref, pool_id, node_id,
                                executor, data["provider_ref"])

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

    @app.post(node_prefix + "/reward-claims/confirm")
    async def confirm_reward_claim(request: Request,
                                   identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, RewardClaimConfirmRequest, max_json_bytes)
        result = await call_store(store.confirm_shared_reward_claim, identity, data["challenge"])
        return JSONResponse(result, headers={
            "Cache-Control": "no-store", "Vary": "Authorization",
        })

    @app.post(node_prefix + "/heartbeat")
    async def heartbeat(request: Request, identity: SharedNodeIdentity = Depends(node)):
        data = await _json_request(request, HeartbeatRequest, max_json_bytes)
        reports = data.get("profile_health") or []
        return await call_store(store.report_shared_heartbeat, identity, data["state"],
                                data["profiles"], reports if data.get("profile_health") is not None else None)

    @app.get(node_prefix + "/tasks/next")
    async def next_task(identity: SharedNodeIdentity = Depends(node)):
        return await call_store(store.next_shared_assignment, identity)

    @app.get(node_prefix + "/status")
    async def node_self_status(identity: SharedNodeIdentity = Depends(node)):
        return JSONResponse(await call_store(store.shared_node_self_status, identity), headers={
            "Cache-Control": "no-store", "Vary": "Authorization",
        })

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
