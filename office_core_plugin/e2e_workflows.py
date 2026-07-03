from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .data_maps import DataDictionary, DataDictionaryStore, SourceSelectionResult
from .e2e_fixtures import (
    ambiguous_source_selection,
    approved_reusable_data_to_deck,
    bridge_plan,
    data_dictionary,
    denied_operation,
    external_send_preview,
    local_file_search,
    messy_spreadsheet_data_package,
    monthly_report_template_update,
    prepare_fixture_files,
    read_operation,
    source_record,
    template_fixture,
)
from .registry_models import TemplateRegistry
from .registry_store import TemplateRegistryStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .handler_contract import JSONObject


@dataclass(frozen=True, slots=True)
class E2EPaths:
    artifact_root: Path
    state_root: Path
    fixture_root: Path


@dataclass(frozen=True, slots=True)
class E2EWorkflowError(Exception):
    field: str
    detail: str

    def __str__(self) -> str:
        return f"{self.field}: {self.detail}"


def build_representative_workflow_probe(paths: E2EPaths) -> JSONObject:
    prepare_fixture_files(paths.fixture_root)
    template = template_fixture()
    source = source_record("source-main", "candidate-main", "main-data.xlsx", 0.87)
    registry = TemplateRegistry(
        templates=(template,),
        source_records=(source,),
        owner_confirmations=(),
    )
    dictionary = data_dictionary(source)
    _write_registry_artifacts(paths.state_root, registry, dictionary)
    draft_path = _write_draft(paths.artifact_root)
    selected = SourceSelectionResult.from_candidates(
        "tpl-quarterly-update",
        registry.source_records,
        registry.owner_confirmations,
    )
    return {
        "scenario": "representative-office-workflows",
        "template_update": _template_update(template.to_dict(), draft_path),
        "messy_data_package": _messy_data_package(dictionary.to_public_dict(), selected),
        "reusable_data_application": dictionary.to_public_dict(),
        "owner_confirmation_workflow": build_ambiguous_latest_main_probe(
            paths.artifact_root / "ambiguous",
        )["source_selection"],
        "bridge_handoff_plan": bridge_plan(),
        "local_candidate_file_search": local_file_search(paths.fixture_root),
        "policy_denied_operation": denied_operation(),
        "operation_records": [read_operation(), denied_operation()],
        "artifact_paths": _artifact_paths(paths.state_root, draft_path),
        "office_correctness_fixtures": list(build_office_correctness_workflows()),
        "no_real_runtime_mutation": True,
        "fixture_root": str(paths.fixture_root),
        "state_root": str(paths.state_root),
    }


def build_office_correctness_workflows() -> tuple[JSONObject, ...]:
    return (
        monthly_report_template_update(),
        messy_spreadsheet_data_package(),
        approved_reusable_data_to_deck(),
        external_send_preview(),
    )


def build_ambiguous_latest_main_probe(artifact_root: Path | str) -> JSONObject:
    root = Path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    payload: JSONObject = {
        "scenario": "ambiguous-latest-main-data",
        "source_selection": ambiguous_source_selection(),
    }
    _write_json(root / "ambiguous-latest-main.json", payload)
    return payload


def write_probe_artifact(scenario: str, paths: E2EPaths, artifact: Path | str) -> JSONObject:
    match scenario:
        case "happy":
            payload = build_representative_workflow_probe(paths)
        case "ambiguous":
            payload = build_ambiguous_latest_main_probe(paths.artifact_root)
        case _:
            field = "scenario"
            detail = f"unsupported scenario {scenario!r}"
            raise E2EWorkflowError(field, detail)
    artifact_path = Path(artifact)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(artifact_path, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("happy", "ambiguous"), required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--fixture-root", required=True)
    parser.add_argument("--artifact", required=True)
    args = parser.parse_args(argv)
    paths = E2EPaths(
        artifact_root=Path(args.artifact_root),
        state_root=Path(args.state_root),
        fixture_root=Path(args.fixture_root),
    )
    payload = write_probe_artifact(args.scenario, paths, Path(args.artifact))
    receipt = {
        "artifact": str(Path(args.artifact)),
        "scenario": payload["scenario"],
        "top_level_keys": sorted(payload),
    }
    sys.stdout.write(json.dumps(receipt, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


def _write_registry_artifacts(
    state_root: Path,
    registry: TemplateRegistry,
    dictionary: DataDictionary,
) -> None:
    TemplateRegistryStore(state_root).save(registry)
    DataDictionaryStore(state_root).save(dictionary)


def _write_draft(artifact_root: Path) -> Path:
    artifact_root.mkdir(parents=True, exist_ok=True)
    draft_path = artifact_root / "template-update-draft.json"
    _write_json(
        draft_path,
        {
            "status": "draft_created",
            "external_side_effect": False,
            "template_id": "tpl-quarterly-update",
        },
    )
    return draft_path


def _template_update(template: JSONObject, draft_path: Path) -> JSONObject:
    return {
        "template": template,
        "draft_artifact": str(draft_path),
        "effect": "draft_only",
    }


def _messy_data_package(dictionary: JSONObject, selected: SourceSelectionResult) -> JSONObject:
    record = selected.selected_record
    return {
        "source_selection": {
            "status": selected.status,
            "selected_record": None if record is None else record.to_dict(),
            "owner_confirmation": None
            if selected.owner_confirmation is None
            else selected.owner_confirmation.to_dict(),
        },
        "data_dictionary": dictionary,
    }


def _artifact_paths(state_root: Path, draft_path: Path) -> JSONObject:
    return {
        "template_registry": str(state_root / "template_registry.json"),
        "data_dictionary": str(state_root / "data_dictionary.json"),
        "draft_document": str(draft_path),
    }


def _write_json(path: Path, payload: JSONObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
