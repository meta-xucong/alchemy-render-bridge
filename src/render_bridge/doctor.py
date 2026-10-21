"""Read-only local readiness checks. Never logs in or generates an image.

By default only explicit config files and executable metadata are inspected.
--probe-codex additionally runs version/help in an empty temporary home.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil

from .agent.config import AgentConfig
from .executors.codex import PINNED_CLI_VERSION, probe_codex
from .profiles import get_profile
from .runtime import RuntimeConfig


EXIT_CODES = {'ready': 0, 'attention': 1, 'invalid': 2}
_CONFIG_ERRORS = (OSError, UnicodeError, ValueError, TypeError, KeyError)


def inspect_local(bridge_path: Path, agent_path: Path, *, probe: bool = False) -> dict:
    """Build a sanitized report without clients, stores, executors or credentials."""
    checks = []

    def add(check_id, status, evidence, code, next_step):
        checks.append(dict(id=check_id, status=status, evidence=evidence,
                           code=code, next_step=next_step))

    bridge = agent = None
    try:
        bridge = RuntimeConfig.load(bridge_path)
        add('bridge_config', 'pass', 'configuration', 'bridge_config_valid',
            'Use this configuration only for the bounded development runtime.')
    except _CONFIG_ERRORS:
        add('bridge_config', 'fail', 'configuration', 'bridge_config_invalid',
            'Provide a readable Bridge JSON object with valid identities, profiles, port and TLS pairing; keep token values out of it.')
    try:
        agent = AgentConfig.load(agent_path)
        add('agent_config', 'pass', 'configuration', 'agent_config_valid',
            'Keep credential values outside the Agent configuration.')
    except _CONFIG_ERRORS:
        add('agent_config', 'fail', 'configuration', 'agent_config_invalid',
            'Use known distinct nonempty enabled_profiles and valid codex fields; acceptance flags must be JSON true or false, not strings.')

    profiles = list(bridge.enabled_profiles if bridge else agent.enabled_profiles if agent else ())
    if bridge and agent:
        aligned = set(bridge.enabled_profiles) == set(agent.enabled_profiles)
        add('profile_alignment', 'pass' if aligned else 'attention', 'configuration',
            'profiles_aligned' if aligned else 'profiles_mismatch',
            'Keep Bridge and Agent enabled_profiles aligned; enabling a profile does not validate a real tool.')
    else:
        add('profile_alignment', 'not_checked', 'not_checked', 'valid_configs_required',
            'Correct both configuration files before comparing enabled profiles.')

    if profiles:
        for profile_id in profiles:
            profile = get_profile(profile_id)
            contract = profile.parameter_contract()
            instruction = {
                'deterministic_fixture': 'Fake creates a synthetic fixture; seed is accepted but not applied and references do not condition it.',
                'text_instruction_only': 'Codex parameters are text instructions, not structured image-tool controls; local image inputs remain unverified.',
                'manual_user_selected': 'A user must submit and select the original; reference requests are not supported by the manual executor.',
            }[contract['executor_control']]
            add('parameter_contract_' + profile_id.replace('-', '_'), 'pass', 'configuration',
                'parameter_contract_v1',
                'Width/height/seed and optional nested image_options v1 are accepted. '
                'Output dimensions must match exactly. ' +
                ('Width/height default to 32 for fake. ' if not contract['required_parameters']
                 else 'Explicit width/height are required. ') + instruction +
                ' Nested quality supports auto (unspecified) only; png/jpeg output_format is checked '
                'against the decoded original, not a native control. Input_fidelity must be omitted.')
    else:
        add('parameter_contract', 'not_checked', 'not_checked', 'valid_profiles_required',
            'Correct the enabled profiles before inspecting parameter semantics.')

    # Presence in configuration is not execution permission or observed capability.
    relevant = set(profiles) | set(agent.enabled_profiles if agent else ())
    codex_selected = 'codex-image' in relevant
    if agent and codex_selected:
        config = agent.codex
        for check_id, accepted, code, instruction in (
            ('codex_execution_enabled', config.enabled, 'codex_execution',
             'Codex execution remains opt-in; enable only after separate local acceptance.'),
            ('codex_model', bool(config.model.strip()), 'codex_model',
             'Select the model used in your separate local acceptance; configuration is not model capability evidence.'),
            ('codex_image_acceptance', config.image_capability_verified, 'codex_image_acceptance',
             'This flag is only an operator declaration of local image acceptance, never an observed doctor result.'),
            ('codex_tool_policy_acceptance', config.tool_policy_verified, 'codex_tool_policy_acceptance',
             'This flag is only an operator declaration; separately validate the dedicated local tool policy.'),
        ):
            add(check_id, 'pass' if accepted else 'attention', 'operator_declaration',
                code + ('_declared' if accepted else '_missing'), instruction)
    else:
        add('codex_acceptance', 'not_checked', 'not_checked',
            'codex_not_enabled' if agent else 'valid_agent_config_required',
            'No Codex acceptance is inferred from missing or unused configuration.')

    if agent and (codex_selected or probe):
        executable_present = shutil.which(agent.codex.executable) is not None
        add('codex_executable', 'pass' if executable_present else 'attention', 'local_probe',
            'codex_executable_found' if executable_present else 'codex_executable_missing',
            'Use an installed trusted Codex executable; this check only inspects executable availability.')
    else:
        executable_present = False
        add('codex_executable', 'not_checked', 'not_checked',
            'codex_not_enabled' if agent else 'valid_agent_config_required',
            'Executable availability is relevant only to Codex or an explicitly requested probe.')

    if probe and bridge and agent and executable_present:
        result = probe_codex(agent.codex.executable)
        observed = result.get('cli_version')
        version_ok = observed == PINNED_CLI_VERSION
        version_note = (' Observed installed version: ' + observed + '.'
                        if isinstance(observed, str) and re.fullmatch(r'codex-cli [0-9A-Za-z.+_-]{1,64}', observed)
                        else '')
        protocol_ok = bool(result.get('schema_version_matches') and result.get('stdio_advertised'))
        add('codex_version', 'pass' if version_ok else 'attention', 'local_probe',
            'codex_version_matches' if version_ok else 'codex_version_not_validated',
            'Use the pinned codex-cli 0.163.0 for this adapter; version output is not account or generation evidence.' + version_note)
        add('codex_protocol', 'pass' if protocol_ok else 'attention', 'local_probe',
            'codex_stdio_advertised' if protocol_ok else 'codex_stdio_not_validated',
            'The pinned version must advertise app-server --stdio; help text does not verify a live protocol session.')
    else:
        code = ('codex_probe_not_requested' if not probe else
                'valid_configs_required' if not (bridge and agent) else 'codex_executable_missing')
        for check_id in ('codex_version', 'codex_protocol'):
            add(check_id, 'not_checked', 'not_checked', code,
                'Use --probe-codex after valid configuration to inspect version/help in an empty temporary home; no login or generation occurs.')

    add('credentials', 'not_checked', 'not_checked', 'credentials_not_checked',
        'Supply distinct service credentials separately when starting the runtime; the doctor never reads credential values or files.')
    add('login', 'not_checked', 'not_checked', 'login_not_checked',
        'Check login yourself on the local machine when ready; the doctor never inspects saved accounts or logs in.')
    real_profiles = bool(relevant - {'fake-image'})
    add('real_generation', 'attention' if real_profiles else 'not_checked', 'not_checked',
        'real_generation_not_verified' if real_profiles else 'fake_only_not_real_verification',
        'Real generation requires separate authorized local acceptance. Alchemy native real-profile adaptation is also unfinished; login alone does not enable it.')
    outcome = ('invalid' if not (bridge and agent) else
               'attention' if any(c['status'] in ('attention', 'fail') for c in checks) else 'ready')
    return {'schema_version': '1', 'kind': 'render_bridge_doctor',
            'producer': {'name': 'alchemy-render-bridge', 'version': '0.1.0a1'},
            'outcome': outcome, 'profiles': profiles, 'checks': checks,
            'real_generation_verified': False, 'login_verified': False}


def render_human(report: dict) -> str:
    meaning = {'ready': 'Local configuration checks passed (not runtime or real-renderer verification).',
               'attention': 'Local checks need attention before the relevant workflow can be used.',
               'invalid': 'Configuration is invalid or unreadable; correct it before proceeding.'}
    lines = [meaning[report['outcome']], 'Profiles: ' + (', '.join(report['profiles']) or '(unavailable)')]
    for check in report['checks']:
        lines.append(f"[{check['status']}] {check['id']} ({check['code']}): {check['next_step']}")
    lines.append('Login verified: no. Real generation verified: no. No configuration was changed.')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge-config', type=Path, required=True)
    parser.add_argument('--agent-config', type=Path, required=True)
    parser.add_argument('--json', action='store_true', help='Print the stable sanitized JSON report')
    parser.add_argument('--probe-codex', action='store_true', help='Explicit version/help only; no login or generation')
    args = parser.parse_args(argv)
    report = inspect_local(args.bridge_config, args.agent_config, probe=args.probe_codex)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_human(report))
    return EXIT_CODES[report['outcome']]


if __name__ == '__main__':
    raise SystemExit(main())
