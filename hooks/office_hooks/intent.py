from __future__ import annotations

from pathlib import Path
import re


SUPPORTED_EXTENSIONS = {
    ".xlsx": "Excel",
    ".xls": "Excel",
    ".xlsm": "Excel",
    ".docx": "Word",
    ".doc": "Word",
    ".docm": "Word",
    ".pptx": "PowerPoint",
    ".ppt": "PowerPoint",
    ".pptm": "PowerPoint",
    ".pdf": "PDF",
}

OBJECT_PATTERNS = {
    "Excel": re.compile(
        r"\b(?:excel|spreadsheet|workbook|worksheet|sheet|xlsx?|xlsm)\b|"
        r"試算表|工作簿|工作表|儲存格|公式|對帳|報表",
        re.IGNORECASE,
    ),
    "Word": re.compile(
        r"\b(?:word|docx?|docm|document|contract|report)\b|"
        r"文件|文檔|合約|報告|公文|段落|章節",
        re.IGNORECASE,
    ),
    "PowerPoint": re.compile(
        r"\b(?:powerpoint|pptx?|pptm|presentation|slide|deck)\b|"
        r"簡報|投影片|幻燈片|母片",
        re.IGNORECASE,
    ),
    "PDF": re.compile(r"\bpdf\b|可攜式文件", re.IGNORECASE),
}

ACTION_PATTERN = re.compile(
    r"\b(?:find|search|open|read|extract|summari[sz]e|analy[sz]e|review|"
    r"check|compare|create|make|write|edit|update|change|fix|format|merge|"
    r"combine|convert|schedule|repeat|recurring|automate)\b|"
    r"找|搜尋|查找|查看|讀取|擷取|摘要|分析|檢查|審閱|比較|建立|製作|"
    r"撰寫|編輯|修改|更新|修正|格式|合併|整合|轉換|排程|定期|循環|自動",
    re.IGNORECASE,
)
SCHEDULE_PATTERN = re.compile(
    r"\b(?:schedule|repeat|recurring|automate|weekly|daily|monthly)\b|排程|定期|循環|自動|每週|每天|每月",
    re.IGNORECASE,
)

FENCED_CODE_PATTERN = re.compile(
    r"(?P<fence>" + chr(96) * 3 + r"|~~~)[^\n]*\n.*?(?P=fence)",
    re.DOTALL,
)
INLINE_CODE_PATTERN = re.compile(
    r"(?<!\w)(" + chr(96) + r"+)(.+?)\1",
    re.DOTALL,
)
URL_PATTERN = re.compile(
    r"\b(?:https?|ftp|file)://[^\s<>\"']+",
    re.IGNORECASE,
)
OFFICE_EXTENSION_PATTERN = re.compile(
    r"\.(?:xlsx|xlsm|xls|docx|docm|doc|pptx|pptm|ppt|pdf)\b",
    re.IGNORECASE,
)
LOCAL_PATH_PATTERN = re.compile(
    r"(?<![\w\"'])(?:[A-Za-z]:[\\/]|~[\\/]|\.{1,2}[\\/]|\\\\[^\\/\s]+[\\/]|/(?!/))[^\s<>\"']*"
)
BARE_OFFICE_FILENAME_PATTERN = re.compile(
    r"(?<![\w.-])[A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:xlsx|xlsm|xls|docx|docm|doc|pptx|pptm|ppt|pdf)\b",
    re.IGNORECASE,
)
QUOTED_OFFICE_FILENAME_PATTERN = re.compile(
    r"(?:\"[^\"\r\n]+\.(?:xlsx|xlsm|xls|docx|docm|doc|pptx|pptm|ppt|pdf)\b\"|'[^'\r\n]+\.(?:xlsx|xlsm|xls|docx|docm|doc|pptx|pptm|ppt|pdf)\b')",
    re.IGNORECASE,
)
QUOTED_LOCAL_PATH_PATTERN = re.compile(
    r'"(?P<double>(?:[A-Za-z]:[\\/]|~[\\/]|\.{1,2}[\\/]|\\\\[^\\/\s]+[\\/]|/(?!/))[^\\"\r\n]+)"'
    r"|'(?P<single>(?:[A-Za-z]:[\\/]|~[\\/]|\.{1,2}[\\/]|\\\\[^\\/\s]+[\\/]|/(?!/))[^'\r\n]+)'"
)


def strip_code(prompt: str) -> str:
    without_fences = FENCED_CODE_PATTERN.sub(" ", prompt)

    def replace_inline(match: re.Match[str]) -> str:
        content = match.group(2)
        if any(extension in content.lower() for extension in SUPPORTED_EXTENSIONS):
            return content
        return " "

    return INLINE_CODE_PATTERN.sub(replace_inline, without_fences)


def object_hints(prompt: str) -> list[str]:
    hints: set[str] = set()
    lower = prompt.lower()
    for extension, object_name in SUPPORTED_EXTENSIONS.items():
        if extension in lower:
            hints.add(object_name)
    for object_name, pattern in OBJECT_PATTERNS.items():
        if pattern.search(prompt):
            hints.add(object_name)
    return sorted(hints)


def is_office_prompt(prompt: str) -> bool:
    cleaned = strip_code(prompt)
    if re.search(r"(?<![\w-])\$office-os\b", cleaned, re.IGNORECASE):
        return True
    if OFFICE_EXTENSION_PATTERN.search(cleaned):
        return True
    return bool(ACTION_PATTERN.search(cleaned) and object_hints(cleaned))


def has_named_local_source(prompt: str, cwd: str) -> bool:
    cleaned = URL_PATTERN.sub("", strip_code(prompt))
    candidates = [match.group() for match in BARE_OFFICE_FILENAME_PATTERN.finditer(cleaned)]
    candidates.extend(
        match.group()[1:-1] for match in QUOTED_OFFICE_FILENAME_PATTERN.finditer(cleaned)
    )
    candidates.extend(
        match.group().rstrip(".,;:!?)]}，。；：！？）】")
        for match in LOCAL_PATH_PATTERN.finditer(cleaned)
    )
    for match in QUOTED_LOCAL_PATH_PATTERN.finditer(cleaned):
        quoted_path = match.group("double") or match.group("single")
        if quoted_path:
            candidates.append(quoted_path)
    directory = Path(cwd)
    return any((directory / Path(candidate).expanduser()).exists() for candidate in candidates)
