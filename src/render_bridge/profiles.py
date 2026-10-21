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
    executor_control: str
    seed_semantics: str
    reference_semantics: str

    def image_options_contract(self) -> dict:
        return {
            'schema_version': '1',
            'quality': {'values': ['auto'], 'semantics': 'unspecified_only'},
            'output_format': {'values': ['png', 'jpeg'], 'semantics': 'original_artifact_postcondition'},
            'input_fidelity': {'values': [], 'semantics': 'omitted_only'},
            'references': {'max_count': 0 if self.execution_mode == 'manual' else 8,
                           'semantics': self.reference_semantics},
        }

    def parameter_contract(self) -> dict:
        """Admission/validation semantics, never a claim of model capability."""
        return {
            'schema_version': '1',
            'accepted_parameters': ['width', 'height', 'seed', 'image_options'],
            'required_parameters': [] if self.id == 'fake-image' else ['width', 'height'],
            'dimension_semantics': 'exact_output_match',
            'executor_control': self.executor_control,
            'seed_semantics': self.seed_semantics,
            'reference_semantics': self.reference_semantics,
            # These names remain unsupported at the top level of parameters.
            'unsupported_parameters': ['quality', 'output_format', 'input_fidelity'],
        }


_PROFILES = {
    'fake-image': ExecutionProfile('fake-image', '1', 'automatic', 1_048_576, 1_048_576, 1024,
        'deterministic_fixture', 'accepted_not_applied', 'hash_checked_not_conditioning'),
    'codex-image': ExecutionProfile('codex-image', '1', 'automatic', 16_777_216, 40_000_000, 40_000_000,
        'text_instruction_only', 'text_instruction_only', 'local_image_input_unverified'),
    'midjourney-manual': ExecutionProfile('midjourney-manual', '1', 'manual', 16_777_216, 40_000_000, 40_000_000,
        'manual_user_selected', 'manual_user_selected', 'not_supported'),
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
