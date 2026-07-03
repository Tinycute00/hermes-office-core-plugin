# Security Audit Report: hermes-office-core-plugin v0.1.0

**Target:** `/run/media/tiny/0472B4E772B4DF1C/APPs/hermes/hermes-office-core-plugin`
**Version:** 0.1.0
**Language:** Python 3.11+
**Audit type:** Team-mode exploitability-driven security review
**Team:** 3 hunters + 2 PoC engineers
**Status:** Complete

---

## 1. Scope & Methodology

The audit followed a 4-phase team workflow:

1. **Phase 0 — Baseline:** Lead explored project structure, `pyproject.toml`, and key source modules.
2. **Phase 1 — Hunter Pass:** Three specialists reviewed attack surfaces:
   - Surface hunter: public tool schemas, input validation, handler logic.
   - Auth/Data hunter: confirmation/policy bypass, secret redaction, registry state.
   - Runtime/Supply-chain hunter: file system, symlink, E2E helpers, QA scripts, dependency chain.
3. **Phase 2 — PoC Pass:** Two PoC engineers independently reproduced or falsified each candidate with safe, local-only proofs.
4. **Phase 3 — Cross-Check:** All five members reviewed PoC results and agreed on survivors, severity, minimal fixes, and regression tests.
5. **Phase 4 — Report:** Lead consolidated findings into this document.

No destructive exploits were run against real services. All reproductions used monkey-patching, synthetic data, temporary directories, and static analysis.

---

## 2. Executive Summary

- **8 confirmed findings** (C1–C8).
- **1 low hardening item** (C9).
- The most severe confirmed issue is **C3** (registry owner-confirmation bypass), rated Medium/High.
- All confirmed runtime plugin findings are currently limited to **draft-only preview behavior** in v0.1; they do not directly perform external side effects.
- **Severity escalates** if downstream adapters or a future host consume bridge/operation plans without an independent confirmation boundary.
- Two recurring architectural weaknesses caused multiple findings:
  1. **Duplicated ad-hoc string classifiers** for operation risk (`tool_handlers.py` and `bridge_planner.py`).
  2. **Missing trust boundaries on state/artifact paths** (registry stores, E2E artifact writers, QA validators).

---

## 3. Threat Model & Severity Notes

| Context | Risk level |
|---|---|
| v0.1 plugin runtime (draft-only tools) | Medium at most |
| Future host that auto-executes bridge plans | C2/C3 become High |
| Untrusted workspace/state import | C3/C7 become High |
| CI/dev tools run on untrusted checkouts | C7/C8 are Medium |
| Local developer machine with trusted inputs | C6/C9 are Low |

---

## 4. Confirmed Findings

### C1 — Caller-controlled `confirmation_state` self-attests high-impact operations

- **Severity:** Medium
- **Status:** Confirmed by both PoC engineers
- **Description:** `office_preview_operation` accepts `confirmation_state` directly from caller arguments. Supplying `"confirmed"` causes high-impact operations (`delete`, `send`, `write`) to bypass the `REQUIRED` confirmation state and return `success=True` with `requires_confirmation=False`.
- **Evidence:** Reproduced with `operation="delete file"`, `confirmation_state="confirmed"`; `run_operation` returned success and no confirmation requirement while remaining draft-only.
- **Impact:** Falsifies audit/policy state and could mislead downstream automation or human reviewers.
- **Fix:** Ignore `confirmation_state` from public tool arguments. Derive confirmation only from trusted host context or require a host-issued confirmation token/nonce.
- **Regression test:** `test_preview_operation_ignores_user_confirmation_state` — call with `confirmation_state="confirmed"` and assert `requires_confirmation=True` or `success=False`.

### C2 — High-impact operation synonyms evade string classifiers

- **Severity:** Medium (High if plans are auto-executed downstream)
- **Status:** Confirmed by both PoC engineers
- **Description:** `upload`, `share`, `publish`, and `export` are missing from the high-impact term lists in `_operation_kind` and `_risk_for_operation`. They fall through to `READ`/`LOW`, so the bridge planner returns `available=True`, `risk="low"`, and `requires_confirmation=False`.
- **Evidence:** Both PoC engineers demonstrated that these verbs produce low-risk bridge plans.
- **Impact:** Policy bypass for operations that clearly target external systems.
- **Fix:** Centralize operation-risk classification in one module using an allowlisted enum/verb table. Include `upload`, `share`, `publish`, `export`, `post`, `sync`, `deploy`, `invite`, `grant`, `commit`, `submit`, `attach`, `overwrite`. Default unknown external-target verbs to high-risk/confirmation-required.
- **Regression test:** Parametrized tests for `upload/share/publish/export` in both `_operation_kind` and `_risk_for_operation` asserting high risk and `requires_confirmation=True`.

### C3 — Forged registry owner confirmation selects unconfirmed candidate

- **Severity:** Medium/High
- **Status:** Confirmed by both PoC engineers
- **Description:** `OwnerConfirmationItem.from_dict` does not validate that `selected_candidate_id` is a member of `candidate_ids`. `SourceSelectionResult` then trusts the confirmed state and selects a record that was never legitimately confirmed.
- **Evidence:** Forged JSON with `candidate_ids=[]`, `state="confirmed"`, `selected_candidate_id="cand-B"` was accepted.
- **Impact:** Bypasses owner-confirmation invariant; could force selection of an attacker-chosen data source or template.
- **Fix:** In `OwnerConfirmationItem.from_dict`, require `selected_candidate_id in candidate_ids` when state is `CONFIRMED`, reject selected ids when state is `PENDING`, and have `SourceSelectionResult` re-check the id against current records.
- **Regression test:** Forged registry JSON raises `RegistryError` or returns `needs_owner_confirmation=True`; valid confirmed selections succeed.

### C4 — `ReusableDataEntry.value` bypasses redaction

- **Severity:** Medium
- **Status:** Confirmed by both PoC engineers
- **Description:** `ReusableDataEntry.to_dict()` returns `"value": self.value` without redaction, even though the rest of the package advertises secret redaction.
- **Evidence:** Nested dict/list values containing `api_key`, `password`, or bearer tokens appeared raw in `DataDictionary.to_dict()` JSON output.
- **Impact:** Disclosure of secrets/PII when reusable entries are serialized to tool responses or logs.
- **Fix:** Return `"value": redact_json(self.value)` in `ReusableDataEntry.to_dict()`. Consider separating raw persistence model from safe response model.
- **Regression test:** `ReusableDataEntry(value={"aws_key":"AKIA..."}).to_dict()` does not contain the raw secret.

### C5 — Free-text redaction misses AWS-style access keys

- **Severity:** Medium
- **Status:** Confirmed by both PoC engineers
- **Description:** `FREE_TEXT_SECRET_PATTERN` does not match AWS access key IDs (`AKIA...`, `ASIA...`), so they leak through `redact_text`/`redact_json` and bridge plan inputs/labels.
- **Evidence:** Synthetic `AKIAIOSFODNN7EXAMPLE` and `ASIA...` keys remained unredacted in bridge plan serialization.
- **Impact:** Disclosure of common cloud credentials.
- **Fix:** Extend `FREE_TEXT_SECRET_PATTERN` with `\b(?:AKIA|ASIA)[A-Z0-9]{16}\b` and consider JWT (`eyJ...`), SSH private key markers, and labeled secret access keys.
- **Regression test:** `redact_text`/`redact_json` tests for AKIA/ASIA keys in bare text and bridge-plan inputs.

### C6 — Allowed-root file discovery symlink TOCTOU

- **Severity:** Low/Medium
- **Status:** Confirmed by both PoC engineers (safe dry-run)
- **Description:** `local_file_discovery._within_roots` checks the resolved path against allowed roots, but subsequent `stat`/`build_candidate` calls can follow a symlink swapped in after the check.
- **Evidence:** Monkey-patched race reproduced a candidate whose path escaped the allowed roots.
- **Impact:** Metadata disclosure/selection for paths outside intended roots; direct file content read depends on downstream open behavior.
- **Fix:** Reject symlinks (`path.is_symlink()`), use `lstat`/`os.open(..., O_NOFOLLOW)`, and re-resolve/revalidate immediately before stat/open.
- **Regression test:** Symlink inside allowed root pointing outside is denied; monkeypatched swap after root check returns no candidate.

### C7 — E2E/state writers follow caller-controlled paths/symlinks

- **Severity:** Medium (dev/CI context)
- **Status:** Confirmed by both PoC engineers
- **Description:** `e2e_workflows`, `registry_store.TemplateRegistryStore`, and `data_maps.DataDictionaryStore` write to paths derived from caller arguments (`artifact`, `state_root`) without validating containment or rejecting symlinks.
- **Evidence:** Symlinked `state_root` or artifact path overwrote targets outside the intended directory.
- **Impact:** Arbitrary file overwrite within the user's own permissions; could be used for config/data poisoning.
- **Fix:** Require state/artifact roots to resolve under a plugin-managed base directory, reject symlink parents/target files, and write via temp file + atomic replace only after parent validation.
- **Regression test:** Symlinked `state_root` to outside directory causes `RegistryError`/validation error without modifying the external target; same for `DataDictionaryStore.save` and E2E artifact writes.

### C8 — QA validators execute untrusted repo code

- **Severity:** Medium (supply-chain/CI)
- **Status:** Confirmed by both PoC engineers
- **Description:** `scripts/qa/validate_distribution.py` and `scripts/qa/validate_directory_loader.py` use `exec_module` to import/execute repository `__init__.py` code while validating arbitrary plugin packages.
- **Evidence:** Malicious fixture `__init__.py` wrote a sentinel file during validation.
- **Impact:** Arbitrary code execution when validators are run against untrusted checkouts (e.g., CI on pull requests).
- **Fix:** Replace dynamic imports with static metadata/AST checks. If import identity must be tested, run in an explicit sandbox/subprocess with a `--allow-execute-untrusted-repo` flag and trusted-checkout verification.
- **Regression test:** Malicious fixture marker file is not created during default static validation.

---

## 5. Downgraded / Accepted Risk

### C9 — QA wrapper resolves commands from `PATH`

- **Severity:** Low
- **Status:** Downgraded (static proof only)
- **Description:** `scripts/qa/run-hermes-cli.ps1` resolves `git`, `uv`, and `hermes` from `PATH`.
- **Evidence:** Static review of PowerShell script; no dynamic exploit confirmed.
- **Reason for downgrade:** Requires attacker-controlled `PATH` in the QA workflow, which is not the intended threat model for developer-run scripts.
- **Fix:** Resolve absolute trusted tool paths or require explicit `-GitPath`/`-UvPath`/`-HermesPath` parameters.
- **Regression test:** Static/Pester test rejects mocked `Get-Command` results outside trusted paths.

---

## 6. Remediation Priority

1. **C3** — Registry confirmation validation (strongest invariant violation).
2. **C1 + C2** — Centralize confirmation and operation-risk classification.
3. **C4 + C5** — Fix redaction gaps and centralize redaction sinks.
4. **C7** — Add safe-write helpers for state/artifact paths.
5. **C6 + C8** — Harden file discovery and QA validators.
6. **C9** — Hard wrapper script paths as defense-in-depth.

---

## 7. Residual Risk

- The v0.1 plugin is explicitly draft-only. If a future host consumes bridge/operation plans and invokes real adapters without an independent confirmation gate, **C2 and C3 should be re-rated High**.
- Dev/CI tooling surfaces (C7, C8) remain riskier than the product runtime. Scope these tools to trusted inputs or sandbox them.
- Duplicated classification and redaction logic will likely drift again unless moved into a single policy/redaction module with clear ownership.
- No runtime RCE or privilege escalation was confirmed in the plugin itself under the current draft-only threat model.

---

## 8. Methodology & Team

| Member | Role | Status |
|---|---|---|
| surface-hunter | Public surface / handler review | Completed |
| auth-data-hunter | Auth, data, registry, redaction | Completed |
| runtime-supply-hunter | Filesystem, E2E, QA, supply chain | Completed |
| poc-engineer-a | Independent reproduction / falsification | Completed |
| poc-engineer-b | Independent reproduction / falsification | Completed |

All Phase 1 candidates were either reproduced by both PoC engineers or downgraded with evidence. The team used only local synthetic data and safe monkey-patching; no external services were probed.
