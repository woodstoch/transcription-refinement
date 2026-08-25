# ZeroType Transcription Refinement

這個 repository 提供 ZeroType 的 `transcription-refinement` Skill。Agent 會分析 deterministic replacement 前的 Recording 文字，整理 `Transcription` 與 `FinalOutput` 候選，最後由使用者人工審核。

完整的判定、驗證與匯入規則以 [`skills/transcription-refinement/SKILL.md`](skills/transcription-refinement/SKILL.md) 為準；本 README 只說明安裝與實際操作入口。

## 安裝

使用 `npx skills` 安裝時需要 Node.js 與 npm。

### Global 安裝

適合跨 project 共用：

```bash
npx skills add woodstoch/transcription-refinement \
  --skill transcription-refinement \
  --global --copy --yes
```

管理 global 安裝：

```bash
npx skills list --global
npx skills update transcription-refinement --global --yes
npx skills remove transcription-refinement --global --yes
```

### Project 安裝

在 project 根目錄執行，適合只讓目前 project 使用：

```bash
npx skills add woodstoch/transcription-refinement \
  --skill transcription-refinement \
  --copy --yes
```

管理 project 安裝：

```bash
npx skills list
npx skills update transcription-refinement --yes
npx skills remove transcription-refinement --yes
```

`--copy` 會複製 Skill，而不是建立 symlink。Global 與 project 的實際安裝位置均由 `npx skills` 及 Agent 管理，不要假設固定目錄。只有直接 checkout 本 repository 時，Skill 路徑才是 `<repo-root>/skills/transcription-refinement`。

## 一般使用流程

多數使用者只需要依序使用三個提示詞。舊版 Recording fallback、模型分布與證據狀態都由 Skill 在同一次整理中處理，不需要拆成不同模式。

### 1. 整理 Recording

```text
請使用已安裝的 transcription-refinement Skill，
整理目前 ZeroType workspace 中最新 <COUNT> 份 Recording。

預設同時分析 Transcription 與 FinalOutput；遇到舊版資料時依 Skill 的 fallback 規則處理。
建立 agent_proposals.json 與 refinement.md，顯示證據與模型摘要，
然後停在人工審核階段，不要匯入。
```

可選擇補充：

- 指定日期範圍，取代最新 `<COUNT>` 份。
- 只整理 `Transcription` 或 `FinalOutput`。這是可用的 Agent 行為：Agent 只為指定的 `target_section` 建立 proposals；但建置腳本沒有 `--stage` 參數，仍會盤點所選 Recording 的完整證據與模型資訊。
- Recording 不在目前 workspace 的標準位置時，提供其實際路徑。

### 2. 審核候選

```text
請使用已安裝的 transcription-refinement Skill，
協助我審核 refinement.md 中的候選 <CANDIDATE_IDS>。

保留原始提議與非破壞性驗證資訊；若我指定回聽，請定位對應 Recording 的 audio.wav 協助查核。
等待我決定修改、approved 或 rejected，這一步不要匯入。
```

### 3. 匯入前 dry-run

```text
請使用已安裝的 transcription-refinement Skill。

refinement：<REFINEMENT_FILE>
匯入模式：<mixed-or-separate>
目標檔案：<TARGET_REPLACEMENTS_FILE>
model_profile：<SEPARATE_ONLY_PROFILE>
scope sidecar：<SCOPE_FILE>

先檢查 batch 註解與所有 approved rows 的 mode、profile、target 是否一致，
再執行唯讀 dry-run，列出兩個 section 的新增、duplicate、conflict 與 scope 警告。
完成後等待我第二次明確確認，不要正式匯入。
```

`model_profile` 只在 `separate` 模式需要。第二次確認前不得移除 `--dry-run`。

## `refinement.md` 目前版本的審核重點

目前遠端 `main` 的 Skill 使用單一 Markdown table；完整欄位仍以已安裝 Skill 的 `SKILL.md` 與腳本輸出為準。人工審核時建議依以下順序閱讀：

1. 先確認 `target_section`、Recording 與 `timestamp`，必要時開啟連結的 `audio.wav`。
2. 檢查 `source_text`、`source_or_pattern`、`replacement`、`rule_type` 與 `reason`。
3. 將 `review_status` 設為 `approved` 或 `rejected`；尚未決定的列保留 `pending`。
4. 將下游證據、模型資訊與 routing 欄位視為驗證和匯入前檢查，不用它們覆蓋 Agent 的原始提議。

`candidate_id` 是人工引用列的索引；`proposal_fingerprint` 是系統完整性檢查值，不需要手動產生或修改。Importer 只匯入 `approved` rows。

### 全域 replacement 的詞彙風險（目前限制）

目前版本尚未提供 `replacement_risk` 或 `review_required` 欄位，也不會自動判斷 source 是否為常用詞。使用者在設為 `approved` 前，仍需自行確認：

- source 是否本來就是正常使用的常用字詞；
- replacement 是否可能改掉其他合法上下文；
- Literal 是否可能命中較長字詞中的子字串；
- 是否需要回聽音訊或改成更精確的 Regex。

這些檢查屬於人工審核，不應把 `validation_status` 解讀成詞彙風險或信心度。`replacement_risk`／`review_required` 若未來加入，必須同步更新 Skill、Importer、欄位 reference 與測試。

## 常規整理到匯入範例

以下是最常見的 mixed 流程。每一步都是獨立的使用者決定；Agent 不會因為完成上一階段就自動匯入。

### 第一步：整理最新 Recording

使用者：

```text
請使用已安裝的 transcription-refinement Skill，整理最新 100 份 Recording。
同時分析 Transcription 與 FinalOutput，建立 agent_proposals.json 與 refinement.md，
列出證據、模型與候選摘要，完成後停在人工審核，不要匯入。
```

Agent 應完成來源分析與非破壞性驗證，並回報 `refinement.md`。如果沒有候選，流程在此結束；不需要為了完成流程而執行匯入。

### 第二步：人工審核候選

使用者檢查 `refinement.md`，必要時要求回聽指定 Recording，修改候選內容，並將每列設為 `approved` 或 `rejected`。尚未決定的列保留 `pending`，Importer 會忽略它們。

### 第三步：選擇 mixed 並執行 dry-run

使用者：

```text
這批使用 mixed 模式，目標是 global_replacements.json。
請更新 refinement.md 的 batch 與 approved rows routing，執行唯讀 dry-run，
回報 Transcription、FinalOutput 的新增、duplicate、conflict 與模型 scope 警告。
不要正式匯入。
```

Agent 應先檢查 routing、結構及 approved rows，再顯示 dry-run 結果。此時 `global_replacements.json` 與 scope sidecar 都不得被修改。

### 第四步：第二次確認後正式匯入

使用者核對 dry-run 結果後，另外發出明確確認：

```text
我確認使用剛才完全相同的 mixed、target file 與 approved rows 正式匯入。
```

Agent 才能移除 `--dry-run`，更新 `global_replacements.json` 與 scope sidecar，完成後回報實際新增、duplicate、conflict 及檔案位置。若 dry-run 後候選或 routing 有變更，必須重新 dry-run，不能沿用舊確認。

## 進階 CLI

一般使用時可讓 Agent 解析已載入 Skill 的位置。只有手動執行腳本時才需要設定路徑：

```bash
SKILL_ROOT="<resolved-installed-skill-root>"
RECORDINGS_DIR="<recordings-directory>"
GLOBAL_REPLACEMENTS_FILE="<path>/global_replacements.json"
MODEL_REPLACEMENTS_FILE="<existing-model-specific-file>"
SCOPE_FILE="<scope-sidecar-file>"
PROPOSALS_FILE="<agent-proposals-file>"
REFINEMENT_FILE="<refinement-output-file>"
```

建置最新 100 份 Recording：

```bash
python3 "$SKILL_ROOT/scripts/build_refinement.py" \
  --count 100 \
  --recordings "$RECORDINGS_DIR" \
  --replacements "$GLOBAL_REPLACEMENTS_FILE" \
  --scope-file "$SCOPE_FILE" \
  --proposals "$PROPOSALS_FILE" \
  --output "$REFINEMENT_FILE"
```

也可改用 `--all --since <TIMESTAMP> --until <TIMESTAMP>`。`--count` 與 `--all` 必須擇一。未提供 `--proposals` 時，建置器只產生來源、證據與模型統計，不會從下游差異建立候選。

## 匯入安全閘門

匯入前必須完成以下準備：

- 使用者已將候選設為 `approved` 或 `rejected`；Importer 只讀取 `approved` rows。
- `refinement.md` 包含正確的 batch routing 註解：

```text
<!-- replacement_mode: mixed|separate -->
<!-- target_file: ... -->
```

- 所有 approved rows 的 `replacement_mode`、`target_file` 與 CLI 相符；`separate` 還必須是同一個 `model_profile`。

### Mixed dry-run

```bash
python3 "$SKILL_ROOT/scripts/import_replacements.py" \
  --refinement "$REFINEMENT_FILE" \
  --mode mixed \
  --replacements "$GLOBAL_REPLACEMENTS_FILE" \
  --scope-file "$SCOPE_FILE" \
  --dry-run
```

`mixed` 的目標檔名必須是 `global_replacements.json`。

### Separate dry-run

```bash
MODEL_PROFILE="<single-model-profile>"

python3 "$SKILL_ROOT/scripts/import_replacements.py" \
  --refinement "$REFINEMENT_FILE" \
  --mode separate \
  --profile "$MODEL_PROFILE" \
  --replacements "$MODEL_REPLACEMENTS_FILE" \
  --scope-file "$SCOPE_FILE" \
  --dry-run
```

`separate` 只接受使用者已建立的模型專用檔案；Skill 不會建立該檔案或切換 ZeroType 設定。其檔名不得是 `global_replacements.json`。

檢查 dry-run 報告後，只有在使用者第二次明確確認時，才以完全相同的參數移除 `--dry-run` 正式匯入。

## Repository 內容

- `skills/transcription-refinement/SKILL.md`：Skill 行為與安全規則
- `skills/transcription-refinement/README.md`：Skill 內部流程摘要
- `skills/transcription-refinement/scripts/`：建置與匯入腳本
- `skills/transcription-refinement/references/`：runtime 與 scope schema
- `skills/transcription-refinement/tests/`：工作流程測試

本 repository 不包含 Recording、音訊、`global_replacements.json`、官方 schema 或 ZeroType App 設定。
