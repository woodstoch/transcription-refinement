#!/usr/bin/env python3
"""Import approved raw-source-first replacement rows into both sections."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROW_SPLIT = re.compile(r"(?<!\\)\|")
APPROVED_STATUSES = {"approved", "keep", "採用", "通過"}
TARGET_SECTIONS = ("Transcription", "FinalOutput")
RUNTIME_SCHEMA = Path(__file__).resolve().parents[1] / "references" / "global_replacements.runtime.schema.json"
SCOPE_SCHEMA = Path(__file__).resolve().parents[1] / "references" / "global_replacements.scope.schema.json"
PROTECTED_TERMS_SCHEMA = Path(__file__).resolve().parents[1] / "references" / "protected_terms.schema.json"
UNKNOWN_MODEL = "unknown"


@dataclass(frozen=True)
class ApprovedRow:
    candidate_id: str
    proposal_fingerprint: str
    target_section: str
    rule_type: str
    source_text: str
    source: str
    target: str
    reason: str
    replacement_risk: str
    protected_term_match: str
    line_number: int
    timestamp: str
    transcription_engine: str
    transcription_model: str
    correction_model: str
    model_profile: str
    model_evidence: str
    evidence_status: str
    downstream_observed: str
    validation_status: str
    validation_note: str
    review_status: str
    replacement_mode: str
    target_file: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import approved rows from refinement.md.")
    parser.add_argument("--refinement", type=Path, default=Path("refinement.md"), help="Reviewed Markdown table.")
    parser.add_argument(
        "--replacements",
        type=Path,
        default=Path("global_replacements.json"),
        help="Replacement JSON file.",
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
        help="Skill-owned model-scope registry; never read by ZeroType.",
    )
    parser.add_argument(
        "--protected-terms",
        type=Path,
        default=Path("global_replacements.protected_terms.json"),
        help="User-owned protected Literal terms; approved matches are removed after import.",
    )
    parser.add_argument(
        "--mode",
        choices=("mixed", "separate"),
        required=True,
        help="Import scope selected by the user for this refinement batch.",
    )
    parser.add_argument(
        "--profile",
        help="Required model_profile when --mode separate is selected.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and report without writing JSON.")
    return parser.parse_args()


def clean_cell(value: str) -> str:
    return value.strip().replace("\\|", "|")


def normalize_term(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def load_protected_terms(path: Path) -> tuple[dict[tuple[str, str], dict[str, str]], bool]:
    if not path.is_file():
        return {}, False
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid protected terms file: {path}: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("entries"), list):
        raise ValueError(f"invalid protected terms structure: {path}")
    required = {"target_section", "term", "reason", "added_at", "updated_at", "source_candidate_id"}
    result: dict[tuple[str, str], dict[str, str]] = {}
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


def save_protected_terms(path: Path, entries: dict[tuple[str, str], dict[str, str]]) -> None:
    document = {"schema_version": 1, "entries": list(entries.values())}
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def parse_refinement_metadata(path: Path) -> dict[str, str]:
    metadata = {"replacement_mode": "pending", "target_file": ""}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"<!--\s*([a-z_]+):\s*(.*?)\s*-->", line.strip())
        if match and match.group(1) in metadata:
            metadata[match.group(1)] = match.group(2)
    return metadata


def approved_pairs(path: Path) -> list[ApprovedRow]:
    """Read the current model-aware table and the legacy four-column table."""
    pairs: list[ApprovedRow] = []
    header: dict[str, int] | None = None
    candidate_id_index: int | None = None
    proposal_fingerprint_index: int | None = None
    source_text_index: int | None = None
    reason_index: int | None = None
    replacement_risk_index: int | None = None
    protected_term_match_index: int | None = None
    evidence_status_index: int | None = None
    downstream_observed_index: int | None = None
    validation_status_index: int | None = None
    validation_note_index: int | None = None
    timestamp_index: int | None = 0
    engine_index: int | None = None
    transcription_model_index: int | None = None
    correction_model_index: int | None = None
    profile_index: int | None = None
    evidence_index: int | None = None
    mode_index: int | None = None
    target_file_index: int | None = None
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped_line = line.strip()
        if not (stripped_line.startswith("|") and stripped_line.endswith("|")):
            continue
        cells = [clean_cell(cell) for cell in ROW_SPLIT.split(stripped_line[1:-1])]
        if len(cells) < 3:
            continue
        if all(re.fullmatch(r"[-: ]+", cell) for cell in cells):
            continue
        if cells[0] in {"timestamp", "candidate_id"}:
            header = {name.strip(): index for index, name in enumerate(cells)}
            continue
        if header and "source_or_pattern" in header:
            source_index = header["source_or_pattern"]
            target_index = header.get("replacement")
            type_index = header.get("rule_type")
            section_index = header.get("target_section")
            status_index = header.get("status")
            if status_index is None:
                status_index = header.get("review_status")
            timestamp_index = header.get("timestamp", 0)
            candidate_id_index = header.get("candidate_id")
            proposal_fingerprint_index = header.get("proposal_fingerprint")
            source_text_index = header.get("source_text")
            reason_index = header.get("reason")
            replacement_risk_index = header.get("replacement_risk")
            protected_term_match_index = header.get("protected_term_match")
            evidence_status_index = header.get("evidence_status")
            downstream_observed_index = header.get("downstream_observed")
            validation_status_index = header.get("validation_status")
            validation_note_index = header.get("validation_note")
            engine_index = header.get("transcription_engine")
            transcription_model_index = header.get("transcription_model")
            correction_model_index = header.get("correction_model")
            profile_index = header.get("model_profile")
            evidence_index = header.get("model_evidence")
            mode_index = header.get("replacement_mode")
            target_file_index = header.get("target_file")
            if target_index is None or max(source_index, target_index) >= len(cells):
                continue
            target_section = (
                cells[section_index].strip()
                if section_index is not None and section_index < len(cells) and cells[section_index].strip()
                else "Transcription"
            )
            rule_type = cells[type_index].strip() if type_index is not None and type_index < len(cells) else "Literal"
            source = cells[source_index]
            target = cells[target_index]
            status = cells[status_index].strip().casefold() if status_index is not None and status_index < len(cells) else ""
        else:
            # Backward-compatible legacy table: timestamp | source | target | remark
            _, source, target = cells[:3]
            target_section = "Transcription"
            rule_type = "Literal"
            status = ""
            timestamp_index = 0
            candidate_id_index = None
            proposal_fingerprint_index = None
            source_text_index = None
            reason_index = None
            replacement_risk_index = None
            protected_term_match_index = None
            evidence_status_index = None
            downstream_observed_index = None
            validation_status_index = None
            validation_note_index = None
            engine_index = None
            transcription_model_index = None
            correction_model_index = None
            profile_index = None
            evidence_index = None
            mode_index = None
            target_file_index = None
        if status and status not in APPROVED_STATUSES:
            continue
        if source and target:
            def cell_at(index: int | None, default: str = "") -> str:
                return cells[index].strip() if index is not None and index < len(cells) else default

            pairs.append(
                ApprovedRow(
                    candidate_id=cell_at(candidate_id_index),
                    proposal_fingerprint=cell_at(proposal_fingerprint_index),
                    target_section=target_section,
                    rule_type=rule_type,
                    source_text=cell_at(source_text_index, source),
                    source=source,
                    target=target,
                    reason=cell_at(reason_index),
                    replacement_risk=cell_at(replacement_risk_index, "unknown") or "unknown",
                    protected_term_match=cell_at(protected_term_match_index, "not_checked") or "not_checked",
                    line_number=line_number,
                    timestamp=cell_at(timestamp_index),
                    transcription_engine=cell_at(engine_index, UNKNOWN_MODEL) or UNKNOWN_MODEL,
                    transcription_model=cell_at(transcription_model_index, UNKNOWN_MODEL) or UNKNOWN_MODEL,
                    correction_model=cell_at(correction_model_index, UNKNOWN_MODEL) or UNKNOWN_MODEL,
                    model_profile=cell_at(profile_index, UNKNOWN_MODEL) or UNKNOWN_MODEL,
                    model_evidence=cell_at(evidence_index),
                    evidence_status=cell_at(evidence_status_index),
                    downstream_observed=cell_at(downstream_observed_index),
                    validation_status=cell_at(validation_status_index),
                    validation_note=cell_at(validation_note_index),
                    review_status=status or "approved",
                    replacement_mode=cell_at(mode_index, "pending") or "pending",
                    target_file=cell_at(target_file_index),
                )
            )
    return pairs


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
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        definition = schema["definitions"]["replacementSection"]
        regex = definition["properties"]["Regex"]
        regex_items = regex["items"]
        required = set(regex_items["required"])
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as error:
        raise ValueError(f"invalid runtime schema: {path}") from error
    if (
        schema.get("type") != "object"
        or regex.get("type") != "array"
        or regex_items.get("type") != "object"
        or required != {"Pattern", "Replacement"}
    ):
        raise ValueError(f"runtime schema does not describe object Regex rules: {path}")


def validate_scope_schema(path: Path) -> None:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        rule = schema["definitions"]["rule"]
        profile = schema["definitions"]["profile"]
        rule_required = set(rule["required"])
        profile_required = set(profile["required"])
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as error:
        raise ValueError(f"invalid scope schema: {path}") from error
    if rule_required != {
        "fingerprint",
        "target_section",
        "rule_type",
        "source_or_pattern",
        "replacement",
        "scope",
        "target_file",
        "observed_profiles",
        "observed_recordings",
    } or profile_required != {
        "transcription_engine",
        "transcription_model",
        "correction_model",
        "first_seen",
        "last_seen",
    }:
        raise ValueError(f"scope schema has an unsupported contract: {path}")


def default_scope_registry() -> dict[str, Any]:
    return {"version": 1, "profiles": {}, "rules": []}


def load_scope_registry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return default_scope_registry()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid scope registry: {path}") from error
    if (
        not isinstance(document, dict)
        or document.get("version") != 1
        or not isinstance(document.get("profiles"), dict)
        or not isinstance(document.get("rules"), list)
    ):
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


def same_target_file(declared: str, actual: Path) -> bool:
    if not declared:
        return False
    declared_path = Path(declared)
    if declared_path.is_absolute():
        return declared_path.resolve() == actual.resolve()
    return declared_path.as_posix() == actual.as_posix() or declared_path.name == actual.name


def validate_import_scope(args: argparse.Namespace, metadata: dict[str, str], rows: list[ApprovedRow]) -> None:
    if args.mode == "separate":
        if not args.profile:
            raise ValueError("--profile is required for --mode separate")
        if not args.replacements.is_file():
            raise ValueError("separate mode requires an existing --replacements file")
        if args.replacements.name == "global_replacements.json":
            raise ValueError("separate mode requires a model-specific replacement file")
    elif args.profile:
        raise ValueError("--profile is only valid with --mode separate")
    if args.mode == "mixed" and args.replacements.name != "global_replacements.json":
        raise ValueError("mixed mode target must be global_replacements.json")

    if metadata.get("replacement_mode") != args.mode:
        raise ValueError(
            f"refinement replacement_mode={metadata.get('replacement_mode')!r} does not match --mode {args.mode!r}"
        )
    if not same_target_file(metadata.get("target_file", ""), args.replacements):
        raise ValueError("refinement target_file does not match --replacements")
    for row in rows:
        if row.replacement_mode != args.mode:
            raise ValueError(f"line {row.line_number}: replacement_mode does not match --mode {args.mode}")
        if not same_target_file(row.target_file, args.replacements):
            raise ValueError(f"line {row.line_number}: target_file does not match --replacements")
        row_has_unknown_model = (
            row.transcription_model == UNKNOWN_MODEL
            or row.correction_model == UNKNOWN_MODEL
            or row.model_profile in {"", UNKNOWN_MODEL, "unknown__unknown__unknown"}
        )
        if args.mode == "separate" and not row_has_unknown_model and row.model_profile != args.profile:
            raise ValueError(
                f"line {row.line_number}: model_profile {row.model_profile!r} does not match --profile {args.profile!r}"
            )
        if args.mode == "separate" and row_has_unknown_model and args.profile in {"", UNKNOWN_MODEL, "unknown__unknown__unknown"}:
            raise ValueError(f"line {row.line_number}: unknown model requires an explicit --profile assignment")


def update_scope_registry(
    registry: dict[str, Any], rows: list[ApprovedRow], mode: str, target_file: Path, profile_override: str | None = None
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    profiles = registry.setdefault("profiles", {})
    rules = registry.setdefault("rules", [])
    by_fingerprint = {
        entry.get("fingerprint"): entry
        for entry in rules
        if isinstance(entry, dict) and isinstance(entry.get("fingerprint"), str)
    }
    for row in rows:
        profile = profile_override if mode == "separate" and profile_override else (row.model_profile or UNKNOWN_MODEL)
        profile_entry = profiles.setdefault(
            profile,
            {
                "transcription_engine": row.transcription_engine or UNKNOWN_MODEL,
                "transcription_model": row.transcription_model or UNKNOWN_MODEL,
                "correction_model": row.correction_model or UNKNOWN_MODEL,
                "first_seen": now,
                "last_seen": now,
            },
        )
        profile_entry["last_seen"] = now
        fingerprint = rule_fingerprint(row.target_section, row.rule_type, row.source, row.target)
        entry = by_fingerprint.get(fingerprint)
        if entry is None:
            entry = {
                "fingerprint": fingerprint,
                "target_section": row.target_section,
                "rule_type": row.rule_type,
                "source_or_pattern": row.source,
                "replacement": row.target,
                "scope": mode,
                "target_file": str(target_file),
                "observed_profiles": [],
                "observed_recordings": [],
            }
            rules.append(entry)
            by_fingerprint[fingerprint] = entry
        entry["scope"] = mode
        entry["target_file"] = str(target_file)
        if profile not in entry["observed_profiles"]:
            entry["observed_profiles"].append(profile)
        if row.timestamp and row.timestamp not in entry["observed_recordings"]:
            entry["observed_recordings"].append(row.timestamp)
    return registry


def scope_warning_for_row(registry: dict[str, Any], row: ApprovedRow, profile_override: str | None = None) -> str | None:
    fingerprint = rule_fingerprint(row.target_section, row.rule_type, row.source, row.target)
    entry = next(
        (
            candidate
            for candidate in registry.get("rules", [])
            if isinstance(candidate, dict) and candidate.get("fingerprint") == fingerprint
        ),
        None,
    )
    if entry is None:
        return "legacy_unscoped"
    profile = profile_override or row.model_profile
    if profile not in set(entry.get("observed_profiles", [])):
        return "cross_model_scope_warning"
    return None


def find_key_casefold(mapping: dict[str, list[str]], target: str) -> str | None:
    matches = [key for key in mapping if key.casefold() == target.casefold()]
    if len(matches) > 1:
        raise ValueError(f"case-only duplicate canonical keys: {', '.join(matches)}")
    return matches[0] if matches else None


def source_owners(mapping: dict[str, list[str]]) -> dict[str, set[str]]:
    owners: dict[str, set[str]] = defaultdict(set)
    for target, sources in mapping.items():
        for source in sources:
            owners[source.casefold()].add(target)
    return owners


def regex_owners(rules: list[dict[str, str]]) -> dict[str, set[str]]:
    owners: dict[str, set[str]] = defaultdict(set)
    for rule in rules:
        owners[rule["Pattern"]].add(rule["Replacement"])
    return owners


def regex_matches_literal_sources(
    rules: list[dict[str, str]], value: str
) -> dict[str, set[str]]:
    """Find existing Regex rules that match a literal source exactly.

    This prevents changing a rule's representation (Literal -> Regex or the
    reverse) from reintroducing an already-known correction. Invalid legacy
    patterns are left to the normal Regex validation path.
    """
    matches: dict[str, set[str]] = defaultdict(set)
    for rule in rules:
        try:
            if re.fullmatch(rule["Pattern"], value):
                matches[rule["Pattern"]].add(rule["Replacement"])
        except re.error:
            continue
    return matches


def main() -> int:
    args = parse_args()
    if not args.refinement.is_file():
        print(f"error: refinement file not found: {args.refinement}", file=sys.stderr)
        return 2
    if not args.replacements.is_file():
        print(f"error: replacement file not found: {args.replacements}", file=sys.stderr)
        return 2

    try:
        validate_runtime_schema(args.schema)
        validate_scope_schema(SCOPE_SCHEMA)
        document = json.loads(args.replacements.read_text(encoding="utf-8"))
        sections: dict[str, tuple[dict[str, list[str]], list[dict[str, str]]]] = {}
        for section_name in TARGET_SECTIONS:
            sections[section_name] = validate_section(document[section_name], section_name)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"error: invalid replacement JSON structure: {error}", file=sys.stderr)
        return 2

    try:
        pairs = approved_pairs(args.refinement)
        refinement_metadata = parse_refinement_metadata(args.refinement)
        scope_registry = load_scope_registry(args.scope_file)
        protected_terms, protected_terms_checked = load_protected_terms(args.protected_terms)
        validate_import_scope(args, refinement_metadata, pairs)
        for row in pairs:
            if not row.proposal_fingerprint:
                continue
            expected = proposal_fingerprint(
                row.target_section,
                row.rule_type,
                row.source_text,
                row.source,
                row.target,
                row.reason,
            )
            if expected != row.proposal_fingerprint and row.review_status != "approved":
                raise ValueError(
                    f"line {row.line_number}: proposal fields changed without explicit approved review"
                )
    except (OSError, UnicodeError) as error:
        print(f"error: cannot read refinement file: {error}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"error: invalid refinement scope: {error}", file=sys.stderr)
        return 2
    modified_after_review = sum(
        bool(row.proposal_fingerprint)
        and proposal_fingerprint(row.target_section, row.rule_type, row.source_text, row.source, row.target, row.reason)
        != row.proposal_fingerprint
        for row in pairs
    )
    if not pairs:
        print("無需匯入 approved_pairs=0; no changes written")
        return 0

    owners = {section: source_owners(sections[section][0]) for section in TARGET_SECTIONS}
    regex_rule_owners = {section: regex_owners(sections[section][1]) for section in TARGET_SECTIONS}
    additions: dict[str, dict[str, list[str]]] = {section: defaultdict(list) for section in TARGET_SECTIONS}
    regex_additions: dict[str, list[tuple[str, str]]] = {section: [] for section in TARGET_SECTIONS}
    skipped = 0
    conflicts: list[str] = []
    section_skips = {section: 0 for section in TARGET_SECTIONS}
    section_conflicts = {section: 0 for section in TARGET_SECTIONS}
    cross_model_scope_warnings = 0
    legacy_unscoped_warnings = 0
    protected_removals: dict[tuple[str, str], dict[str, str]] = {}

    def queue_protected_removal(row: ApprovedRow) -> None:
        if row.rule_type == "Literal" and protected_terms_checked:
            key = (row.target_section, normalize_term(row.source))
            if key in protected_terms:
                protected_removals[key] = protected_terms[key]

    def count_scope_warning(row: ApprovedRow) -> None:
        nonlocal cross_model_scope_warnings, legacy_unscoped_warnings
        warning = scope_warning_for_row(scope_registry, row, args.profile if args.mode == "separate" else None)
        if warning == "cross_model_scope_warning":
            cross_model_scope_warnings += 1
        elif warning == "legacy_unscoped":
            legacy_unscoped_warnings += 1

    def add_conflict(row: ApprovedRow, message: str) -> None:
        section_conflicts[row.target_section] += 1
        conflicts.append(message)

    for row in pairs:
        target_section = row.target_section
        rule_type = row.rule_type
        source = row.source
        requested_target = row.target
        line_number = row.line_number
        if target_section not in TARGET_SECTIONS:
            conflicts.append(f"line {line_number}: unsupported target_section {target_section!r}")
            continue
        normalized_type = rule_type.casefold()
        if normalized_type not in {"literal", "regex"}:
            add_conflict(row, f"line {line_number}: unsupported rule_type {rule_type!r}")
            continue

        if normalized_type == "regex":
            try:
                re.compile(source)
            except re.error as error:
                add_conflict(row, f"line {line_number}: invalid Regex pattern {source!r}: {error}")
                continue
            literal_matches: dict[str, set[str]] = defaultdict(set)
            for target, literal_sources in sections[target_section][0].items():
                for literal_source in literal_sources:
                    try:
                        if re.fullmatch(source, literal_source):
                            literal_matches[source].add(target)
                    except re.error:
                        break
            if literal_matches.get(source):
                existing_targets = literal_matches[source]
                if any(target.casefold() == requested_target.casefold() for target in existing_targets):
                    skipped += 1
                    section_skips[target_section] += 1
                    count_scope_warning(row)
                    continue
                add_conflict(row,
                    f"line {line_number}: {target_section} Regex pattern {source!r} overlaps Literal targets "
                    f"{', '.join(sorted(existing_targets))}"
                )
                continue
            existing_replacements = regex_rule_owners[target_section].get(source, set())
            if existing_replacements and requested_target not in existing_replacements:
                add_conflict(row,
                    f"line {line_number}: {target_section} Regex pattern {source!r} already maps to "
                    f"{', '.join(sorted(existing_replacements))}"
                )
                continue
            if existing_replacements and requested_target in existing_replacements:
                skipped += 1
                section_skips[target_section] += 1
                count_scope_warning(row)
                queue_protected_removal(row)
                continue
            if (source, requested_target) in regex_additions[target_section]:
                skipped += 1
                section_skips[target_section] += 1
                queue_protected_removal(row)
                continue
            regex_additions[target_section].append((source, requested_target))
            regex_rule_owners[target_section][source].add(requested_target)
            continue

        literals = sections[target_section][0]
        target = find_key_casefold(literals, requested_target) or requested_target
        source_key = source.casefold()
        existing_targets = owners[target_section].get(source_key, set())
        equivalent_targets = {name for name in existing_targets if name.casefold() == target.casefold()}
        conflicting_targets = existing_targets - equivalent_targets
        if conflicting_targets:
            add_conflict(row,
                f"line {line_number}: {target_section} source {source!r} already belongs to "
                f"{', '.join(sorted(conflicting_targets))}"
            )
            continue

        regex_matches = regex_matches_literal_sources(sections[target_section][1], source)
        regex_targets = {
            replacement
            for replacements in regex_matches.values()
            for replacement in replacements
        }
        if regex_targets:
            if any(replacement.casefold() == target.casefold() for replacement in regex_targets):
                skipped += 1
                section_skips[target_section] += 1
                count_scope_warning(row)
                queue_protected_removal(row)
                continue
            add_conflict(row,
                f"line {line_number}: {target_section} Literal source {source!r} overlaps Regex targets "
                f"{', '.join(sorted(regex_targets))}"
            )
            continue

        current_sources = literals.get(target, [])
        pending_sources = additions[target_section][target]
        if source in current_sources or source in pending_sources:
            skipped += 1
            section_skips[target_section] += 1
            if source in current_sources:
                count_scope_warning(row)
            queue_protected_removal(row)
            continue
        additions[target_section][target].append(source)
        owners[target_section][source_key].add(target)
        queue_protected_removal(row)

    if conflicts:
        print(
            "conflicts detected; no changes written: "
            + " ".join(f"{section.lower()}_conflict={section_conflicts[section]}" for section in TARGET_SECTIONS),
            file=sys.stderr,
        )
        print("\n".join(conflicts), file=sys.stderr)
        return 1

    literal_counts = {
        section: sum(len(sources) for sources in additions[section].values()) for section in TARGET_SECTIONS
    }
    regex_counts = {section: len(regex_additions[section]) for section in TARGET_SECTIONS}
    added = sum(literal_counts.values()) + sum(regex_counts.values())

    if args.dry_run:
        print(
            f"dry_run mode={args.mode} profile={args.profile or '-'} target_file={args.replacements} "
            f"approved_pairs={len(pairs)} total_add={added} skip={skipped} "
            f"proposal_modified_after_review={modified_after_review} "
            f"protected_terms_checked={str(protected_terms_checked).lower()} "
            f"protected_terms_remove={len(protected_removals)} "
            + " ".join(
                f"{section.lower()}_literal_add={literal_counts[section]} "
                f"{section.lower()}_regex_add={regex_counts[section]} "
                f"{section.lower()}_duplicate={section_skips[section]} "
                f"{section.lower()}_conflict={section_conflicts[section]} "
                f"{section.lower()}_target_groups={len(additions[section])}"
                for section in TARGET_SECTIONS
            )
        )
        print(
            f"scope_profiles={len(scope_registry.get('profiles', {}))} "
            f"scope_rules={len(scope_registry.get('rules', []))} "
            f"cross_model_scope_warnings={cross_model_scope_warnings} "
            f"legacy_unscoped_warnings={legacy_unscoped_warnings}"
        )
        for section in TARGET_SECTIONS:
            print(f"[{section}]")
            for target in sorted(additions[section], key=str.casefold):
                print(f"{target}: {', '.join(additions[section][target])}")
            for pattern, replacement in regex_additions[section]:
                print(f"Regex {pattern}: {replacement}")
        for section, term in sorted(protected_removals):
            print(f"protected term remove {section}: {term}")
        return 0

    for section in TARGET_SECTIONS:
        literals, regex_rules = sections[section]
        for target, sources in additions[section].items():
            canonical_target = find_key_casefold(literals, target) or target
            literals.setdefault(canonical_target, []).extend(sources)
        regex_rules.extend({"Pattern": pattern, "Replacement": replacement} for pattern, replacement in regex_additions[section])

    args.replacements.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        written = json.loads(args.replacements.read_text(encoding="utf-8"))
        for section in TARGET_SECTIONS:
            validate_section(written[section], section)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"error: imported JSON failed validation: {error}", file=sys.stderr)
        return 1
    updated_scope = update_scope_registry(scope_registry, pairs, args.mode, args.replacements, args.profile)
    args.scope_file.write_text(json.dumps(updated_scope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        load_scope_registry(args.scope_file)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"error: updated scope sidecar failed validation: {error}", file=sys.stderr)
        return 1
    if protected_removals:
        if not protected_terms_checked:
            print("error: protected term removals requested but protected terms file was not checked", file=sys.stderr)
            return 1
        remaining = {
            key: entry for key, entry in protected_terms.items() if key not in protected_removals
        }
        try:
            save_protected_terms(args.protected_terms, remaining)
            load_protected_terms(args.protected_terms)
        except (OSError, UnicodeError, ValueError) as error:
            print(f"error: protected terms update failed after replacement import: {error}", file=sys.stderr)
            return 1
    print(
        f"imported mode={args.mode} profile={args.profile or '-'} target_file={args.replacements} "
        f"approved_pairs={len(pairs)} total_add={added} skip={skipped} "
        f"proposal_modified_after_review={modified_after_review} "
        f"protected_terms_checked={str(protected_terms_checked).lower()} protected_terms_remove={len(protected_removals)} "
        + " ".join(
            f"{section.lower()}_literal_add={literal_counts[section]} "
            f"{section.lower()}_regex_add={regex_counts[section]} "
            f"{section.lower()}_duplicate={section_skips[section]} "
            f"{section.lower()}_conflict={section_conflicts[section]}"
            for section in TARGET_SECTIONS
        )
        + f" scope_profiles={len(updated_scope.get('profiles', {}))} scope_rules={len(updated_scope.get('rules', []))}"
        + f" cross_model_scope_warnings={cross_model_scope_warnings} legacy_unscoped_warnings={legacy_unscoped_warnings}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
