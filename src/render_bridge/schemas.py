"""Strict, versioned request shapes for the experimental source API.

Only the FakeRenderer image contract is enabled. These are wire schemas, not
Alchemy business objects, node commands, or a production capability catalog.
The domain validates the same invariants for non-HTTP fixture callers.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator
from pydantic.json_schema import SkipJsonSchema


Key = Annotated[StrictStr, Field(min_length=1, max_length=128)]
OriginKey = Annotated[
    StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
OpaqueReference = Annotated[StrictStr, Field(min_length=1, max_length=256)]
SHA256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
ImageDimension = Annotated[StrictInt, Field(ge=1, le=1024)]
ImageSeed = Annotated[StrictInt, Field(ge=0, le=2**32 - 1)]


class StrictRequest(BaseModel):
    """Never coerce types or silently discard unsupported execution fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class InputCreate(StrictRequest):
    input_key: Key
    original_sha256: SHA256
    size: Annotated[StrictInt, Field(ge=1, le=1_048_576)]
    mime_type: Literal["image/png", "image/jpeg"]


class ImageParameters(StrictRequest):
    width: ImageDimension = 32
    height: ImageDimension = 32
    seed: ImageSeed = 0


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
    execution_profile_id: Literal["fake-image"]
    execution_profile_revision: Literal["1"]
    prompt: Annotated[StrictStr, Field(min_length=1, max_length=16_000)]
    parameters: ImageParameters
    references: Annotated[list[InputReference], Field(max_length=8)]
    output_slot: OutputSlot
    # Keep the exact frozen wire value; the domain checks UTC and the time bound.
    deadline: Annotated[StrictStr, Field(min_length=20, max_length=40)]
    routing: Literal["bridge_only"]


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
