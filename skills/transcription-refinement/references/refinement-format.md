# refinement.md 欄位契約

本文件定義 Skill 產生的單一 Markdown canonical table。它描述人工審核輸出，不是 ZeroType 的 global_replacements.json schema；Importer 仍依表頭名稱解析，因此欄位名稱不可任意翻譯或改名。

## 閱讀順序

1. 先看「提議與審核」，決定候選是否值得保留。
2. 再看「下游驗證」，了解現有結果是否支持、不同或缺少證據。
3. 接著看「模型稽核」，確認候選來自哪些 STT／校正模型。
4. 最後看「匯入路由與系統稽核」，確認批次模式與完整性資訊。

## 欄位分組

| 群組 | 欄位 |
| --- | --- |
| 提議與審核 | candidate_id、target_section、recording、timestamp、replacement_risk、protected_term_match、review_status、source_text、source_or_pattern、replacement、rule_type、reason |
| 下游驗證 | evidence_status、downstream_observed、validation_status、validation_note |
| 模型稽核 | transcription_engine、transcription_model、correction_model、model_profile、model_evidence |
| 匯入路由與系統稽核 | replacement_mode、target_file、proposal_fingerprint、review_stage、remark |

## 提議與審核

| 欄位 | 用途 | 產生者／擁有者 | 格式或允許值 | 可人工修改 | Importer 影響 |
| --- | --- | --- | --- | --- | --- |
| candidate_id | 人工引用候選列的穩定索引 | 建置腳本；可接受既有 proposal ID | 通常為 candidate-<16位十六進位> | 不應修改 | 不決定寫入內容 |
| target_section | 決定寫入 Transcription 或 FinalOutput | Agent proposal | Transcription、FinalOutput | 可改但應重新審核 | 決定寫入區塊 |
| recording | 候選所屬 Recording，含 audio.wav 回聽連結 | Agent proposal／建置腳本 | Recording 時間戳目錄名稱 | 可改但應重新審核 | 用於來源與稽核，不直接成為 replacement |
| timestamp | 顯示 Recording 時間並提供回聽入口 | 建置腳本 | Markdown audio.wav link 或反引號時間戳 | 不應修改 | 僅供人工回聽與定位 |
| replacement_risk | 協助人工排序的來源詞風險分類，不是信心度 | Agent | none、common_term、rare_or_domain、context_sensitive、unknown（舊提議缺欄位時） | 可補充但應重新審核 | 非 approved 不可匯入；不會自動淘汰候選 |
| protected_term_match | 顯示 Literal 是否命中不可替換詞表 | 建置腳本／Importer 即時複查 | matched、not_matched、not_applicable、not_checked | 僅可加註 | matched 或 not_checked 需人工明確決定 |
| review_status | 人工審核狀態 | 使用者 | pending、review_required、approved、rejected | 使用者唯一可設定者 | 只有 approved 才可匯入 |
| source_text | replacement 前的原始上下文 | Agent proposal | 非空字串 | 可改但會影響完整性檢查 | 參與 fingerprint；供人工理解 |
| source_or_pattern | Literal 來源或 Regex Pattern | Agent proposal | 非空字串 | 可改但會影響匯入 | 決定寫入的來源／Pattern |
| replacement | 建議寫入的 canonical 文字 | Agent proposal | 非空字串 | 可改但會影響匯入 | 決定寫入的 Replacement |
| rule_type | 指定來源是 Literal 或 Regex | Agent proposal | Literal、Regex | 可改但會影響匯入 | 決定 replacement rule 類型 |
| reason | Agent 提出規則的理由 | Agent proposal | 非空說明字串 | 可改但會影響 fingerprint | 不寫入 JSON，但會參與完整性檢查 |

## 下游驗證

| 欄位 | 用途 | 產生者／擁有者 | 格式或允許值 | 可人工修改 | Importer 影響 |
| --- | --- | --- | --- | --- | --- |
| evidence_status | 說明來源與下游證據完整度 | 建置腳本 | full_evidence、partial_evidence、missing_downstream | 僅可加註，不應覆寫 | 不淘汰候選 |
| downstream_observed | 記錄下游實際觀察到的結果或差異 | 建置腳本 | 字串，可為空 | 僅可加註 | 不覆蓋 Agent 提議 |
| validation_status | 下游／既有規則的驗證結果 | 建置腳本 | matched、mismatch、no_observed_change、duplicate、conflict、batch_duplicate、evidence_missing | 僅可加註 | duplicate 不重複寫入；conflict 阻止該規則 |
| validation_note | 驗證、缺檔或 policy warning 的補充 | 建置腳本 | 字串，可為空 | 可補充人工備註 | 不覆寫提議欄位 |

## 模型稽核

| 欄位 | 用途 | 產生者／擁有者 | 格式或允許值 | 可人工修改 | Importer 影響 |
| --- | --- | --- | --- | --- | --- |
| transcription_engine | STT 引擎或 provider | Recording metadata | 字串；未知為 unknown | 不應修改 | 僅用於 scope 稽核 |
| transcription_model | Whisper／STT 模型 | Recording metadata | 字串；未知為 unknown | 不應修改 | separate 模式稽核來源 |
| correction_model | AI 校正模型 | Recording metadata | 字串；未知為 unknown | 不應修改 | separate 模式稽核來源 |
| model_profile | 引擎、STT、校正模型的組合識別 | 建置腳本 | engine__transcription_model__correction_model | 不應修改 | separate 必須與 CLI profile 一致 |
| model_evidence | 模型欄位的來源檔案證據 | 建置腳本 | 分號分隔的來源說明，可為空 | 僅可加註 | 不改變候選 |

## 匯入路由與系統稽核

| 欄位 | 用途 | 產生者／擁有者 | 格式或允許值 | 可人工修改 | Importer 影響 |
| --- | --- | --- | --- | --- | --- |
| replacement_mode | 指定本批次的匯入範圍 | 初始由建置腳本設為 pending；使用者選擇模式 | pending、mixed、separate | 使用者選擇 | 必須與 CLI 和 batch metadata 一致 |
| target_file | 指定實際 replacement 檔案 | 使用者／批次路由 | 檔案路徑；pending 時可為空 | 使用者設定 | 必須與 CLI 相符 |
| proposal_fingerprint | 防止 proposal 欄位被驗證流程靜默改寫 | 建置腳本 | 64 位小寫 SHA-256 hex；由 target section、rule type、source、pattern、replacement、reason 計算 | 不應修改 | mismatch 需人工確認；不改變 replacement 值 |
| review_stage | 指示候選所屬證據階段 | 建置腳本 | STT、PromptCorrection | 不應修改 | 不決定寫入區塊；以 target_section 為準 |
| remark | 缺音訊、legacy fallback、來源替代等提示 | 建置腳本／人工補充 | 字串，可為空 | 可補充 | 不改變候選或匯入規則 |

## 不可混淆的欄位

- target_section 決定 replacement 寫入哪個 section；review_stage 只描述證據階段。
- review_status 決定是否能進入 Importer；validation_status 只描述驗證結果。`review_required` 永遠不可直接匯入。
- `replacement_risk` 是 Agent 的風險標籤；`protected_term_match` 是詞表命中狀態。兩者都不代表自動拒絕。
- `protected_term_match=matched` 表示該 Literal source 位於工作區的 `global_replacements.protected_terms.json`；正式核准匯入後會移除該保護項。
- candidate_id 是人工定位用；proposal_fingerprint 是完整性檢查用。
- 下游欄位可以補充驗證資訊，但不得取代 source_text、source_or_pattern、replacement 或 reason。
