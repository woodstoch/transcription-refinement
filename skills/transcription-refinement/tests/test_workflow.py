from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
BUILD = SKILL_ROOT / "scripts" / "build_refinement.py"
IMPORT = SKILL_ROOT / "scripts" / "import_replacements.py"
RUNTIME_SCHEMA = SKILL_ROOT / "references" / "global_replacements.runtime.schema.json"
SCOPE_SCHEMA = SKILL_ROOT / "references" / "global_replacements.scope.schema.json"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def empty_replacements() -> dict[str, object]:
    return {
        "FinalOutput": {"Literals": {}, "Regex": []},
        "Transcription": {"Literals": {}, "Regex": []},
    }


def make_recording(root: Path, name: str, **files: str) -> Path:
    recording = root / name
    recording.mkdir(parents=True)
    for filename, value in files.items():
        (recording / filename).write_text(value, encoding="utf-8")
    return recording


def write_proposals(path: Path, proposals: list[dict[str, object]]) -> None:
    write_json(path, {"version": 1, "proposals": proposals})


def run_build(recordings: Path, replacements: Path, output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(BUILD), "--all", "--recordings", str(recordings), "--replacements", str(replacements), "--output", str(output), *extra],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


class WorkflowTests(unittest.TestCase):
    def test_runtime_schema_declares_observed_object_regex(self) -> None:
        schema = json.loads(RUNTIME_SCHEMA.read_text(encoding="utf-8"))
        definition = schema["definitions"]["replacementSection"]
        regex_items = definition["properties"]["Regex"]["items"]
        actual = empty_replacements()
        actual["FinalOutput"]["Regex"] = [{"Pattern": "(?i)reasoning", "Replacement": "reasoning"}]
        for section in ("Transcription", "FinalOutput"):
            self.assertEqual(schema["properties"][section]["$ref"], "#/definitions/replacementSection")
            self.assertTrue(all(isinstance(rule, dict) for rule in actual[section]["Regex"]))
            self.assertTrue(all(set(rule) == {"Pattern", "Replacement"} for rule in actual[section]["Regex"]))
        self.assertEqual(regex_items["type"], "object")
        self.assertEqual(set(regex_items["required"]), {"Pattern", "Replacement"})

    def test_scope_schema_is_skill_owned_and_model_aware(self) -> None:
        schema = json.loads(SCOPE_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["version"]["minimum"], 1)
        self.assertIn("profiles", schema["properties"])
        self.assertIn("rules", schema["properties"])
        self.assertEqual(
            set(schema["definitions"]["rule"]["required"]),
            {
                "fingerprint", "target_section", "rule_type", "source_or_pattern",
                "replacement", "scope", "target_file", "observed_profiles", "observed_recordings",
            },
        )

    def test_build_uses_raw_sources_for_both_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            make_recording(
                recordings,
                "20260814-100000-001",
                **{
                    "transcription_text.txt": "badword input",
                    "transcription_processed_text.txt": "goodword input",
                    "prompt_correction_text.txt": "badfinal input",
                    "output_text.txt": "goodfinal input",
                },
            )
            replacements = root / "global_replacements.json"
            write_json(replacements, empty_replacements())
            proposals = root / "agent_proposals.json"
            write_proposals(
                proposals,
                [
                    {"recording": "20260814-100000-001", "target_section": "Transcription", "source_text": "badword", "source_or_pattern": "badword", "replacement": "goodword", "rule_type": "Literal", "reason": "Agent lexical proposal"},
                    {"recording": "20260814-100000-001", "target_section": "FinalOutput", "source_text": "badfinal", "source_or_pattern": "badfinal", "replacement": "goodfinal", "rule_type": "Literal", "reason": "Agent lexical proposal"},
                ],
            )
            output = root / "refinement.md"

            result = run_build(recordings, replacements, output, "--proposals", str(proposals))

            self.assertEqual(result.returncode, 0, result.stderr)
            table = output.read_text(encoding="utf-8")
            self.assertIn("| Transcription | STT |", table)
            self.assertIn("| FinalOutput | PromptCorrection |", table)
            self.assertIn("badword", table)
            self.assertIn("candidate-", table)
            self.assertIn("badfinal", table)
            self.assertIn("transcription_review_items=1", result.stdout)
            self.assertIn("final_output_review_items=1", result.stdout)

    def test_duplicates_are_checked_within_each_section(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            make_recording(
                recordings,
                "20260814-100000-002",
                **{
                    "transcription_text.txt": "badword",
                    "transcription_processed_text.txt": "goodword",
                    "prompt_correction_text.txt": "badfinal",
                    "output_text.txt": "goodfinal",
                },
            )
            document = empty_replacements()
            document["Transcription"]["Literals"] = {"goodword": ["badword"]}
            document["FinalOutput"]["Literals"] = {"goodfinal": ["badfinal"]}
            replacements = root / "global_replacements.json"
            write_json(replacements, document)
            proposals = root / "agent_proposals.json"
            write_proposals(
                proposals,
                [
                    {"recording": "20260814-100000-002", "target_section": "Transcription", "source_text": "badword", "source_or_pattern": "badword", "replacement": "goodword"},
                    {"recording": "20260814-100000-002", "target_section": "FinalOutput", "source_text": "badfinal", "source_or_pattern": "badfinal", "replacement": "goodfinal"},
                ],
            )
            output = root / "refinement.md"

            result = run_build(recordings, replacements, output, "--proposals", str(proposals))

            self.assertEqual(result.returncode, 0, result.stderr)
            table = output.read_text(encoding="utf-8")
            self.assertIn("badword", table)
            self.assertIn("| duplicate |", table)
            self.assertIn("existing_duplicate_items=2", result.stdout)
            self.assertIn("review_items=2", result.stdout)

    def test_same_source_can_be_new_in_both_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            make_recording(
                recordings,
                "20260814-100000-003",
                **{
                    "transcription_text.txt": "sameword",
                    "transcription_processed_text.txt": "targetword",
                    "prompt_correction_text.txt": "sameword",
                    "output_text.txt": "targetword",
                },
            )
            replacements = root / "global_replacements.json"
            write_json(replacements, empty_replacements())
            proposals = root / "agent_proposals.json"
            write_proposals(
                proposals,
                [
                    {"recording": "20260814-100000-003", "target_section": "Transcription", "source_text": "sameword", "source_or_pattern": "sameword", "replacement": "targetword"},
                    {"recording": "20260814-100000-003", "target_section": "FinalOutput", "source_text": "sameword", "source_or_pattern": "sameword", "replacement": "targetword"},
                ],
            )
            output = root / "refinement.md"

            result = run_build(recordings, replacements, output, "--proposals", str(proposals))

            self.assertEqual(result.returncode, 0, result.stderr)
            table = output.read_text(encoding="utf-8")
            self.assertEqual(table.count("sameword"), 4)
            self.assertIn("transcription_review_items=1", result.stdout)
            self.assertIn("final_output_review_items=1", result.stdout)

    def test_final_output_conversion_is_not_invented_as_a_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            recording = make_recording(
                recordings,
                "20260814-100000-004",
                **{
                    "transcription_text.txt": "unchanged",
                    "transcription_processed_text.txt": "unchanged",
                    "prompt_correction_text.txt": "badfinal",
                    "output_text.txt": "goodfinal",
                },
            )
            write_json(recording / "foreground_injection_diagnostics.json", {"conversionChanged": True})
            replacements = root / "global_replacements.json"
            write_json(replacements, empty_replacements())
            proposals = root / "agent_proposals.json"
            write_proposals(
                proposals,
                [{"recording": "20260814-100000-004", "target_section": "FinalOutput", "source_text": "badfinal", "source_or_pattern": "badfinal", "replacement": "goodfinal"}],
            )
            output = root / "refinement.md"

            result = run_build(recordings, replacements, output, "--proposals", str(proposals))

            self.assertEqual(result.returncode, 0, result.stderr)
            table = output.read_text(encoding="utf-8")
            self.assertIn("badfinal", table)
            self.assertIn("conversionChanged=true", table)
            self.assertIn("validation_status", table)
            self.assertNotIn("rejected_low_confidence", result.stdout)

    def test_punctuation_spacing_and_case_only_changes_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            make_recording(
                recordings,
                "20260814-100000-006",
                **{
                    "transcription_text.txt": "GET AD PROMPT",
                    "transcription_processed_text.txt": "GET AD prompt",
                    "prompt_correction_text.txt": "same words",
                    "output_text.txt": "same, words",
                },
            )
            replacements = root / "global_replacements.json"
            write_json(replacements, empty_replacements())
            proposals = root / "agent_proposals.json"
            write_proposals(
                proposals,
                [
                    {"recording": name, "target_section": "Transcription", "source_text": source, "source_or_pattern": source, "replacement": target}
                    for name, source, target in (
                        ("20260814-100000-010", "badword", "goodword"),
                        ("20260814-100000-009", "badword2", "goodword2"),
                        ("20260814-100000-008", "badword3", "goodword3"),
                        ("20260814-100000-007", "badword4", "goodword4"),
                    )
                ],
            )
            output = root / "refinement.md"

            result = run_build(recordings, replacements, output, "--proposals", str(proposals))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("review_items=0", result.stdout)
            self.assertEqual(output.read_text(encoding="utf-8").count("| `"), 0)

    def test_agent_proposal_is_retained_even_when_policy_warning_applies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            make_recording(
                recordings,
                "20260814-100000-012",
                **{
                    "transcription_text.txt": "GET AD PROMPT",
                    "transcription_processed_text.txt": "GET AD prompt",
                    "prompt_correction_text.txt": "same words",
                    "output_text.txt": "same, words",
                },
            )
            replacements = root / "global_replacements.json"
            write_json(replacements, empty_replacements())
            proposals = root / "agent_proposals.json"
            write_proposals(
                proposals,
                [{"recording": "20260814-100000-012", "target_section": "Transcription", "source_text": "PROMPT", "source_or_pattern": "PROMPT", "replacement": "prompt"}],
            )
            output = root / "refinement.md"
            result = run_build(recordings, replacements, output, "--proposals", str(proposals))
            self.assertEqual(result.returncode, 0, result.stderr)
            table = output.read_text(encoding="utf-8")
            self.assertIn("PROMPT", table)
            self.assertIn("policy_warning=case_only", table)

    def test_legacy_fallback_is_partial_and_never_reconstructs_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            recording = make_recording(
                recordings,
                "20260814-100000-005",
                **{
                    "prompt_correction_text.txt": "badfinal",
                },
            )
            write_json(recording / "transcription_response_body.json", {"text": "rawword"})
            write_json(
                recording / "prompt_correction_response_body.json",
                {"choices": [{"message": {"content": "badfinal"}}]},
            )
            write_json(recording / "history_entry.json", {"transcriptionText": "rawword", "outputText": "goodfinal"})
            replacements = root / "global_replacements.json"
            write_json(replacements, empty_replacements())
            proposals = root / "agent_proposals.json"
            write_proposals(
                proposals,
                [
                    {"recording": "20260814-100000-005", "target_section": "Transcription", "source_text": "rawword", "source_or_pattern": "rawword", "replacement": "goodword"},
                    {"recording": "20260814-100000-005", "target_section": "FinalOutput", "source_text": "badfinal", "source_or_pattern": "badfinal", "replacement": "goodfinal"},
                ],
            )
            output = root / "refinement.md"

            result = run_build(recordings, replacements, output, "--proposals", str(proposals))

            self.assertEqual(result.returncode, 0, result.stderr)
            table = output.read_text(encoding="utf-8")
            self.assertIn("partial evidence / legacy fallback", table)
            self.assertIn("| FinalOutput | PromptCorrection |", table)
            self.assertIn("| partial_evidence |", table)
            self.assertIn("| evidence_missing |", table)
            self.assertIn("full_evidence_records=0", result.stdout)
            self.assertIn("partial_evidence_records=1", result.stdout)

    def test_missing_downstream_files_do_not_remove_agent_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            make_recording(
                recordings,
                "20260814-100000-013",
                **{
                    "transcription_text.txt": "rawword",
                    "prompt_correction_text.txt": "promptword",
                },
            )
            replacements = root / "global_replacements.json"
            write_json(replacements, empty_replacements())
            proposals = root / "agent_proposals.json"
            write_proposals(
                proposals,
                [
                    {"recording": "20260814-100000-013", "target_section": "Transcription", "source_text": "rawword", "source_or_pattern": "rawword", "replacement": "canonical"},
                    {"recording": "20260814-100000-013", "target_section": "FinalOutput", "source_text": "promptword", "source_or_pattern": "promptword", "replacement": "finalcanonical"},
                ],
            )
            output = root / "refinement.md"
            result = run_build(recordings, replacements, output, "--proposals", str(proposals))
            self.assertEqual(result.returncode, 0, result.stderr)
            table = output.read_text(encoding="utf-8")
            self.assertEqual(result.stdout.count("review_items=2"), 1)
            self.assertEqual(table.count("| missing_downstream |"), 2)
            self.assertIn("rawword", table)
            self.assertIn("promptword", table)

    def test_import_reports_explicit_human_proposal_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replacements = root / "global_replacements.json"
            write_json(replacements, empty_replacements())
            refinement = root / "refinement.md"
            refinement.write_text(
                "\n".join(
                    [
                        "<!-- replacement_mode: mixed -->",
                        "<!-- target_file: global_replacements.json -->",
                        "| candidate_id | proposal_fingerprint | timestamp | recording | target_section | source_text | source_or_pattern | replacement | rule_type | review_status | transcription_engine | transcription_model | correction_model | model_profile | replacement_mode | target_file |",
                        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
                        "| c1 | stale-fingerprint | t1 | t1 | Transcription | badword | badword | goodword | Literal | approved | Remote | whisper | correction | Remote__whisper__correction | mixed | global_replacements.json |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(IMPORT), "--refinement", str(refinement), "--replacements", str(replacements), "--mode", "mixed", "--scope-file", str(root / "scope.json"), "--dry-run"],
                text=True,
                capture_output=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("proposal_modified_after_review=1", result.stdout)

    def test_model_metadata_uses_request_response_loop_and_unknown_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            first = make_recording(
                recordings,
                "20260814-100000-010",
                **{
                    "transcription_text.txt": "badword",
                    "transcription_processed_text.txt": "goodword",
                    "prompt_correction_text.txt": "badfinal",
                    "output_text.txt": "goodfinal",
                },
            )
            write_json(first / "user_prompt_overrides.json", {"effectiveTranscriptionEngine": "Remote"})
            write_json(first / "transcription_request.json", {"model": "whisper-a"})
            write_json(first / "prompt_correction_request.json", {"model": "correction-a"})

            second = make_recording(
                recordings,
                "20260814-100000-009",
                **{
                    "transcription_text.txt": "badword2",
                    "transcription_processed_text.txt": "goodword2",
                    "prompt_correction_text.txt": "badfinal2",
                    "output_text.txt": "goodfinal2",
                },
            )
            write_json(second / "user_prompt_overrides.json", {"effectiveTranscriptionEngine": "Local"})
            write_json(second / "transcription_request.json", {"model": "whisper-b"})
            write_json(second / "prompt_correction_response_body.json", {"model": "correction-response"})
            write_json(second / "prompt_correction_loop_1_response_body.json", {"model": "correction-loop"})

            third = make_recording(
                recordings,
                "20260814-100000-008",
                **{
                    "transcription_text.txt": "badword3",
                    "transcription_processed_text.txt": "goodword3",
                    "prompt_correction_text.txt": "badfinal3",
                    "output_text.txt": "goodfinal3",
                },
            )
            write_json(third / "user_prompt_overrides.json", {"effectiveTranscriptionEngine": "Local"})
            write_json(third / "transcription_request.json", {"model": "whisper-c"})
            write_json(third / "prompt_correction_loop_1_response_body.json", {"model": "correction-loop"})

            make_recording(
                recordings,
                "20260814-100000-007",
                **{
                    "transcription_text.txt": "badword4",
                    "transcription_processed_text.txt": "goodword4",
                    "prompt_correction_text.txt": "badfinal4",
                    "output_text.txt": "goodfinal4",
                },
            )
            replacements = root / "global_replacements.json"
            write_json(replacements, empty_replacements())
            proposals = root / "agent_proposals.json"
            write_proposals(
                proposals,
                [
                    {"recording": name, "target_section": "Transcription", "source_text": source, "source_or_pattern": source, "replacement": target}
                    for name, source, target in (
                        ("20260814-100000-010", "badword", "goodword"),
                        ("20260814-100000-009", "badword2", "goodword2"),
                        ("20260814-100000-008", "badword3", "goodword3"),
                        ("20260814-100000-007", "badword4", "goodword4"),
                    )
                ],
            )
            output = root / "refinement.md"

            result = run_build(recordings, replacements, output, "--proposals", str(proposals))

            self.assertEqual(result.returncode, 0, result.stderr)
            table = output.read_text(encoding="utf-8")
            self.assertIn("Remote__whisper-a__correction-a", table)
            self.assertIn("Local__whisper-b__correction-response", table)
            self.assertIn("Local__whisper-c__correction-loop", table)
            self.assertIn("unknown__unknown__unknown", table)
            self.assertIn("transcription_request.json:model", table)
            self.assertIn("prompt_correction_response_body.json:model", table)
            self.assertIn("prompt_correction_loop_1_response_body.json:model", table)
            self.assertIn("unknown_model_records=1", result.stdout)
            self.assertIn("model_profiles=", result.stdout)

    def test_duplicate_under_new_profile_reports_cross_model_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            recording = make_recording(
                recordings,
                "20260814-100000-011",
                **{
                    "transcription_text.txt": "badword",
                    "transcription_processed_text.txt": "goodword",
                    "prompt_correction_text.txt": "same",
                    "output_text.txt": "same",
                },
            )
            write_json(recording / "user_prompt_overrides.json", {"effectiveTranscriptionEngine": "Remote"})
            write_json(recording / "transcription_request.json", {"model": "new-whisper"})
            write_json(recording / "prompt_correction_request.json", {"model": "new-correction"})
            replacements = root / "global_replacements.json"
            document = empty_replacements()
            document["Transcription"]["Literals"] = {"goodword": ["badword"]}
            write_json(replacements, document)
            proposals = root / "agent_proposals.json"
            write_proposals(
                proposals,
                [{"recording": "20260814-100000-011", "target_section": "Transcription", "source_text": "badword", "source_or_pattern": "badword", "replacement": "goodword"}],
            )
            scope = root / "global_replacements.scope.json"
            write_json(
                scope,
                {
                    "version": 1,
                    "profiles": {
                        "Remote__old-whisper__old-correction": {
                            "transcription_engine": "Remote",
                            "transcription_model": "old-whisper",
                            "correction_model": "old-correction",
                            "first_seen": "2026-01-01T00:00:00+00:00",
                            "last_seen": "2026-01-01T00:00:00+00:00",
                        }
                    },
                    "rules": [
                        {
                            "fingerprint": hashlib.sha256("\x1f".join(("Transcription", "Literal", "badword", "goodword")).encode("utf-8")).hexdigest(),
                            "target_section": "Transcription",
                            "rule_type": "Literal",
                            "source_or_pattern": "badword",
                            "replacement": "goodword",
                            "scope": "separate",
                            "target_file": "old.json",
                            "observed_profiles": ["Remote__old-whisper__old-correction"],
                            "observed_recordings": ["old"],
                        }
                    ],
                },
            )
            output = root / "refinement.md"
            result = run_build(recordings, replacements, output, "--scope-file", str(scope), "--proposals", str(proposals))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("cross_model_scope_warnings=1", result.stdout)
            self.assertIn("legacy_unscoped_warnings=0", result.stdout)

    def test_separate_mode_requires_existing_target_and_matching_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replacements = root / "model-replacements.json"
            write_json(replacements, empty_replacements())
            global_file = root / "global_replacements.json"
            write_json(global_file, empty_replacements())
            refinement = root / "refinement.md"
            refinement.write_text(
                "\n".join(
                    [
                        "<!-- replacement_mode: separate -->",
                        "<!-- target_file: model-replacements.json -->",
                        "| timestamp | target_section | review_stage | evidence_source | source_or_pattern | replacement | rule_type | confidence | reason | status | transcription_engine | transcription_model | correction_model | model_profile | model_evidence | replacement_mode | target_file | remark |",
                        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
                        "| t1 | Transcription | STT | raw | badword | goodword | Literal | medium | test | 採用 | Remote | whisper | correction | Remote__whisper__correction | request | separate | model-replacements.json |  |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            missing = subprocess.run(
                [sys.executable, str(IMPORT), "--refinement", str(refinement), "--replacements", str(root / "missing.json"), "--mode", "separate", "--profile", "Remote__whisper__correction", "--scope-file", str(root / "scope.json")],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertNotEqual(missing.returncode, 0)
            before = replacements.read_bytes()
            dry = subprocess.run(
                [sys.executable, str(IMPORT), "--refinement", str(refinement), "--replacements", str(replacements), "--mode", "separate", "--profile", "Remote__whisper__correction", "--scope-file", str(root / "scope.json"), "--dry-run"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(before, replacements.read_bytes())
            self.assertFalse((root / "scope.json").exists())
            imported = subprocess.run(
                [sys.executable, str(IMPORT), "--refinement", str(refinement), "--replacements", str(replacements), "--mode", "separate", "--profile", "Remote__whisper__correction", "--scope-file", str(root / "scope.json")],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(json.loads(replacements.read_text(encoding="utf-8"))["Transcription"]["Literals"], {"goodword": ["badword"]})
            self.assertEqual(json.loads(global_file.read_text(encoding="utf-8")), empty_replacements())

    def test_separate_mode_rejects_unknown_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replacements = root / "model.json"
            write_json(replacements, empty_replacements())
            refinement = root / "refinement.md"
            refinement.write_text(
                "\n".join(
                    [
                        "<!-- replacement_mode: separate -->",
                        "<!-- target_file: model.json -->",
                        "| timestamp | target_section | source_or_pattern | replacement | rule_type | status | transcription_engine | transcription_model | correction_model | model_profile | replacement_mode | target_file |",
                        "|---|---|---|---|---|---|---|---|---|---|---|---|",
                        "| t1 | Transcription | badword | goodword | Literal | 採用 | unknown | unknown | unknown | unknown__unknown__unknown | separate | model.json |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(IMPORT), "--refinement", str(refinement), "--replacements", str(replacements), "--mode", "separate", "--profile", "unknown__unknown__unknown", "--scope-file", str(root / "scope.json")],
                text=True,
                capture_output=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "scope.json").exists())
            explicit = subprocess.run(
                [sys.executable, str(IMPORT), "--refinement", str(refinement), "--replacements", str(replacements), "--mode", "separate", "--profile", "Remote__whisper__correction", "--scope-file", str(root / "scope.json")],
                text=True,
                capture_output=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                check=False,
            )
            self.assertEqual(explicit.returncode, 0, explicit.stderr)

    def test_import_routes_rows_to_target_sections_and_dry_run_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replacements = root / "global_replacements.json"
            write_json(replacements, empty_replacements())
            refinement = root / "refinement.md"
            refinement.write_text(
                "\n".join(
                    [
                        "<!-- replacement_mode: mixed -->",
                        "<!-- target_file: global_replacements.json -->",
                        "| timestamp | target_section | review_stage | evidence_source | source_or_pattern | replacement | rule_type | confidence | reason | status | transcription_engine | transcription_model | correction_model | model_profile | model_evidence | replacement_mode | target_file | remark |",
                        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
                        "| t1 | Transcription | STT | raw | badword | goodword | Literal | medium | test | 採用 | Remote | whisper | correction | Remote__whisper__correction | test | mixed | global_replacements.json |  |",
                        "| t2 | FinalOutput | PromptCorrection | prompt | (?i)\\bbadfinal\\b | goodfinal | Regex | medium | test | 採用 | Remote | whisper | correction | Remote__whisper__correction | test | mixed | global_replacements.json |  |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            before = replacements.read_bytes()
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            dry = subprocess.run(
                [sys.executable, str(IMPORT), "--refinement", str(refinement), "--replacements", str(replacements), "--mode", "mixed", "--scope-file", str(root / "scope.json"), "--dry-run"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(before, replacements.read_bytes())
            self.assertIn("transcription_literal_add=1", dry.stdout)
            self.assertIn("finaloutput_regex_add=1", dry.stdout)

            imported = subprocess.run(
                [sys.executable, str(IMPORT), "--refinement", str(refinement), "--replacements", str(replacements), "--mode", "mixed", "--scope-file", str(root / "scope.json")],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            document = json.loads(replacements.read_text(encoding="utf-8"))
            self.assertEqual(document["Transcription"]["Literals"], {"goodword": ["badword"]})
            self.assertEqual(
                document["FinalOutput"]["Regex"],
                [{"Pattern": "(?i)\\bbadfinal\\b", "Replacement": "goodfinal"}],
            )
            scope = json.loads((root / "scope.json").read_text(encoding="utf-8"))
            self.assertEqual(scope["rules"][0]["scope"], "mixed")

    def test_generated_agent_proposal_can_be_approved_and_imported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recordings = root / "recordings"
            recordings.mkdir()
            make_recording(
                recordings,
                "20260814-100000-014",
                **{
                    "transcription_text.txt": "badword",
                    "transcription_processed_text.txt": "goodword",
                    "prompt_correction_text.txt": "same",
                    "output_text.txt": "same",
                },
            )
            replacements = root / "global_replacements.json"
            write_json(replacements, empty_replacements())
            proposals = root / "agent_proposals.json"
            write_proposals(
                proposals,
                [{"recording": "20260814-100000-014", "target_section": "Transcription", "source_text": "badword", "source_or_pattern": "badword", "replacement": "goodword", "reason": "Agent proposal"}],
            )
            refinement = root / "refinement.md"
            build = run_build(recordings, replacements, refinement, "--proposals", str(proposals))
            self.assertEqual(build.returncode, 0, build.stderr)
            reviewed = refinement.read_text(encoding="utf-8")
            reviewed = reviewed.replace("<!-- replacement_mode: pending -->", "<!-- replacement_mode: mixed -->")
            reviewed = reviewed.replace("<!-- target_file: -->", "<!-- target_file: global_replacements.json -->")
            reviewed = reviewed.replace("| pending |", "| approved |", 1)
            reviewed = reviewed.replace("| pending |", "| mixed |", 1)
            reviewed = reviewed.replace("|  |", "| global_replacements.json |", 1)
            refinement.write_text(reviewed, encoding="utf-8")
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            dry = subprocess.run(
                [sys.executable, str(IMPORT), "--refinement", str(refinement), "--replacements", str(replacements), "--mode", "mixed", "--scope-file", str(root / "scope.json"), "--dry-run"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertIn("transcription_literal_add=1", dry.stdout)
            imported = subprocess.run(
                [sys.executable, str(IMPORT), "--refinement", str(refinement), "--replacements", str(replacements), "--mode", "mixed", "--scope-file", str(root / "scope.json")],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(json.loads(replacements.read_text(encoding="utf-8"))["Transcription"]["Literals"], {"goodword": ["badword"]})

    def test_import_does_not_reintroduce_literal_as_regex_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replacements = root / "global_replacements.json"
            document = empty_replacements()
            document["Transcription"]["Literals"] = {"goodword": ["badword"]}
            write_json(replacements, document)
            refinement = root / "refinement.md"
            refinement.write_text(
                "\n".join(
                    [
                        "<!-- replacement_mode: mixed -->",
                        "<!-- target_file: global_replacements.json -->",
                        "| timestamp | target_section | review_stage | evidence_source | source_or_pattern | replacement | rule_type | confidence | reason | status | transcription_engine | transcription_model | correction_model | model_profile | model_evidence | replacement_mode | target_file | remark |",
                        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
                        "| t1 | Transcription | STT | raw | (?i)\\bbadword\\b | goodword | Regex | medium | test | 採用 | Remote | whisper | correction | Remote__whisper__correction | test | mixed | global_replacements.json |  |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            dry = subprocess.run(
                [sys.executable, str(IMPORT), "--refinement", str(refinement), "--replacements", str(replacements), "--mode", "mixed", "--scope-file", str(root / "scope.json"), "--dry-run"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertIn("transcription_regex_add=0", dry.stdout)


if __name__ == "__main__":
    unittest.main()
