# ZeroType Transcription Refinement

這個 repository 提供 ZeroType 的 `transcription-refinement` Skill。Agent 會分析 deterministic replacement 前的 Recording 文字，整理 `Transcription` 與 `FinalOutput` 候選，最後由使用者人工審核。下游處理結果只作非破壞性驗證，不會反向改寫或刪除 Agent 提議。

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

## 最新版行為

- `Transcription` 候選只來自 replacement 前的 `transcription_text.txt`；`FinalOutput` 候選只來自 replacement 前的 `prompt_correction_text.txt`。
- `transcription_processed_text.txt`、`output_text.txt` 與既有 replacement 只提供非破壞性驗證、duplicate 與 conflict 註記；下游差異不得覆蓋或刪除 Agent 候選。
- 缺少 downstream 檔案不會刪除候選；只有缺少該 stage 的原始來源時，該 stage 才無法建立候選。
- 建置後的 `refinement.md` 表格前會保存 `selection_mode`、`selected_count`、`selected_from`、`selected_to`，Chat 摘要也應顯示相同範圍。
- `review_status` 是逐列人工決定；`replacement_mode` 是整批一次選擇的 `mixed` 或 `separate`。完整欄位定義請見 [`skills/transcription-refinement/references/refinement-format.md`](skills/transcription-refinement/references/refinement-format.md)。
- `replacement_risk` 只是人工排序提示，不是 confidence，也不會自動淘汰候選。
- protected terms 不需要先手動初始化；只有使用者在 `rejected` 的 Literal 列填入 `protected_term_action=add`，正式確認匯入時才會建立或更新詞表。
- `global_replacements.scope.json` 與 protected terms 檔案是 Skill 的稽核 sidecar，不會成為 ZeroType replacement JSON 的內容。

### 兩個 replacement stage

| 目標 section | Agent 候選來源 | downstream 驗證 | 寫入位置 |
| --- | --- | --- | --- |
| `Transcription` | `transcription_text.txt` | `transcription_processed_text.txt` | `Transcription` |
| `FinalOutput` | `prompt_correction_text.txt` | `output_text.txt` | `FinalOutput` |

Agent 先分析左欄原始文字並寫入 `agent_proposals.json`；建置器再把提議與證據、模型及稽核資訊合併成 `refinement.md`。不提供 `--proposals` 時只建立 inventory，絕不從下游差異自行發明 replacement。

### `refinement.md` 批次標頭

建置器會保留實際選取範圍，格式如下：

```text
<!-- selection_mode: count -->
<!-- selected_count: 10 -->
<!-- selected_from: 20260818-003715-313 -->
<!-- selected_to: 20260818-164727-144 -->
<!-- replacement_mode: pending -->
<!-- target_file: -->
```

`selected_from` 是本批次最早選取的 Recording，`selected_to` 是最晚選取的 Recording；兩者是實際資料夾邊界，不代表中間每個時間戳都有錄音。

## 一般使用流程

多數使用者只需要依序使用三個提示詞。舊版 Recording fallback、模型分布與證據狀態都由 Skill 在同一次整理中處理，不需要拆成不同模式。

### 1. 整理 Recording

```text
請使用已安裝的 transcription-refinement Skill，
整理目前 ZeroType workspace 中最新 <COUNT> 份 Recording。

預設同時分析 Transcription 與 FinalOutput；遇到舊版資料時依 Skill 的 fallback 規則處理。
建立 agent_proposals.json 與 refinement.md，顯示選取範圍、證據與模型摘要，
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

### 舊版 Recording 與模型資訊

- 原始文字缺少時，Skill 依自身文件的 response/history fallback 讀取；不會用現有 replacement 反推缺失文字。
- 缺少 `transcription_processed_text.txt` 或 `output_text.txt` 時，候選仍保留，並在 `evidence_status`／`validation_status` 標示缺少 downstream 證據。
- STT、Whisper 與 AI 校正模型從該筆 Recording 的 request/response metadata 解析；不以目前 `appsettings.Local.json` 猜測歷史模型。
- 多個 `model_profile` 會在 Chat 與 dry-run 顯示 `mixed_model_profiles` 提醒；模型資訊只用於人工稽核與 separate routing，不會寫入 replacement JSON。

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

整理回覆至少要列出：`selected_count`、`selected_from`、`selected_to`、full/partial/missing evidence、缺少的核心檔案／音訊、各 `model_profile`、`Transcription`／`FinalOutput` 統計，以及 duplicate、conflict、cross-model scope 警告。

### 第二步：人工審核候選

使用者檢查 `refinement.md`，必要時要求回聽指定 Recording，修改候選內容，並將每列設為 `approved` 或 `rejected`。`common_term`、`rare_or_domain`、`context_sensitive` 或命中不可替換詞表的列會先標為 `review_required`；它們仍完整保留，只有使用者決定後才改成 `approved`／`rejected`。尚未決定的列保留 `pending` 或 `review_required`，Importer 會忽略它們。

若使用者判定某個 Literal 來源詞本身不可被 replacement，先將候選設為 `rejected`，再在同一列將 `protected_term_action` 填為精確值 `add`：

```text
review_status=rejected
protected_term_action=add
```

這是使用者後續動作，Agent proposal 不得預先填入。它只接受 `rejected` + `Literal` + 有效 `target_section` + 非空來源；任何不符合條件的 `add` 都會使整批停止。詞表只保存依 `target_section` 區分的 Literal；`entries[].term` 是受保護字詞。同 section、同 term 會採 idempotent 行為，不重複新增；與既有 replacement 衝突時會停止。命中時 `refinement.md` 的 `protected_term_match` 會顯示 `matched`。dry-run 只預覽建立或加入；第二次確認後的正式匯入才會寫檔。若使用者之後仍核准同一個 replacement，正式匯入會同步移除該保護項。需要直接管理詞表時，才使用 `manage_protected_terms.py`。

### 第三步：選擇 mixed 並執行 dry-run

使用者：

```text
這批使用 mixed 模式，目標是 global_replacements.json。
我已逐列完成 review_status；請在 refinement.md 的 batch metadata 設定 replacement_mode=mixed 與 target_file=global_replacements.json，並同步 approved rows routing，執行唯讀 dry-run，
回報 Transcription、FinalOutput 的新增、duplicate、conflict 與模型 scope 警告。
不要正式匯入。
```

Agent 應先檢查 routing、結構及 approved rows，再顯示 dry-run 結果。此時 `global_replacements.json` 與 scope sidecar 都不得被修改。

若有 protected-term 請求，dry-run 另列出 `protected_terms_file_missing`、`protected_terms_create`、`protected_terms_add`、duplicate 與 conflict；即使詞表不存在，也只預覽，不會建立檔案。

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
PROTECTED_TERMS_FILE="<protected-terms-file>"
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
  --protected-terms "$PROTECTED_TERMS_FILE" \
  --proposals "$PROPOSALS_FILE" \
  --output "$REFINEMENT_FILE"
```

也可改用 `--all --since <TIMESTAMP> --until <TIMESTAMP>`。`--count` 與 `--all` 必須擇一。未提供 `--proposals` 時，建置器只產生來源、證據與模型統計，不會從下游差異建立候選。

## 匯入安全閘門

匯入前必須完成以下準備：

- 使用者已將候選設為 `approved` 或 `rejected`；Importer 只讀取 `approved` rows。
- `review_required` 是人工審核提示，不是信心度；候選不會因 evidence、conversionChanged、duplicate 或 conflict 被自動刪除。
- `protected_term_action` 只有在使用者明確填入 `add` 時才會處理，且必須搭配 `rejected` + `Literal`。
- `review_status` 是逐列欄位；`replacement_mode` 與 `target_file` 是整批 routing 設定，表格列值必須與表頭及 CLI 一致。
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
  --protected-terms "$PROTECTED_TERMS_FILE" \
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
  --protected-terms "$PROTECTED_TERMS_FILE" \
  --dry-run
```

`separate` 只接受使用者已建立的模型專用檔案；Skill 不會建立該檔案或切換 ZeroType 設定。其檔名不得是 `global_replacements.json`。

檢查 dry-run 報告後，只有在使用者第二次明確確認時，才以完全相同的參數移除 `--dry-run` 正式匯入。

## Repository 內容

- `skills/transcription-refinement/SKILL.md`：Skill 行為與安全規則
- `skills/transcription-refinement/README.md`：Skill 內部流程摘要
- `skills/transcription-refinement/scripts/`：建置與匯入腳本
- `skills/transcription-refinement/references/`：runtime、scope、refinement 欄位與 protected terms schema
- `skills/transcription-refinement/tests/`：工作流程測試
- `skills/transcription-refinement/scripts/manage_protected_terms.py`：受保護 Literal 詞表管理

本 repository 不包含 Recording、音訊、`global_replacements.json`、protected terms 實例、官方 schema 或 ZeroType App 設定。
