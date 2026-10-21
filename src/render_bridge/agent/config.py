"""Strict, non-secret local Agent configuration shared by runtime and doctor."""
from dataclasses import dataclass, field
import json
from pathlib import Path

from render_bridge.executors.codex import CodexConfig
from render_bridge.profiles import enabled_profile_ids


@dataclass(frozen=True)
class AgentConfig:
    enabled_profiles: tuple[str, ...] = ('fake-image',)
    health_reporting_enabled: bool = False
    account_health_probe_enabled: bool = False
    health_revalidation_generation: int = 0
    codex: CodexConfig = field(default_factory=CodexConfig)

    def __post_init__(self):
        object.__setattr__(self, 'enabled_profiles', enabled_profile_ids(self.enabled_profiles))
        if type(self.health_revalidation_generation) is not int or not 0 <= self.health_revalidation_generation < 2**63:
            raise ValueError('health_revalidation_generation must be a nonnegative integer')
        if type(self.account_health_probe_enabled) is not bool:
            raise ValueError('account_health_probe_enabled must be boolean')
        if type(self.health_reporting_enabled) is not bool:
            raise ValueError('health_reporting_enabled must be boolean')
        if not isinstance(self.codex, CodexConfig):
            raise ValueError('codex must be a Codex configuration object')

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, dict):
            raise ValueError('Agent config must be an object')
        if set(value) - {'enabled_profiles', 'codex', 'health_reporting_enabled', 'health_revalidation_generation', 'account_health_probe_enabled'}:
            raise ValueError('Unknown Agent configuration fields; tokens belong in environment')
        codex = value.get('codex', {})
        if not isinstance(codex, dict):
            raise ValueError('codex must be an object')
        if set(codex) - set(CodexConfig.__dataclass_fields__):
            raise ValueError('Unknown Codex configuration fields')
        return cls(enabled_profiles=enabled_profile_ids(value.get('enabled_profiles', ['fake-image'])),
                   codex=CodexConfig(**codex), health_reporting_enabled=value.get('health_reporting_enabled', False),
                   health_revalidation_generation=value.get('health_revalidation_generation', 0),
                   account_health_probe_enabled=value.get('account_health_probe_enabled', False))

    @classmethod
    def load(cls, path: Path | None = None):
        return cls.from_mapping(json.loads(Path(path).read_text(encoding='utf-8')) if path else {})
