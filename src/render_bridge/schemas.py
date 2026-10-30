"""Strict, versioned request shapes for the experimental source API.

Profiles are admitted by explicit server configuration; fake is the default. These are wire schemas, not
Alchemy business objects, node commands, or a production capability catalog.
The domain validates the same invariants for non-HTTP fixture callers.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator
from pydantic.json_schema import SkipJsonSchema
from .domain import MAX_INPUT_BYTES
from .profiles import get_profile
from .image_options import validate_image_request


Key = Annotated[StrictStr, Field(min_length=1, max_length=128)]
OriginKey = Annotated[
    StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
OpaqueReference = Annotated[StrictStr, Field(min_length=1, max_length=256)]
SHA256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
ImageDimension = Annotated[StrictInt, Field(ge=1, le=40_000_000)]
ImageSeed = Annotated[StrictInt, Field(ge=0, le=2**32 - 1)]


class StrictRequest(BaseModel):
    """Never coerce types or silently discard unsupported execution fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class TerminalFailureEvidence(StrictRequest):
    schema_version: Literal['1']
    thread_id: Key
    turn_id: Key
    reason_code: Literal['auth_unavailable', 'quota_exhausted']
    completion_sha256: SHA256


class TerminalFailureRequest(StrictRequest):
    attempt_id: Key
    evidence: TerminalFailureEvidence


class InputCreate(StrictRequest):
    input_key: Key
    original_sha256: SHA256
    size: Annotated[StrictInt, Field(ge=1, le=MAX_INPUT_BYTES)]
    mime_type: Literal["image/png", "image/jpeg"]


class ImageOptions(StrictRequest):
    schema_version: Literal["1"]
    quality: Literal["auto", "low", "medium", "high"] | SkipJsonSchema[None] = Field(default_factory=lambda: None)
    output_format: Literal["png", "jpeg", "webp"] | SkipJsonSchema[None] = Field(default_factory=lambda: None)
    input_fidelity: Literal["auto", "low", "high"] | SkipJsonSchema[None] = Field(default_factory=lambda: None)
    transparent_background: StrictBool | SkipJsonSchema[None] = Field(default_factory=lambda: None)


class ImageParameters(StrictRequest):
    codex_adapter: Literal["codex_cli_imagegen_v1"] | SkipJsonSchema[None] = Field(default_factory=lambda: None)
    width: ImageDimension = 32
    height: ImageDimension = 32
    seed: ImageSeed = 0
    image_options: ImageOptions | SkipJsonSchema[None] = Field(default_factory=lambda: None)

    @model_validator(mode="before")
    @classmethod
    def image_options_must_not_be_null(cls, value):
        if isinstance(value, dict) and 'image_options' in value and value['image_options'] is None:
            raise ValueError('image_options must be omitted instead of null')
        return value


class InputReference(StrictRequest):
    input_ref: Key
    role: Literal["reference"]
    hash: SHA256


class OutputSlot(StrictRequest):
    slot_id: Literal["primary"]
    # Literal[1] alone also accepts True; an explicitly strict integer does not.
    count: Annotated[StrictInt, Field(ge=1, le=1)]


class TaskCreate(StrictRequest):
    schema_version: Literal["1"]
    origin_key: OriginKey
    binding_id: OpaqueReference
    operation: Literal["image.generate"]
    execution_profile_id: Literal["fake-image", "codex-image", "midjourney-manual"]
    execution_profile_revision: Literal["1"]
    prompt: Annotated[StrictStr, Field(min_length=1, max_length=16_000)]
    parameters: ImageParameters
    references: Annotated[list[InputReference], Field(max_length=8)]
    output_slot: OutputSlot
    # Keep the exact frozen wire value; the domain checks UTC and the time bound.
    deadline: Annotated[StrictStr, Field(min_length=20, max_length=40)]
    routing: Literal["bridge_only"]

    @model_validator(mode="before")
    @classmethod
    def explicit_real_dimensions(cls, value):
        if isinstance(value, dict) and value.get("execution_profile_id") == "midjourney-manual":
            parameters = value.get("parameters")
            if not isinstance(parameters, dict) or not {"width", "height"} <= parameters.keys():
                raise ValueError("Real profiles require explicit width and height")
        return value

    @model_validator(mode="after")
    def enforce_profile_dimensions(self):
        validate_image_request(self.parameters.model_dump(exclude_unset=True),
                               self.references, self.execution_profile_id)
        profile = get_profile(self.execution_profile_id)
        if (max(self.parameters.width, self.parameters.height) > profile.max_dimension
                or self.parameters.width * self.parameters.height > profile.max_pixels):
            raise ValueError("Dimensions exceed execution profile limits")
        return self


class ReceiptAck(StrictRequest):
    client_receipt_id: Key
    decision: Literal["accepted", "rejected"]
    reason_code: Key
    original_hash: SHA256
    # Omission is supported, explicit null is not. The internal None default is
    # hidden from the wire schema; factories also avoid exporting default:null.
    normalized_hash: SHA256 | SkipJsonSchema[None] = Field(default_factory=lambda: None)
    native_registration_ref: OpaqueReference | SkipJsonSchema[None] = Field(
        default_factory=lambda: None
    )

    @model_validator(mode="before")
    @classmethod
    def optional_fields_must_not_be_null(cls, value: object) -> object:
        if isinstance(value, dict):
            for field in ("normalized_hash", "native_registration_ref"):
                if field in value and value[field] is None:
                    raise ValueError("Optional fields must be omitted instead of null.")
        return value


class WithdrawalRequest(StrictRequest):
    schema_version: Literal['1']
    withdrawal_key: OriginKey
    origin_key: OriginKey
    attempt_id: Key
    task_digest: SHA256


class WithdrawalProof(StrictRequest):
    schema_version: Literal['1']
    status: Literal['withdrawn_before_start']
    withdrawal_key: OriginKey
    withdrawal_ref: Key
    task_id: Key
    origin_key: OriginKey
    attempt_id: Key
    task_digest: SHA256


class AvailabilityProfile(StrictRequest):
    profile_id: Literal['fake-image', 'codex-image', 'midjourney-manual']
    profile_revision: Literal['1']
    health: Literal['available', 'unavailable', 'unknown']
    capacity: Literal['idle', 'busy']
    reason_code: Literal['ready', 'tool_missing', 'version_unsupported', 'auth_unavailable', 'quota_exhausted', 'resource_unavailable', 'probe_failed', 'unverified', 'stale']
    evidence_kind: Literal['local_probe', 'provider_error', 'operator_validation', 'operator_attested_preflight', 'account_status', 'none']
    observed_at: StrictStr | None
    valid_until: StrictStr | None


class RoutingAvailability(StrictRequest):
    schema_version: Literal['1']
    revision: Annotated[StrictInt, Field(ge=0)]
    profiles: Annotated[list[AvailabilityProfile], Field(max_length=3)]
