---
name: transcription-refinement
description: Review ZeroType recordings by having the Agent propose raw-source replacements, while downstream files provide non-destructive validation and model-scope audit.
---

# Transcription Refinement

Use this Skill for ZeroType recording-to-vocabulary review. The Agent proposes deterministic replacements from the text immediately before each target stage. Downstream text and `global_replacements.json` are read-only validation evidence; they must never create, rewrite, hide, or delete an Agent proposal. This copy is self-contained; commands use `skills/transcription-refinement/...` and scripts resolve their own `references/` with `__file__`.

The Skill-owned `references/global_replacements.runtime.schema.json` describes the observed runtime `{ "Pattern": "...", "Replacement": "..." }` Regex object format. The scope schema is provenance metadata only. Neither changes or claims conformance to the bundled official `global_replacements.schema.json`.

The generated `refinement.md` field contract is documented in [`references/refinement-format.md`](references/refinement-format.md). Keep the Markdown table as one canonical table so the Importer can continue parsing named headers.
The protected-term contract is documented in [`references/protected_terms.schema.json`](references/protected_terms.schema.json); manage the user-owned `global_replacements.protected_terms.json` only through explicit operator actions.

## Sources and model evidence

- `transcription_text.txt` (or the documented legacy fallback) is the only proposal source for `Transcription`.
- `prompt_correction_text.txt` (or its documented response fallback) is the only proposal source for `FinalOutput`.
- `transcription_processed_text.txt` and `output_text.txt` are downstream validation only.
- `global_replacements.json` is read-only during build and is used only for duplicate/conflict checks.
- `transcription_request.json`, `user_prompt_overrides.json`, prompt request/response files, and loop fallbacks provide model metadata. Never infer historical models from `appsettings.Local.json`.
- Missing downstream evidence does not remove a proposal. Missing stage source means that stage cannot produce a candidate.
- `prompt_correction_loop_*` files are traces and must not be counted twice. Do not read `audio.wav` unless the user asks to review a recording.

## Build an Agent proposal batch

First select recordings and prepare an Agent proposal JSON. The proposal file is an array or `{ "version": 1, "proposals": [...] }` with these fields:

```json
{
  "recording": "20260814-100000-001",
  "target_section": "Transcription",
  "source_text": "raw term",
  "source_or_pattern": "raw term",
  "replacement": "canonical term",
  "rule_type": "Literal",
  "replacement_risk": "common_term",
  "reason": "why this deterministic correction is proposed"
}
```

For `FinalOutput`, use `prompt_correction_text.txt` as `source_text`. The Agent must preserve exact proposal intent and should normally avoid punctuation, whitespace, formatting, grammar/style, or Traditional/Simplified-only changes. These are review guidance, not post-build deletion rules.

Run from the workspace root:

```bash
python3 skills/transcription-refinement/scripts/build_refinement.py \
  --all --scope-file global_replacements.scope.json \
  --protected-terms global_replacements.protected_terms.json \
  --proposals agent_proposals.json
```

Without `--proposals`, the script produces an empty review table plus evidence/model statistics. It reports observed downstream differences as hints only; it never turns those differences into candidates.

The generated `refinement.md` begins with selection metadata: `selection_mode`, `selected_count`, `selected_from`, and `selected_to`. `selected_from` is the oldest selected Recording and `selected_to` is the newest; the values describe the actual selected directories, not every timestamp between them. Chat must repeat this range in its summary. The table then displays four adjacent field groups: proposal/review, downstream validation, model audit, and import routing/system audit. `target_section` decides the replacement section; `review_stage` only identifies the evidence stage. `candidate_id` is the human row index; `proposal_fingerprint` is a system integrity value and should not be edited.

The proposal/review group contains the Agent proposal fields, `replacement_risk`, `protected_term_match`, `review_status` (`pending`, `review_required`, `approved`, or `rejected`), and the operator-only `protected_term_action`. A non-`none` risk, a protected-term hit, or an unchecked protected-term file starts at `review_required`; this is an audit gate, not confidence. The validation group is informational and cannot replace proposal values. Model and routing fields are generated or selected for audit and import scope.

Protected terms are exact Literal sources scoped independently to `Transcription` or `FinalOutput`. A match remains visible in the table and is never auto-rejected. The Agent must leave `protected_term_action` blank; after review, the user may set it to the exact value `add` only when also setting `review_status=rejected` for a Literal row. This request is applied by the Importer after dry-run and second confirmation. If the protected terms file is missing, that confirmed import creates a valid `schema_version=1` file before adding the term. The existing direct management command remains available:

```bash
python3 skills/transcription-refinement/scripts/manage_protected_terms.py add \
  --file global_replacements.protected_terms.json \
  --refinement refinement.md --candidate-id <candidate-id> \
  --reason "正常用語，不應被全域替換"
```

Validation status can be `matched`, `mismatch`, `no_observed_change`, `duplicate`, `conflict`, `batch_duplicate`, or `evidence_missing`. It is informational and never overwrites the Agent proposal. `conversionChanged=true` is recorded as a mismatch note; it is not low-confidence rejection. There is no confidence field or low-confidence filter.

Every proposal remains in the table, including duplicates and conflicts. A proposal fingerprint protects the proposal fields from accidental automated mutation. A human may intentionally edit a proposal and mark it `approved`; the importer reports that the approved proposal was modified.

After building, Chat must summarize selected range, full/partial/missing evidence, missing core/audio files, each model profile, `mixed_model_profiles`, both sections’ review/duplicate/conflict/rejected counts, and scope warnings. Model metadata is for review and routing only, never written to ZeroType replacement JSON.

## Human review and import gate

The user reviews each row, may edit it, may listen to the linked audio, and sets `review_status=approved` or `rejected`. `review_status` is a per-row decision; only `approved` rows can be imported. `replacement_mode` is a batch-level choice set once in the metadata comment (`mixed` or `separate`), with `target_file` identifying the destination. The table routing cells must mirror those batch values for Importer consistency. No confidence rating is required.

Set one mode for the batch:

```text
<!-- replacement_mode: mixed|separate -->
<!-- target_file: ... -->
```

`mixed` writes approved rows to `global_replacements.json` and updates only the Skill-owned scope sidecar. `separate` requires an existing user-provided model file and exactly one profile; it never creates that file or changes ZeroType settings.

Run a read-only dry-run, then obtain a second explicit confirmation before removing `--dry-run`:

```bash
python3 skills/transcription-refinement/scripts/import_replacements.py \
  --refinement refinement.md --mode mixed \
  --scope-file global_replacements.scope.json \
  --protected-terms global_replacements.protected_terms.json --dry-run

python3 skills/transcription-refinement/scripts/import_replacements.py \
  --refinement refinement.md --mode separate \
  --profile Remote__whisper-large-v3-turbo__openai-gpt-oss-120b \
  --replacements existing-model-replacements.json \
  --scope-file global_replacements.scope.json \
  --protected-terms global_replacements.protected_terms.json --dry-run
```

Dry-run reports mode, profile, target file, both sections’ additions/duplicates/conflicts, protected terms scheduled for addition/removal, whether a missing protected terms file would be created, validation warnings, and cross-model warnings without writing replacement JSON, scope metadata, or the protected-term file. CLI mode/profile/target must match the batch and every row. Regex objects and both replacement sections are validated before and after import. Pending/review_required/rejected rows are ignored for replacement import; rejected rows with `protected_term_action=add` are processed only as protected-term requests. Duplicate approved rows remain visible in the report but are not written twice. An approved Literal that still matches the live protected-term file removes that protection only during the confirmed formal import.

Never modify recordings, `appsettings.Local.json`, the official schema, the app bundle, or ZeroType settings.
