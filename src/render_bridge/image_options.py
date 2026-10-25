"""Additive image admission contract, not native model/CLI controls.

Validation never normalizes a frozen request. Omission keeps legacy identity.
"""
from .profiles import get_profile


def validate_image_options(parameters: dict, profile_id: str | None = None) -> dict | None:
    if not isinstance(parameters, dict):
        raise ValueError('Image parameters must be an object')
    if set(parameters) - {'width', 'height', 'seed', 'image_options'}:
        raise ValueError('Unsupported image parameter; use the nested image_options contract')
    if 'image_options' not in parameters:
        return None
    options = parameters['image_options']
    if not isinstance(options, dict) or set(options) - {
        'schema_version', 'quality', 'output_format', 'input_fidelity', 'transparent_background'
    } or options.get('schema_version') != '1':
        raise ValueError('Unsupported image_options schema version')
    if any(value is None for key, value in options.items() if key != 'schema_version'):
        raise ValueError('Optional image options must be omitted instead of null')
    if profile_id == 'codex-image':
        if options.get('quality') not in (None, 'auto', 'low', 'medium', 'high'):
            raise ValueError('Unsupported requested quality')
        if options.get('output_format') not in (None, 'png', 'jpeg', 'webp'):
            raise ValueError('Unsupported requested output format')
        if options.get('input_fidelity') not in (None, 'auto', 'low', 'high'):
            raise ValueError('Unsupported requested input fidelity')
        if 'transparent_background' in options and type(options['transparent_background']) is not bool:
            raise ValueError('transparent_background must be a boolean')
        return options
    if set(options) != {'schema_version', 'quality', 'output_format'}:
        raise ValueError('Non-Codex profiles require schema_version, quality, output_format')
    if options['quality'] != 'auto':
        raise ValueError('Only unspecified quality auto is supported')
    if options['output_format'] not in ('png', 'jpeg'):
        raise ValueError('Output format must be png or jpeg')
    return options


def validate_image_request(parameters: dict, references, profile_id: str) -> dict | None:
    options = validate_image_options(parameters, profile_id)
    profile = get_profile(profile_id)
    maximum = 0 if profile.execution_mode == 'manual' else (5 if profile_id == 'codex-image' else 8)
    if not isinstance(references, (list, tuple)) or len(references) > maximum:
        raise ValueError('References are unsupported for this profile or exceed its limit')
    return options


def requested_output_mime(parameters: dict, profile_id: str | None = None) -> str | None:
    options = validate_image_options(parameters, profile_id)
    if profile_id == 'codex-image':
        return None  # Codex imagegen does not expose a native output-format argument.
    return 'image/' + options['output_format'] if options else None


def codex_imagegen_arguments(parameters: dict, cli_version: str) -> dict:
    """Map Bridge's accepted request onto the Codex imagegen tool contract.

    Dimensions, seed, quality and format are advisory source-provider fields;
    the Codex tool has no corresponding arguments and uses its own defaults.
    The only explicit image option currently exposed by the tool is alpha.
    """
    options = validate_image_request(parameters, (), 'codex-image') or {}
    requested_transparency = options.get('transparent_background') is True
    # 0.157.1 has no transparency argument and always requests ImageBackground::Auto.
    # 0.163.0 adds transparent_background (default false). Do not ask an older
    # strict tool schema to accept an argument it does not expose.
    transparent_supported = cli_version == 'codex-cli 0.163.0'
    return {
        'transparent_background': requested_transparency and transparent_supported,
        'transparent_background_mapping': (
            'native_tool_argument' if requested_transparency and transparent_supported
            else 'codex_cli_default_auto' if requested_transparency
            else 'native_default'
        ),
    }
