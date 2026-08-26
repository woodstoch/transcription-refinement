#!/usr/bin/env python3
"""Manage the user-owned Literal terms that must not be replaced."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TARGET_SECTIONS = ("Transcription", "FinalOutput")
ENTRY_FIELDS = {"target_section", "term", "reason", "added_at", "updated_at", "source_candidate_id"}
ROW_SPLIT = re.compile(r"(?<!\\)\|")
SCHEMA = Path(__file__).resolve().parents[1] / "references" / "protected_terms.schema.json"


def normalize_term(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def default_document() -> dict[str, Any]:
    return {"schema_version": 1, "entries": []}


def load_document(path: Path, allow_missing: bool = False) -> dict[str, Any]:
    if not path.is_file():
        if allow_missing:
            return default_document()
        raise ValueError(f"protected terms file not found: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid protected terms file: {path}: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("entries"), list):
        raise ValueError(f"invalid protected terms structure: {path}")
    seen: set[tuple[str, str]] = set()
    for entry in document["entries"]:
        if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
            raise ValueError("each protected term entry must contain exactly the schema fields")
        if entry["target_section"] not in TARGET_SECTIONS or not all(isinstance(entry[field], str) and entry[field] for field in ENTRY_FIELDS):
            raise ValueError("protected term entries contain invalid values")
        normalized = normalize_term(entry["term"])
        if normalized != entry["term"]:
            raise ValueError("protected terms must be NFC-normalized")
        key = (entry["target_section"], normalized)
        if key in seen:
            raise ValueError(f"duplicate protected term: {key[0]} / {key[1]!r}")
        seen.add(key)
    return document


def save_document(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_cell(value: str) -> str:
    return value.strip().replace("\\|", "|")


def read_candidate(path: Path, candidate_id: str) -> dict[str, str]:
    header: dict[str, int] | None = None
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [clean_cell(cell) for cell in ROW_SPLIT.split(stripped[1:-1])]
        if len(cells) < 3 or all(re.fullmatch(r"[-: ]+", cell) for cell in cells):
            continue
        if cells[0] in {"candidate_id", "timestamp"}:
            header = {name: index for index, name in enumerate(cells)}
            continue
        if not header or "candidate_id" not in header or "source_or_pattern" not in header:
            continue
        def value(name: str, default: str = "") -> str:
            index = header.get(name)
            return cells[index].strip() if index is not None and index < len(cells) else default
        if value("candidate_id") == candidate_id:
            return {
                "candidate_id": candidate_id,
                "target_section": value("target_section"),
                "rule_type": value("rule_type", "Literal"),
                "source_or_pattern": value("source_or_pattern"),
                "review_status": value("review_status", value("status")).casefold(),
                "line_number": str(line_number),
            }
    raise ValueError(f"candidate_id not found: {candidate_id}")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_for(section: str, term: str) -> tuple[str, str]:
    if section not in TARGET_SECTIONS:
        raise ValueError(f"unsupported target_section: {section}")
    normalized = normalize_term(term.strip())
    if not normalized:
        raise ValueError("protected term must not be empty")
    return section, normalized


def add_term(args: argparse.Namespace) -> int:
    document = load_document(args.file, allow_missing=True)
    section = args.section
    term = args.term
    source_candidate_id = "manual"
    if args.candidate_id:
        if not args.refinement:
            raise ValueError("--refinement is required with --candidate-id")
        candidate = read_candidate(args.refinement, args.candidate_id)
        if candidate["review_status"] != "rejected":
            raise ValueError("a candidate must be review_status=rejected before adding its term to the protected list")
        if candidate["target_section"] not in TARGET_SECTIONS:
            raise ValueError("candidate has an unsupported target_section")
        if candidate["rule_type"].casefold() != "literal":
            raise ValueError("only Literal candidates can be added to the protected list")
        section = candidate["target_section"]
        term = candidate["source_or_pattern"]
        source_candidate_id = candidate["candidate_id"]
    elif not (section and term):
        raise ValueError("provide --candidate-id or both --section and --term")
    section, term = key_for(section, term)
    timestamp = now()
    for entry in document["entries"]:
        if entry["target_section"] == section and entry["term"] == term:
            entry["reason"] = args.reason
            entry["updated_at"] = timestamp
            entry["source_candidate_id"] = source_candidate_id
            break
    else:
        document["entries"].append({
            "target_section": section,
            "term": term,
            "reason": args.reason,
            "added_at": timestamp,
            "updated_at": timestamp,
            "source_candidate_id": source_candidate_id,
        })
    save_document(args.file, document)
    print(f"protected term added/updated section={section} term={term!r} file={args.file}")
    return 0


def resolve_remove(args: argparse.Namespace) -> tuple[str, str]:
    if args.candidate_id:
        if not args.refinement:
            raise ValueError("--refinement is required with --candidate-id")
        candidate = read_candidate(args.refinement, args.candidate_id)
        if candidate["rule_type"].casefold() != "literal":
            raise ValueError("only Literal candidates can identify a protected term")
        return key_for(candidate["target_section"], candidate["source_or_pattern"])
    if args.section and args.term:
        return key_for(args.section, args.term)
    raise ValueError("provide --candidate-id or both --section and --term")


def remove_term(args: argparse.Namespace) -> int:
    document = load_document(args.file)
    section, term = resolve_remove(args)
    before = len(document["entries"])
    document["entries"] = [entry for entry in document["entries"] if (entry["target_section"], entry["term"]) != (section, term)]
    if len(document["entries"]) == before:
        raise ValueError(f"protected term not found: {section} / {term!r}")
    save_document(args.file, document)
    print(f"protected term removed section={section} term={term!r} file={args.file}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage protected Literal replacement terms.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--file", type=Path, default=Path("global_replacements.protected_terms.json"))
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--file", type=Path, default=Path("global_replacements.protected_terms.json"))
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--file", type=Path, default=Path("global_replacements.protected_terms.json"))
    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("--file", type=Path, default=Path("global_replacements.protected_terms.json"))
    add_parser.add_argument("--candidate-id")
    add_parser.add_argument("--refinement", type=Path)
    add_parser.add_argument("--section", choices=TARGET_SECTIONS)
    add_parser.add_argument("--term")
    add_parser.add_argument("--reason", required=True)
    remove_parser = subparsers.add_parser("remove")
    remove_parser.add_argument("--file", type=Path, default=Path("global_replacements.protected_terms.json"))
    remove_parser.add_argument("--candidate-id")
    remove_parser.add_argument("--refinement", type=Path)
    remove_parser.add_argument("--section", choices=TARGET_SECTIONS)
    remove_parser.add_argument("--term")
    args = parser.parse_args()
    try:
        if args.command == "init":
            if args.file.exists():
                raise ValueError(f"protected terms file already exists: {args.file}")
            save_document(args.file, default_document())
            print(f"initialized protected terms file={args.file}")
            return 0
        if args.command == "validate":
            document = load_document(args.file)
            print(f"valid protected terms file={args.file} entries={len(document['entries'])}")
            return 0
        if args.command == "list":
            document = load_document(args.file)
            for entry in document["entries"]:
                print(f"{entry['target_section']}\t{entry['term']}\t{entry['reason']}")
            return 0
        if args.command == "add":
            return add_term(args)
        return remove_term(args)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
