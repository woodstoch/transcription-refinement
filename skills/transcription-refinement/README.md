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
  --protected-terms global_replacements.protected_terms.json \
  --proposals agent_proposals.json
```

沒有 `--proposals` 時只建立證據與模型統計，不從下游差異自動產生候選。每列保留 `candidate_id`、`proposal_fingerprint`、`source_text`、`source_or_pattern`、`replacement`、`reason`，並另外記錄 `evidence_status`、`downstream_observed`、`validation_status`、`validation_note`。不使用信心度或低信心淘汰；duplicate、conflict、`conversionChanged=true` 都保留給使用者審核。

`refinement.md` 表格前會保存本批次的 `selection_mode`、`selected_count`、`selected_from` 與 `selected_to`。`selected_from` 是實際選取的最早一筆 Recording，`selected_to` 是最晚一筆；它們描述實際資料夾，不代表中間每個時間戳都有錄音。Chat 整理報告也應重複顯示這些值。

`refinement.md` 的欄位依序分為「提議與審核」、「下游驗證」、「模型稽核」及「匯入路由與系統稽核」四組；完整的用途、格式、可修改性與 Importer 影響請見 [`references/refinement-format.md`](references/refinement-format.md)。`candidate_id` 是人工引用索引，`proposal_fingerprint` 是系統完整性檢查值。`replacement_risk` 與 `protected_term_match` 只協助人工排序與安全審核，不會刪除候選。`protected_term_action` 位於 `review_status` 後，只有使用者填入精確值 `add` 才提出詞表加入請求。

## 審核與匯入

使用者逐列將 `review_status` 設為 `approved` 或 `rejected`，可自行回聽音訊或修改候選。`replacement_mode` 則是整批只選一次的 `mixed` 或 `separate`，並在 metadata 與各列 routing 欄位填入相同的 `target_file`；Importer 會檢查一致性。常用或不可替換詞會是 `review_required`；若使用者拒絕 Literal 候選並確認來源詞不可替換，將 `protected_term_action` 填為 `add`。dry-run 會預覽新增；若檔案不存在，第二次確認後的正式匯入會建立符合 schema 的詞表並加入詞項。也可使用直接管理指令：

```bash
python3 skills/transcription-refinement/scripts/manage_protected_terms.py add \
  --file global_replacements.protected_terms.json \
  --refinement refinement.md --candidate-id <candidate-id> \
  --reason "正常用語，不應被全域替換"
```

匯入前必須指定單一模式並先 dry-run：

```bash
python3 skills/transcription-refinement/scripts/import_replacements.py \
  --refinement refinement.md --mode mixed \
  --scope-file global_replacements.scope.json \
  --protected-terms global_replacements.protected_terms.json --dry-run
```

`mixed` 維護 `global_replacements.json`；`separate` 只接受使用者提供且已存在的模型檔案。若 approved Literal 仍命中保護詞，正式匯入會在第二次確認後同步移除該保護項。第二次明確確認後才移除 `--dry-run`。Skill 不修改 Recording、官方 schema、`appsettings.Local.json` 或 ZeroType 設定。
