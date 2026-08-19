# Transcription Refinement

這個 Skill 由 Agent 直接分析 deterministic replacement 前的原始文字，建立 `Transcription` 與 `FinalOutput` 候選；下游文字只作非破壞性驗證。

- `transcription_text.txt` → `Transcription`
- `prompt_correction_text.txt` → `FinalOutput`
- `transcription_processed_text.txt`、`output_text.txt`、`global_replacements.json` 只作驗證、duplicate 與 conflict 註記

## 建置

先由 Agent 建立 `agent_proposals.json`，再執行：

```bash
python3 skills/transcription-refinement/scripts/build_refinement.py \
  --all --scope-file global_replacements.scope.json \
  --proposals agent_proposals.json
```

沒有 `--proposals` 時只建立證據與模型統計，不從下游差異自動產生候選。每列保留 `candidate_id`、`proposal_fingerprint`、`source_text`、`source_or_pattern`、`replacement`、`reason`，並另外記錄 `evidence_status`、`downstream_observed`、`validation_status`、`validation_note`。不使用信心度或低信心淘汰；duplicate、conflict、`conversionChanged=true` 都保留給使用者審核。

## 審核與匯入

使用者將 `review_status` 設為 `approved` 或 `rejected`，可自行回聽音訊或修改候選。匯入前必須指定單一模式並先 dry-run：

```bash
python3 skills/transcription-refinement/scripts/import_replacements.py \
  --refinement refinement.md --mode mixed \
  --scope-file global_replacements.scope.json --dry-run
```

`mixed` 維護 `global_replacements.json`；`separate` 只接受使用者提供且已存在的模型檔案。第二次明確確認後才移除 `--dry-run`。Skill 不修改 Recording、官方 schema、`appsettings.Local.json` 或 ZeroType 設定。
