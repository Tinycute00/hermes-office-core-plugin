from __future__ import annotations

import re

from office_hooks.intent import SCHEDULE_PATTERN, object_hints, strip_code


STOP_CORRECTION_PREFIX = "The source-free Office intake final reply was not canonical."
CANONICAL_SOURCE_FREE_REPLY_PATTERN = re.compile(
    r"\A意圖：(查找|分析|檢查|建立|更新|整合|排程)｜"
    r"物件：(Excel|Word|PowerPoint|PDF|跨檔案)｜權限：唯讀｜"
    r"檢查：(快速|加強|完整)\n"
    r"(Excel|Word|PowerPoint|PDF|跨檔案) 來源檔或資料夾路徑是什麼？\Z"
)


def source_free_intent(prompt: str) -> str:
    cleaned = strip_code(prompt)
    if SCHEDULE_PATTERN.search(cleaned):
        return "排程"
    if re.search(r"\b(?:update|edit|change|fix|format)\b|更新|編輯|修改|修正|格式", cleaned, re.I):
        return "更新"
    if re.search(r"\b(?:create|make|write)\b|建立|製作|撰寫", cleaned, re.I):
        return "建立"
    if re.search(r"\b(?:find|search)\b|找|搜尋|查找", cleaned, re.I):
        return "查找"
    if re.search(r"\b(?:analy[sz]e|compare)\b|分析|比較", cleaned, re.I):
        return "分析"
    return "檢查"


def source_free_object(prompt: str) -> str:
    hints = object_hints(strip_code(prompt))
    if len(hints) == 1:
        return hints[0]
    return "跨檔案"


def source_free_check(prompt: str) -> str:
    cleaned = strip_code(prompt)
    if re.search(r"\b(?:complete|full)\b|完整", cleaned, re.I):
        return "完整"
    if re.search(r"\b(?:enhanced|strong)\b|加強", cleaned, re.I):
        return "加強"
    return "快速"


def expected_source_free_reply(prompt: str) -> str:
    object_name = source_free_object(prompt)
    return (
        f"意圖：{source_free_intent(prompt)}｜物件：{object_name}｜權限：唯讀｜"
        f"檢查：{source_free_check(prompt)}\n"
        f"{object_name} 來源檔或資料夾路徑是什麼？"
    )


def source_free_intake_context(prompt: str) -> str:
    expected = expected_source_free_reply(prompt)
    return (
        "<office-os-source-free-intake>\n"
        "<required-first-user-visible-contract>\n"
        "The first user-visible message MUST be one compact classification-and-skill-rationale sentence, "
        "before any tool call or skill load. It must classify the Office workflow, state the read-only "
        "boundary, name office-os with or without the $ invocation sigil, and explain why it applies.\n"
        "</required-first-user-visible-contract>\n"
        "Emit no other preamble, plan, progress, or tool-activity message.\n"
        "FINAL USER-VISIBLE REPLY MUST BE EXACTLY TWO NON-EMPTY LINES. "
        "THE FOLLOWING BLOCK IS AUTHORITATIVE:\n"
        "<required-final-reply>\n"
        f"{expected}\n"
        "</required-final-reply>\n"
        "Copy the two lines inside <required-final-reply> verbatim as the entire final reply; "
        "do not reconstruct or paraphrase them from skill text.\n"
        "The Stop hook validates this exact final reply once and requests one bounded correction "
        "when a source-question reply is paraphrased.\n"
        "SKILL.md is ASCII-only and should be loaded exactly once with the host's normal text reader; "
        "do not reload it. After a source is named, read Markdown references with explicit UTF-8 on Windows PowerShell "
        "(Get-Content -Raw -Encoding UTF8).\n\n"
        "Do not inspect or alter Office data. Do not call `office_os.py`, OfficeCLI, or an MCP tool; "
        "do not create workspace state, a candidate, an output, or a schedule. Wait for the user to name a local source path or folder.\n\n"
        "Loading this skill to honor an explicit $office-os invocation is allowed, but do not load workflow references "
        "or inspect Office data until the source is named. The final reply must remain the supplied two-line envelope after any allowed skill load.\n"
        "</office-os-source-free-intake>"
    )


def normalized_message(message: str) -> str:
    return message.strip().replace("\r\n", "\n").replace("\r", "\n")


def canonical_source_free_reply(message: str) -> bool:
    normalized = normalized_message(message)
    match = CANONICAL_SOURCE_FREE_REPLY_PATTERN.fullmatch(normalized)
    return bool(match and match.group(2) == match.group(4))
