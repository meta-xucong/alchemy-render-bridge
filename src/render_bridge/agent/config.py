"""Strict, non-secret local Agent configuration shared by runtime and doctor."""
from dataclasses import dataclass, field
import json
from pathlib import Path

from render_bridge.executors.codex import CodexConfig
from render_bridge.profiles import enabled_profile_ids


@dataclass(frozen=True)
class AgentConfig:
    enabled_profiles: tuple[str, ...] = ('fake-image',)
    codex: CodexConfig = field(default_factory=CodexConfig)

    def __post_init__(self):
        object.__setattr__(self, 'enabled_profiles', enabled_profile_ids(self.enabled_profiles))
        if not isinstance(self.codex, CodexConfig):
            raise ValueError('codex must be a Codex configuration object')

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, dict):
            raise ValueError('Agent config must be an object')
        if set(value) - {'enabled_profiles', 'codex'}:
            raise ValueError('Unknown Agent configuration fields; tokens belong in environment')
        codex = value.get('codex', {})
        if not isinstance(codex, dict):
            raise ValueError('codex must be an object')
        if set(codex) - set(CodexConfig.__dataclass_fields__):
            raise ValueError('Unknown Codex configuration fields')
        return cls(enabled_profiles=enabled_profile_ids(value.get('enabled_profiles', ['fake-image'])),
                   codex=CodexConfig(**codex))

    @classmethod
    def load(cls, path: Path | None = None):
        return cls.from_mapping(json.loads(Path(path).read_text(encoding='utf-8')) if path else {})
