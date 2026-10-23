"""Opt-in tool/status probes. No new login, paid generation or scheduling.

Account status is a separate explicit opt-in; the CLI may refresh its own login.
"""
import shutil
import threading
import time
from render_bridge.executors.codex import probe_codex
from render_bridge.profiles import get_profile
from .account_health import probe_account_health


class ProfileHealthReporter:
    def __init__(self, journal, registry, *, clock=time.monotonic, wall_clock=time.time, codex_probe=probe_codex, account_probe=probe_account_health):
        self.journal, self.registry = journal, registry
        self.clock, self.codex_probe = clock, codex_probe
        self.wall_clock = wall_clock
        self.account_probe = account_probe
        self.lock = threading.Lock()
        self.last_probe = None
        self.cached = []

    def record_result(self, profile, result, evidence_id):
        reason = result.details.get('profile_health_reason') if isinstance(result.details, dict) else None
        if profile != 'codex-image' or reason not in {'auth_unavailable', 'quota_exhausted', 'probe_failed', 'tool_missing'}:
            return
        with self.lock:
            self.journal.record_health_fault(profile, reason, evidence_id, self.registry.health_revalidation_generation)
            self.last_probe = None

    def reports(self):
        # Task-edge heartbeats and the liveness loop share one bounded probe cadence.
        # Reusing report revisions cannot extend the server's freshness window.
        with self.lock:
            now = self.clock()
            if self.last_probe is not None and 0 <= now-self.last_probe < 30:
                return self.cached
            reports = []
            revalidated = self.journal.revalidation_active(self.registry.health_revalidation_generation, self.wall_clock())
            for name, executor in sorted(self.registry.executors.items()):
                health, reason, evidence = 'available', 'ready', 'local_probe'
                try:
                    resources_ready = shutil.disk_usage(self.journal.root).free >= max(4*1_048_576, 2*get_profile(name).max_output_bytes)
                except OSError:
                    resources_ready = False
                if not resources_ready:
                    health, reason = 'unavailable', 'resource_unavailable'
                elif name == 'codex-image':
                    config = executor.config
                    probe = self.codex_probe(config.executable)
                    if probe.get('code') == 'codex_unavailable':
                        health, reason = 'unavailable', probe.get('health_reason', 'tool_missing')
                    elif not (probe.get('schema_version_matches') and probe.get('stdio_advertised')):
                        health, reason = 'unavailable', probe.get('health_reason', 'version_unsupported')
                    elif probe.get('cli_version') != config.accepted_cli_version:
                        if config.accepted_cli_version:
                            health, reason = 'unavailable', 'version_unsupported'
                        else:
                            health, reason = 'unknown', 'unverified'
                    elif not (config.enabled and config.model and config.approval_policy == 'on-request' and config.image_capability_verified and config.tool_policy_verified):
                        health, reason = 'unknown', 'unverified'
                    else:
                        evidence = 'operator_validation' if revalidated else 'operator_attested_preflight'
                    if health == 'available' and self.registry.account_health_probe_enabled:
                        if config.accepted_cli_version != 'codex-cli 0.163.0':
                            health, reason, evidence = 'unknown', 'unverified', 'none'
                        else:
                            allowed = self.account_probe(config.executable, self.journal.root)
                            if allowed is True:
                                evidence = 'account_status'
                            elif allowed is False:
                                health, reason, evidence = 'unavailable', 'probe_failed', 'account_status'
                                self.journal.record_health_fault(name, reason, 'status-'+str(self.clock()), self.registry.health_revalidation_generation)
                            elif self.journal.health_fault(name) is None:
                                health, reason, evidence = 'unknown', 'unverified', 'none'
                    # These flags are only pre-existing local acceptance gates, not
                    # proof of account validity, quota or production acceptance.
                fault = self.journal.health_fault(name)
                if fault and health == 'available':
                    if fault['reason'] == 'tool_missing' or evidence == 'account_status' or (revalidated and self.registry.health_revalidation_generation > fault['generation']):
                        self.journal.clear_health_fault(name)
                    else:
                        health, reason, evidence = 'unavailable', fault['reason'], 'provider_error'
                reports.append(dict(profile_id=name, profile_revision='1',
                    report_revision=self.journal.next_health_revision(name),
                    health=health, reason_code=reason, evidence_kind=evidence,
                    revalidation_generation=self.registry.health_revalidation_generation))
            self.cached, self.last_probe = reports, now
            return reports
