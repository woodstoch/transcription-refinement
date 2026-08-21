# ZeroType Transcription Refinement

這個 repository 提供 ZeroType 的 `transcription-refinement` Skill。它讓 Agent 從 deterministic replacement 前的原始文字整理 `Transcription` 與 `FinalOutput` 候選，再交由使用者人工審核。

Skill 詳細規則位於 [`skills/transcription-refinement/SKILL.md`](skills/transcription-refinement/SKILL.md)；本 README 著重安裝方式、路徑設定與可複製的 Prompt Harness，不重複 Skill 內的判定規則。

## 安裝

需要 Node.js/npm，以及目前的 `npx skills` CLI。

### Global 安裝

適合多個 project 共用：

```bash
npx skills add woodstoch/transcription-refinement \
  --skill transcription-refinement \
  --global --copy --yes
```

### Project 安裝

在 project 根目錄執行，適合只讓單一 project 使用：

```bash
npx skills add woodstoch/transcription-refinement \
  --skill transcription-refinement \
  --copy --yes
```

常用管理指令：

```bash
npx skills list
npx skills update transcription-refinement
npx skills remove transcription-refinement
```

`--copy` 會複製 Skill 檔案，而不是建立 symlink。Global 安裝的實際目錄由 `npx skills` 與 Agent 管理；README 不假設任何作業系統的絕對路徑。

## 通用路徑

以下變數由使用者依環境設定，所有操作都避免寫死作業系統路徑：

```bash
PROJECT_ROOT="<project-root>"
SKILL_ROOT="<installed-or-checkout-skill-root>"
RECORDINGS_DIR="<recordings-directory>"
REPLACEMENTS_FILE="<global-replacements-file>"
SCOPE_FILE="<scope-sidecar-file>"
PROPOSALS_FILE="<agent-proposals-file>"
REFINEMENT_FILE="<refinement-output-file>"
```

若使用 project checkout，可將 `SKILL_ROOT` 設為：

```bash
SKILL_ROOT="$PROJECT_ROOT/skills/transcription-refinement"
```

Global 安裝時，請讓 Agent 解析已載入 Skill 的自身路徑，不要自行猜測全域安裝目錄。

## 基本執行

Agent 先依下方 Prompt Harness 建立 `PROPOSALS_FILE`，再執行：

```bash
python3 "$SKILL_ROOT/scripts/build_refinement.py" \
  --all \
  --recordings "$RECORDINGS_DIR" \
  --replacements "$REPLACEMENTS_FILE" \
  --scope-file "$SCOPE_FILE" \
  --proposals "$PROPOSALS_FILE" \
  --output "$REFINEMENT_FILE"
```

未提供 proposals 時，建置器只建立證據與模型統計，不會從下游差異自動發明候選。

## Prompt Harness

以下提示詞只負責任務編排。候選來源、fallback、模型 scope、人工審核與匯入安全規則，均以已安裝的 `transcription-refinement` Skill 為準。

每個 Harness 都應填入：`<RANGE>`、`<STAGE>`、`<RECORDINGS_DIR>`、`<PROPOSALS_FILE>`、`<REFINEMENT_FILE>`。

### 1. 最新完整 Recording 整理

```text
請使用已安裝的 transcription-refinement Skill。

工作模式：latest-full
Recording 範圍：<RANGE>
目標 stage：Transcription + FinalOutput
Recordings 路徑：<RECORDINGS_DIR>
候選輸出：<PROPOSALS_FILE>
整理輸出：<REFINEMENT_FILE>

請完成候選整理與模型分布報告，停在人工審核階段，不要執行正式匯入。
```

### 2. 舊版／部分證據 Recording 整理

```text
請使用已安裝的 transcription-refinement Skill。

工作模式：legacy-partial
Recording 範圍：<RANGE>
目標 stage：<STAGE>
Recordings 路徑：<RECORDINGS_DIR>
候選輸出：<PROPOSALS_FILE>
整理輸出：<REFINEMENT_FILE>

請整理仍有原始來源的可用部分，將缺少的下游檔案列為驗證註記，並停在人工審核階段。
```

### 3. 只分析 Transcription

```text
請使用已安裝的 transcription-refinement Skill。

工作模式：transcription-only
Recording 範圍：<RANGE>
目標 stage：Transcription
Recordings 路徑：<RECORDINGS_DIR>
候選輸出：<PROPOSALS_FILE>
整理輸出：<REFINEMENT_FILE>

只整理 Transcription 候選，不建立 FinalOutput 候選；完成後停在人工審核階段。
```

### 4. 只分析 FinalOutput

```text
請使用已安裝的 transcription-refinement Skill。

工作模式：final-output-only
Recording 範圍：<RANGE>
目標 stage：FinalOutput
Recordings 路徑：<RECORDINGS_DIR>
候選輸出：<PROPOSALS_FILE>
整理輸出：<REFINEMENT_FILE>

只整理 FinalOutput 候選，不建立 Transcription 候選；完成後停在人工審核階段。
```

### 5. 多模型 profile 範圍檢查

```text
請使用已安裝的 transcription-refinement Skill。

工作模式：model-scope-audit
Recording 範圍：<RANGE>
目標 stage：<STAGE>
Recordings 路徑：<RECORDINGS_DIR>
候選輸出：<PROPOSALS_FILE>
整理輸出：<REFINEMENT_FILE>

請先列出每個 model_profile、候選數、未知模型與跨模型規則警告，等待我選擇 mixed 或 separate，不要自行匯入。
```

### 6. 指定 Recording 回聽驗證

```text
請使用已安裝的 transcription-refinement Skill。

工作模式：audio-review
指定 Recording：<TIMESTAMP>
指定候選編號：<CANDIDATE_ID>
整理輸出：<REFINEMENT_FILE>

請提供該候選的音訊路徑與原始判定，等待我回聽後再由我決定修改、核准或拒絕；不要自動寫入 replacement。
```

### 7. 人工審核後的 dry-run 與正式匯入

```text
請使用已安裝的 transcription-refinement Skill。

工作模式：review-and-import
整理輸出：<REFINEMENT_FILE>
匯入模式：<mixed-or-separate>
model_profile：<PROFILE-OR-NONE>
目標 replacement 檔案：<REPLACEMENTS_FILE>
scope sidecar：<SCOPE_FILE>

請先確認人工審核完成，執行唯讀 dry-run 並列出兩個 section 的新增、duplicate、conflict 與 scope 警告。除非我再次明確確認，不要執行正式匯入。
```

## 匯入原則

人工審核完成並明確選擇模式後，先執行 dry-run：

```bash
python3 "$SKILL_ROOT/scripts/import_replacements.py" \
  --refinement "$REFINEMENT_FILE" \
  --mode <mixed-or-separate> \
  --replacements "$REPLACEMENTS_FILE" \
  --scope-file "$SCOPE_FILE" \
  --dry-run
```

只有第二次明確確認後才移除 `--dry-run`。`mixed` 維護統一 replacement 檔案；`separate` 只使用已存在的模型專用檔案，不建立新檔或切換 ZeroType 設定。

## Repository 內容

- `skills/transcription-refinement/SKILL.md`：Skill 行為與安全規則
- `skills/transcription-refinement/scripts/`：建置與匯入腳本
- `skills/transcription-refinement/references/`：runtime 與 scope schema
- `skills/transcription-refinement/tests/`：工作流程測試

本 repository 不包含 Recording、音訊、`global_replacements.json`、官方 schema 或 ZeroType App 設定。
