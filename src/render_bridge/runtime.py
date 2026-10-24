"""Configured, single-workspace development server. No engine runs in this process."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import logging

import anyio
from dataclasses import dataclass
import json
import os
from pathlib import Path

from .api import create_app
from .domain import BridgeStore, SourceScope
from .node_api import NodeScope
from .profiles import enabled_profile_ids


def create_runtime_app(*, store, source_tokens, node_tokens, dispatch_tokens):
    """Explicit injected fixture/development authority; never generates credentials."""
    return create_app(store=store, source_tokens=source_tokens,
                      node_tokens=node_tokens, dispatch_tokens=dispatch_tokens)


@dataclass(frozen=True)
class RuntimeConfig:
    data_dir: Path
    source_id: str
    workspace_id: str
    node_id: str
    executor_instance: str
    enabled_profiles: tuple[str, ...] = ("fake-image",)
    accepting_new_tasks: bool = True
    max_queued_tasks: int = 10
    host: str = '127.0.0.1'
    port: int = 8787
    source_token_env: str = 'BRIDGE_SOURCE_TOKEN'
    node_token_env: str = 'BRIDGE_NODE_TOKEN'
    dispatch_token_env: str = 'BRIDGE_DISPATCH_TOKEN'
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    retention_enabled: bool = False
    retention_seconds: int = 86400
    maintenance_interval_seconds: int = 60
    maintenance_batch_size: int = 100

    @classmethod
    def load(cls, path):
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Runtime config must be an object')
        value['data_dir'] = Path(value['data_dir']).expanduser()
        config = cls(**value)
        config.validate()
        return config

    def validate(self):
        enabled_profile_ids(self.enabled_profiles)
        if type(self.retention_enabled) is not bool:
            raise ValueError('retention_enabled must be a boolean')
        for value, low, high in ((self.retention_seconds, 1, 365 * 86400),
                                 (self.maintenance_interval_seconds, 1, 86400),
                                 (self.maintenance_batch_size, 1, 100)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError('Invalid bounded maintenance configuration')
        if type(self.accepting_new_tasks) is not bool:
            raise ValueError("accepting_new_tasks must be boolean")
        if type(self.max_queued_tasks) is not int or not 1 <= self.max_queued_tasks <= 10_000:
            raise ValueError("max_queued_tasks must be an integer from 1 through 10000")
        for value in (self.source_id, self.workspace_id, self.node_id, self.executor_instance):
            if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(ord(c) < 32 for c in value):
                raise ValueError('Invalid configured identity')
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('Invalid server port')
        if not isinstance(self.host, str) or not self.host:
            raise ValueError('Invalid server host')
        if bool(self.tls_certfile) != bool(self.tls_keyfile):
            raise ValueError('TLS requires both certificate and key')
        if self.host not in ('127.0.0.1', '::1', 'localhost') and not self.tls_certfile:
            raise ValueError('Non-loopback binding requires explicit TLS certificate and key')
        for value in (self.source_token_env, self.node_token_env, self.dispatch_token_env):
            if not isinstance(value, str) or not value or not value.replace('_', '').isalnum():
                raise ValueError('Invalid credential environment variable name')

    def credentials(self, environ=None):
        env = os.environ if environ is None else environ
        names = (self.source_token_env, self.node_token_env, self.dispatch_token_env)
        values = [env.get(name) for name in names]
        if any(not isinstance(value, str) or not value for value in values) or len(set(values)) != 3:
            raise ValueError('Three distinct explicitly supplied credentials are required')
        return values

    def build_app(self, environ=None):
        self.validate()
        source, node, dispatcher = self.credentials(environ)
        store = BridgeStore(self.data_dir / 'bridge.sqlite', self.data_dir / 'artifacts',
                            enabled_profiles=self.enabled_profiles, accepting_new_tasks=self.accepting_new_tasks,
                            max_queued_tasks=self.max_queued_tasks)
        scope = SourceScope(self.source_id, self.workspace_id)
        app = create_runtime_app(store=store, source_tokens={source: scope},
            node_tokens={node: NodeScope(self.source_id, self.workspace_id, self.node_id, self.executor_instance)},
            dispatch_tokens={dispatcher: scope})
        if self.retention_enabled:
            previous_lifespan = app.router.lifespan_context

            async def maintain():
                while True:
                    try:
                        await anyio.to_thread.run_sync(lambda: store.maintain_retention(
                            scope, retention_seconds=self.retention_seconds,
                            batch_size=self.maintenance_batch_size))
                    except Exception:
                        # No payload, credential, path, or exception text in logs.
                        logging.getLogger(__name__).warning('Bridge retention pass failed; will retry')
                    await asyncio.sleep(self.maintenance_interval_seconds)

            @asynccontextmanager
            async def lifespan(application):
                async with previous_lifespan(application):
                    async with anyio.create_task_group() as group:
                        group.start_soon(maintain)
                        try:
                            yield
                        finally:
                            group.cancel_scope.cancel()

            app.router.lifespan_context = lifespan
        return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    import uvicorn
    config = RuntimeConfig.load(args.config)
    app = config.build_app()
    uvicorn.run(app, host=config.host, port=config.port, access_log=False,
                ssl_certfile=config.tls_certfile, ssl_keyfile=config.tls_keyfile)


if __name__ == '__main__':
    main()
