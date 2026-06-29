from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
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

if TYPE_CHECKING:
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
        confirmed = _confirmed_selection(template_id, source_records, owner_confirmations)
        if confirmed is not None:
            return cls("selected", confirmed.record, confirmed.confirmation)
        if len(source_records) == 1 and source_records[0].confidence >= CONFIDENCE_HIGH_MIN:
            return cls("selected", source_records[0], None)
        return cls(
            "needs_owner_confirmation",
            None,
            _pending_confirmation(template_id, source_records),
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


class DataDictionaryStore:
    def __init__(self, state_root: Path | str | None) -> None:
        if state_root is None:
            field = "state_root"
            detail = "explicit plugin-managed path required"
            raise RegistryError(field, detail)
        self._path = Path(state_root) / DATA_DICTIONARY_FILENAME

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
            selected = _selected_record(source_records, confirmation.selected_candidate_id)
            if selected is not None:
                return ConfirmedSelection(selected, confirmation)
    return None


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
        reason=(
            "Multiple source candidates require owner confirmation before "
            "latest/main selection."
        ),
        candidate_ids=candidate_ids,
        state=OwnerConfirmationState.PENDING,
        selected_candidate_id=None,
        provenance=provenance,
    )
