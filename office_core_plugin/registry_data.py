from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from .operation_policy import confidence_band
from .redaction import redact_text
from .registry_base import confidence, objects, one_object, text, text_tuple
from .registry_core import (
    ProvenanceRecord,
    RegistryError,
    SourceLocation,
    provenance,
)

if TYPE_CHECKING:
    from .handler_contract import JSONObject, JSONValue


@dataclass(frozen=True, slots=True)
class ReusableDataEntry:
    field_id: str
    template_id: str
    field_name: str
    value: JSONValue
    confidence: float
    provenance: tuple[ProvenanceRecord, ...]
    source_record_ids: tuple[str, ...]
    downstream_output_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        if "value" not in data:
            field = "value"
            detail = "required"
            raise RegistryError(field, detail)
        return cls(
            field_id=text(data, "field_id"),
            template_id=text(data, "template_id"),
            field_name=text(data, "field_name"),
            value=data["value"],
            confidence=confidence(data, "confidence"),
            provenance=provenance(data),
            source_record_ids=text_tuple(data, "source_record_ids"),
            downstream_output_ids=text_tuple(data, "downstream_output_ids"),
        )

    def to_dict(self) -> JSONObject:
        return {
            "confidence": self.confidence,
            "confidence_band": confidence_band(self.confidence),
            "downstream_output_ids": [*self.downstream_output_ids],
            "field_id": redact_text(self.field_id),
            "field_name": redact_text(self.field_name),
            "provenance": [item.to_dict() for item in self.provenance],
            "source_record_ids": [*self.source_record_ids],
            "template_id": redact_text(self.template_id),
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class DownstreamOutput:
    output_id: str
    template_id: str
    output_type: str
    location: SourceLocation
    source_record_ids: tuple[str, ...]
    confidence: float
    provenance: tuple[ProvenanceRecord, ...]

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        return cls(
            output_id=text(data, "output_id"),
            template_id=text(data, "template_id"),
            output_type=text(data, "output_type"),
            location=SourceLocation.from_dict(one_object(data, "location")),
            source_record_ids=text_tuple(data, "source_record_ids"),
            confidence=confidence(data, "confidence"),
            provenance=provenance(data),
        )

    def to_dict(self) -> JSONObject:
        return {
            "confidence": self.confidence,
            "confidence_band": confidence_band(self.confidence),
            "location": self.location.to_dict(),
            "output_id": redact_text(self.output_id),
            "output_type": redact_text(self.output_type),
            "provenance": [item.to_dict() for item in self.provenance],
            "source_record_ids": [*self.source_record_ids],
            "template_id": redact_text(self.template_id),
        }


def reusable_entries(data: JSONObject) -> tuple[ReusableDataEntry, ...]:
    return tuple(ReusableDataEntry.from_dict(item) for item in objects(data, "entries"))


def downstream_outputs(data: JSONObject) -> tuple[DownstreamOutput, ...]:
    return tuple(DownstreamOutput.from_dict(item) for item in objects(data, "downstream_outputs"))
