from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest

from office_core_plugin.registry_models import (
    CandidateFile,
    OwnerConfirmationItem,
    OwnerConfirmationState,
    ProvenanceRecord,
    ProvenanceSource,
    RegistryError,
    SourceLocation,
    SourceRecord,
    TemplateClassification,
    TemplateIdentity,
    TemplateRegistry,
    TemplateRegistryStore,
)

if TYPE_CHECKING:
    from pathlib import Path

OBSERVED_AT: Final = "2026-06-29T10:00:00Z"


def _provenance(content: str = "invoice total") -> ProvenanceRecord:
    return ProvenanceRecord.from_inspected_content(
        ProvenanceSource(
            source_type="local_fixture",
            source_uri="state://fixture/source.docx",
            observed_at=OBSERVED_AT,
            method="unit_fixture",
        ),
        content=content,
        confidence=0.72,
    )


def _template(classification: TemplateClassification) -> TemplateIdentity:
    return TemplateIdentity(
        template_id="tpl-invoice",
        name="Invoice",
        version="2026.06",
        classification=classification,
        confidence=0.91,
        provenance=(_provenance("template identity"),),
    )


@pytest.mark.parametrize("classification", list(TemplateClassification))
def test_template_identity_round_trips_for_all_classifications(
    classification: TemplateClassification,
) -> None:
    # Given: a template identity using one supported classification.
    template = _template(classification)

    # When: it crosses the JSON boundary and is parsed back.
    parsed = TemplateIdentity.from_dict(template.to_dict())

    # Then: the classification, confidence, and provenance survive unchanged.
    assert parsed == template
    assert parsed.to_dict()["classification"] == classification.value
    assert parsed.provenance[0].confidence == 0.72


def test_malformed_registry_models_are_rejected() -> None:
    # Given / When / Then: required text, confidence, enum, and provenance fields are parsed once.
    with pytest.raises(RegistryError, match="template_id"):
        TemplateIdentity.from_dict(
            {
                "template_id": "",
                "name": "Invoice",
                "version": "1",
                "classification": "same",
                "confidence": 0.9,
                "provenance": [_provenance().to_dict()],
            },
        )

    with pytest.raises(RegistryError, match="confidence"):
        ProvenanceRecord.from_dict(
            {
                "source_type": "fixture",
                "source_uri": "state://x",
                "observed_at": OBSERVED_AT,
                "method": "unit",
                "evidence_hash": "a" * 64,
                "confidence": 1.01,
            },
        )

    with pytest.raises(RegistryError, match="classification"):
        TemplateIdentity.from_dict(
            {
                "template_id": "tpl",
                "name": "Invoice",
                "version": "1",
                "classification": "latest",
                "confidence": 0.9,
                "provenance": [_provenance().to_dict()],
            },
        )

    with pytest.raises(RegistryError, match="evidence_hash"):
        ProvenanceRecord.from_dict(
            {
                "source_type": "fixture",
                "source_uri": "state://x",
                "observed_at": OBSERVED_AT,
                "method": "unit",
                "confidence": 0.7,
            },
        )


def test_non_hex_evidence_hash_is_rejected() -> None:
    # Given: a provenance payload with the right digest length but non-hex content.
    payload = {
        "source_type": "fixture",
        "source_uri": "state://x",
        "observed_at": OBSERVED_AT,
        "method": "unit",
        "evidence_hash": "z" * 64,
        "confidence": 0.7,
    }

    # When / Then: boundary parsing rejects it as malformed provenance.
    with pytest.raises(RegistryError, match="evidence_hash"):
        ProvenanceRecord.from_dict(payload)


def test_invalid_classification_diagnostic_redacts_untrusted_secret_text() -> None:
    # Given: an invalid enum value containing untrusted authorization text.
    token = "sk-test-" + "SECRET-1234567890"
    raw_authorization = f"Authorization: Bearer {token}"
    payload = {
        "template_id": "tpl",
        "name": "Invoice",
        "version": "1",
        "classification": f"latest {raw_authorization}",
        "confidence": 0.9,
        "provenance": [_provenance().to_dict()],
    }

    # When: the payload is parsed at the registry boundary.
    with pytest.raises(RegistryError) as exc_info:
        TemplateIdentity.from_dict(payload)

    # Then: diagnostics identify the enum failure without leaking the raw secret.
    assert "classification" in str(exc_info.value)
    assert raw_authorization not in str(exc_info.value)
    assert raw_authorization not in repr(exc_info.value)


def test_owner_confirmation_serializes_round_trip() -> None:
    # Given: an unresolved owner confirmation item for ambiguous source records.
    item = OwnerConfirmationItem(
        confirmation_id="confirm-tpl-invoice-main",
        template_id="tpl-invoice",
        reason="Multiple candidates below high confidence require owner choice.",
        candidate_ids=("candidate-a", "candidate-b"),
        state=OwnerConfirmationState.PENDING,
        selected_candidate_id=None,
        provenance=(_provenance("conflict"),),
    )

    # When: the item is serialized and parsed back.
    parsed = OwnerConfirmationItem.from_dict(item.to_dict())

    # Then: all owner state remains stable for later confirmation.
    assert parsed == item
    assert parsed.to_dict()["state"] == "pending"


def test_registry_store_writes_deterministic_json_under_supplied_state_root(
    tmp_path: Path,
) -> None:
    # Given: a caller-supplied plugin state root and a populated registry.
    store = TemplateRegistryStore(tmp_path)
    registry = TemplateRegistry(
        templates=(_template(TemplateClassification.RULE_BASED),),
        source_records=(
            SourceRecord(
                record_id="source-a",
                template_id="tpl-invoice",
                candidate=CandidateFile(
                    candidate_id="candidate-a",
                    location=SourceLocation(
                        uri="state://fixture/invoice.docx",
                        label="invoice.docx",
                        location_type="plugin_state",
                    ),
                    modified_at="2026-06-28T09:00:00Z",
                ),
                classification=TemplateClassification.RULE_BASED,
                confidence=0.86,
                provenance=(_provenance("candidate"),),
            ),
        ),
        owner_confirmations=(),
    )

    # When: the registry is saved and loaded.
    store.save(registry)
    parsed = store.load()

    # Then: the file is deterministic JSON in plugin-managed state and round-trips.
    raw = (tmp_path / "template_registry.json").read_text(encoding="utf-8")
    assert json.loads(raw) == registry.to_dict()
    assert raw == json.dumps(registry.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    assert parsed == registry


def test_registry_store_requires_explicit_state_root() -> None:
    # Given / When / Then: runtime storage cannot silently default to the repo or cwd.
    with pytest.raises(RegistryError, match="state_root"):
        TemplateRegistryStore(None)
