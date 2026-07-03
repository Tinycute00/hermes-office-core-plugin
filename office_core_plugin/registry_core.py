from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, assert_never

from .operation_policy import confidence_band
from .redaction import redact_text
from .registry_base import (
    OwnerConfirmationState,
    ProvenanceSource,
    RegistryError,
    TemplateClassification,
    classification,
    confidence,
    confirmation_state,
    hash_text,
    objects,
    one_object,
    optional_text,
    text,
    text_tuple,
    validate_confidence,
)

if TYPE_CHECKING:
    from .handler_contract import JSONObject


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    source_type: str
    source_uri: str
    observed_at: str
    method: str
    evidence_hash: str
    confidence: float

    @classmethod
    def from_inspected_content(
        cls,
        source: ProvenanceSource,
        content: str,
        confidence: float,
    ) -> Self:
        return cls(
            source_type=source.source_type,
            source_uri=source.source_uri,
            observed_at=source.observed_at,
            method=source.method,
            evidence_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            confidence=confidence,
        )

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        return cls(
            source_type=text(data, "source_type"),
            source_uri=text(data, "source_uri"),
            observed_at=text(data, "observed_at"),
            method=text(data, "method"),
            evidence_hash=hash_text(data, "evidence_hash"),
            confidence=confidence(data, "confidence"),
        )

    def to_dict(self) -> JSONObject:
        validate_confidence(self.confidence)
        return {
            "confidence": self.confidence,
            "confidence_band": confidence_band(self.confidence),
            "evidence_hash": self.evidence_hash,
            "method": redact_text(self.method),
            "observed_at": redact_text(self.observed_at),
            "source_type": redact_text(self.source_type),
            "source_uri": redact_text(self.source_uri),
        }


@dataclass(frozen=True, slots=True)
class TemplateIdentity:
    template_id: str
    name: str
    version: str
    classification: TemplateClassification
    confidence: float
    provenance: tuple[ProvenanceRecord, ...]

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        return cls(
            template_id=text(data, "template_id"),
            name=text(data, "name"),
            version=text(data, "version"),
            classification=classification(data, "classification"),
            confidence=confidence(data, "confidence"),
            provenance=provenance(data),
        )

    def to_dict(self) -> JSONObject:
        return {
            "classification": self.classification.value,
            "confidence": self.confidence,
            "confidence_band": confidence_band(self.confidence),
            "name": redact_text(self.name),
            "provenance": [item.to_dict() for item in self.provenance],
            "template_id": redact_text(self.template_id),
            "version": redact_text(self.version),
        }


@dataclass(frozen=True, slots=True)
class SourceLocation:
    uri: str
    label: str
    location_type: str

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        return cls(text(data, "uri"), text(data, "label"), text(data, "location_type"))

    def to_dict(self) -> JSONObject:
        return {
            "label": redact_text(self.label),
            "location_type": redact_text(self.location_type),
            "uri": redact_text(self.uri),
        }


@dataclass(frozen=True, slots=True)
class CandidateFile:
    candidate_id: str
    location: SourceLocation
    modified_at: str | None = None

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        location = one_object(data, "location")
        return cls(
            candidate_id=text(data, "candidate_id"),
            location=SourceLocation.from_dict(location),
            modified_at=optional_text(data, "modified_at"),
        )

    def to_dict(self) -> JSONObject:
        data: JSONObject = {
            "candidate_id": redact_text(self.candidate_id),
            "location": self.location.to_dict(),
        }
        if self.modified_at is not None:
            data["modified_at"] = redact_text(self.modified_at)
        return data


@dataclass(frozen=True, slots=True)
class SourceRecord:
    record_id: str
    template_id: str
    candidate: CandidateFile
    classification: TemplateClassification
    confidence: float
    provenance: tuple[ProvenanceRecord, ...]

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        return cls(
            record_id=text(data, "record_id"),
            template_id=text(data, "template_id"),
            candidate=CandidateFile.from_dict(one_object(data, "candidate")),
            classification=classification(data, "classification"),
            confidence=confidence(data, "confidence"),
            provenance=provenance(data),
        )

    def to_dict(self) -> JSONObject:
        return {
            "candidate": self.candidate.to_dict(),
            "classification": self.classification.value,
            "confidence": self.confidence,
            "confidence_band": confidence_band(self.confidence),
            "provenance": [item.to_dict() for item in self.provenance],
            "record_id": redact_text(self.record_id),
            "template_id": redact_text(self.template_id),
        }


@dataclass(frozen=True, slots=True)
class OwnerConfirmationItem:
    confirmation_id: str
    template_id: str
    reason: str
    candidate_ids: tuple[str, ...]
    state: OwnerConfirmationState
    selected_candidate_id: str | None
    provenance: tuple[ProvenanceRecord, ...]

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        candidate_ids = text_tuple(data, "candidate_ids")
        state = confirmation_state(data, "state")
        selected_candidate_id = optional_text(data, "selected_candidate_id")
        _validate_owner_confirmation_selection(state, candidate_ids, selected_candidate_id)
        return cls(
            confirmation_id=text(data, "confirmation_id"),
            template_id=text(data, "template_id"),
            reason=text(data, "reason"),
            candidate_ids=candidate_ids,
            state=state,
            selected_candidate_id=selected_candidate_id,
            provenance=provenance(data),
        )

    def confirm(self, candidate_id: str) -> OwnerConfirmationItem:
        if candidate_id not in self.candidate_ids:
            field = "selected_candidate_id"
            detail = "must reference a candidate"
            raise RegistryError(field, detail)
        return OwnerConfirmationItem(
            self.confirmation_id,
            self.template_id,
            self.reason,
            self.candidate_ids,
            OwnerConfirmationState.CONFIRMED,
            candidate_id,
            self.provenance,
        )

    def to_dict(self) -> JSONObject:
        return {
            "candidate_ids": [redact_text(item) for item in self.candidate_ids],
            "confirmation_id": redact_text(self.confirmation_id),
            "provenance": [item.to_dict() for item in self.provenance],
            "reason": redact_text(self.reason),
            "selected_candidate_id": _redact_optional(self.selected_candidate_id),
            "state": self.state.value,
            "template_id": redact_text(self.template_id),
        }


def provenance(data: JSONObject) -> tuple[ProvenanceRecord, ...]:
    return tuple(ProvenanceRecord.from_dict(item) for item in objects(data, "provenance"))


def _redact_optional(value: str | None) -> str | None:
    if value is None:
        return None
    return redact_text(value)


def _validate_owner_confirmation_selection(
    state: OwnerConfirmationState,
    candidate_ids: tuple[str, ...],
    selected_candidate_id: str | None,
) -> None:
    match state:
        case OwnerConfirmationState.CONFIRMED:
            if selected_candidate_id in candidate_ids:
                return
            field = "selected_candidate_id"
            detail = "must reference a candidate for confirmed owner confirmation"
            raise RegistryError(field, detail)
        case OwnerConfirmationState.PENDING:
            if selected_candidate_id is None:
                return
            field = "selected_candidate_id"
            detail = "must be absent until owner confirmation is confirmed"
            raise RegistryError(field, detail)
        case unreachable:
            assert_never(unreachable)
