"""Additive image admission contract, not native model/CLI controls.

Validation never normalizes a frozen request. Omission keeps legacy identity.
"""
from .profiles import get_profile


def validate_image_options(parameters: dict) -> dict | None:
    if not isinstance(parameters, dict):
        raise ValueError('Image parameters must be an object')
    if set(parameters) - {'width', 'height', 'seed', 'image_options'}:
        raise ValueError('Unsupported image parameter; use the nested image_options contract')
    if 'image_options' not in parameters:
        return None
    options = parameters['image_options']
    if not isinstance(options, dict) or set(options) != {'schema_version', 'quality', 'output_format'}:
        raise ValueError('image_options requires schema_version, quality, output_format; input_fidelity must be omitted')
    if options['schema_version'] != '1':
        raise ValueError('Unsupported image_options schema version')
    if options['quality'] != 'auto':
        raise ValueError('Only unspecified quality auto is supported')
    if options['output_format'] not in ('png', 'jpeg'):
        raise ValueError('Output format must be png or jpeg')
    return options


def validate_image_request(parameters: dict, references, profile_id: str) -> dict | None:
    options = validate_image_options(parameters)
    profile = get_profile(profile_id)
    maximum = 0 if profile.execution_mode == 'manual' else 8
    if not isinstance(references, (list, tuple)) or len(references) > maximum:
        raise ValueError('References are unsupported for this profile or exceed its limit')
    return options


def requested_output_mime(parameters: dict) -> str | None:
    options = validate_image_options(parameters)
    return 'image/' + options['output_format'] if options else None
