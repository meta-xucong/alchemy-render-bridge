"""Frozen revision-1 execution contracts shared by server and node.

Enabling a profile admits requests; it does not verify a real tool or log it in.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionProfile:
    id: str
    revision: str
    execution_mode: str
    max_output_bytes: int
    max_pixels: int
    max_dimension: int


_PROFILES = {
    'fake-image': ExecutionProfile('fake-image', '1', 'automatic', 1_048_576, 1_048_576, 1024),
    'codex-image': ExecutionProfile('codex-image', '1', 'automatic', 16_777_216, 40_000_000, 40_000_000),
    'midjourney-manual': ExecutionProfile('midjourney-manual', '1', 'manual', 16_777_216, 40_000_000, 40_000_000),
}
MAX_PROFILE_OUTPUT_BYTES = 16_777_216


def get_profile(profile_id: str) -> ExecutionProfile:
    if not isinstance(profile_id, str) or profile_id not in _PROFILES:
        raise ValueError('Unknown execution profile')
    return _PROFILES[profile_id]


def enabled_profile_ids(values):
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError('enabled_profiles must be a nonempty list of distinct profile IDs')
    if any(not isinstance(value, str) for value in values) or len(set(values)) != len(values):
        raise ValueError('enabled_profiles must be a nonempty list of distinct profile IDs')
    return tuple(get_profile(value).id for value in values)
