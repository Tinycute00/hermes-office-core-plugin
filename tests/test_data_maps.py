from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from office_core_plugin.data_maps import DataDictionary, DataDictionaryStore, SourceSelectionResult
from office_core_plugin.registry_models import (
    CandidateFile,
    DownstreamOutput,
    ProvenanceRecord,
    ProvenanceSource,
    ReusableDataEntry,
    SourceLocation,
    SourceRecord,
    TemplateClassification,
)

if TYPE_CHECKING:
    from pathlib import Path

OBSERVED_AT: Final = "2026-06-29T11:00:00Z"


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
) -> SourceRecord:
    return SourceRecord(
        record_id=record_id,
        template_id="tpl-customer",
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
