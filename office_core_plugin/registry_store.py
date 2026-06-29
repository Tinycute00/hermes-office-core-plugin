from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self

from .registry_base import RegistryError, objects
from .registry_core import (
    OwnerConfirmationItem,
    SourceRecord,
    TemplateIdentity,
)

if TYPE_CHECKING:
    from .handler_contract import JSONObject

REGISTRY_FILENAME: Final = "template_registry.json"


@dataclass(frozen=True, slots=True)
class TemplateRegistry:
    templates: tuple[TemplateIdentity, ...]
    source_records: tuple[SourceRecord, ...]
    owner_confirmations: tuple[OwnerConfirmationItem, ...]

    @classmethod
    def from_dict(cls, data: JSONObject) -> Self:
        return cls(
            templates=tuple(
                TemplateIdentity.from_dict(item) for item in objects(data, "templates")
            ),
            source_records=tuple(
                SourceRecord.from_dict(item) for item in objects(data, "source_records")
            ),
            owner_confirmations=tuple(
                OwnerConfirmationItem.from_dict(item)
                for item in objects(data, "owner_confirmations")
            ),
        )

    def to_dict(self) -> JSONObject:
        return {
            "owner_confirmations": [item.to_dict() for item in self.owner_confirmations],
            "source_records": [item.to_dict() for item in self.source_records],
            "templates": [item.to_dict() for item in self.templates],
        }


class TemplateRegistryStore:
    def __init__(self, state_root: Path | str | None) -> None:
        if state_root is None:
            field = "state_root"
            detail = "explicit plugin-managed path required"
            raise RegistryError(field, detail)
        self._path = Path(state_root) / REGISTRY_FILENAME

    def load(self) -> TemplateRegistry:
        if not self._path.exists():
            return TemplateRegistry(templates=(), source_records=(), owner_confirmations=())
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            field = "template_registry"
            detail = "must be an object"
            raise RegistryError(field, detail)
        return TemplateRegistry.from_dict(raw)

    def save(self, registry: TemplateRegistry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(dump_json(registry.to_dict()), encoding="utf-8")


def dump_json(data: JSONObject) -> str:
    return json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
