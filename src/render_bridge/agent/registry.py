"""Explicit local executor selection. Configuration contains no credentials."""
from pathlib import Path

from render_bridge.executors.codex import CodexExecutor
from render_bridge.executors.fake import FakeExecutor
from render_bridge.executors.manual import MidjourneyManualExecutor
from render_bridge.profiles import get_profile
from .config import AgentConfig


class ExecutorRegistry:
    def __init__(self, executors, *, health_reporting_enabled=False, health_revalidation_generation=0, account_health_probe_enabled=False):
        self.executors = dict(executors)
        self.health_reporting_enabled = health_reporting_enabled
        self.health_revalidation_generation = health_revalidation_generation
        self.account_health_probe_enabled = account_health_probe_enabled
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
        return cls(executors, health_reporting_enabled=config.health_reporting_enabled,
                   health_revalidation_generation=config.health_revalidation_generation,
                   account_health_probe_enabled=config.account_health_probe_enabled)
