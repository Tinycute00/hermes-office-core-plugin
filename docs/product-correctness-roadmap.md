# Product Correctness Roadmap

`office-core` is currently in a draft/preview/handoff stage. The plugin validates sanitized workflow
metadata and returns owner-reviewable handoffs; it does not perform real Office mutations, SaaS
writes, uploads, deletes, publishes, or sends.

## Roadmap Pillars

1. Contract-first planning: every workflow starts with an `OfficeTaskContract` that names the final
   artifact, owner, sources, risk, confirmations, bridge target, validation plan, provenance, and
   confidence band.
2. Source/data correctness: source requirements, reusable data, downstream outputs, freshness rules,
   evidence hashes, and confidence thresholds are first-class. Ambiguity fails closed to owner
   confirmation.
3. Completion validation: the shared `completion_validation.py` framework checks draft deliverable
   type, bridge target, placeholders, source provenance, field confidence, policy confirmation,
   secret redaction, and `external_side_effect=False`.
4. Fail-closed bridge profiles: bridge inventory records current capability status, fallback paths,
   required owner confirmation, and `mutation_allowed=false` until a later enablement plan proves a
   connector safe.
5. Skill guidance: shipped skills must remain contract-first, treat office content as untrusted data,
   validate before handoff, and preview policy before any high-impact action.
6. Self-evolution governance: memory, skill, hook, and scheduled improvement ideas require owner
   approval and may store only summaries, procedures, and evidence hashes, never raw private office
   content or credentials.

## Representative E2E Fixtures

- `monthly_report_template_update`: template/document draft with high-confidence sanitized source
  provenance.
- `messy_spreadsheet_data_package`: spreadsheet data package with pending owner confirmation for a
  medium-confidence extracted field.
- `approved_reusable_data_to_deck`: presentation draft from approved reusable data without a deck
  write.
- `external_send_preview`: email/message preview that requires owner confirmation and remains
  draft-only with no send.

## Explicit Non-Claims

The roadmap does not claim marketplace listing, public release, production readiness, live connector
success, real Office file mutation, real SaaS write/delete/upload, or real email/message send.
Future work must add separate evidence before making any of those claims.
