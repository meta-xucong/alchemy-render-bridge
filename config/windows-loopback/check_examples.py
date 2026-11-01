"""Offline example regression checks. No environment secrets or runtime construction."""
import json
from pathlib import Path

from render_bridge.agent.config import AgentConfig
from render_bridge.runtime import RuntimeConfig


def main():
    root = Path(__file__).resolve().parent
    bridge = RuntimeConfig.load(root / 'bridge.fake.json')
    agent = AgentConfig.load(root / 'agent.fake.json')
    assert bridge.host == '127.0.0.1' and bridge.port == 8787
    assert tuple(bridge.enabled_profiles) == agent.enabled_profiles == ('fake-image',)
    assert not agent.codex.enabled
    assert not agent.codex.image_capability_verified
    assert not agent.codex.tool_policy_verified
    assert not getattr(agent, 'health_reporting_enabled', False)
    assert getattr(agent, 'health_revalidation_generation', 0) == 0
    assert not getattr(agent, 'account_health_probe_enabled', False)
    assert getattr(agent.codex, 'approval_policy', '') == ''
    assert len({bridge.source_token_env, bridge.node_token_env, bridge.dispatch_token_env}) == 3
    compose = json.loads((root / 'hatchet.compose.json').read_text(encoding='utf-8'))
    services = compose['services']
    assert set(services) == {'postgres', 'hatchet-lite'}
    assert 'ports' not in services['postgres']
    assert services['hatchet-lite']['ports'] == ['127.0.0.1:8888:8888', '127.0.0.1:7077:7077']
    assert all(':latest' not in service['image'] for service in services.values())
    assert all('network_mode' not in service and not service.get('privileged') for service in services.values())
    assert services['postgres']['environment']['POSTGRES_PASSWORD'].startswith('${HATCHET_POSTGRES_PASSWORD:?')
    assert '${HATCHET_POSTGRES_PASSWORD:?' in services['hatchet-lite']['environment']['DATABASE_URL']
    assert set(compose['volumes']) == {'postgres_data', 'engine_config'}
    print('PASS: fake-only runtime/agent parsers and static loopback Compose invariants')


if __name__ == '__main__':
    main()
