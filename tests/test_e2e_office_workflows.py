from __future__ import annotations

from typing import TYPE_CHECKING

from office_core_plugin.e2e_workflows import build_ambiguous_latest_main_probe

if TYPE_CHECKING:
    from pathlib import Path


def test_ambiguous_latest_main_fixture_emits_owner_questions(tmp_path: Path) -> None:
    # Given: a workflow fixture with two plausible latest/main sources.
    probe = build_ambiguous_latest_main_probe(tmp_path)

    # When: the source selection result is serialized for the E2E failure scenario.
    selection = probe["source_selection"]

    # Then: it asks the owner instead of auto-selecting either candidate.
    assert selection["status"] == "needs_owner_confirmation"
    assert selection["selected_record"] is None
    assert selection["owner_confirmation"]["state"] == "pending"
    assert selection["owner_confirmation_questions"] == [
        "Which source should office-core treat as the main source for tpl-quarterly-update?",
    ]
