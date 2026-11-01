"""Internal node and dispatcher APIs for one trusted development deployment.

Tokens bind identities in configuration; callers cannot select another workspace
or executor identity. These routes are never installed by source create_app alone.
"""
from dataclasses import dataclass
from typing import Annotated, Literal, Mapping

from fastapi import Depends, Request
from fastapi.responses import Response
from pydantic import Field, StrictStr, StrictInt

from .api import _APIError, _bounded_body, _json_request, _TOKEN
from .domain import BridgeStore, SourceScope
from .profiles import MAX_PROFILE_OUTPUT_BYTES
from .schemas import StrictRequest, Key, SHA256, TerminalFailureRequest


@dataclass(frozen=True)
class NodeScope:
    source_id: str
    workspace_id: str
    node_id: str
    executor_instance: str

    @property
    def source_scope(self):
        return SourceScope(self.source_id, self.workspace_id)


class ClaimRequest(StrictRequest):
    mode: Literal['execute', 'upload_only'] = 'execute'


class ClaimIdentity(StrictRequest):
    claim_id: Key


class ProfileHealth(StrictRequest):
    profile_id: Literal['fake-image', 'codex-image', 'midjourney-manual']
    profile_revision: Literal['1']
    report_revision: Annotated[StrictInt, Field(ge=1, le=2**63-1)]
    revalidation_generation: Annotated[StrictInt, Field(ge=0, le=2**63-1)] = 0
    health: Literal['available', 'unavailable', 'unknown']
    reason_code: Literal['ready', 'tool_missing', 'version_unsupported', 'auth_unavailable', 'quota_exhausted', 'resource_unavailable', 'probe_failed', 'unverified', 'stale']
    evidence_kind: Literal['local_probe', 'provider_error', 'operator_validation', 'operator_attested_preflight', 'account_status', 'none']


class HeartbeatRequest(StrictRequest):
    state: Literal['available', 'busy', 'attention']
    profiles: Annotated[list[Literal['fake-image', 'codex-image', 'midjourney-manual']], Field(max_length=3)]
    profile_health: Annotated[list[ProfileHealth], Field(max_length=3)] | None = None


class AttentionRequest(StrictRequest):
    attempt_id: Key
    code: Annotated[StrictStr, Field(min_length=1, max_length=64)]


class UploadMetadata(StrictRequest):
    attempt_id: Key
    slot_id: Literal['primary']
    original_sha256: SHA256
    size: Annotated[int, Field(strict=True, ge=1, le=MAX_PROFILE_OUTPUT_BYTES)]
    mime_type: Literal['image/png', 'image/jpeg']


class UploadRequest(ClaimIdentity):
    payload: UploadMetadata


class DispatchAck(StrictRequest):
    engine_run_id: Annotated[StrictStr, Field(min_length=1, max_length=256)]


def _credential_map(values, expected):
    if not values:
        raise ValueError('Explicit credentials are required')
    for token, identity in values.items():
        if not isinstance(token, str) or len(token) > 4096 or not _TOKEN.fullmatch(token) or not isinstance(identity, expected):
            raise ValueError('Invalid credential configuration')
    return dict(values)


def _bearer(request, mapping):
    values = request.headers.getlist('authorization')
    if len(values) != 1:
        raise _APIError('unauthorized', 401)
    parts = values[0].split(' ')
    if len(parts) != 2 or parts[0].lower() != 'bearer' or parts[1] not in mapping:
        raise _APIError('unauthorized', 401)
    return mapping[parts[1]]


def install_node_routes(app, store: BridgeStore, node_tokens: Mapping[str, NodeScope], call_store):
    tokens = _credential_map(node_tokens, NodeScope)
    for identity in tokens.values():
        store.provision_node(identity.source_scope, identity.node_id, identity.executor_instance)
    prefix = '/bridge/node/v1'

    async def node(request: Request):
        identity = _bearer(request, tokens)
        await call_store(store.authorize_active_node, identity.source_scope, identity.node_id, identity.executor_instance)
        return identity

    async def claim_body(request):
        return await _json_request(request, ClaimIdentity, 16384)

    async def check(identity, task_id, claim_id):
        return await call_store(store.authorize_node_claim, identity.source_scope, task_id,
                                claim_id, identity.node_id, identity.executor_instance)

    @app.post(prefix + '/heartbeat')
    async def heartbeat(request: Request, identity: NodeScope = Depends(node)):
        data = await _json_request(request, HeartbeatRequest, 4096)
        return await call_store(store.report_heartbeat, identity.source_scope, identity.node_id,
                                identity.executor_instance, data['state'], data['profiles'], data.get('profile_health'))

    @app.post(prefix + '/tasks/{task_id}/attention')
    async def attention(task_id: str, request: Request, identity: NodeScope = Depends(node)):
        data = await _json_request(request, AttentionRequest, 4096)
        return await call_store(store.report_attention, identity.source_scope, task_id, data['attempt_id'],
                                identity.node_id, identity.executor_instance, data['code'])

    @app.get(prefix + '/tasks/{task_id}')
    async def task(task_id: str, identity: NodeScope = Depends(node)):
        return await call_store(store.get_task, identity.source_scope, task_id)

    @app.post(prefix + '/tasks/{task_id}/claim')
    async def claim(task_id: str, request: Request, identity: NodeScope = Depends(node)):
        data = await _json_request(request, ClaimRequest, 16384)
        def acquire():
            return store.claim_task(identity.source_scope, task_id, node_id=identity.node_id,
                                    executor_instance=identity.executor_instance,
                                    mode=data.get('mode', 'execute'), lease_seconds=3600)
        return await call_store(acquire)

    @app.post(prefix + '/tasks/{task_id}/start')
    async def start(task_id: str, request: Request, identity: NodeScope = Depends(node)):
        data = await claim_body(request)
        await check(identity, task_id, data['claim_id'])
        return await call_store(store.start_execution, identity.source_scope, task_id, data['claim_id'])

    @app.post(prefix + '/tasks/{task_id}/manual-ready')
    async def manual_ready(task_id: str, request: Request, identity: NodeScope = Depends(node)):
        data = await claim_body(request)
        await check(identity, task_id, data['claim_id'])
        return await call_store(store.begin_manual, identity.source_scope, task_id, data['claim_id'])

    @app.post(prefix + '/tasks/{task_id}/terminal-failure')
    async def terminal_failure(task_id: str, request: Request, identity: NodeScope = Depends(node)):
        data = await _json_request(request, TerminalFailureRequest, 4096)
        return await call_store(store.confirm_terminal_failure, identity.source_scope, task_id,
                                identity.node_id, identity.executor_instance, data)

    @app.post(prefix + '/tasks/{task_id}/unknown')
    async def unknown(task_id: str, request: Request, identity: NodeScope = Depends(node)):
        data = await claim_body(request)
        await check(identity, task_id, data['claim_id'])
        return await call_store(store.mark_execution_unknown, identity.source_scope, task_id, data['claim_id'])

    @app.get(prefix + '/tasks/{task_id}/inputs/{input_id}/content')
    async def input_content(task_id: str, input_id: str, request: Request, identity: NodeScope = Depends(node)):
        claims = request.headers.getlist('x-bridge-claim')
        if len(claims) != 1:
            raise _APIError('invalid_request', 400)
        content, mime = await call_store(store.get_node_input_content, identity.source_scope, task_id,
                                        input_id, claims[0], identity.node_id, identity.executor_instance)
        return Response(content, media_type=mime)

    @app.post(prefix + '/tasks/{task_id}/uploads')
    async def upload(task_id: str, request: Request, identity: NodeScope = Depends(node)):
        data = await _json_request(request, UploadRequest, 16384)
        await check(identity, task_id, data['claim_id'])
        return await call_store(store.create_upload, identity.source_scope, task_id, data['claim_id'], data['payload'])

    @app.put(prefix + '/uploads/{upload_id}/content')
    async def upload_content(upload_id: str, request: Request, identity: NodeScope = Depends(node)):
        claims = request.headers.getlist('x-bridge-claim')
        if len(claims) != 1:
            raise _APIError('invalid_request', 400)
        reserved = await call_store(store.authorize_node_upload, identity.source_scope, upload_id,
                                   claims[0], identity.node_id, identity.executor_instance)
        if request.headers.getlist('content-type') != [reserved['mime_type']]:
            raise _APIError('unsupported_media_type', 415)
        content = await _bounded_body(request, reserved['size'])
        return await call_store(store.put_upload_content, identity.source_scope, upload_id, claims[0], content)

    @app.post(prefix + '/uploads/{upload_id}/commit')
    async def commit(upload_id: str, request: Request, identity: NodeScope = Depends(node)):
        data = await claim_body(request)
        await call_store(store.authorize_node_upload, identity.source_scope, upload_id,
                         data['claim_id'], identity.node_id, identity.executor_instance)
        return await call_store(store.commit_upload, identity.source_scope, upload_id, data['claim_id'])


def install_dispatch_routes(app, store, dispatch_tokens: Mapping[str, SourceScope], call_store):
    tokens = _credential_map(dispatch_tokens, SourceScope)
    prefix = '/bridge/dispatch/v1'

    async def dispatcher(request: Request):
        return _bearer(request, tokens)

    @app.get(prefix + '/pending')
    async def pending(limit: int = 10, scope: SourceScope = Depends(dispatcher)):
        return await call_store(store.pending_dispatches, scope, limit)

    @app.post(prefix + '/{dispatch_key}/ack')
    async def ack(dispatch_key: str, request: Request, scope: SourceScope = Depends(dispatcher)):
        data = await _json_request(request, DispatchAck, 16384)
        return await call_store(store.ack_dispatch, scope, dispatch_key, data['engine_run_id'])
