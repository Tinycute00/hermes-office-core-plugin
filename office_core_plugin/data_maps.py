from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self

from .operation_policy import CONFIDENCE_HIGH_MIN
from .registry_base import objects
from .registry_models import (
    DownstreamOutput,
    OwnerConfirmationItem,
    OwnerConfirmationState,
    RegistryError,
    ReusableDataEntry,
    SourceRecord,
)
from .registry_store import dump_json
from .safe_path import validate_state_root

if TYPE_CHECKING:
    from pathlib import Path

    from .handler_contract import JSONObject

DATA_DICTIONARY_FILENAME: Final = "data_dictionary.json"


@dataclass(frozen=True, slots=True)
class SourceSelectionResult:
    status: str
    selected_record: SourceRecord | None
    owner_confirmation: OwnerConfirmationItem | None

    @classmethod
    def from_candidates(
        cls,
        template_id: str,
        source_records: tuple[SourceRecord, ...],
        owner_confirmations: tuple[OwnerConfirmationItem, ...],
    ) -> Self:
        matching_records = tuple(
            record for record in source_records if record.template_id == template_id
        )
        confirmed = _confirmed_selection(template_id, matching_records, owner_confirmations)
        if confirmed is not None:
            return cls("selected", confirmed.record, confirmed.confirmation)
        if _has_invalid_confirmed_selection(
            template_id,
            matching_records,
            owner_confirmations,
        ):
            return cls(
                "needs_owner_confirmation",
                None,
                _pending_confirmation(template_id, matching_records),
            )
        if len(matching_records) == 1 and matching_records[0].confidence >= CONFIDENCE_HIGH_MIN:
            return cls("selected", matching_records[0], None)
        return cls(
            "needs_owner_confirmation",
            None,
            _pending_confirmation(template_id, matching_records),
        )


@dataclass(frozen=True, slots=True)
class ConfirmedSelection:
    record: SourceRecord
    confirmation: OwnerConfirmationItem


@dataclass(frozen=True, slots=True)
class DataDictionary:
    entries: tuple[ReusableDataEntry, ...]
    downstream_outputs: tuple[DownstreamOutput, ...]

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        return cls(
            entries=tuple(
                ReusableDataEntry.from_dict(item) for item in objects(data, "entries")
            ),
            downstream_outputs=tuple(
                DownstreamOutput.from_dict(item) for item in objects(data, "downstream_outputs")
            ),
        )

    def to_dict(self) -> JSONObject:
        return {
            "downstream_outputs": [item.to_dict() for item in self.downstream_outputs],
            "entries": [item.to_dict() for item in self.entries],
        }

    def to_public_dict(self) -> JSONObject:
        return {
            "downstream_outputs": [item.to_public_dict() for item in self.downstream_outputs],
            "entries": [item.to_public_dict() for item in self.entries],
        }


class DataDictionaryStore:
    def __init__(self, state_root: Path | str | None) -> None:
        if state_root is None:
            field = "state_root"
            detail = "explicit plugin-managed path required"
            raise RegistryError(field, detail)
        self._path = validate_state_root(state_root) / DATA_DICTIONARY_FILENAME

    def load(self) -> DataDictionary:
        if not self._path.exists():
            return DataDictionary(entries=(), downstream_outputs=())
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            field = "data_dictionary"
            detail = "must be an object"
            raise RegistryError(field, detail)
        return DataDictionary.from_dict(raw)

    def save(self, dictionary: DataDictionary) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(dump_json(dictionary.to_dict()), encoding="utf-8")


def _confirmed_selection(
    template_id: str,
    source_records: tuple[SourceRecord, ...],
    confirmations: tuple[OwnerConfirmationItem, ...],
) -> ConfirmedSelection | None:
    for confirmation in confirmations:
        if _matches_template(confirmation, template_id):
            selected_candidate_id = confirmation.selected_candidate_id
            if selected_candidate_id not in confirmation.candidate_ids:
                continue
            selected = _selected_record(source_records, selected_candidate_id)
            if selected is not None:
                return ConfirmedSelection(selected, confirmation)
    return None


def _has_invalid_confirmed_selection(
    template_id: str,
    source_records: tuple[SourceRecord, ...],
    confirmations: tuple[OwnerConfirmationItem, ...],
) -> bool:
    for confirmation in confirmations:
        if not _matches_template(confirmation, template_id):
            continue
        selected_candidate_id = confirmation.selected_candidate_id
        if selected_candidate_id not in confirmation.candidate_ids:
            return True
        if _selected_record(source_records, selected_candidate_id) is None:
            return True
    return False


def _matches_template(confirmation: OwnerConfirmationItem, template_id: str) -> bool:
    return (
        confirmation.template_id == template_id
        and confirmation.state is OwnerConfirmationState.CONFIRMED
        and confirmation.selected_candidate_id is not None
    )


def _selected_record(
    source_records: tuple[SourceRecord, ...],
    candidate_id: str | None,
) -> SourceRecord | None:
    if candidate_id is None:
        return None
    for record in source_records:
        if record.candidate.candidate_id == candidate_id:
            return record
    return None


def _pending_confirmation(
    template_id: str,
    source_records: tuple[SourceRecord, ...],
) -> OwnerConfirmationItem:
    candidate_ids = tuple(record.candidate.candidate_id for record in source_records)
    provenance = tuple(record.provenance[0] for record in source_records if record.provenance)
    return OwnerConfirmationItem(
        confirmation_id=f"confirm-{template_id}-main-source",
        template_id=template_id,
        reason=_confirmation_reason(source_records),
        candidate_ids=candidate_ids,
        state=OwnerConfirmationState.PENDING,
        selected_candidate_id=None,
        provenance=provenance,
    )


def _confirmation_reason(source_records: tuple[SourceRecord, ...]) -> str:
    if len(source_records) > 1:
        return (
            "Multiple source candidates require owner confirmation before "
            "latest/main selection."
        )
    if not source_records:
        return (
            "No current source candidates are available; owner confirmation "
            "is required before latest/main selection."
        )
    source = source_records[0]
    if source.confidence < CONFIDENCE_HIGH_MIN:
        return (
            "Low-confidence source candidate requires owner confirmation before "
            "latest/main selection."
        )
    return (
        "Owner confirmation is required before the selected latest/main source "
        "can be reused."
    )
