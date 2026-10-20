"""Explicit local executor selection. Configuration contains no credentials."""
import json
from pathlib import Path

from render_bridge.executors.codex import CodexConfig, CodexExecutor
from render_bridge.executors.fake import FakeExecutor
from render_bridge.executors.manual import MidjourneyManualExecutor
from render_bridge.profiles import get_profile


class ExecutorRegistry:
    def __init__(self, executors):
        self.executors = dict(executors)
        for name in self.executors:
            get_profile(name)

    def get(self, profile_id):
        return self.executors.get(profile_id)

    @classmethod
    def from_config(cls, root: Path, path: Path | None = None):
        config = json.loads(path.read_text(encoding='utf-8')) if path else {}
        if set(config) - {'enabled_profiles', 'codex'}:
            raise ValueError('Unknown Agent configuration fields; tokens belong in environment')
        enabled = config.get('enabled_profiles', ['fake-image'])
        if not isinstance(enabled, list) or len(set(enabled)) != len(enabled):
            raise ValueError('enabled_profiles must be a unique list')
        executors = {}
        for name in enabled:
            get_profile(name)
            if name == 'fake-image':
                executors[name] = FakeExecutor(root)
            elif name == 'codex-image':
                executors[name] = CodexExecutor(root, CodexConfig(**config.get('codex', {})))
            elif name == 'midjourney-manual':
                executors[name] = MidjourneyManualExecutor(root)
        return cls(executors)
