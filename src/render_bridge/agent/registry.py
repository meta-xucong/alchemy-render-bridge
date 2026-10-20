"""Explicit local executor selection. Configuration contains no credentials."""
from pathlib import Path

from render_bridge.executors.codex import CodexExecutor
from render_bridge.executors.fake import FakeExecutor
from render_bridge.executors.manual import MidjourneyManualExecutor
from render_bridge.profiles import get_profile
from .config import AgentConfig


class ExecutorRegistry:
    def __init__(self, executors):
        self.executors = dict(executors)
        for name in self.executors:
            get_profile(name)

    def get(self, profile_id):
        return self.executors.get(profile_id)

    @classmethod
    def from_config(cls, root: Path, path: Path | None = None):
        config = AgentConfig.load(path)
        executors = {}
        for name in config.enabled_profiles:
            get_profile(name)
            if name == 'fake-image':
                executors[name] = FakeExecutor(root)
            elif name == 'codex-image':
                executors[name] = CodexExecutor(root, config.codex)
            elif name == 'midjourney-manual':
                executors[name] = MidjourneyManualExecutor(root)
        return cls(executors)
