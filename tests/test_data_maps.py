from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest

from office_core_plugin.data_maps import DataDictionary, DataDictionaryStore, SourceSelectionResult
from office_core_plugin.registry_models import (
    CandidateFile,
    DownstreamOutput,
    OwnerConfirmationItem,
    OwnerConfirmationState,
    ProvenanceRecord,
    ProvenanceSource,
    RegistryError,
    ReusableDataEntry,
    SourceLocation,
    SourceRecord,
    TemplateClassification,
)

if TYPE_CHECKING:
    from pathlib import Path

OBSERVED_AT: Final = "2026-06-29T11:00:00Z"


def _omits(value: str, forbidden_values: tuple[str, ...]) -> bool:
    return all(forbidden_value not in value for forbidden_value in forbidden_values)


def _provenance(content: str, confidence: float) -> ProvenanceRecord:
    return ProvenanceRecord.from_inspected_content(
        ProvenanceSource(
            source_type="local_fixture",
            source_uri="state://fixture/customer.yml",
            observed_at=OBSERVED_AT,
            method="unit_fixture",
        ),
        content=content,
        confidence=confidence,
    )


def _source_record(
    record_id: str,
    candidate_id: str,
    confidence: float,
    mtime: str,
    template_id: str = "tpl-customer",
) -> SourceRecord:
    return SourceRecord(
        record_id=record_id,
        template_id=template_id,
        candidate=CandidateFile(
            candidate_id=candidate_id,
            location=SourceLocation(
                uri=f"state://fixture/{candidate_id}.docx",
                label=f"{candidate_id}.docx",
                location_type="plugin_state",
            ),
            modified_at=mtime,
        ),
        classification=TemplateClassification.SIMILAR,
        confidence=confidence,
        provenance=(_provenance(candidate_id, confidence),),
    )


def test_conflicting_low_confidence_sources_need_owner_confirmation() -> None:
    # Given: two conflicting records where the newest modified file has weak confidence.
    older = _source_record("source-a", "candidate-a", 0.79, "2026-06-01T00:00:00Z")
    newer = _source_record("source-b", "candidate-b", 0.62, "2026-06-28T00:00:00Z")

    # When: latest/main source selection is evaluated without an owner decision.
    result = SourceSelectionResult.from_candidates(
        template_id="tpl-customer",
        source_records=(older, newer),
        owner_confirmations=(),
    )

    # Then: no mtime-only selection occurs and owner confirmation is created.
    assert result.status == "needs_owner_confirmation"
    assert result.selected_record is None
    assert result.owner_confirmation is not None
    assert result.owner_confirmation.candidate_ids == ("candidate-a", "candidate-b")


def test_owner_confirmation_selects_matching_source_record() -> None:
    # Given: conflicting records and an owner decision naming the older candidate.
    older = _source_record("source-a", "candidate-a", 0.71, "2026-06-01T00:00:00Z")
    newer = _source_record("source-b", "candidate-b", 0.70, "2026-06-28T00:00:00Z")
    unresolved = SourceSelectionResult.from_candidates(
        template_id="tpl-customer",
        source_records=(older, newer),
        owner_confirmations=(),
    )
    confirmation = unresolved.owner_confirmation.confirm("candidate-a")

    # When: selection is evaluated with the owner-confirmed candidate.
    result = SourceSelectionResult.from_candidates(
        template_id="tpl-customer",
        source_records=(older, newer),
        owner_confirmations=(confirmation,),
    )

    # Then: the owner decision wins over modification time.
    assert result.status == "selected"
    assert result.selected_record == older
    assert result.owner_confirmation == confirmation


def test_forged_confirmation_candidate_list_does_not_select_current_record() -> None:
    # Given: current source records contain the selected ID, but the confirmation did not offer it.
    source = _source_record("source-b", "candidate-b", 0.70, "2026-06-28T00:00:00Z")
    forged = OwnerConfirmationItem(
        confirmation_id="confirm-tpl-customer-main-source",
        template_id="tpl-customer",
        reason="Multiple candidates require owner confirmation.",
        candidate_ids=(),
        state=OwnerConfirmationState.CONFIRMED,
        selected_candidate_id="candidate-b",
        provenance=(_provenance("forged", 0.70),),
    )

    # When: source selection evaluates the forged confirmation against current records.
    result = SourceSelectionResult.from_candidates(
        template_id="tpl-customer",
        source_records=(source,),
        owner_confirmations=(forged,),
    )

    # Then: the forged state cannot produce a selected/success result.
    assert result.status == "needs_owner_confirmation"
    assert result.selected_record is None
    assert result.owner_confirmation is not None


def test_stale_confirmation_for_removed_source_needs_owner_confirmation() -> None:
    # Given: an owner confirmation references a source candidate that no longer exists.
    stale = OwnerConfirmationItem(
        confirmation_id="confirm-tpl-customer-main-source",
        template_id="tpl-customer",
        reason="Multiple candidates require owner confirmation.",
        candidate_ids=("candidate-b",),
        state=OwnerConfirmationState.CONFIRMED,
        selected_candidate_id="candidate-b",
        provenance=(_provenance("stale", 0.70),),
    )

    # When: source selection re-checks confirmations against the current source records.
    result = SourceSelectionResult.from_candidates(
        template_id="tpl-customer",
        source_records=(),
        owner_confirmations=(stale,),
    )

    # Then: stale confirmed state falls back to explicit owner confirmation.
    assert result.status == "needs_owner_confirmation"
    assert result.selected_record is None
    assert result.owner_confirmation is not None


def test_pending_confirmation_with_selected_id_rejected_before_source_selection() -> None:
    # Given: untrusted JSON has a pending state but also includes a selected candidate.
    payload = {
        "candidate_ids": ["candidate-a"],
        "confirmation_id": "confirm-tpl-customer-main-source",
        "provenance": [_provenance("pending", 0.70).to_dict()],
        "reason": "Multiple candidates require owner confirmation.",
        "selected_candidate_id": "candidate-a",
        "state": "pending",
        "template_id": "tpl-customer",
    }

    # When / Then: parsing fails before source selection can treat it as trusted input.
    with pytest.raises(RegistryError, match="selected_candidate_id"):
        OwnerConfirmationItem.from_dict(payload)


def test_high_confidence_source_can_be_selected_without_mtime_tiebreak() -> None:
    # Given: one high-confidence source record.
    source = _source_record("source-a", "candidate-a", 0.83, "2026-06-01T00:00:00Z")

    # When: source selection is evaluated.
    result = SourceSelectionResult.from_candidates(
        template_id="tpl-customer",
        source_records=(source,),
        owner_confirmations=(),
    )

    # Then: the high-confidence source is selected.
    assert result.status == "selected"
    assert result.selected_record == source


def test_single_low_confidence_source_requires_owner_confirmation() -> None:
    # Given: one low-confidence source record.
    source = _source_record("source-a", "candidate-a", 0.61, "2026-06-01T00:00:00Z")

    # When: source selection is evaluated without an owner decision.
    result = SourceSelectionResult.from_candidates("tpl-customer", (source,), ())

    # Then: office-core asks the owner and leaves selected_record empty.
    assert result.status == "needs_owner_confirmation"
    assert result.selected_record is None
    assert result.owner_confirmation is not None
    assert "Low-confidence" in result.owner_confirmation.reason


def test_high_confidence_source_for_other_template_is_not_selected() -> None:
    # Given: stale state has one latest high-confidence source for a different template.
    source = _source_record(
        "source-other",
        "candidate-other",
        0.93,
        "2026-06-28T00:00:00Z",
        template_id="tpl-other",
    )

    # When: the caller asks for main source selection for the requested template.
    result = SourceSelectionResult.from_candidates(
        template_id="tpl-requested",
        source_records=(source,),
        owner_confirmations=(),
    )

    # Then: the mismatched source cannot satisfy the requested template.
    assert result.status == "needs_owner_confirmation"
    assert result.selected_record is None
    assert result.owner_confirmation is not None
    assert result.owner_confirmation.template_id == "tpl-requested"
    assert result.owner_confirmation.candidate_ids == ()


def test_reusable_data_dictionary_preserves_provenance_and_downstream_outputs(
    tmp_path: Path,
) -> None:
    # Given: reusable fields with provenance and downstream output references.
    output = DownstreamOutput(
        output_id="output-summary",
        template_id="tpl-customer",
        output_type="summary",
        location=SourceLocation(
            uri="state://outputs/summary.json",
            label="summary.json",
            location_type="plugin_state",
        ),
        source_record_ids=("source-a",),
        confidence=0.88,
        provenance=(_provenance("summary", 0.88),),
    )
    dictionary = DataDictionary(
        entries=(
            ReusableDataEntry(
                field_id="field-customer-name",
                template_id="tpl-customer",
                field_name="customer_name",
                value="Acme Ltd",
                confidence=0.81,
                provenance=(_provenance("Acme Ltd", 0.81),),
                source_record_ids=("source-a",),
                downstream_output_ids=("output-summary",),
            ),
        ),
        downstream_outputs=(output,),
    )
    store = DataDictionaryStore(tmp_path)

    # When: the dictionary is saved and loaded from plugin-managed state.
    store.save(dictionary)
    parsed = store.load()

    # Then: provenance, confidence, and downstream references round-trip deterministically.
    raw = (tmp_path / "data_dictionary.json").read_text(encoding="utf-8")
    assert json.loads(raw) == dictionary.to_dict()
    assert parsed == dictionary
    assert parsed.entries[0].confidence == 0.81
    assert parsed.entries[0].downstream_output_ids == ("output-summary",)
    assert parsed.entries[0].provenance[0].evidence_hash


def test_reusable_data_entry_public_dict_redacts_nested_secret_values(tmp_path: Path) -> None:
    # Given: a reusable data entry stores nested reusable values from office/cloud sources.
    aws_access_key = "AKIA" + "IOSFODNN7EXAMPLE"
    bearer_value = "abc.def"
    password_value = "customer_password_2bb51a91"  # noqa: S105 - deliberately fake redaction test fixture.
    entry = ReusableDataEntry(
        field_id="field-cloud-account",
        template_id="tpl-cloud",
        field_name="cloud_account",
        value={
            "aws_key": aws_access_key,
            "nested": {"Authorization": f"Bearer {bearer_value}"},
            "rows": [f"password: {password_value}"],
        },
        confidence=0.82,
        provenance=(_provenance("cloud account", 0.82),),
        source_record_ids=("source-cloud",),
        downstream_output_ids=(),
    )
    dictionary = DataDictionary(entries=(entry,), downstream_outputs=())
    store = DataDictionaryStore(tmp_path)
    forbidden_values = (aws_access_key, bearer_value, password_value)

    # When: callers request public JSON while persistence keeps its raw storage contract.
    public_json = json.dumps(dictionary.to_public_dict(), allow_nan=False, sort_keys=True)
    store.save(dictionary)
    persisted_json = (tmp_path / "data_dictionary.json").read_text(encoding="utf-8")

    # Then: public serialization redacts nested values without migrating raw persistence.
    assert _omits(public_json, forbidden_values)
    assert "[REDACTED]" in public_json
    assert not _omits(persisted_json, forbidden_values)


def test_data_dictionary_store_rejects_symlinked_state_root_escaping_base(
    tmp_path: Path,
) -> None:
    # Given: a state_root that is a symlink pointing outside the intended base.
    intended = tmp_path / "intended"
    intended.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    symlinked_root = intended / "state"
    try:
        symlinked_root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    # When / Then: constructing the store raises before any write occurs.
    with pytest.raises(RegistryError, match="symlink_escape"):
        DataDictionaryStore(symlinked_root)
