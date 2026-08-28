#!/usr/bin/env python3
"""Build raw-source-first review rows for both replacement stages.

The recording archive contains two independent deterministic replacement
boundaries:

* ``transcription_text.txt`` -> ``transcription_processed_text.txt``
  (``Transcription`` rules)
* ``prompt_correction_text.txt`` -> ``output_text.txt``
  (``FinalOutput`` rules plus output conversion)

The left-hand side of each pair is the only candidate source.  The right-hand
side and ``global_replacements.json`` are read-only evidence used for
deduplication and validation decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CORE_FILES = (
    "transcription_text.txt",
    "transcription_processed_text.txt",
    "prompt_correction_text.txt",
    "output_text.txt",
)
TIMESTAMP_DIRECTORY = re.compile(r"^\d{8}-\d{6}-\d+$")
TARGET_SECTIONS = ("Transcription", "FinalOutput")
RUNTIME_SCHEMA = Path(__file__).resolve().parents[1] / "references" / "global_replacements.runtime.schema.json"
SCOPE_SCHEMA = Path(__file__).resolve().parents[1] / "references" / "global_replacements.scope.schema.json"
PROTECTED_TERMS_SCHEMA = Path(__file__).resolve().parents[1] / "references" / "protected_terms.schema.json"
UNKNOWN_MODEL = "unknown"
RISK_VALUES = {"none", "common_term", "rare_or_domain", "context_sensitive", "unknown"}


@dataclass(frozen=True)
class StageSpec:
    target_section: str
    review_stage: str
    source_name: str
    evidence_name: str


@dataclass(frozen=True)
class ModelMetadata:
    transcription_engine: str
    transcription_model: str
    correction_model: str
    model_profile: str
    model_evidence: str


STAGES = (
    StageSpec("Transcription", "STT", "transcription_text.txt", "transcription_processed_text.txt"),
    StageSpec("FinalOutput", "PromptCorrection", "prompt_correction_text.txt", "output_text.txt"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create raw-source-first refinement rows for Transcription and FinalOutput."
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--count", type=int, help="Number of latest recordings to inspect.")
    selection.add_argument("--all", action="store_true", help="Inspect every recording in the selected range.")
    parser.add_argument("--since", help="Inclusive lower timestamp directory bound.")
    parser.add_argument("--until", help="Inclusive upper timestamp directory bound.")
    parser.add_argument("--recordings", type=Path, default=Path("recordings"), help="Recordings directory.")
    parser.add_argument("--output", type=Path, default=Path("refinement.md"), help="Markdown output path.")
    parser.add_argument(
        "--replacements",
        type=Path,
        default=Path("global_replacements.json"),
        help="Read-only replacement JSON used for per-section deduplication.",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=RUNTIME_SCHEMA,
        help="Observed runtime-format schema used to validate replacement structure.",
    )
    parser.add_argument(
        "--scope-file",
        type=Path,
        default=Path("global_replacements.scope.json"),
        help="Skill-owned model-scope registry used for warnings; never read by ZeroType.",
    )
    parser.add_argument(
        "--protected-terms",
        type=Path,
        default=Path("global_replacements.protected_terms.json"),
        help="User-owned Literal terms that are protected from replacement; missing means not_checked.",
    )
    parser.add_argument(
        "--proposals",
        type=Path,
        help=(
            "JSON file containing Agent proposals. The file is optional; without it the command "
            "only builds the source/evidence inventory and never invents candidates from downstream diffs."
        ),
    )
    return parser.parse_args()


def read_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value if value else None


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def text_value(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def normalize_term(value: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", value)


def load_protected_terms(path: Path) -> tuple[dict[tuple[str, str], dict[str, str]], bool]:
    """Return exact section/term entries and whether the file was checked."""
    if not path.is_file():
        return {}, False
    document = read_json(path)
    if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("entries"), list):
        raise ValueError(f"invalid protected terms structure: {path}")
    result: dict[tuple[str, str], dict[str, str]] = {}
    required = {"target_section", "term", "reason", "added_at", "updated_at", "source_candidate_id"}
    for entry in document["entries"]:
        if not isinstance(entry, dict) or set(entry) != required:
            raise ValueError(f"invalid protected term entry: {path}")
        if entry["target_section"] not in TARGET_SECTIONS or not all(isinstance(entry[field], str) and entry[field] for field in required):
            raise ValueError(f"invalid protected term values: {path}")
        term = normalize_term(entry["term"])
        if term != entry["term"]:
            raise ValueError(f"protected term is not NFC-normalized: {entry['term']!r}")
        key = (entry["target_section"], term)
        if key in result:
            raise ValueError(f"duplicate protected term: {key[0]} / {key[1]!r}")
        result[key] = entry
    return result, True


def protected_term_match(
    proposal: dict[str, Any],
    protected_terms: dict[tuple[str, str], dict[str, str]],
    protected_terms_checked: bool,
) -> str:
    if proposal["rule_type"] != "Literal":
        return "not_applicable"
    if not protected_terms_checked:
        return "not_checked"
    key = (proposal["target_section"], normalize_term(proposal["source_or_pattern"]))
    return "matched" if key in protected_terms else "not_matched"


def initial_review_status(risk: str, protected_match: str) -> str:
    if risk != "none" or protected_match in {"matched", "not_checked"}:
        return "review_required"
    return "pending"


def profile_part(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return normalized or UNKNOWN_MODEL


def make_model_profile(engine: str, transcription_model: str, correction_model: str) -> str:
    return "__".join(profile_part(value) for value in (engine, transcription_model, correction_model))


def model_metadata(recording: Path) -> ModelMetadata:
    overrides = read_json(recording / "user_prompt_overrides.json")
    engine = text_value(overrides.get("effectiveTranscriptionEngine")) if isinstance(overrides, dict) else None
    engine = engine or UNKNOWN_MODEL

    evidence: list[str] = []
    transcription_model = UNKNOWN_MODEL
    transcription_request = read_json(recording / "transcription_request.json")
    if isinstance(transcription_request, dict):
        transcription_model = text_value(transcription_request.get("model")) or UNKNOWN_MODEL
        if transcription_model != UNKNOWN_MODEL:
            evidence.append("transcription_request.json:model")
    if engine != UNKNOWN_MODEL:
        evidence.append("user_prompt_overrides.json:effectiveTranscriptionEngine")

    correction_model = UNKNOWN_MODEL
    correction_request_path = recording / "prompt_correction_request.json"
    correction_request = read_json(correction_request_path)
    correction_response = read_json(recording / "prompt_correction_response_body.json")
    if isinstance(correction_request, dict):
        correction_model = text_value(correction_request.get("model")) or UNKNOWN_MODEL
        if correction_model != UNKNOWN_MODEL:
            evidence.append("prompt_correction_request.json:model")
    if correction_model == UNKNOWN_MODEL and isinstance(correction_response, dict):
        correction_model = text_value(correction_response.get("model")) or UNKNOWN_MODEL
        if correction_model != UNKNOWN_MODEL:
            evidence.append("prompt_correction_response_body.json:model")

    if correction_model == UNKNOWN_MODEL and not correction_request_path.is_file():
        loop_requests = sorted(recording.glob("prompt_correction_loop_*_request.json"))
        loop_responses = sorted(recording.glob("prompt_correction_loop_*_response_body.json"))
        for path in reversed(loop_requests):
            body = read_json(path)
            if isinstance(body, dict) and text_value(body.get("model")):
                correction_model = text_value(body["model"]) or UNKNOWN_MODEL
                evidence.append(f"{path.name}:model")
                break
        if correction_model == UNKNOWN_MODEL:
            for path in reversed(loop_responses):
                body = read_json(path)
                if isinstance(body, dict) and text_value(body.get("model")):
                    correction_model = text_value(body["model"]) or UNKNOWN_MODEL
                    evidence.append(f"{path.name}:model")
                    break

    if transcription_model == UNKNOWN_MODEL:
        evidence.append("transcription_model:unknown")
    if correction_model == UNKNOWN_MODEL:
        evidence.append("correction_model:unknown")
    return ModelMetadata(
        transcription_engine=engine,
        transcription_model=transcription_model,
        correction_model=correction_model,
        model_profile=make_model_profile(engine, transcription_model, correction_model),
        model_evidence="; ".join(evidence),
    )


def default_scope_registry() -> dict[str, Any]:
    return {"version": 1, "profiles": {}, "rules": []}


def load_scope_registry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return default_scope_registry()
    document = read_json(path)
    if not isinstance(document, dict):
        raise ValueError(f"invalid scope registry: {path}")
    if document.get("version") != 1 or not isinstance(document.get("profiles"), dict) or not isinstance(document.get("rules"), list):
        raise ValueError(f"invalid scope registry structure: {path}")
    profile_fields = {"transcription_engine", "transcription_model", "correction_model", "first_seen", "last_seen"}
    rule_fields = {
        "fingerprint", "target_section", "rule_type", "source_or_pattern", "replacement",
        "scope", "target_file", "observed_profiles", "observed_recordings",
    }
    for profile_name, profile in document["profiles"].items():
        if not isinstance(profile_name, str) or not isinstance(profile, dict) or set(profile) != profile_fields:
            raise ValueError(f"invalid scope profile: {profile_name!r}")
        if not all(isinstance(profile[field], str) for field in profile_fields):
            raise ValueError(f"invalid scope profile values: {profile_name!r}")
    for rule in document["rules"]:
        if not isinstance(rule, dict) or set(rule) != rule_fields:
            raise ValueError("invalid scope rule")
        if not all(isinstance(rule[field], str) for field in rule_fields - {"observed_profiles", "observed_recordings"}):
            raise ValueError("invalid scope rule values")
        if not isinstance(rule["observed_profiles"], list) or not isinstance(rule["observed_recordings"], list):
            raise ValueError("invalid scope rule observations")
        if not all(isinstance(value, str) for value in rule["observed_profiles"] + rule["observed_recordings"]):
            raise ValueError("invalid scope rule observations")
    return document


def rule_fingerprint(target_section: str, rule_type: str, source_or_pattern: str, replacement: str) -> str:
    payload = "\x1f".join((target_section, rule_type, source_or_pattern, replacement)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def find_scope_entry(
    registry: dict[str, Any], target_section: str, pair: tuple[str, str, str, str]
) -> dict[str, Any] | None:
    rule_type, field, replacement, root = pair
    fingerprints = {
        rule_fingerprint(target_section, rule_type, field, replacement),
        rule_fingerprint(target_section, "Literal", root, replacement),
    }
    for entry in registry.get("rules", []):
        if isinstance(entry, dict) and entry.get("fingerprint") in fingerprints:
            return entry
    return None


def scope_warning(registry: dict[str, Any], target_section: str, pair: tuple[str, str, str, str], profile: str) -> str | None:
    entry = find_scope_entry(registry, target_section, pair)
    if entry is None:
        return "legacy_unscoped"
    observed = set(entry.get("observed_profiles", []))
    if profile not in observed:
        return "cross_model_scope_warning"
    return None


def escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def timestamp_cell(recording: Path, output: Path) -> str:
    audio_path = recording / "audio.wav"
    if not audio_path.is_file():
        return f"`{recording.name}`"
    relative_audio_path = Path(os.path.relpath(audio_path, start=output.parent)).as_posix()
    return f"[`{recording.name}`]({relative_audio_path})"


def validate_section(section: Any, section_name: str) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    if not isinstance(section, dict):
        raise ValueError(f"{section_name} must be an object")
    literals = section.get("Literals")
    regex_rules = section.get("Regex")
    if not isinstance(literals, dict) or not all(
        isinstance(target, str)
        and isinstance(sources, list)
        and all(isinstance(source, str) for source in sources)
        for target, sources in literals.items()
    ):
        raise ValueError(f"{section_name}.Literals must be an object of string arrays")
    if not isinstance(regex_rules, list) or not all(
        isinstance(rule, dict)
        and isinstance(rule.get("Pattern"), str)
        and isinstance(rule.get("Replacement"), str)
        for rule in regex_rules
    ):
        raise ValueError(f"{section_name}.Regex must be an array of {{Pattern, Replacement}}")
    return literals, regex_rules


def validate_runtime_schema(path: Path) -> None:
    schema = read_json(path)
    try:
        definition = schema["definitions"]["replacementSection"]
        regex = definition["properties"]["Regex"]
        regex_items = regex["items"]
        required = set(regex_items["required"])
    except (TypeError, KeyError) as error:
        raise ValueError(f"invalid runtime schema: {path}") from error
    if (
        schema.get("type") != "object"
        or regex.get("type") != "array"
        or regex_items.get("type") != "object"
        or required != {"Pattern", "Replacement"}
    ):
        raise ValueError(f"runtime schema does not describe object Regex rules: {path}")


def load_existing_rules(
    path: Path, schema_path: Path
) -> dict[str, tuple[dict[str, set[str]], dict[str, set[str]], set[str]]]:
    validate_runtime_schema(schema_path)
    document = read_json(path)
    if document is None:
        raise ValueError(f"invalid replacement JSON: {path}")
    result: dict[str, tuple[dict[str, set[str]], dict[str, set[str]], set[str]]] = {}
    for section_name in TARGET_SECTIONS:
        try:
            literals, regex_rules = validate_section(document[section_name], section_name)
        except KeyError as error:
            raise ValueError(f"replacement JSON missing section: {error.args[0]}") from error
        literal_owners: dict[str, set[str]] = {}
        for target, sources in literals.items():
            for source in sources:
                literal_owners.setdefault(source.casefold(), set()).add(target)
        regex_owners: dict[str, set[str]] = {}
        for rule in regex_rules:
            regex_owners.setdefault(rule["Pattern"], set()).add(rule["Replacement"])
        canonical_targets = {target.casefold() for target in literals}
        result[section_name] = (literal_owners, regex_owners, canonical_targets)
    return result


def filter_existing_pair(
    pair: tuple[str, str, str, str],
    existing: tuple[dict[str, set[str]], dict[str, set[str]], set[str]],
) -> tuple[str, str | None]:
    """Return (decision, reason), where decision is new/duplicate/conflict."""
    rule_type, field, replacement, root = pair
    literal_owners, regex_owners, canonical_targets = existing
    # A representation change (for example, suggesting Regex for an English
    # source that is already stored as a Literal) must not reintroduce an
    # existing correction.  Compare the raw source against Literal owners
    # before applying the representation-specific Regex check.
    literal_targets_for_source = literal_owners.get(root.casefold(), set())
    if literal_targets_for_source:
        if any(target.casefold() == replacement.casefold() for target in literal_targets_for_source):
            return "duplicate", None
        return "conflict", f"source already maps to {', '.join(sorted(literal_targets_for_source))}"
    if rule_type == "Literal":
        if root.casefold() in canonical_targets:
            return "conflict", "source is already a canonical target key"
    else:
        existing_replacements = regex_owners.get(field, set())
        if existing_replacements:
            if replacement in existing_replacements:
                return "duplicate", None
            return "conflict", f"Regex pattern already maps to {', '.join(sorted(existing_replacements))}"
    return "new", None


def fallback_text(recording: Path, filename: str) -> tuple[str | None, str | None]:
    """Resolve a text artifact from the current format and safe legacy fallbacks."""
    direct = read_text(recording / filename)
    if direct is not None:
        return direct, filename

    if filename == "transcription_text.txt":
        body = read_json(recording / "transcription_response_body.json")
        if isinstance(body, dict) and isinstance(body.get("text"), str) and body["text"].strip():
            return body["text"].strip(), "transcription_response_body.json:text"
        history = read_json(recording / "history_entry.json")
        if isinstance(history, dict) and isinstance(history.get("transcriptionText"), str):
            return history["transcriptionText"].strip(), "history_entry.json:transcriptionText"

    if filename == "prompt_correction_text.txt":
        body = read_json(recording / "prompt_correction_response_body.json")
        content = prompt_response_content(body)
        if content is not None:
            return content, "prompt_correction_response_body.json:choices[0].message.content"
        loop_candidates = sorted(recording.glob("prompt_correction_loop_*_response_body.json"))
        for path in reversed(loop_candidates):
            content = prompt_response_content(read_json(path))
            if content is not None:
                return content, f"{path.name}:choices[0].message.content"

    if filename == "output_text.txt":
        history = read_json(recording / "history_entry.json")
        if isinstance(history, dict):
            for key in ("correctedText", "outputText"):
                value = history.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip(), f"history_entry.json:{key}"
    return None, None


def prompt_response_content(body: Any) -> str | None:
    if not isinstance(body, dict) or not isinstance(body.get("choices"), list) or not body["choices"]:
        return None
    first = body["choices"][0]
    if not isinstance(first, dict) or not isinstance(first.get("message"), dict):
        return None
    content = first["message"].get("content")
    return content.strip() if isinstance(content, str) and content.strip() else None


def conversion_changed(recording: Path) -> bool | None:
    diagnostics = read_json(recording / "foreground_injection_diagnostics.json")
    if isinstance(diagnostics, dict) and isinstance(diagnostics.get("conversionChanged"), bool):
        return diagnostics["conversionChanged"]
    return None


def select_recordings(args: argparse.Namespace) -> list[Path]:
    if not args.recordings.is_dir():
        raise ValueError(f"recordings directory not found: {args.recordings}")
    if args.since and not TIMESTAMP_DIRECTORY.fullmatch(args.since):
        raise ValueError(f"invalid --since timestamp: {args.since}")
    if args.until and not TIMESTAMP_DIRECTORY.fullmatch(args.until):
        raise ValueError(f"invalid --until timestamp: {args.until}")
    available = sorted(
        (
            path
            for path in args.recordings.iterdir()
            if path.is_dir() and TIMESTAMP_DIRECTORY.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    available = [
        path
        for path in available
        if (not args.since or path.name >= args.since) and (not args.until or path.name <= args.until)
    ]
    if not available:
        raise ValueError(f"no recording directories found in selected range: {args.recordings}")
    if args.all:
        return available
    if args.count is None or args.count <= 0:
        raise ValueError("--count must be a positive integer")
    if len(available) < args.count:
        raise ValueError(f"requested {args.count} recordings but only {len(available)} are available")
    return available[: args.count]


def append_remark(existing: str, value: str) -> str:
    if not value:
        return existing
    if not existing:
        return value
    return f"{existing}; {value}"


def proposal_fingerprint(
    target_section: str,
    rule_type: str,
    source_text: str,
    source_or_pattern: str,
    replacement: str,
    reason: str,
) -> str:
    payload = "\x1f".join(
        (target_section, rule_type, source_text, source_or_pattern, replacement, reason)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_agent_proposals(path: Path | None) -> list[dict[str, Any]]:
    """Load immutable Agent proposals without deriving any from downstream text."""
    if path is None:
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"proposal file not found: {path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid proposal file: {path}: {error}") from error
    if isinstance(document, dict):
        proposals = document.get("proposals")
    else:
        proposals = document
    if not isinstance(proposals, list):
        raise ValueError("proposal file must be a JSON array or an object with a proposals array")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(proposals, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"proposal {index} must be an object")
        recording = text_value(item.get("recording") or item.get("timestamp"))
        section = text_value(item.get("target_section"))
        replacement = text_value(item.get("replacement"))
        rule_type = text_value(item.get("rule_type")) or "Literal"
        source_or_pattern = text_value(item.get("source_or_pattern"))
        source_text = text_value(item.get("source_text")) or source_or_pattern
        reason = text_value(item.get("reason")) or "Agent 主動判定，待人工審核"
        replacement_risk = text_value(item.get("replacement_risk")) or "unknown"
        if not recording or section not in TARGET_SECTIONS or not source_or_pattern or not replacement:
            raise ValueError(
                f"proposal {index} requires recording, target_section, source_or_pattern, and replacement"
            )
        if rule_type.casefold() not in {"literal", "regex"}:
            raise ValueError(f"proposal {index}: rule_type must be Literal or Regex")
        if not source_text:
            raise ValueError(f"proposal {index}: source_text must not be empty")
        if replacement_risk not in RISK_VALUES:
            raise ValueError(f"proposal {index}: replacement_risk must be one of {sorted(RISK_VALUES)}")
        requested_status = text_value(item.get("review_status") or item.get("status"))
        if requested_status and requested_status != "pending":
            raise ValueError(f"proposal {index}: Agent proposal status must be pending; approval is user-only")
        normalized.append(
            {
                "recording": recording,
                "target_section": section,
                "source_text": source_text,
                "source_or_pattern": source_or_pattern,
                "replacement": replacement,
                "rule_type": "Regex" if rule_type.casefold() == "regex" else "Literal",
                "reason": reason,
                "replacement_risk": replacement_risk,
                "review_status": "pending",
                "candidate_id": text_value(item.get("candidate_id")),
                "proposal_fingerprint": text_value(item.get("proposal_fingerprint")),
            }
        )
    return normalized


def apply_proposal(source: str, proposal: dict[str, Any]) -> str | None:
    """Apply one proposed rule only for evidence comparison; never rewrite the proposal."""
    try:
        if proposal["rule_type"] == "Regex":
            return re.sub(proposal["source_or_pattern"], proposal["replacement"], source)
        return source.replace(proposal["source_text"], proposal["replacement"])
    except (re.error, KeyError, TypeError):
        return None


def downstream_validation(
    proposal: dict[str, Any],
    source: str | None,
    evidence: str | None,
    recording: Path,
) -> tuple[str, str, str, str]:
    """Return evidence status, observed text, validation status, and note."""
    if source is None:
        return "missing_source", "", "evidence_missing", "replacement前原始來源不存在"
    if evidence is None:
        return "missing_downstream", "", "evidence_missing", "下游檔案不存在；保留 Agent 候選供人工審核"
    if source == evidence:
        return "full_evidence", "", "no_observed_change", "下游文字沒有變化；不代表刪除候選"
    changed = conversion_changed(recording)
    if proposal["target_section"] == "FinalOutput" and changed is True:
        return "full_evidence", "conversionChanged=true", "mismatch", "下游可能混合了其他轉換"
    transformed = apply_proposal(source, proposal)
    if transformed == evidence:
        return "full_evidence", proposal["replacement"], "matched", "下游結果與 Agent 建議相符"
    return "full_evidence", "different downstream text", "mismatch", "下游結果與 Agent 建議不同；保留雙方內容"


def policy_warning(proposal: dict[str, Any]) -> str:
    source = proposal["source_text"]
    replacement = proposal["replacement"]
    if source.casefold() == replacement.casefold() and source != replacement:
        return "policy_warning=case_only"
    if re.sub(r"\s+", "", source) == re.sub(r"\s+", "", replacement) and source != replacement:
        return "policy_warning=spacing_only"
    return ""


def main() -> int:
    args = parse_args()
    try:
        recordings = select_recordings(args)
        existing_rules = load_existing_rules(args.replacements, args.schema)
        scope_registry = load_scope_registry(args.scope_file)
        protected_terms, protected_terms_checked = load_protected_terms(args.protected_terms)
        proposals = load_agent_proposals(args.proposals)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    recordings_by_name = {recording.name: recording for recording in recordings}
    values_by_recording: dict[str, dict[str, str | None]] = {}
    provenance_by_recording: dict[str, dict[str, str | None]] = {}
    metadata_by_recording: dict[str, ModelMetadata] = {}
    rows: list[dict[str, str]] = []
    full_evidence_records = 0
    partial_evidence_records = 0
    skipped_no_evidence = 0
    missing_audio_files = 0
    missing_core_files: Counter[str] = Counter()
    observed_diff_items = 0
    invalid_proposals = 0
    existing_duplicate_items = 0
    existing_conflict_items = 0
    batch_duplicate_items = 0
    section_review_items = {section: 0 for section in TARGET_SECTIONS}
    section_duplicate_items = {section: 0 for section in TARGET_SECTIONS}
    section_conflict_items = {section: 0 for section in TARGET_SECTIONS}
    model_record_counts: Counter[str] = Counter()
    model_candidate_counts: Counter[str] = Counter()
    model_unknown_record_counts: Counter[str] = Counter()
    transcription_model_record_counts: Counter[str] = Counter()
    correction_model_record_counts: Counter[str] = Counter()
    transcription_model_candidate_counts: Counter[str] = Counter()
    correction_model_candidate_counts: Counter[str] = Counter()
    unknown_model_records = 0
    cross_model_scope_warnings = 0
    legacy_unscoped_warnings = 0
    scope_warning_profiles: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    protected_match_counts: Counter[str] = Counter()
    review_required_items = 0
    review_records: set[str] = set()
    pending_literal_owners: dict[str, dict[str, set[str]]] = {section: {} for section in TARGET_SECTIONS}
    pending_regex_owners: dict[str, dict[str, set[str]]] = {section: {} for section in TARGET_SECTIONS}

    for recording in recordings:
        if not (recording / "audio.wav").is_file():
            missing_audio_files += 1
        values: dict[str, str | None] = {}
        provenance: dict[str, str | None] = {}
        for filename in CORE_FILES:
            if not (recording / filename).is_file():
                missing_core_files[filename] += 1
            values[filename], provenance[filename] = fallback_text(recording, filename)
        values_by_recording[recording.name] = values
        provenance_by_recording[recording.name] = provenance
        if not any(value is not None for value in values.values()):
            skipped_no_evidence += 1
            continue
        direct_core = all((recording / filename).is_file() for filename in CORE_FILES)
        if direct_core:
            full_evidence_records += 1
        else:
            partial_evidence_records += 1
        metadata = model_metadata(recording)
        metadata_by_recording[recording.name] = metadata
        model_record_counts[metadata.model_profile] += 1
        transcription_model_record_counts[metadata.transcription_model] += 1
        correction_model_record_counts[metadata.correction_model] += 1
        if metadata.transcription_model == UNKNOWN_MODEL or metadata.correction_model == UNKNOWN_MODEL:
            unknown_model_records += 1
            model_unknown_record_counts[metadata.model_profile] += 1
        for stage in STAGES:
            source = values[stage.source_name]
            evidence = values[stage.evidence_name]
            if source is not None and evidence is not None and source != evidence:
                observed_diff_items += 1

    for proposal in proposals:
        recording_name = proposal["recording"]
        recording = recordings_by_name.get(recording_name)
        if recording is None:
            invalid_proposals += 1
            continue
        values = values_by_recording.get(recording_name, {})
        provenance = provenance_by_recording.get(recording_name, {})
        metadata = metadata_by_recording.get(recording_name) or model_metadata(recording)
        stage = next(stage for stage in STAGES if stage.target_section == proposal["target_section"])
        source = values.get(stage.source_name)
        evidence = values.get(stage.evidence_name)
        if source is None:
            invalid_proposals += 1
            continue
        source_not_found = proposal["source_text"] not in source
        fingerprint = proposal_fingerprint(
            proposal["target_section"], proposal["rule_type"], proposal["source_text"],
            proposal["source_or_pattern"], proposal["replacement"], proposal["reason"],
        )
        candidate_id = proposal["candidate_id"] or f"candidate-{fingerprint[:16]}"
        stored_fingerprint = proposal["proposal_fingerprint"] or fingerprint
        pair = (
            proposal["rule_type"], proposal["source_or_pattern"], proposal["replacement"], proposal["source_text"]
        )
        decision, decision_reason = filter_existing_pair(pair, existing_rules[proposal["target_section"]])
        evidence_status, downstream_observed, validation_status, validation_note = downstream_validation(
            proposal, source, evidence, recording
        )
        if evidence_status == "full_evidence" and not all((recording / filename).is_file() for filename in CORE_FILES):
            evidence_status = "partial_evidence"
        if source_not_found:
            invalid_proposals += 1
            validation_status = "evidence_missing"
            validation_note = append_remark(validation_note, "source_text not found in replacement前原始文字；保留候選供人工修正")
        if decision == "duplicate":
            existing_duplicate_items += 1
            section_duplicate_items[proposal["target_section"]] += 1
            validation_status = "duplicate"
            warning = scope_warning(scope_registry, proposal["target_section"], pair, metadata.model_profile)
            if warning == "cross_model_scope_warning":
                cross_model_scope_warnings += 1
                scope_warning_profiles[metadata.model_profile] += 1
            elif warning == "legacy_unscoped":
                legacy_unscoped_warnings += 1
                scope_warning_profiles[metadata.model_profile] += 1
        elif decision == "conflict":
            existing_conflict_items += 1
            section_conflict_items[proposal["target_section"]] += 1
            validation_status = "conflict"
            validation_note = append_remark(validation_note, decision_reason or "existing rule conflict")
        else:
            if proposal["rule_type"] == "Literal":
                owners = pending_literal_owners[proposal["target_section"]]
                if proposal["source_text"].casefold() in owners:
                    batch_duplicate_items += 1
                    validation_status = "batch_duplicate"
                owners.setdefault(proposal["source_text"].casefold(), set()).add(proposal["replacement"])
            else:
                owners = pending_regex_owners[proposal["target_section"]]
                if proposal["source_or_pattern"] in owners:
                    batch_duplicate_items += 1
                    validation_status = "batch_duplicate"
                owners.setdefault(proposal["source_or_pattern"], set()).add(proposal["replacement"])
        validation_note = append_remark(validation_note, policy_warning(proposal))
        protected_match = protected_term_match(proposal, protected_terms, protected_terms_checked)
        risk = proposal["replacement_risk"]
        review_status = initial_review_status(risk, protected_match)
        risk_counts[risk] += 1
        protected_match_counts[protected_match] += 1
        if review_status == "review_required":
            review_required_items += 1
            if protected_match == "matched":
                validation_note = append_remark(validation_note, "protected_term_match; user must explicitly decide whether to remove protection")
            elif protected_match == "not_checked":
                validation_note = append_remark(validation_note, "protected_terms_not_checked; initialize or provide the protected terms file")
        if proposal["proposal_fingerprint"] and proposal["proposal_fingerprint"] != fingerprint:
            validation_note = append_remark(validation_note, "proposal_fingerprint_mismatch; requires human review")
        remark = ""
        if not (recording / "audio.wav").is_file():
            remark = "missing audio.wav"
        if not all((recording / filename).is_file() for filename in CORE_FILES):
            remark = append_remark(remark, "partial evidence / legacy fallback")
        if provenance.get(stage.source_name) != stage.source_name:
            remark = append_remark(remark, f"source={provenance.get(stage.source_name)}")
        if provenance.get(stage.evidence_name) != stage.evidence_name:
            remark = append_remark(remark, f"evidence={provenance.get(stage.evidence_name)}")
        rows.append(
            {
                "candidate_id": candidate_id,
                "proposal_fingerprint": stored_fingerprint,
                "timestamp": timestamp_cell(recording, args.output),
                "recording": recording.name,
                "target_section": proposal["target_section"],
                "review_stage": stage.review_stage,
                "replacement_risk": risk,
                "protected_term_match": protected_match,
                "source_text": proposal["source_text"],
                "source_or_pattern": proposal["source_or_pattern"],
                "replacement": proposal["replacement"],
                "rule_type": proposal["rule_type"],
                "reason": proposal["reason"],
                "review_status": review_status,
                # Protected-term additions are an operator-only action.  Never
                # copy a request from Agent proposal JSON into the review table.
                "protected_term_action": "",
                "evidence_status": evidence_status,
                "downstream_observed": downstream_observed,
                "validation_status": validation_status,
                "validation_note": validation_note,
                "transcription_engine": metadata.transcription_engine,
                "transcription_model": metadata.transcription_model,
                "correction_model": metadata.correction_model,
                "model_profile": metadata.model_profile,
                "model_evidence": metadata.model_evidence,
                "replacement_mode": "pending",
                "target_file": "",
                "remark": remark,
            }
        )
        section_review_items[proposal["target_section"]] += 1
        model_candidate_counts[metadata.model_profile] += 1
        if proposal["target_section"] == "Transcription":
            transcription_model_candidate_counts[metadata.transcription_model] += 1
        else:
            correction_model_candidate_counts[metadata.correction_model] += 1
        review_records.add(recording.name)

    selected_from = recordings[-1].name
    selected_to = recordings[0].name
    selection_mode = "all" if args.all else "count"
    headers = (
        "candidate_id", "target_section", "recording", "timestamp", "replacement_risk", "protected_term_match",
        "review_status", "protected_term_action", "source_text", "source_or_pattern", "replacement", "rule_type", "reason",
        "evidence_status", "downstream_observed", "validation_status", "validation_note",
        "transcription_engine", "transcription_model", "correction_model", "model_profile", "model_evidence",
        "replacement_mode", "target_file", "proposal_fingerprint", "review_stage", "remark",
    )
    markdown = [
        f"<!-- selection_mode: {selection_mode} -->",
        f"<!-- selected_count: {len(recordings)} -->",
        f"<!-- selected_from: {selected_from} -->",
        f"<!-- selected_to: {selected_to} -->",
        "<!-- replacement_mode: pending -->",
        "<!-- target_file: -->",
        "<!-- proposal_source: agent_proposals.json; downstream files are validation only -->",
        "## 欄位分組（由左至右）",
        "",
        "1. **提議與審核**：candidate_id、target_section、recording、timestamp、replacement_risk、protected_term_match、review_status、protected_term_action、source_text、source_or_pattern、replacement、rule_type、reason。",
        "2. **下游驗證**：evidence_status、downstream_observed、validation_status、validation_note。",
        "3. **模型稽核**：transcription_engine、transcription_model、correction_model、model_profile、model_evidence。",
        "4. **匯入路由與系統稽核**：replacement_mode、target_file、proposal_fingerprint、review_stage、remark。",
        "",
        "candidate_id 是人工引用索引；proposal_fingerprint 是完整性檢查值，兩者由建置腳本維護。",
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "---|" * len(headers),
    ]
    for row in rows:
        markdown.append("| " + " | ".join(escape_cell(row[key]) for key in headers) + " |")
    args.output.write_text("\n".join(markdown) + "\n", encoding="utf-8")

    mixed_model_profiles = len(model_record_counts) > 1
    print(
        f"selected={len(recordings)} selected_from={selected_from} selected_to={selected_to} "
        f"selected_range={selected_from}..{selected_to} full_evidence_records={full_evidence_records} "
        f"partial_evidence_records={partial_evidence_records} review_items={len(rows)} "
        f"review_records={len(review_records)} transcription_review_items={section_review_items['Transcription']} "
        f"final_output_review_items={section_review_items['FinalOutput']} "
        f"existing_duplicate_items={existing_duplicate_items} existing_conflict_items={existing_conflict_items} "
        f"transcription_duplicates={section_duplicate_items['Transcription']} "
        f"final_output_duplicates={section_duplicate_items['FinalOutput']} "
        f"transcription_conflicts={section_conflict_items['Transcription']} "
        f"final_output_conflicts={section_conflict_items['FinalOutput']} "
        f"batch_duplicate_items={batch_duplicate_items} observed_diff_items={observed_diff_items} "
        f"invalid_proposals={invalid_proposals} skipped_no_evidence={skipped_no_evidence} "
        f"missing_audio_files={missing_audio_files} "
        f"missing_core_files={','.join(f'{filename}:{missing_core_files[filename]}' for filename in CORE_FILES if missing_core_files[filename]) or '-'} "
        f"unknown_model_records={unknown_model_records} mixed_model_profiles={str(mixed_model_profiles).lower()} "
        f"review_required_items={review_required_items} protected_terms_checked={str(protected_terms_checked).lower()} "
        f"replacement_risks={','.join(f'{risk}:{risk_counts[risk]}' for risk in sorted(risk_counts)) or '-'} "
        f"protected_term_matches={','.join(f'{status}:{protected_match_counts[status]}' for status in sorted(protected_match_counts)) or '-'} "
        f"cross_model_scope_warnings={cross_model_scope_warnings} legacy_unscoped_warnings={legacy_unscoped_warnings} "
        f"model_profiles={','.join(f'{profile}:{model_record_counts[profile]}:{model_candidate_counts[profile]}:{model_unknown_record_counts[profile]}' for profile in sorted(model_record_counts))} "
        f"transcription_models={','.join(f'{model}:{transcription_model_record_counts[model]}:{transcription_model_candidate_counts[model]}' for model in sorted(transcription_model_record_counts))} "
        f"correction_models={','.join(f'{model}:{correction_model_record_counts[model]}:{correction_model_candidate_counts[model]}' for model in sorted(correction_model_record_counts))} "
        f"scope_warning_profiles={','.join(f'{profile}:{count}' for profile, count in sorted(scope_warning_profiles.items()))} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
