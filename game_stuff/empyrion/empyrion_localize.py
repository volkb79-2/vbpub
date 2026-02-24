#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import html
import json
import random
import re
import time
import tomllib
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from urllib.error import HTTPError
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Dict, List, Set, Tuple

from google_mobile_translate import GoogleMobileTranslate, GoogleMobileTranslateError

try:
    from googleapiclient.discovery import build as google_build
    from googleapiclient.errors import HttpError as GoogleHttpError
except ImportError:  # pragma: no cover - optional dependency at runtime
    google_build = None
    GoogleHttpError = Exception

CSV_FILES = ["Dialogues.csv", "Localization.csv", "PDA.csv"]
ENGLISH_STOPWORDS = {
    "the", "and", "you", "your", "for", "with", "this", "that", "have", "are", "was",
    "what", "where", "when", "how", "can", "will", "from", "into", "unknown", "mission",
}
GERMAN_HINTS = {
    "der", "die", "das", "und", "ist", "nicht", "mit", "für", "ein", "eine", "du", "ich",
    "wir", "sie", "von", "zu", "auf", "im", "den", "dem", "des", "kommandant",
}
DEFAULT_PATTERNS = [
    r"\{[^{}]+\}",
    r"<[^>\\n]+>",
    r"\[(?:/?(?:[bicuv]|sub|sup)|c|-)\]",
    r"\[/?url\]",
    r"\[url=[^\]\n]+\]",
    r"\[[SEF]-[\d?]+\]",
    r"\[\s+[^\]\n]{1,60}\s+\]",
    r"\[[0-9A-Fa-f]{6}\]",
    r"@[dpqw]\d+",
    r"\bgive\s+item\s+Token\s+6995\b",
    r"\bgive\s+item\s+[A-Za-z_]+\s+\d+\b",
    r"\\n",
]

_LOG_LEVELS = {
    "ERROR": 40,
    "WARN": 30,
    "INFO": 20,
    "DEBUG": 10,
    "TRACE": 5,
}
_RUNTIME_LOG_LEVEL = _LOG_LEVELS["INFO"]
_RUNTIME_LOG_FILE: Path | None = None
_RUNTIME_LOG_LOCK = Lock()
TRANSPORT_TOKEN_CORE_PATTERN = r"TKB?PH\d+(?:LR|L|R)?TK"
TRANSPORT_TOKEN_PATTERN = rf"(?:{TRANSPORT_TOKEN_CORE_PATTERN}|\({TRANSPORT_TOKEN_CORE_PATTERN}\))"
PLACEHOLDER_CLUSTER_SEPARATOR_PATTERN = r"[ \t]*(?:[.,:;!?][ \t]*)*"
PLACEHOLDER_CLUSTER_PATTERN = rf"__PH_\d+__(?:{PLACEHOLDER_CLUSTER_SEPARATOR_PATTERN}__PH_\d+__)+"


@dataclass
class Candidate:
    row_index: int
    key: str
    english: str
    deutsch: str
    status: str


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _tokenize_words(value: str) -> List[str]:
    cleaned = re.sub(r"[{}<>\[\]()/\\\\.,!?;:'\"=*+-]", " ", value.lower())
    return [token for token in cleaned.split() if len(token) > 1]


def _looks_english(value: str) -> bool:
    words = _tokenize_words(value)
    if len(words) < 3:
        return False
    en_hits = sum(1 for word in words if word in ENGLISH_STOPWORDS)
    de_hits = sum(1 for word in words if word in GERMAN_HINTS)
    ascii_ratio = sum(1 for ch in value if ord(ch) < 128) / max(1, len(value))
    return en_hits >= 2 and en_hits > de_hits and ascii_ratio > 0.93


def _split_by_placeholders(masked: str) -> List[str]:
    return [part for part in re.split(r"__PH_\d+__", masked) if part and part.strip()]


def _plain_for_quality(text: str) -> str:
    plain = re.sub(r"\{[^{}]+\}", " ", text)
    plain = re.sub(r"<[^>\n]+>", " ", plain)
    plain = re.sub(r"\[(?:/?(?:[bicuv]|sub|sup)|c|-)\]", " ", plain)
    plain = re.sub(r"\[/?url\]", " ", plain)
    plain = re.sub(r"\[url=[^\]\n]+\]", " ", plain)
    plain = re.sub(r"\[[SEF]-[\d?]+\]", " ", plain)
    plain = re.sub(r"\[\s+[^\]\n]{1,60}\s+\]", " ", plain)
    plain = re.sub(r"\[[0-9A-Fa-f]{6}\]", " ", plain)
    plain = re.sub(r"@[dpqw]\d+", " ", plain)
    plain = re.sub(r"\bgive\s+item\s+Token\s+6995\b", " ", plain, flags=re.IGNORECASE)
    plain = re.sub(r"\bgive\s+item\s+[A-Za-z_]+\s+\d+\b", " ", plain, flags=re.IGNORECASE)
    plain = plain.replace("\\n", " ")
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain


def _normalize_inline_italics_markup(text: str) -> str:
    if not text:
        return ""

    def repl(match: re.Match[str]) -> str:
        inner = re.sub(r"\s+", " ", match.group(1)).strip()
        if not inner:
            return ""
        return inner.upper()

    normalized = re.sub(r"<i>\s*(.*?)\s*</i>", repl, text, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"</?i>", "", normalized, flags=re.IGNORECASE)
    return normalized


def _extract_ph_index(token: str) -> int:
    match = re.fullmatch(r"__PH_(\d+)__", token)
    if not match:
        return -1
    return int(match.group(1))


def _normalize_italics_in_masked_source(masked_text: str, protected: Dict[str, str]) -> str:
    if not masked_text or not protected:
        return masked_text

    sorted_tokens = sorted(protected.keys(), key=_extract_ph_index)
    open_stack: List[str] = []
    italic_pairs: List[Tuple[str, str]] = []

    for token in sorted_tokens:
        raw_value = protected.get(token, "")
        value = raw_value.strip().lower()
        if value == "<i>":
            open_stack.append(token)
        elif value == "</i>" and open_stack:
            open_token = open_stack.pop()
            italic_pairs.append((open_token, token))

    normalized = masked_text
    for open_token, close_token in italic_pairs:
        segment_re = re.compile(rf"{re.escape(open_token)}(.*?){re.escape(close_token)}", flags=re.DOTALL)

        def repl(match: re.Match[str]) -> str:
            inner = re.sub(r"\s+", " ", match.group(1)).strip()
            if not inner:
                return ""
            return inner.upper()

        normalized = segment_re.sub(repl, normalized)

    return normalized


def compute_risk(
    english: str,
    source_masked: str,
    protected: Dict[str, str],
    medium_threshold: int = 3,
    high_threshold: int = 6,
) -> Tuple[int, str, List[str]]:
    flags: List[str] = []
    score = 0

    protected_count = len(protected)
    segments = _split_by_placeholders(source_masked)
    plain = _plain_for_quality(english)
    words = _tokenize_words(plain)

    if protected_count > 0 and len(segments) > 1:
        flags.append("mixed_markup_plain")
        score += 3

    if re.search(r"\w__PH_\d+__|__PH_\d+__\w", source_masked):
        flags.append("placeholder_adjacent_text")
        score += 2

    if len(words) <= 3 and plain.endswith((".", "!")) and "?" not in plain:
        flags.append("short_dialogue_utterance")
        score += 2

    if any(marker in english for marker in ["...", "\"", "'", "?!", "!?"]):
        flags.append("dialogue_cues")
        score += 1

    if english.count("\\n") >= 2 or protected_count >= 6:
        flags.append("structure_dense")
        score += 2

    if len(words) >= 18 and protected_count >= 2:
        flags.append("long_sentence_with_markup")
        score += 2

    adjacent_placeholder_pairs = len(re.findall(r"__PH_\d+____PH_\d+__", source_masked))
    if adjacent_placeholder_pairs >= 4:
        flags.append("placeholder_cluster_dense")
        score += 2

    punctuation_boundary_stress = bool(
        re.search(r"__PH_\d+__[.,:;!?]", source_masked)
        or re.search(r"[.,:;!?]__PH_\d+__", source_masked)
    )
    if punctuation_boundary_stress:
        flags.append("punctuation_placeholder_boundary")
        score += 1

    structure_segments = _split_by_placeholders(source_masked)
    very_short_segments = 0
    for segment in structure_segments:
        segment_words = _tokenize_words(segment)
        if segment_words and len(segment_words) <= 2:
            very_short_segments += 1
    if very_short_segments >= 4:
        flags.append("fragmented_micro_segments")
        score += 2

    if english.count("\\n") >= 3:
        flags.append("heavy_multiline_structure")
        score += 2

    if len(re.findall(r"@[dpqw]\d+", english)) >= 2:
        flags.append("control_code_dense")
        score += 1

    if protected_count > 0 and len(words) > 0:
        placeholder_density = protected_count / max(1, len(words))
        if placeholder_density >= 0.35:
            flags.append("high_placeholder_density")
            score += 2

    if score >= high_threshold:
        risk_level = "high"
    elif score >= medium_threshold:
        risk_level = "medium"
    else:
        risk_level = "low"

    return score, risk_level, flags


def _build_patterns(pattern_file: Path | None) -> List[re.Pattern[str]]:
    patterns: List[str] = []
    if pattern_file and pattern_file.exists():
        for line in pattern_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    if not patterns:
        patterns = DEFAULT_PATTERNS
    return [re.compile(p) for p in patterns]


def protect_text(text: str, patterns: List[re.Pattern[str]]) -> Tuple[str, Dict[str, str]]:
    if not text:
        return "", {}

    matches: List[Tuple[int, int, str]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), match.group(0)))

    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))

    merged: List[Tuple[int, int, str]] = []
    cursor = -1
    for start, end, value in matches:
        if start < cursor:
            continue
        merged.append((start, end, value))
        cursor = end

    protected: Dict[str, str] = {}
    out: List[str] = []
    pos = 0
    for idx, (start, end, value) in enumerate(merged):
        token = f"__PH_{idx}__"
        protected[token] = value
        out.append(text[pos:start])
        out.append(token)
        pos = end
    out.append(text[pos:])
    return "".join(out), protected


def restore_text(text: str, protected: Dict[str, str]) -> str:
    restored = text
    for token, value in protected.items():
        restored = restored.replace(token, value)
    return restored


def _compact_empyrion_tag_spacing(text: str) -> str:
    if not text:
        return text

    opening_tag = r"\[(?:b|i|u|c|[0-9A-Fa-f]{6})\]"
    closing_tag = r"\[(?:-|/b|/i|/u|/c)\]"
    opening_html_tag = r"<(?!/)[^>\n]+>"
    closing_html_tag = r"</[^>\n]+>"

    compacted = text
    compacted = re.sub(rf"({opening_tag})\s+(?={opening_tag})", r"\1", compacted)
    compacted = re.sub(rf"({closing_tag})\s+(?={closing_tag})", r"\1", compacted)
    compacted = re.sub(rf"\s+(?={closing_tag})", "", compacted)
    compacted = re.sub(rf"(^|[\n\r])((?:{opening_tag})+)\s+(?=[A-Za-zÀ-ÿ0-9])", r"\1\2", compacted)

    compacted = re.sub(rf"({opening_html_tag})\s+(?={opening_html_tag})", r"\1", compacted)
    compacted = re.sub(rf"({closing_html_tag})\s+(?={closing_html_tag})", r"\1", compacted)
    compacted = re.sub(rf"\s+(?={closing_html_tag})", "", compacted)
    compacted = re.sub(rf"(^|[\n\r])((?:{opening_html_tag})+)\s+(?=[A-Za-zÀ-ÿ0-9])", r"\1\2", compacted)

    return compacted


def load_glossary(glossary_file: Path | None) -> List[Tuple[str, str]]:
    if not glossary_file or not glossary_file.exists():
        return []
    rows: List[Tuple[str, str]] = []
    with glossary_file.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            source = (row.get("source") or "").strip()
            target = (row.get("target") or "").strip()
            if source and target:
                rows.append((source, target))
    return rows


def enforce_glossary(text: str, glossary: List[Tuple[str, str]]) -> str:
    out = text
    for source, target in glossary:
        out = re.sub(rf"\b{re.escape(source)}\b", target, out, flags=re.IGNORECASE)
    return out


def load_csv_rows(file_path: Path) -> List[dict]:
    with file_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def find_candidates(rows: List[dict]) -> List[Candidate]:
    candidates: List[Candidate] = []
    for idx, row in enumerate(rows, start=2):
        key = (row.get("KEY") or "").strip()
        english = row.get("English") or ""
        deutsch = row.get("Deutsch") or ""
        if not key or not english.strip():
            continue

        norm_en = _normalize_text(english)
        norm_de = _normalize_text(deutsch)

        if not norm_de:
            status = "de_empty"
            candidates.append(Candidate(idx, key, english, deutsch, status))
            continue

        if norm_de == norm_en or _looks_english(deutsch):
            status = "de_contains_english"
            candidates.append(Candidate(idx, key, english, deutsch, status))

    return candidates


def build_translation_memory(rows: List[dict]) -> Dict[str, str]:
    memory: Dict[str, str] = {}
    for row in rows:
        english = row.get("English") or ""
        deutsch = row.get("Deutsch") or ""
        if not english.strip() or not deutsch.strip():
            continue
        if _looks_english(deutsch):
            continue
        memory[_normalize_text(english)] = deutsch
    return memory


def make_id(file_name: str, key: str, row_index: int) -> str:
    raw = f"{file_name}|{row_index}|{key}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{file_name}:{row_index}:{key}:{digest}"


def _extract_entry_key(entry: dict) -> str:
    key = entry.get("key")
    if isinstance(key, str) and key.strip():
        return key.strip()

    item_id = entry.get("id")
    if isinstance(item_id, str) and item_id:
        parts = item_id.split(":", 3)
        if len(parts) >= 3:
            parsed_key = parts[2].strip()
            if parsed_key:
                return parsed_key

    return ""


def cmd_audit(args: argparse.Namespace) -> None:
    base = Path(args.base_dir)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    summary = {}
    details_path = report_dir / "audit_candidates.csv"
    with details_path.open("w", encoding="utf-8", newline="") as out_handle:
        writer = csv.writer(out_handle)
        writer.writerow(["file", "row", "key", "status", "english", "deutsch"])

        for file_name in CSV_FILES:
            file_path = base / file_name
            rows = load_csv_rows(file_path)
            candidates = find_candidates(rows)
            summary[file_name] = {
                "total_rows": len(rows),
                "candidates": len(candidates),
                "de_empty": sum(1 for c in candidates if c.status == "de_empty"),
                "de_contains_english": sum(1 for c in candidates if c.status == "de_contains_english"),
            }
            for item in candidates:
                writer.writerow([file_name, item.row_index, item.key, item.status, item.english, item.deutsch])

    summary_path = report_dir / "audit_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] Wrote summary: {summary_path}")
    print(f"[INFO] Wrote candidate list: {details_path}")


def cmd_export(args: argparse.Namespace) -> None:
    base = Path(args.base_dir)
    output = Path(args.output)
    pattern_file = Path(args.pattern_file) if args.pattern_file else None
    patterns = _build_patterns(pattern_file)

    entries: List[dict] = []
    high_risk_rows: List[dict] = []
    for file_name in CSV_FILES:
        rows = load_csv_rows(base / file_name)
        for candidate in find_candidates(rows):
            source_for_mt = _normalize_inline_italics_markup(candidate.english)
            masked_en, protected = protect_text(source_for_mt, patterns)
            risk_score, risk_level, risk_flags = compute_risk(
                candidate.english,
                masked_en,
                protected,
                medium_threshold=args.risk_medium_threshold,
                high_threshold=args.risk_high_threshold,
            )
            entry = {
                "id": make_id(file_name, candidate.key, candidate.row_index),
                "file": file_name,
                "row": candidate.row_index,
                "key": candidate.key,
                "status": candidate.status,
                "english": candidate.english,
                "deutsch_current": candidate.deutsch,
                "source_masked": masked_en,
                "protected": protected,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "risk_flags": risk_flags,
                "risk_version": "v2",
            }
            entries.append(entry)
            if risk_score >= args.high_risk_min_score:
                high_risk_rows.append(entry)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    prompt_file = output.with_suffix(".prompt.txt")
    prompt_file.write_text(
        "Translate each JSONL entry from English to German.\n"
        "Rules:\n"
        "1) Keep id unchanged.\n"
        "2) Translate ONLY source_masked into translation_masked.\n"
        "3) Keep __PH_n__ tokens exactly unchanged.\n"
        "4) Keep tone suitable for in-game UI/dialogue.\n"
        "Output JSONL with fields: id, translation_masked\n",
        encoding="utf-8",
    )

    if args.high_risk_report:
        high_risk_report = Path(args.high_risk_report)
        high_risk_report.parent.mkdir(parents=True, exist_ok=True)
        sorted_rows = sorted(high_risk_rows, key=lambda row: row["risk_score"], reverse=True)
        sample_size = max(1, int(args.high_risk_sample_size))
        sample_rows = sorted_rows[:sample_size]
        with high_risk_report.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "id",
                "file",
                "row",
                "key",
                "status",
                "risk_score",
                "risk_level",
                "risk_flags",
                "english",
                "source_masked",
            ])
            for row in sample_rows:
                writer.writerow([
                    row["id"],
                    row["file"],
                    row["row"],
                    row["key"],
                    row["status"],
                    row["risk_score"],
                    row["risk_level"],
                    "|".join(row["risk_flags"]),
                    row["english"],
                    row["source_masked"],
                ])
        print(f"[INFO] Wrote optional high-risk sample report: {high_risk_report}")

    print(f"[INFO] Exported {len(entries)} translation entries: {output}")
    print(f"[INFO] Wrote helper prompt: {prompt_file}")


def cmd_risk_report(args: argparse.Namespace) -> None:
    export_file = Path(args.export_file)
    output_csv = Path(args.output_csv)

    entries = _read_jsonl(export_file)
    if not entries:
        raise ValueError(f"No entries found in export file: {export_file}")

    score_counter: Counter[int] = Counter()
    level_counter: Counter[str] = Counter()

    for entry in entries:
        score = int(entry.get("risk_score", 0))
        level = str(entry.get("risk_level", "unknown") or "unknown")
        score_counter[score] += 1
        level_counter[level] += 1

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["risk_score", "rows"])
        for score in sorted(score_counter.keys()):
            writer.writerow([score, score_counter[score]])

    total_rows = len(entries)
    low_count = level_counter.get("low", 0)
    medium_count = level_counter.get("medium", 0)
    high_count = level_counter.get("high", 0)
    medium_high_count = medium_count + high_count

    print(f"[INFO] Risk report source: {export_file}")
    print(f"[INFO] Total rows: {total_rows}")
    print(
        f"[INFO] Levels: low={low_count} medium={medium_count} high={high_count} medium+high={medium_high_count}"
    )
    print(f"[INFO] Wrote risk distribution CSV: {output_csv}")


def cmd_risk_sample(args: argparse.Namespace) -> None:
    export_file = Path(args.export_file)
    output = Path(args.output)
    report_csv = Path(args.report_csv) if args.report_csv else output.with_suffix(".csv")

    entries = _read_jsonl(export_file)
    if not entries:
        raise ValueError(f"No entries found in export file: {export_file}")

    requested_levels = [level.strip().lower() for level in (args.risk_levels or []) if level.strip()]
    requested_scores = set(int(score) for score in (args.risk_scores or []))
    min_score = args.min_score
    max_score = args.max_score

    if not requested_levels and not requested_scores and min_score is None and max_score is None:
        raise ValueError(
            "At least one selector is required: --risk-levels, --risk-scores, --min-score, or --max-score"
        )

    selected: List[dict] = []
    for entry in entries:
        risk_level = str(entry.get("risk_level", "")).lower()
        risk_score = int(entry.get("risk_score", 0))

        if requested_levels and risk_level not in requested_levels:
            continue
        if requested_scores and risk_score not in requested_scores:
            continue
        if min_score is not None and risk_score < int(min_score):
            continue
        if max_score is not None and risk_score > int(max_score):
            continue

        selected.append(entry)

    if not selected:
        raise ValueError("No rows matched the selected risk filters.")

    sample_size = max(1, int(args.size))
    sample_size = min(sample_size, len(selected))

    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    sampled = rng.sample(selected, sample_size) if sample_size < len(selected) else list(selected)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in sampled:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report_csv.parent.mkdir(parents=True, exist_ok=True)
    with report_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "file", "row", "key", "risk_score", "risk_level", "risk_flags", "english"])
        for row in sorted(sampled, key=lambda item: (int(item.get("risk_score", 0)), item.get("id", ""))):
            writer.writerow(
                [
                    row.get("id", ""),
                    row.get("file", ""),
                    row.get("row", ""),
                    row.get("key", ""),
                    row.get("risk_score", ""),
                    row.get("risk_level", ""),
                    "|".join(row.get("risk_flags", [])),
                    row.get("english", ""),
                ]
            )

    print(f"[INFO] Risk sample source: {export_file}")
    print(f"[INFO] Matched rows: {len(selected)}")
    print(f"[INFO] Sampled rows: {len(sampled)}")
    print(f"[INFO] Wrote risk sample JSONL: {output}")
    print(f"[INFO] Wrote risk sample CSV: {report_csv}")


def _set_runtime_logging(level: str, log_file: str = "") -> None:
    global _RUNTIME_LOG_LEVEL
    global _RUNTIME_LOG_FILE

    level_name = (level or "INFO").strip().upper()
    _RUNTIME_LOG_LEVEL = _LOG_LEVELS.get(level_name, _LOG_LEVELS["INFO"])
    _RUNTIME_LOG_FILE = Path(log_file) if log_file else None
    if _RUNTIME_LOG_FILE:
        _RUNTIME_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def _runtime_log(level: str, message: str) -> None:
    level_name = (level or "INFO").strip().upper()
    level_value = _LOG_LEVELS.get(level_name, _LOG_LEVELS["INFO"])
    if level_value < _RUNTIME_LOG_LEVEL:
        return

    line = f"[{level_name}] {message}"
    print(line, flush=True)
    if _RUNTIME_LOG_FILE:
        with _RUNTIME_LOG_LOCK:
            with _RUNTIME_LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def _read_jsonl(path: Path, *, strict: bool = True) -> List[dict]:
    rows: List[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                if strict:
                    raise
                _runtime_log(
                    "WARN",
                    f"Skipping invalid JSONL line {line_number} in {path}: {exc}",
                )
    return rows


def _write_jsonl(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _append_jsonl(path: Path, rows: List[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _ordered_mt_pipeline_field_names(*, include_internal_masked_fields: bool) -> List[str]:
    ordered = [
        "en_original_raw",
        "source_masked_placeholders",
    ]
    if include_internal_masked_fields:
        ordered.append("source_masked_internal")
    ordered.extend(
        [
            "en_sent_to_mt_normalized",
            "de_returned_by_mt_raw",
        ]
    )
    if include_internal_masked_fields:
        ordered.append("translation_masked_final")
    ordered.append("de_final_game_ready")
    return ordered


def _append_mt_pipeline_documentation(lines: List[str], *, include_internal_masked_fields: bool) -> None:
    lines.append("## MT Pipeline (Ordered Fields)")
    lines.append("")
    lines.append("Fields are emitted in strict execution order:")
    lines.append("")
    lines.append("1. `en_original_raw` — original dataset English.")
    lines.append("2. `source_masked_placeholders` — canonical `__PH_*__` source; this defines expected placeholder order for QA.")
    if include_internal_masked_fields:
        lines.append("3. `source_masked_internal` *(optional)* — internal protected/masked source before MT normalization.")
        lines.append("4. `en_sent_to_mt_normalized` — exact pass-1 payload sent to MT (direct transport tokens).")
        lines.append("5. `de_returned_by_mt_raw` — raw pass-1 MT response.")
        lines.append("6. `translation_masked_final` *(optional)* — internal post-processed masked German used for placeholder QA.")
        lines.append("7. `de_final_game_ready` — final German with original control/markup restored.")
    else:
        lines.append("3. `en_sent_to_mt_normalized` — exact pass-1 payload sent to MT (direct transport tokens).")
        lines.append("4. `de_returned_by_mt_raw` — raw pass-1 MT response.")
        lines.append("5. `de_final_game_ready` — final German with original control/markup restored.")
    lines.append("")
    lines.append("## Placeholder QA and `token_drop`")
    lines.append("")
    lines.append("- Placeholder QA compares the full expected token sequence from `source_masked_placeholders` against tokens extracted from MT output.")
    lines.append("- `token_drop` means one or more expected placeholders are missing in returned output (even if later placeholders are present).")
    lines.append("- Direct transport uses `TKPHnTK` anchors (optionally with `L`/`R` boundary flags) for placeholder runs and restores original placeholders after MT.")
    lines.append("- Newline placeholders are converted to real line breaks before MT and restored in deterministic order after MT.")
    lines.append("- Missing placeholders cannot be reconstructed post hoc; preservation must happen at transport/provider stage.")
    lines.append("- Newline placeholder tokens are excluded from token-drop comparison to avoid false positives from formatting-only line-break handling.")
    lines.append("")


def _write_mt_review_markdown(
    review_path: Path,
    *,
    input_path: str,
    output_path: str,
    failures_path: str,
    source_field: str,
    jobs: List[dict],
    entries_by_id: Dict[str, dict],
    translated_rows: Dict[str, dict],
    failures: List[dict],
    include_internal_masked_fields: bool = False,
) -> None:
    bracket_label_pattern = re.compile(r"\[[^\]\n]{1,60}\]")

    failures_by_id: Dict[str, dict] = {}
    for row in failures:
        item_id = row.get("id")
        if item_id and item_id not in failures_by_id:
            failures_by_id[item_id] = row

    grouped: Dict[str, List[dict]] = {}
    for job in jobs:
        item_id = job["id"]
        entry = entries_by_id.get(item_id, {})
        risk_score = entry.get("risk_score")
        try:
            group_key = f"{int(risk_score):02d}"
        except (TypeError, ValueError):
            group_key = "none"
        grouped.setdefault(group_key, []).append(job)

    translated_count = 0
    failed_count = 0
    missing_count = 0
    for job in jobs:
        item_id = job["id"]
        if translated_rows.get(item_id):
            translated_count += 1
        elif failures_by_id.get(item_id):
            failed_count += 1
        else:
            missing_count += 1

    lines: List[str] = []
    lines.append("# MT Translation Row Review")
    lines.append("")
    lines.append(f"- input source: `{input_path}`")
    lines.append(f"- translated output: `{output_path}`")
    lines.append(f"- failures output: `{failures_path}`")
    lines.append(f"- source field used: `{source_field}`")
    lines.append(f"- include internal masked fields: `{include_internal_masked_fields}`")
    lines.append(f"- rows considered: **{len(jobs)}**")
    lines.append(f"- translated rows: **{translated_count}**")
    lines.append(f"- failed rows: **{failed_count}**")
    lines.append(f"- missing rows: **{missing_count}**")
    lines.append("")

    def append_pipeline_codeblock(field_name: str, field_value: str) -> None:
        lines.append(f"- **{field_name}**:")
        lines.append("```text")
        lines.extend(field_value.splitlines() or [""])
        lines.append("```")
    _append_mt_pipeline_documentation(
        lines,
        include_internal_masked_fields=include_internal_masked_fields,
    )
    lines.append("## Status Fields")
    lines.append("")
    lines.append("- `mt_status`: row treatment result (`translated`, `blocked`, or `missing`).")
    lines.append("- `mt_provider`: provider used for this row (e.g. `deepl`, `easygoogletranslate`); `none` means no provider could process the row.")
    lines.append("- `mt_ok`: `True` when translation passed placeholder sequence QA; otherwise `False`.")
    lines.append("- `mt_error`: provider-stage error (transport/provider/quota failures).")
    lines.append("- `qa_status`: QA result (`passed` or `failed`) for placeholder-sequence validation.")
    lines.append("- `qa_error`: QA failure reason (`token_drop`, `token_reorder`, `token_insert_dup`).")
    lines.append("")

    watch_counts: Dict[str, int] = {}
    watch_rows: Dict[str, set[str]] = {}
    for job in jobs:
        item_id = job["id"]
        source_masked = job.get("source_masked") or entries_by_id.get(item_id, {}).get("source_masked") or ""
        if not source_masked:
            continue
        entry = entries_by_id.get(item_id, {})
        file_name = entry.get("file")
        row_number = entry.get("row")
        key = entry.get("key")
        origin_parts = [part for part in [file_name, str(row_number) if row_number is not None else "", key] if part]
        origin = ":".join(origin_parts) if origin_parts else item_id

        for token in bracket_label_pattern.findall(source_masked):
            watch_counts[token] = watch_counts.get(token, 0) + 1
            watch_rows.setdefault(token, set()).add(origin)

    lines.append("## Non-Protected Bracket-Label Watchlist")
    lines.append("")
    lines.append("These bracket labels remained visible in `source_masked` and were not placeholder-protected. Review them for potential MT drift in final text.")
    lines.append("")
    if watch_counts:
        affected_rows = sum(1 for job in jobs if bracket_label_pattern.search(job.get("source_masked") or entries_by_id.get(job["id"], {}).get("source_masked") or ""))
        lines.append(f"- affected rows: **{affected_rows}**")
        lines.append(f"- distinct bracket labels: **{len(watch_counts)}**")
        lines.append("")
        for token, count in sorted(watch_counts.items(), key=lambda item: (-item[1], item[0]))[:40]:
            origins = sorted(watch_rows.get(token, set()))
            sample_origins = ", ".join(origins[:2]) if origins else "n/a"
            lines.append(f"- `{token}` — occurrences: **{count}**, sample rows: {sample_origins}")
    else:
        lines.append("- No non-protected bracket labels detected in this run.")
    lines.append("")

    for group_key in sorted(grouped.keys()):
        group_rows = grouped[group_key]
        heading = f"Risk Score {int(group_key)}" if group_key != "none" else "Risk Score N/A"
        lines.append(f"## {heading} ({len(group_rows)} rows)")
        lines.append("")

        for idx, job in enumerate(group_rows, start=1):
            item_id = job["id"]
            entry = entries_by_id.get(item_id, {})
            translated_meta = translated_rows.get(item_id, {})
            translation = translated_meta.get("translation_masked", "")
            failure = failures_by_id.get(item_id, {})
            provider = translated_meta.get("provider") or failure.get("provider", "none")
            mt_error = failure.get("mt_error", "")
            qa_error = failure.get("qa_error", "")
            qa_status = failure.get("qa_status")
            if translation:
                status = "translated"
                ok = True
            elif mt_error:
                status = "blocked"
                ok = False
            elif qa_error:
                status = "blocked"
                ok = False
                translation = failure.get("translation_masked", "")
            else:
                status = "missing"
                ok = False

            if not provider:
                provider = "none"
            if status == "translated":
                qa_status = "passed"
            elif qa_error:
                qa_status = "failed"

            source_masked = job.get("source_masked") or entry.get("source_masked") or ""
            source_english_raw = entry.get("english") if isinstance(entry.get("english"), str) else ""
            en_original_raw = source_english_raw
            source_masked_placeholders = source_masked
            en_sent_to_mt_normalized = job.get("transport_payload") or ""
            de_returned_by_mt_raw = (
                translated_meta.get("translation_provider_raw")
                or failure.get("translation_provider_raw", "")
                or ""
            )
            translation_masked_final = translation
            de_final_game_ready = ""
            if translation_masked_final:
                de_final_game_ready = restore_text(translation_masked_final, entry.get("protected", {}))
                de_final_game_ready = _compact_empyrion_tag_spacing(de_final_game_ready)

            risk_level = entry.get("risk_level", "n/a")
            risk_flags = entry.get("risk_flags", [])
            file_name = entry.get("file")
            row_number = entry.get("row")
            key = entry.get("key")
            origin_parts = [part for part in [file_name, str(row_number) if row_number is not None else "", key] if part]
            origin = ":".join(origin_parts) if origin_parts else item_id

            lines.append(f"### {idx}. {origin}")
            lines.append(f"- id: `{item_id}`")
            lines.append(f"- risk_level: `{risk_level}`")
            lines.append(f"- risk_flags: `{', '.join(risk_flags) if risk_flags else 'none'}`")
            lines.append(f"- mt_status: `{status}`")
            lines.append(f"- mt_provider: `{provider}`")
            lines.append(f"- mt_ok: `{ok}`")
            pipeline_field_values: Dict[str, str] = {
                "en_original_raw": en_original_raw,
                "source_masked_placeholders": source_masked_placeholders,
                "source_masked_internal": source_masked,
                "en_sent_to_mt_normalized": en_sent_to_mt_normalized,
                "de_returned_by_mt_raw": de_returned_by_mt_raw,
                "translation_masked_final": translation_masked_final,
                "de_final_game_ready": de_final_game_ready,
            }

            for field_name in _ordered_mt_pipeline_field_names(
                include_internal_masked_fields=include_internal_masked_fields,
            ):
                field_value = pipeline_field_values.get(field_name, "")
                append_pipeline_codeblock(field_name, field_value)
            if mt_error:
                lines.append(f"- mt_error: {mt_error}")
            if qa_status:
                lines.append(f"- qa_status: `{qa_status}`")
            if qa_error:
                lines.append(f"- qa_error: {qa_error}")
            lines.append("")

    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text("\n".join(lines), encoding="utf-8")


def _write_mt_failures_markdown(
    report_path: Path,
    *,
    input_path: str,
    output_path: str,
    failures_path: str,
    source_field: str,
    jobs: List[dict],
    entries_by_id: Dict[str, dict],
    translated_rows: Dict[str, dict],
    failures: List[dict],
    include_internal_masked_fields: bool = False,
) -> None:
    failures_by_id: Dict[str, dict] = {}
    for row in failures:
        item_id = row.get("id")
        if item_id and item_id not in failures_by_id:
            failures_by_id[item_id] = row

    failed_jobs = [job for job in jobs if job["id"] in failures_by_id]

    lines: List[str] = []
    lines.append("# MT Remaining Failed Rows Report")
    lines.append("")
    lines.append(f"- input source: `{input_path}`")
    lines.append(f"- translated output: `{output_path}`")
    lines.append(f"- failures output: `{failures_path}`")
    lines.append(f"- source field used: `{source_field}`")
    lines.append(f"- include internal masked fields: `{include_internal_masked_fields}`")
    lines.append(f"- failed rows: **{len(failed_jobs)}**")
    lines.append("")

    _append_mt_pipeline_documentation(
        lines,
        include_internal_masked_fields=include_internal_masked_fields,
    )
    lines.append("## Status Fields")
    lines.append("")
    lines.append("- `mt_status`: row treatment result (`translated`, `blocked`, or `missing`).")
    lines.append("- `mt_provider`: provider used for this row (e.g. `deepl`, `easygoogletranslate`); `none` means no provider could process the row.")
    lines.append("- `mt_ok`: `True` when translation passed placeholder sequence QA; otherwise `False`.")
    lines.append("- `mt_error`: provider-stage error (transport/provider/quota failures).")
    lines.append("- `qa_status`: QA result (`passed` or `failed`) for placeholder-sequence validation.")
    lines.append("- `qa_error`: QA failure reason (`token_drop`, `token_reorder`, `token_insert_dup`).")
    lines.append("")

    def append_pipeline_codeblock(field_name: str, field_value: str) -> None:
        lines.append(f"- **{field_name}**:")
        lines.append("```text")
        lines.extend(field_value.splitlines() or [""])
        lines.append("```")

    if not failed_jobs:
        lines.append("No errors remaining. All rows passed MT and placeholder QA.")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return

    for idx, job in enumerate(failed_jobs, start=1):
        item_id = job["id"]
        entry = entries_by_id.get(item_id, {})
        translated_meta = translated_rows.get(item_id, {})
        failure = failures_by_id.get(item_id, {})

        provider = translated_meta.get("provider") or failure.get("provider", "none") or "none"
        mt_error = failure.get("mt_error") or failure.get("error", "")
        qa_error = failure.get("qa_error", "")
        qa_status = failure.get("qa_status")
        status = "blocked"

        source_masked = job.get("source_masked") or entry.get("source_masked") or ""
        source_english_raw = entry.get("english") if isinstance(entry.get("english"), str) else ""
        en_original_raw = source_english_raw
        source_masked_placeholders = source_masked
        en_sent_to_mt_normalized = job.get("transport_payload") or ""
        de_returned_by_mt_raw = (
            failure.get("translation_provider_raw")
            or translated_meta.get("translation_provider_raw")
            or ""
        )
        translation_masked_final = failure.get("translation_masked") or translated_meta.get("translation_masked", "")
        de_final_game_ready = ""
        if translation_masked_final:
            de_final_game_ready = restore_text(translation_masked_final, entry.get("protected", {}))
            de_final_game_ready = _compact_empyrion_tag_spacing(de_final_game_ready)

        risk_level = entry.get("risk_level", "n/a")
        risk_flags = entry.get("risk_flags", [])
        file_name = entry.get("file")
        row_number = entry.get("row")
        key = entry.get("key")
        origin_parts = [part for part in [file_name, str(row_number) if row_number is not None else "", key] if part]
        origin = ":".join(origin_parts) if origin_parts else item_id

        if qa_error and not qa_status:
            qa_status = "failed"

        lines.append(f"### {idx}. {origin}")
        lines.append(f"- id: `{item_id}`")
        lines.append(f"- risk_level: `{risk_level}`")
        lines.append(f"- risk_flags: `{', '.join(risk_flags) if risk_flags else 'none'}`")
        lines.append(f"- mt_status: `{status}`")
        lines.append(f"- mt_provider: `{provider}`")
        lines.append("- mt_ok: `False`")

        pipeline_field_values: Dict[str, str] = {
            "en_original_raw": en_original_raw,
            "source_masked_placeholders": source_masked_placeholders,
            "source_masked_internal": source_masked,
            "en_sent_to_mt_normalized": en_sent_to_mt_normalized,
            "de_returned_by_mt_raw": de_returned_by_mt_raw,
            "translation_masked_final": translation_masked_final,
            "de_final_game_ready": de_final_game_ready,
        }

        for field_name in _ordered_mt_pipeline_field_names(
            include_internal_masked_fields=include_internal_masked_fields,
        ):
            field_value = pipeline_field_values.get(field_name, "")
            append_pipeline_codeblock(field_name, field_value)

        if mt_error:
            lines.append(f"- mt_error: {mt_error}")
        if failure.get("mt_error_kind"):
            lines.append(f"- mt_error_kind: `{failure.get('mt_error_kind')}`")
        if failure.get("mt_retryable") is not None:
            lines.append(f"- mt_retryable: `{bool(failure.get('mt_retryable'))}`")
        if failure.get("mt_error_message"):
            lines.append(f"- mt_error_message: {failure.get('mt_error_message')}")
        if qa_status:
            lines.append(f"- qa_status: `{qa_status}`")
        if qa_error:
            lines.append(f"- qa_error: {qa_error}")
        lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _write_mt_success_markdown(
    report_path: Path,
    *,
    input_path: str,
    output_path: str,
    source_field: str,
    jobs: List[dict],
    entries_by_id: Dict[str, dict],
    translated_rows: Dict[str, dict],
    prep_context: Dict[str, str],
    include_internal_masked_fields: bool = False,
) -> None:
    success_jobs = [job for job in jobs if translated_rows.get(job["id"]) and translated_rows[job["id"]].get("translation_masked")]

    lines: List[str] = []
    lines.append("# Translation Success Trace")
    lines.append("")
    lines.append(f"- input source: `{input_path}`")
    lines.append(f"- translated output: `{output_path}`")
    lines.append(f"- source field used: `{source_field}`")
    lines.append(f"- include internal masked fields: `{include_internal_masked_fields}`")
    lines.append(f"- successful rows: **{len(success_jobs)}**")
    lines.append("")
    lines.append("## Preparation and Execution")
    lines.append("")
    lines.append(f"- mt config: `{prep_context.get('mt_config', '')}`")
    lines.append(f"- mt local config: `{prep_context.get('mt_local_config', '')}`")
    lines.append(f"- providers requested: `{prep_context.get('providers_requested', '')}`")
    lines.append(f"- providers enabled: `{prep_context.get('providers_enabled', '')}`")
    lines.append(f"- source language: `{prep_context.get('source_lang', '')}`")
    lines.append(f"- target language: `{prep_context.get('target_lang', '')}`")
    lines.append(f"- batch size: `{prep_context.get('batch_size', '')}`")
    lines.append(f"- max parallel per provider: `{prep_context.get('max_parallel', '')}`")
    lines.append(
        f"- parenthesized transport token edges: `{prep_context.get('parenthesized_transport_token_edges', '')}`"
    )
    lines.append("")

    _append_mt_pipeline_documentation(
        lines,
        include_internal_masked_fields=include_internal_masked_fields,
    )

    def append_pipeline_codeblock(field_name: str, field_value: str) -> None:
        lines.append(f"- **{field_name}**:")
        lines.append("```text")
        lines.extend(field_value.splitlines() or [""])
        lines.append("```")

    for idx, job in enumerate(success_jobs, start=1):
        item_id = job["id"]
        entry = entries_by_id.get(item_id, {})
        translated_meta = translated_rows.get(item_id, {})

        source_masked = job.get("source_masked") or entry.get("source_masked") or ""
        source_english_raw = entry.get("english") if isinstance(entry.get("english"), str) else ""
        en_original_raw = source_english_raw
        source_masked_placeholders = source_masked
        en_sent_to_mt_normalized = job.get("transport_payload") or ""
        de_returned_by_mt_raw = translated_meta.get("translation_provider_raw", "")
        translation_masked_final = translated_meta.get("translation_masked", "")
        de_final_game_ready = ""
        if translation_masked_final:
            de_final_game_ready = restore_text(translation_masked_final, entry.get("protected", {}))
            de_final_game_ready = _compact_empyrion_tag_spacing(de_final_game_ready)

        risk_level = entry.get("risk_level", "n/a")
        risk_flags = entry.get("risk_flags", [])
        file_name = entry.get("file")
        row_number = entry.get("row")
        key = entry.get("key")
        origin_parts = [part for part in [file_name, str(row_number) if row_number is not None else "", key] if part]
        origin = ":".join(origin_parts) if origin_parts else item_id

        lines.append(f"### {idx}. {origin}")
        lines.append(f"- id: `{item_id}`")
        lines.append(f"- risk_level: `{risk_level}`")
        lines.append(f"- risk_flags: `{', '.join(risk_flags) if risk_flags else 'none'}`")
        lines.append(f"- mt_provider: `{translated_meta.get('provider', 'unknown')}`")
        lines.append("- qa_status: `passed`")

        pipeline_field_values: Dict[str, str] = {
            "en_original_raw": en_original_raw,
            "source_masked_placeholders": source_masked_placeholders,
            "source_masked_internal": source_masked,
            "en_sent_to_mt_normalized": en_sent_to_mt_normalized,
            "de_returned_by_mt_raw": de_returned_by_mt_raw,
            "translation_masked_final": translation_masked_final,
            "de_final_game_ready": de_final_game_ready,
        }

        for field_name in _ordered_mt_pipeline_field_names(
            include_internal_masked_fields=include_internal_masked_fields,
        ):
            field_value = pipeline_field_values.get(field_name, "")
            append_pipeline_codeblock(field_name, field_value)
        lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _bundle_adjacent_placeholders(masked: str) -> Tuple[str, Dict[str, str]]:
    bundle_map: Dict[str, str] = {}

    def repl(match: re.Match[str]) -> str:
        token = f"__BPH_{len(bundle_map)}__"
        bundle_map[token] = match.group(0)
        return token

    bundled = re.sub(PLACEHOLDER_CLUSTER_PATTERN, repl, masked)
    return bundled, bundle_map


def _strip_parenthesized_placeholder_runs(text: str) -> str:
    token_pattern = r"__(?:PH|BPH)_\d+__"
    run_pattern = rf"(?:{token_pattern}(?:[ \t]*{token_pattern})*)"
    return re.sub(rf"\(\s*({run_pattern})\s*\)", r"\1", text)


def _prepare_direct_tkbph_transport(
    masked_text: str,
    protected: Dict[str, str],
    *,
    parenthesized_transport_token_edges: bool = False,
) -> Tuple[str, Dict[str, str], List[str], str]:
    def _token_has_adjacent_whitespace(text: str, token: str) -> Tuple[bool, bool]:
        idx = text.find(token)
        if idx < 0:
            return False, False
        left_ws = idx > 0 and text[idx - 1].isspace()
        right_pos = idx + len(token)
        right_ws = right_pos < len(text) and text[right_pos].isspace()
        return left_ws, right_ws

    normalized_masked_text = masked_text
    if parenthesized_transport_token_edges:
        normalized_masked_text = _strip_parenthesized_placeholder_runs(normalized_masked_text)

    masked_with_newlines, ordered_newline_tokens = _replace_newline_placeholders_with_real_newlines(
        normalized_masked_text,
        protected,
    )

    run_map_base: Dict[str, str] = {}

    def repl(match: re.Match[str]) -> str:
        token_core = f"TKPH{len(run_map_base)}TK"
        token = token_core
        if parenthesized_transport_token_edges:
            token = f"({token_core})"
        run_map_base[token] = match.group(0)
        return token

    payload = masked_with_newlines

    payload = re.sub(
        PLACEHOLDER_CLUSTER_PATTERN,
        repl,
        payload,
    )
    payload = re.sub(r"__PH_\d+__", repl, payload)

    payload_before_spacing = payload

    payload = re.sub(rf"(?<![ \t\n\(\[\{{])({TRANSPORT_TOKEN_PATTERN})", r" \1", payload)
    payload = re.sub(rf"({TRANSPORT_TOKEN_PATTERN})(?![ \t\n]|$|[.,:;!?\)\]\}}])", r"\1 ", payload)

    if "\n" in payload:
        lines = [
            _enforce_placeholder_spacing(line, token_pattern=rf"{TRANSPORT_TOKEN_PATTERN}|__PH_\d+__")
            for line in payload.split("\n")
        ]
        payload = "\n".join(lines)
        payload = re.sub(r"[ \t]+", " ", payload)
        payload = re.sub(r"[ \t]+([.,:;!?])", r"\1", payload)
        payload = re.sub(r"\n{3,}", "\n\n", payload).strip()
    else:
        payload = _enforce_placeholder_spacing(payload, token_pattern=rf"{TRANSPORT_TOKEN_PATTERN}|__PH_\d+__")
        payload = re.sub(r"\s+([.,:;!?])", r"\1", payload).strip()

    run_map: Dict[str, str] = {}
    for payload_token_base, placeholder_run in run_map_base.items():
        before_left_ws, before_right_ws = _token_has_adjacent_whitespace(payload_before_spacing, payload_token_base)
        after_left_ws, after_right_ws = _token_has_adjacent_whitespace(payload, payload_token_base)

        inserted_left_ws = after_left_ws and not before_left_ws
        inserted_right_ws = after_right_ws and not before_right_ws

        flags = "LR" if inserted_left_ws and inserted_right_ws else "L" if inserted_left_ws else "R" if inserted_right_ws else ""
        if not flags:
            run_map[payload_token_base] = placeholder_run
            continue

        if payload_token_base.startswith("(") and payload_token_base.endswith(")"):
            token_core = payload_token_base[1:-1]
            token_core = token_core[:-2] + flags + "TK"
            payload_token_flagged = f"({token_core})"
        else:
            payload_token_flagged = payload_token_base[:-2] + flags + "TK"

        payload = payload.replace(payload_token_base, payload_token_flagged)
        run_map[payload_token_flagged] = placeholder_run

    return payload, run_map, ordered_newline_tokens, masked_with_newlines


def _restore_direct_tkbph_transport(
    translated_text: str,
    run_map: Dict[str, str],
    ordered_newline_tokens: List[str],
) -> str:
    def _decode_transport_flags(token: str) -> Tuple[bool, bool]:
        match = re.fullmatch(r"\(?(?:TKB?PH\d+(?P<flags>LR|L|R)?TK)\)?", token)
        if not match:
            return False, False
        flags = match.group("flags") or ""
        return ("L" in flags, "R" in flags)

    restored = translated_text
    for token, value in run_map.items():
        trim_left, trim_right = _decode_transport_flags(token)
        if trim_left:
            restored = re.sub(rf"[ \t]+{re.escape(token)}", token, restored)
        if trim_right:
            restored = re.sub(rf"{re.escape(token)}[ \t]+", token, restored)
        restored = restored.replace(token, value)

    restored = _restore_newline_placeholders_from_text(restored, ordered_newline_tokens)
    restored = _enforce_placeholder_spacing(restored, token_pattern=r"__(?:PH|BPH)_\d+__")
    return restored


def _next_transport_token_index(run_map: Dict[str, str]) -> int:
    max_idx = -1
    for token in run_map.keys():
        match = re.search(r"TKB?PH(\d+)", token)
        if not match:
            continue
        idx = int(match.group(1))
        if idx > max_idx:
            max_idx = idx
    return max_idx + 1


def _coalesce_transport_token_clusters(
    translated_text: str,
    run_map: Dict[str, str],
) -> Tuple[str, Dict[str, str]]:
    if not translated_text or not run_map:
        return translated_text, run_map

    token_re = re.compile(TRANSPORT_TOKEN_PATTERN)
    cluster_re = re.compile(
        rf"(?P<a>{TRANSPORT_TOKEN_PATTERN})(?P<sep>{PLACEHOLDER_CLUSTER_SEPARATOR_PATTERN})(?P<b>{TRANSPORT_TOKEN_PATTERN})"
    )

    out_text = translated_text
    out_map = dict(run_map)
    next_idx = _next_transport_token_index(out_map)

    while True:
        match = cluster_re.search(out_text)
        if not match:
            break

        token_a = match.group("a")
        token_b = match.group("b")
        sep = match.group("sep")

        if not sep:
            break

        if token_a not in out_map or token_b not in out_map:
            break

        if token_re.search(sep):
            break

        core = f"TKPH{next_idx}TK"
        next_idx += 1
        if token_a.startswith("(") and token_a.endswith(")") and token_b.startswith("(") and token_b.endswith(")"):
            merged_token = f"({core})"
        else:
            merged_token = core

        merged_value = f"{out_map[token_a]}{sep}{out_map[token_b]}"
        out_map[merged_token] = merged_value
        out_text = out_text[:match.start()] + merged_token + out_text[match.end():]

    return out_text, out_map


def _unbundle_placeholders(text: str, bundle_map: Dict[str, str]) -> str:
    out = text
    for token, value in bundle_map.items():
        out = out.replace(token, value)
    return out


def _extract_token_sequence(text: str) -> List[str]:
    return re.findall(r"__(?:PH|BPH)_\d+__", text)


def _extract_newline_placeholder_tokens(protected: Dict[str, str]) -> Set[str]:
    if not protected:
        return set()
    tokens: Set[str] = set()
    for token, value in protected.items():
        if value != "\\n":
            continue
        if re.fullmatch(r"__PH_\d+__", token):
            tokens.add(token)
    return tokens


def _filter_newline_placeholders(tokens: List[str], newline_tokens: Set[str]) -> List[str]:
    if not tokens or not newline_tokens:
        return tokens
    return [token for token in tokens if token not in newline_tokens]


def _normalize_for_mt(text: str) -> str:
    if "\n" in text:
        normalized_lines = [
            _enforce_placeholder_spacing(line, token_pattern=r"__(?:PH|BPH)_\d+__")
            for line in text.split("\n")
        ]
        normalized = "\n".join(normalized_lines)
        normalized = re.sub(r"[ \t]+([.,:;!?])", r"\1", normalized)
        normalized = re.sub(r"[ \t]+", " ", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    normalized = _enforce_placeholder_spacing(text, token_pattern=r"__(?:PH|BPH)_\d+__")
    normalized = re.sub(r"\s+([.,:;!?])", r"\1", normalized)
    return normalized


def _replace_newline_placeholders_with_real_newlines(
    masked_text: str,
    protected: Dict[str, str],
) -> Tuple[str, List[str]]:
    if not masked_text or not protected:
        return masked_text, []

    newline_tokens: List[Tuple[int, str]] = []
    for token, value in protected.items():
        if value == "\\n":
            match = re.fullmatch(r"__PH_(\d+)__", token)
            if match:
                newline_tokens.append((int(match.group(1)), token))

    if not newline_tokens:
        return masked_text, []

    newline_tokens.sort(key=lambda item: item[0])
    ordered_tokens = [token for _, token in newline_tokens]

    out = masked_text
    for token in ordered_tokens:
        out = out.replace(token, "\n")
    return out, ordered_tokens


def _restore_newline_placeholders_from_text(
    text: str,
    ordered_newline_tokens: List[str],
) -> str:
    if not text or not ordered_newline_tokens:
        return text

    restored = text
    for token in ordered_newline_tokens:
        if "\n" in restored:
            restored = restored.replace("\n", token, 1)
            continue
        if "\\n" in restored:
            restored = restored.replace("\\n", token, 1)
    return restored


def _remove_token_hyphenation_artifacts(text: str, token_pattern: str = r"__(?:PH|BPH)_\d+__") -> str:
    if not text:
        return text

    cleaned = text
    cleaned = re.sub(rf"({token_pattern})\s*-(?=[A-Za-zÀ-ÿ0-9])", r"\1 ", cleaned)
    cleaned = re.sub(rf"(?<=[A-Za-zÀ-ÿ0-9])-\s*({token_pattern})", r" \1", cleaned)
    return cleaned


def _classify_placeholder_mismatch(expected_tokens: List[str], actual_tokens: List[str]) -> str:
    if actual_tokens == expected_tokens:
        return ""
    if len(actual_tokens) < len(expected_tokens):
        return "token_drop"
    if len(actual_tokens) > len(expected_tokens):
        return "token_insert_dup"
    return "token_reorder"


def _enforce_placeholder_spacing(text: str, token_pattern: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    normalized = re.sub(rf"(?<!\s)(?<![\(\[\{{])({token_pattern})", r" \1", normalized)
    normalized = re.sub(rf"({token_pattern})(?!\s|$|[.,:;!?\)\]\}}])", r"\1 ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _enforce_placeholder_spacing_preserving_newline_tokens(
    text: str,
    newline_placeholder_tokens: List[str],
) -> str:
    if not text:
        return text

    if not newline_placeholder_tokens:
        return _enforce_placeholder_spacing(text, token_pattern=r"__PH_\d+__")

    protected = text
    marker_map: Dict[str, str] = {}
    for idx, token in enumerate(newline_placeholder_tokens):
        marker = f"__NLMARK_{idx}__"
        marker_map[marker] = token
        protected = protected.replace(token, marker)

    normalized = _enforce_placeholder_spacing(protected, token_pattern=r"__PH_\d+__")
    for marker, token in marker_map.items():
        normalized = normalized.replace(marker, token)

    for token in newline_placeholder_tokens:
        normalized = re.sub(rf"\s*{re.escape(token)}\s*", token, normalized)

    normalized = re.sub(r"\s*(?:\\n)\s*", r"\\n", normalized)
    return normalized


def _apply_source_placeholder_boundary_spacing(
    translated_masked: str,
    source_masked: str,
) -> str:
    if not translated_masked or not source_masked:
        return translated_masked

    token_pattern = r"__(?:PH|BPH)_\d+__"
    source_tokens = re.findall(token_pattern, source_masked)
    translated_tokens = re.findall(token_pattern, translated_masked)
    if source_tokens != translated_tokens:
        return translated_masked

    source_parts = re.split(rf"({token_pattern})", source_masked)
    translated_parts = re.split(rf"({token_pattern})", translated_masked)
    if len(source_parts) != len(translated_parts):
        return translated_masked

    source_text_parts = source_parts[::2]
    tokens = source_parts[1::2]
    translated_text_parts = translated_parts[::2]

    def _has_lexical_content(value: str) -> bool:
        return bool(re.search(r"[A-Za-zÀ-ÿ0-9]", value or ""))

    for idx in range(1, len(translated_text_parts) - 1):
        source_segment = source_text_parts[idx]
        translated_segment = translated_text_parts[idx]
        if source_segment.strip() != "":
            continue
        if not _has_lexical_content(translated_segment):
            continue

        target_idx = idx - 1
        for probe in range(idx - 1, -1, -1):
            if _has_lexical_content(source_text_parts[probe]):
                target_idx = probe
                break

        moved = re.sub(r"\s+", " ", translated_segment).strip()
        if moved:
            left = re.sub(r"[ \t\n]+$", "", translated_text_parts[target_idx])
            translated_text_parts[target_idx] = f"{left} {moved}" if left else moved
            translated_text_parts[idx] = source_segment

    for idx in range(len(tokens)):
        translated_text_parts[idx] = re.sub(r"[ \t\n]+$", "", translated_text_parts[idx])
        translated_text_parts[idx + 1] = re.sub(r"^[ \t\n]+", "", translated_text_parts[idx + 1])

    rebuilt = translated_text_parts[0]
    for idx, token in enumerate(tokens):
        left_ws_match = re.search(r"[ \t\n]*$", source_text_parts[idx])
        right_ws_match = re.match(r"[ \t\n]*", source_text_parts[idx + 1])
        left_ws = left_ws_match.group(0) if left_ws_match else ""
        right_ws = right_ws_match.group(0) if right_ws_match else ""

        if idx > 0 and translated_text_parts[idx] == "":
            left_ws = ""

        rebuilt += left_ws + token + right_ws + translated_text_parts[idx + 1]

    rebuilt = re.sub(rf"({token_pattern})[ \t]+([.,:;!?])", r"\1\2", rebuilt)

    return rebuilt


def _split_masked_text_for_limit(text: str, max_chars: int) -> List[str]:
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    token_re = re.compile(r"(__PH_\d+__|__BPH_\d+__)")
    parts = token_re.split(text)
    atomic_units: List[str] = []
    for part in parts:
        if not part:
            continue
        if token_re.fullmatch(part):
            atomic_units.append(part)
            continue
        # split plain text by word+trailing-space to preserve original spacing
        words = re.findall(r"\S+\s*", part)
        if words:
            atomic_units.extend(words)
        else:
            atomic_units.append(part)

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    def _flush_current() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("".join(current))
            current = []
            current_len = 0

    for unit in atomic_units:
        unit_len = len(unit)

        # oversize plain segment fallback split (should be rare)
        if unit_len > max_chars:
            _flush_current()
            start = 0
            while start < unit_len:
                chunks.append(unit[start:start + max_chars])
                start += max_chars
            continue

        if current and (current_len + unit_len > max_chars):
            _flush_current()

        current.append(unit)
        current_len += unit_len

    _flush_current()
    return chunks or [text]


def _translate_with_provider_limits(
    provider: BaseMTProvider,
    texts: List[str],
    source_lang: str,
    target_lang: str,
) -> List[str]:
    if not texts:
        return []

    cfg = provider.config if isinstance(provider.config, dict) else {}

    def _to_int(name: str, default: int) -> int:
        try:
            value = int(cfg.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(0, value)

    max_text_chars = _to_int("max_text_chars", 0)
    max_request_texts = _to_int("max_request_texts", 0)
    max_request_chars = _to_int("max_request_chars", 0)
    auto_split_long_texts = bool(cfg.get("auto_split_long_texts", True))

    if provider.name == "easygoogletranslate" and max_text_chars <= 0:
        max_text_chars = 5000

    segmented_inputs: List[List[str]] = []
    flat_segments: List[str] = []
    for text in texts:
        if max_text_chars > 0 and len(text) > max_text_chars:
            if not auto_split_long_texts:
                raise MTProviderError(
                    (
                        f"{provider.name} max_text_chars exceeded: "
                        f"len={len(text)} limit={max_text_chars}"
                    ),
                    kind="request",
                    retryable=False,
                )
            segments = _split_masked_text_for_limit(text, max_text_chars)
        else:
            segments = [text]

        segmented_inputs.append(segments)
        flat_segments.extend(segments)

    request_batches: List[List[str]] = []
    current_batch: List[str] = []
    current_chars = 0
    for segment in flat_segments:
        seg_len = len(segment)
        would_exceed_texts = bool(max_request_texts > 0 and len(current_batch) >= max_request_texts)
        would_exceed_chars = bool(max_request_chars > 0 and current_batch and (current_chars + seg_len > max_request_chars))
        if would_exceed_texts or would_exceed_chars:
            request_batches.append(current_batch)
            current_batch = []
            current_chars = 0

        current_batch.append(segment)
        current_chars += seg_len

    if current_batch:
        request_batches.append(current_batch)

    flat_outputs: List[str] = []
    for request_batch in request_batches:
        translated_batch = provider.translate_batch(request_batch, source_lang, target_lang)
        if len(translated_batch) != len(request_batch):
            raise MTProviderError(
                f"{provider.name} response size mismatch after request chunking",
                kind="service",
                retryable=True,
            )
        flat_outputs.extend(translated_batch)

    output_texts: List[str] = []
    cursor = 0
    for segments in segmented_inputs:
        count = len(segments)
        translated_segments = flat_outputs[cursor: cursor + count]
        if len(translated_segments) != count:
            raise MTProviderError(
                f"{provider.name} segmented output reconstruction mismatch",
                kind="service",
                retryable=True,
            )
        output_texts.append("".join(translated_segments))
        cursor += count

    return output_texts


def _utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_step(message: str) -> None:
    _runtime_log("INFO", f"{_utc_ts()} {message}")


def _count_words_for_stats(text: str) -> int:
    return len(re.findall(r"[A-Za-zÀ-ÿ0-9']+", text or ""))


def _deep_merge_dict(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_mt_config(path: Path, local_override_path: Path | None = None) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"MT config not found: {path}. Create it from mt.sample.toml and add API keys."
        )
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Invalid MT config TOML")

    if local_override_path and local_override_path.exists():
        with local_override_path.open("rb") as handle:
            local_data = tomllib.load(handle)
        if not isinstance(local_data, dict):
            raise ValueError("Invalid local MT override TOML")
        data = _deep_merge_dict(data, local_data)

    return data


class MTProviderError(RuntimeError):
    def __init__(self, message: str, kind: str = "generic", retryable: bool = True):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


class BaseMTProvider:
    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config

    def translate_batch(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        raise NotImplementedError


class DeepLProvider(BaseMTProvider):
    def translate_batch(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        if not texts:
            return []
        api_key = (self.config.get("api_key") or "").strip()
        endpoint = (self.config.get("endpoint") or "https://api-free.deepl.com/v2/translate").strip()
        if not api_key:
            raise MTProviderError("DeepL api_key missing in mt.toml")

        request_body = {
            "text": texts,
            "source_lang": source_lang.upper(),
            "target_lang": target_lang.upper(),
            "preserve_formatting": True,
        }
        data = json.dumps(request_body).encode("utf-8")
        request = urllib.request.Request(endpoint, method="POST", data=data)
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"DeepL-Auth-Key {api_key}")

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 429:
                raise MTProviderError(f"DeepL rate limit: {body}", kind="rate_limit", retryable=True) from exc
            if exc.code == 456:
                raise MTProviderError(f"DeepL quota exceeded: {body}", kind="quota", retryable=False) from exc
            if exc.code >= 500:
                raise MTProviderError(f"DeepL server error {exc.code}: {body}", kind="service", retryable=True) from exc
            raise MTProviderError(f"DeepL HTTP {exc.code}: {body}", kind="request", retryable=False) from exc
        except Exception as exc:
            raise MTProviderError(f"DeepL request failed: {exc}", kind="service", retryable=True) from exc

        translations = payload.get("translations", [])
        if len(translations) != len(texts):
            raise MTProviderError("DeepL response size mismatch")
        return [item.get("text", "") for item in translations]


class GoogleProvider(BaseMTProvider):
    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self._service = None

    def _service_client(self):
        if google_build is None:
            raise MTProviderError(
                "google-api-python-client not installed. Install dependency to use Google provider.",
                kind="config",
                retryable=False,
            )
        if self._service is None:
            api_key = (self.config.get("api_key") or "").strip()
            if not api_key:
                raise MTProviderError("Google api_key missing in mt.toml", kind="config", retryable=False)
            self._service = google_build("translate", "v2", developerKey=api_key, cache_discovery=False)
        return self._service

    def translate_batch(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        if not texts:
            return []
        service = self._service_client()

        try:
            payload = (
                service.translations()
                .list(
                    q=texts,
                    source=source_lang.lower(),
                    target=target_lang.lower(),
                    format="text",
                )
                .execute()
            )
        except GoogleHttpError as exc:
            body = ""
            try:
                body = exc.content.decode("utf-8", errors="ignore") if getattr(exc, "content", None) else ""
                parsed = json.loads(body) if body else {}
                reason = (
                    parsed.get("error", {})
                    .get("errors", [{}])[0]
                    .get("reason", "")
                    .lower()
                )
            except Exception:
                reason = ""

            status = getattr(getattr(exc, "resp", None), "status", 0)
            if status in (429,) or "ratelimit" in reason:
                raise MTProviderError(f"Google rate limit: {body}", kind="rate_limit", retryable=True) from exc
            if status in (403,) and ("dailylimit" in reason or "quota" in reason):
                raise MTProviderError(f"Google quota exceeded: {body}", kind="quota", retryable=False) from exc
            if status >= 500:
                raise MTProviderError(f"Google server error {status}: {body}", kind="service", retryable=True) from exc
            raise MTProviderError(f"Google request failed {status}: {body}", kind="request", retryable=False) from exc
        except MTProviderError:
            raise
        except Exception as exc:
            raise MTProviderError(f"Google request failed: {exc}", kind="service", retryable=True) from exc

        translations = payload.get("data", {}).get("translations", [])
        if len(translations) != len(texts):
            raise MTProviderError("Google response size mismatch", kind="service", retryable=True)
        return [html.unescape(item.get("translatedText", "")) for item in translations]


class EasyGoogleTranslateProvider(BaseMTProvider):
    def translate_batch(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        if not texts:
            return []
        timeout = int(self.config.get("timeout", 15))

        try:
            translator = GoogleMobileTranslate(
                source_language=source_lang.lower(),
                target_language=target_lang.lower(),
                timeout=timeout,
            )
        except Exception as exc:
            raise MTProviderError(f"easygoogletranslate init failed: {exc}", kind="config", retryable=False) from exc

        outputs: List[str] = []
        for text in texts:
            try:
                outputs.append(translator.translate(text))
            except GoogleMobileTranslateError as exc:
                raise MTProviderError(
                    f"easygoogletranslate (google-mobile) request failed: {exc}",
                    kind=exc.kind,
                    retryable=exc.retryable,
                ) from exc
            except Exception as exc:
                raise MTProviderError(
                    f"easygoogletranslate (google-mobile) request failed: {exc}",
                    kind="service",
                    retryable=True,
                ) from exc
        return outputs


def _build_provider(name: str, providers_cfg: dict) -> BaseMTProvider:
    cfg = providers_cfg.get(name, {}) if isinstance(providers_cfg, dict) else {}
    if name == "deepl":
        return DeepLProvider(name, cfg)
    if name == "google":
        return GoogleProvider(name, cfg)
    if name == "easygoogletranslate":
        return EasyGoogleTranslateProvider(name, cfg)
    raise ValueError(f"Unknown MT provider: {name}")


def _provider_enabled_and_weight(name: str, providers_cfg: dict) -> Tuple[bool, int]:
    cfg = providers_cfg.get(name, {}) if isinstance(providers_cfg, dict) else {}
    enabled = bool(cfg.get("enabled", True))
    try:
        weight = int(cfg.get("weight", 1))
    except (TypeError, ValueError):
        weight = 1
    if weight < 0:
        weight = 0
    return enabled, weight


def _build_weighted_provider_cycle(active_providers: List[BaseMTProvider], provider_weights: Dict[str, int]) -> List[str]:
    cycle: List[str] = []
    for provider in active_providers:
        weight = max(1, int(provider_weights.get(provider.name, 1)))
        cycle.extend([provider.name] * weight)
    return cycle


def _translate_with_retry(
    provider: BaseMTProvider,
    texts: List[str],
    source_lang: str,
    target_lang: str,
    max_attempts: int,
    base_backoff: float,
) -> List[str]:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _translate_with_provider_limits(provider, texts, source_lang, target_lang)
        except MTProviderError as exc:  # noqa: PERF203
            last_error = exc
            if not exc.retryable or exc.kind == "quota":
                break
            if attempt == max_attempts:
                break
            time.sleep(base_backoff * attempt)
        except Exception as exc:  # noqa: PERF203
            last_error = exc
            if attempt == max_attempts:
                break
            time.sleep(base_backoff * attempt)
    if isinstance(last_error, MTProviderError):
        raise MTProviderError(
            f"{provider.name} failed after retries: {last_error}",
            kind=last_error.kind,
            retryable=last_error.retryable,
        )
    raise MTProviderError(f"{provider.name} failed after retries: {last_error}", kind="service", retryable=True)


def _probe_provider(provider: BaseMTProvider, source_lang: str, target_lang: str) -> Tuple[bool, str, str]:
    probe_text = "Hello world."
    try:
        outputs = _translate_with_retry(
            provider,
            [probe_text],
            source_lang=source_lang,
            target_lang=target_lang,
            max_attempts=1,
            base_backoff=0.0,
        )
        translated = outputs[0] if outputs else ""
        return True, translated, ""
    except Exception as exc:  # noqa: PERF203
        return False, "", str(exc)


def cmd_translate_mt(args: argparse.Namespace) -> None:
    _log_step("translate-mt start")
    _log_step("loading MT config")
    local_cfg = Path(args.mt_local_config) if args.mt_local_config else None
    config = _load_mt_config(Path(args.mt_config), local_override_path=local_cfg)
    mt_cfg = config.get("mt", {})
    providers_cfg = config.get("providers", {})

    _set_runtime_logging(
        str(mt_cfg.get("log_level", "INFO")),
        str(mt_cfg.get("log_file", "")).strip(),
    )
    _runtime_log("DEBUG", f"translate-mt args: {args}")

    explicit_providers = args.providers or mt_cfg.get("providers", []) or []
    if explicit_providers:
        provider_order = [str(name) for name in explicit_providers]
    else:
        provider_name = args.provider or mt_cfg.get("provider", "deepl")
        provider_order = [provider_name]

    # de-duplicate while preserving order
    seen: set[str] = set()
    provider_order = [name for name in provider_order if not (name in seen or seen.add(name))]

    source_lang = args.source_lang or mt_cfg.get("source_lang", "EN")
    target_lang = args.target_lang or mt_cfg.get("target_lang", "DE")
    batch_size = int(args.batch_size or mt_cfg.get("batch_size", 20))
    max_parallel_per_provider = int(args.max_parallel or mt_cfg.get("max_parallel_per_provider", 2))
    max_attempts = int(mt_cfg.get("max_attempts", 3))
    backoff_seconds = float(mt_cfg.get("base_backoff_seconds", 1.0))
    rate_limit_cooldown_seconds = int(mt_cfg.get("rate_limit_cooldown_seconds", 120))
    rate_limit_cooldown_max_seconds = int(mt_cfg.get("rate_limit_cooldown_max_seconds", 900))
    parenthesized_transport_token_edges = bool(mt_cfg.get("parenthesized_transport_token_edges", True))
    if args.parenthesized_transport_token_edges is not None:
        parenthesized_transport_token_edges = bool(args.parenthesized_transport_token_edges)
    max_batch_attempts = int(mt_cfg.get("max_batch_attempts", 0))
    status_interval_seconds = max(5, int(mt_cfg.get("status_interval_seconds", 30)))
    checkpoint_every_batches = max(1, int(mt_cfg.get("checkpoint_every_batches", 1)))

    _log_step(f"reading input JSONL: {args.input}")
    entries = _read_jsonl(Path(args.input))

    requested_keys: List[str] = []
    if args.keys:
        seen_keys: Set[str] = set()
        for raw_item in args.keys:
            for part in str(raw_item).split(","):
                key = part.strip()
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    requested_keys.append(key)

    if requested_keys:
        requested_key_set = set(requested_keys)
        filtered_entries: List[dict] = []
        found_keys: Set[str] = set()
        for entry in entries:
            entry_key = _extract_entry_key(entry)
            if entry_key in requested_key_set:
                filtered_entries.append(entry)
                found_keys.add(entry_key)

        missing_keys = sorted(requested_key_set - found_keys)
        if missing_keys:
            raise ValueError(
                "Requested KEY(s) not found in input JSONL: " + ", ".join(missing_keys)
            )

        entries = filtered_entries
        _log_step(
            f"KEY filter enabled: selected {len(entries)} rows for {len(requested_keys)} key(s)"
        )

    entries_by_id: Dict[str, dict] = {
        entry.get("id"): entry for entry in entries if isinstance(entry.get("id"), str) and entry.get("id")
    }
    if args.sample_mode:
        sample_size = max(1, int(args.sample_size))
        sample_size = min(sample_size, len(entries))
        if sample_size < len(entries):
            if args.sample_seed is None:
                entries = random.sample(entries, sample_size)
                _log_step(f"sample mode enabled: using random sample of {sample_size} entries")
            else:
                rng = random.Random(args.sample_seed)
                entries = rng.sample(entries, sample_size)
                _log_step(
                    f"sample mode enabled: using seeded random sample of {sample_size} entries "
                    f"(seed={args.sample_seed})"
                )
        else:
            _log_step(f"sample mode enabled: using all {sample_size} entries (input smaller than sample size)")

    source_field = args.source_field
    patterns = _build_patterns(None)
    jobs: List[dict] = []
    for entry in entries:
        source_value = entry.get(source_field)
        item_id = entry.get("id")
        if not item_id or not isinstance(source_value, str) or not source_value.strip():
            continue

        protected_map: Dict[str, str] = {}
        source_masked = entry.get("source_masked")
        if (
            source_field == "source_masked"
            and isinstance(source_masked, str)
            and source_masked.strip()
        ):
            masked_for_mt = source_masked
            protected_raw = entry.get("protected")
            if isinstance(protected_raw, dict):
                protected_map = {
                    str(token): str(value)
                    for token, value in protected_raw.items()
                    if isinstance(token, str)
                }
                masked_for_mt = _normalize_italics_in_masked_source(masked_for_mt, protected_map)
        else:
            source_for_mt = _normalize_inline_italics_markup(source_value)
            masked_for_mt, protected_map = protect_text(source_for_mt, patterns)

        expected_masked = masked_for_mt
        if parenthesized_transport_token_edges:
            expected_masked = _strip_parenthesized_placeholder_runs(expected_masked)

        transport_payload, transport_run_map, ordered_newline_tokens, masked_with_newlines = _prepare_direct_tkbph_transport(
            masked_for_mt,
            protected_map,
            parenthesized_transport_token_edges=parenthesized_transport_token_edges,
        )
        jobs.append(
            {
                "id": item_id,
                "source_masked": expected_masked,
                "transport_payload": transport_payload,
                "transport_run_map": transport_run_map,
                "ordered_newline_tokens": ordered_newline_tokens,
                "masked_with_newlines": masked_with_newlines,
                "expected_tokens": _extract_token_sequence(expected_masked),
                "newline_placeholder_tokens": sorted(_extract_newline_placeholder_tokens(protected_map)),
                "expected_tokens_for_qa": _filter_newline_placeholders(
                    _extract_token_sequence(expected_masked),
                    _extract_newline_placeholder_tokens(protected_map),
                ),
            }
        )

    _log_step(f"prepared jobs: {len(jobs)}")
    all_jobs = list(jobs)

    output_path = Path(args.output)
    failures_output_path = Path(args.failures_output) if args.failures_output else None

    resume_source: Path | None = None
    if args.resume_from_output:
        resume_source = Path(args.resume_from_output)
    elif args.resume:
        output_path = Path(args.output)
        if output_path.exists():
            resume_source = output_path

    resumed_translated_rows: Dict[str, dict] = {}
    if resume_source:
        if not resume_source.exists():
            raise FileNotFoundError(f"Resume source not found: {resume_source}")
        resumed_rows = _read_jsonl(resume_source, strict=False)
        for row in resumed_rows:
            item_id = row.get("id")
            translation_masked = row.get("translation_masked")
            if not isinstance(item_id, str) or not item_id:
                continue
            if not isinstance(translation_masked, str) or not translation_masked.strip():
                continue
            resumed_translated_rows[item_id] = {
                "translation_provider_raw": row.get("translation_provider_raw", ""),
                "translation_masked": translation_masked,
                "provider": row.get("provider", "resume"),
                "qa_status": row.get("qa_status", "passed"),
            }

        if resumed_translated_rows:
            _log_step(
                f"resume mode: loaded {len(resumed_translated_rows)} translated rows from {resume_source}"
            )

    if resumed_translated_rows:
        remaining_jobs = [job for job in jobs if job["id"] not in resumed_translated_rows]
        skipped_count = len(jobs) - len(remaining_jobs)
        jobs = remaining_jobs
        _log_step(f"resume mode: skipping {skipped_count} already translated jobs; remaining {len(jobs)}")

    if not args.resume and not args.resume_from_output:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")
        if failures_output_path:
            failures_output_path.parent.mkdir(parents=True, exist_ok=True)
            failures_output_path.write_text("", encoding="utf-8")

    configured_disabled: Dict[str, str] = {}
    provider_weights: Dict[str, int] = {}
    enabled_provider_order: List[str] = []
    for provider_name in provider_order:
        enabled, weight = _provider_enabled_and_weight(provider_name, providers_cfg)
        if not enabled:
            configured_disabled[provider_name] = "config_enabled_false"
            continue
        if weight <= 0:
            configured_disabled[provider_name] = "config_weight_zero"
            continue
        enabled_provider_order.append(provider_name)
        provider_weights[provider_name] = weight

    providers = [_build_provider(name, providers_cfg) for name in enabled_provider_order]
    if not providers:
        raise ValueError("No enabled MT providers configured")

    _log_step(
        "provider init complete: "
        + ", ".join(f"{name}(w={provider_weights.get(name, 1)})" for name in enabled_provider_order)
    )

    batches = [jobs[i:i + batch_size] for i in range(0, len(jobs), batch_size)]
    _log_step(f"created {len(batches)} batches (batch_size={batch_size})")

    translated_rows: Dict[str, dict] = dict(resumed_translated_rows)
    jobs_by_id: Dict[str, dict] = {job["id"]: job for job in all_jobs}
    failures: List[dict] = []
    accepted_failures: List[dict] = []
    batch_attempts: Dict[int, int] = {}
    batch_tried_providers: Dict[int, set[str]] = {}
    batch_retry_overrides: Dict[int, List[dict]] = {}
    batch_last_failure_ids: Dict[int, Tuple[str, ...]] = {}
    batch_same_failure_streak: Dict[int, int] = {}
    disabled_providers: Dict[str, str] = {}
    permanently_disabled_providers: Dict[str, str] = {}
    temporarily_disabled_until: Dict[str, float] = {}
    provider_rate_limit_streak: Dict[str, int] = {provider.name: 0 for provider in providers}
    checkpoint_rows_pending: List[dict] = []
    completed_since_checkpoint = 0

    def flush_translation_checkpoint(force: bool = False) -> None:
        nonlocal completed_since_checkpoint
        if not checkpoint_rows_pending:
            return
        if not force and completed_since_checkpoint < checkpoint_every_batches:
            return
        _append_jsonl(output_path, checkpoint_rows_pending)
        _runtime_log(
            "DEBUG",
            f"checkpoint wrote {len(checkpoint_rows_pending)} translated rows to {output_path}",
        )
        checkpoint_rows_pending.clear()
        completed_since_checkpoint = 0

    for provider_name, reason in configured_disabled.items():
        print(f"[INFO] Provider skipped by config: {provider_name} ({reason})")

    _log_step("running provider preflight checks")
    for provider in providers:
        ok, translated_probe, error_message = _probe_provider(
            provider,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        if ok:
            print(
                f"[INFO] Provider preflight OK: {provider.name} "
                f"probe='Hello world.' => '{translated_probe}'"
            )
        else:
            disabled_providers[provider.name] = "preflight"
            print(
                f"[WARN] Provider preflight FAILED: {provider.name} error={error_message}"
            )

    telemetry_lock = Lock()
    provider_stats: Dict[str, dict] = {
        provider.name: {
            "calls": 0,
            "calls_ok": 0,
            "calls_error": 0,
            "rows_total": 0,
            "rows_ok": 0,
            "rows_failed": 0,
            "words_total": 0,
            "response_sec_total": 0.0,
            "response_sec_min": None,
            "response_sec_max": None,
            "last_error": "",
        }
        for provider in providers
    }
    call_log: List[dict] = []

    def _record_call(provider_name: str, *, started_ts: str, duration_sec: float, rows_sent: int, words_sent: int, ok: bool, error: str = "") -> None:
        with telemetry_lock:
            stats = provider_stats[provider_name]
            stats["calls"] += 1
            stats["rows_total"] += rows_sent
            stats["words_total"] += words_sent
            stats["response_sec_total"] += duration_sec
            if stats["response_sec_min"] is None or duration_sec < stats["response_sec_min"]:
                stats["response_sec_min"] = duration_sec
            if stats["response_sec_max"] is None or duration_sec > stats["response_sec_max"]:
                stats["response_sec_max"] = duration_sec
            if ok:
                stats["calls_ok"] += 1
            else:
                stats["calls_error"] += 1
                stats["last_error"] = error

            call_log.append(
                {
                    "ts": started_ts,
                    "provider": provider_name,
                    "rows": rows_sent,
                    "words": words_sent,
                    "duration_sec": round(duration_sec, 3),
                    "ok": ok,
                    "error": error,
                }
            )

    def process_batch_with_provider(batch_index: int, batch: List[dict], provider: BaseMTProvider) -> dict:
        texts = [item["transport_payload"] for item in batch]
        rows_sent = len(texts)
        words_sent = sum(_count_words_for_stats(text) for text in texts)
        started_ts = _utc_ts()
        started_perf = time.perf_counter()
        try:
            outputs = _translate_with_retry(
                provider,
                texts,
                source_lang=source_lang,
                target_lang=target_lang,
                max_attempts=max_attempts,
                base_backoff=backoff_seconds,
            )
            _record_call(
                provider.name,
                started_ts=started_ts,
                duration_sec=time.perf_counter() - started_perf,
                rows_sent=rows_sent,
                words_sent=words_sent,
                ok=True,
            )
        except Exception as exc:  # noqa: PERF203
            _record_call(
                provider.name,
                started_ts=started_ts,
                duration_sec=time.perf_counter() - started_perf,
                rows_sent=rows_sent,
                words_sent=words_sent,
                ok=False,
                error=str(exc),
            )
            raise

        result_rows: List[dict] = []
        placeholder_error = False
        for item, output in zip(batch, outputs):
            output_sanitized = _remove_token_hyphenation_artifacts(
                output,
                token_pattern=rf"{TRANSPORT_TOKEN_PATTERN}|__(?:PH|BPH)_\d+__",
            )
            output_compacted, compacted_run_map = _coalesce_transport_token_clusters(
                output_sanitized,
                item.get("transport_run_map", {}),
            )
            unbundled = _restore_direct_tkbph_transport(
                output_compacted,
                compacted_run_map,
                item.get("ordered_newline_tokens", []),
            )
            unbundled = _apply_source_placeholder_boundary_spacing(
                unbundled,
                item.get("source_masked", ""),
            )
            token_seq_full = _extract_token_sequence(unbundled)
            newline_placeholder_tokens = set(item.get("newline_placeholder_tokens", []))
            token_seq = _filter_newline_placeholders(token_seq_full, newline_placeholder_tokens)
            expected_tokens_for_qa = item.get("expected_tokens_for_qa", item["expected_tokens"])
            if token_seq != expected_tokens_for_qa:
                placeholder_error = True
                qa_error = _classify_placeholder_mismatch(expected_tokens_for_qa, token_seq)

                result_rows.append(
                    {
                        "id": item["id"],
                        "ok": False,
                        "qa_status": "failed",
                        "qa_error": qa_error,
                        "translation_provider_raw": output,
                        "translation_masked": unbundled,
                        "provider": provider.name,
                    }
                )
            else:
                result_rows.append(
                    {
                        "id": item["id"],
                        "ok": True,
                        "translation_provider_raw": output,
                        "translation_masked": unbundled,
                        "provider": provider.name,
                        "qa_status": "passed",
                    }
                )

        if placeholder_error:
            return {
                "batch_index": batch_index,
                "provider": provider.name,
                "ok": False,
                "error_kind": "placeholder",
                "rows": result_rows,
                "message": "placeholder_sequence_mismatch",
            }

        return {
            "batch_index": batch_index,
            "provider": provider.name,
            "ok": True,
            "error_kind": "",
            "rows": result_rows,
            "message": "",
        }

    pending = list(range(len(batches)))
    inflight: Dict[object, Tuple[int, str]] = {}
    rr_index = 0
    weighted_cycle: List[str] = []
    weighted_cycle_key: str = ""

    total_workers = max_parallel_per_provider * max(1, len(providers))
    next_status_log_ts = time.time() + status_interval_seconds
    with ThreadPoolExecutor(max_workers=total_workers) as executor:
        while pending or inflight:
            now_ts = time.time()
            if now_ts >= next_status_log_ts:
                _runtime_log(
                    "DEBUG",
                    "scheduler_status "
                    f"pending={len(pending)} inflight={len(inflight)} "
                    f"translated={len(translated_rows)} failures={len(failures)} "
                    f"temporary_disabled={len(temporarily_disabled_until)} "
                    f"permanent_disabled={len(permanently_disabled_providers)}",
                )
                next_status_log_ts = now_ts + status_interval_seconds

            now = time.time()
            for provider_name, wake_ts in list(temporarily_disabled_until.items()):
                if now >= wake_ts:
                    temporarily_disabled_until.pop(provider_name, None)
                    provider_rate_limit_streak[provider_name] = 0
                    _log_step(f"provider re-enabled after cooldown: {provider_name}")

            active_providers = [
                provider
                for provider in providers
                if provider.name not in permanently_disabled_providers
                and provider.name not in temporarily_disabled_until
            ]
            active_key = ",".join(provider.name for provider in active_providers)
            if active_key != weighted_cycle_key:
                weighted_cycle = _build_weighted_provider_cycle(active_providers, provider_weights)
                weighted_cycle_key = active_key
                rr_index = 0

            while pending and active_providers and len(inflight) < total_workers:
                batch_index = pending.pop(0)
                tried_for_batch = batch_tried_providers.setdefault(batch_index, set())
                candidate_providers = [provider for provider in active_providers if provider.name not in tried_for_batch]
                if not candidate_providers:
                    candidate_providers = active_providers

                if weighted_cycle:
                    provider = candidate_providers[0]
                    cycle_len = len(weighted_cycle)
                    for offset in range(cycle_len):
                        provider_name = weighted_cycle[(rr_index + offset) % cycle_len]
                        matched = next(
                            (item for item in candidate_providers if item.name == provider_name),
                            None,
                        )
                        if matched is not None:
                            provider = matched
                            break
                else:
                    provider = candidate_providers[rr_index % len(candidate_providers)]

                tried_for_batch.add(provider.name)
                rr_index += 1
                retry_batch = batch_retry_overrides.get(batch_index)
                selected_batch = retry_batch if retry_batch is not None else batches[batch_index]
                future = executor.submit(process_batch_with_provider, batch_index, selected_batch, provider)
                inflight[future] = (batch_index, provider.name)

            if not inflight:
                if pending and not active_providers:
                    if temporarily_disabled_until:
                        next_wake_ts = min(temporarily_disabled_until.values())
                        sleep_seconds = max(1.0, next_wake_ts - time.time())
                        _log_step(
                            f"all providers temporarily rate-limited; waiting {sleep_seconds:.1f}s for retry"
                        )
                        time.sleep(sleep_seconds)
                        continue

                    _log_step("no active providers remaining; marking pending batches as failed")
                    while pending:
                        failed_batch_index = pending.pop(0)
                        failed_batch = batches[failed_batch_index]
                        failures.extend(
                            {
                                "id": item["id"],
                                "ok": False,
                                "error": "provider_unavailable",
                                "mt_error": "provider_unavailable:no_active_providers_remaining",
                                "mt_error_kind": "provider_unavailable",
                                "mt_retryable": False,
                                "provider": "none",
                            }
                            for item in failed_batch
                        )
                break

            done_futures = []
            for future in as_completed(list(inflight.keys()), timeout=None):
                done_futures.append(future)
                break

            for future in done_futures:
                batch_index, provider_name = inflight.pop(future)
                try:
                    result = future.result()
                except MTProviderError as exc:
                    result = {
                        "batch_index": batch_index,
                        "provider": provider_name,
                        "ok": False,
                        "error_kind": exc.kind,
                        "retryable": exc.retryable,
                        "rows": [],
                        "message": str(exc),
                    }
                except Exception as exc:  # noqa: PERF203
                    result = {
                        "batch_index": batch_index,
                        "provider": provider_name,
                        "ok": False,
                        "error_kind": "service",
                        "retryable": True,
                        "rows": [],
                        "message": str(exc),
                    }

                if result["ok"]:
                    batch_retry_overrides.pop(batch_index, None)
                    batch_last_failure_ids.pop(batch_index, None)
                    batch_same_failure_streak.pop(batch_index, None)
                    _log_step(
                        f"batch {batch_index + 1}/{len(batches)} ok via {provider_name} "
                        f"(rows={len(result['rows'])})"
                    )
                    with telemetry_lock:
                        stats = provider_stats[provider_name]
                        stats["rows_ok"] += len(result["rows"])
                    newly_completed_for_batch = 0
                    for row in result["rows"]:
                        is_new = row["id"] not in translated_rows
                        translated_rows[row["id"]] = {
                            "translation_provider_raw": row.get("translation_provider_raw", ""),
                            "translation_masked": row["translation_masked"],
                            "provider": row.get("provider", provider_name),
                            "qa_status": row.get("qa_status", "passed"),
                        }
                        if is_new:
                            newly_completed_for_batch += 1
                            checkpoint_rows_pending.append(
                                {
                                    "id": row["id"],
                                    "translation_provider_raw": row.get("translation_provider_raw", ""),
                                    "translation_masked": row["translation_masked"],
                                    "provider": row.get("provider", provider_name),
                                    "qa_status": row.get("qa_status", "passed"),
                                }
                            )
                    completed_since_checkpoint += 1
                    if newly_completed_for_batch > 0:
                        flush_translation_checkpoint(force=False)
                    continue

                row_failures: List[dict] = []
                if result.get("rows"):
                    newly_completed_for_batch = 0
                    for row in result["rows"]:
                        if row.get("ok") and row.get("translation_masked"):
                            is_new = row["id"] not in translated_rows
                            translated_rows[row["id"]] = {
                                "translation_provider_raw": row.get("translation_provider_raw", ""),
                                "translation_masked": row["translation_masked"],
                                "provider": row.get("provider", provider_name),
                                "qa_status": row.get("qa_status", "passed"),
                            }
                            if is_new:
                                newly_completed_for_batch += 1
                                checkpoint_rows_pending.append(
                                    {
                                        "id": row["id"],
                                        "translation_provider_raw": row.get("translation_provider_raw", ""),
                                        "translation_masked": row["translation_masked"],
                                        "provider": row.get("provider", provider_name),
                                        "qa_status": row.get("qa_status", "passed"),
                                    }
                                )
                        else:
                            row_failures.append(row)

                    completed_since_checkpoint += 1
                    if newly_completed_for_batch > 0:
                        flush_translation_checkpoint(force=False)

                    with telemetry_lock:
                        stats = provider_stats[provider_name]
                        stats["rows_ok"] += sum(1 for row in result["rows"] if row.get("ok"))
                        stats["rows_failed"] += sum(1 for row in result["rows"] if not row.get("ok"))

                    if not row_failures:
                        batch_retry_overrides.pop(batch_index, None)
                        batch_last_failure_ids.pop(batch_index, None)
                        batch_same_failure_streak.pop(batch_index, None)
                        _log_step(
                            f"batch {batch_index + 1}/{len(batches)} completed with per-row recovery via {provider_name}"
                        )
                        continue

                    failed_ids = tuple(sorted(str(row.get("id", "")) for row in row_failures if row.get("id")))
                    if failed_ids:
                        previous_failed_ids = batch_last_failure_ids.get(batch_index)
                        if previous_failed_ids == failed_ids:
                            batch_same_failure_streak[batch_index] = batch_same_failure_streak.get(batch_index, 0) + 1
                        else:
                            batch_same_failure_streak[batch_index] = 1
                        batch_last_failure_ids[batch_index] = failed_ids

                        narrowed_retry_batch = [jobs_by_id[item_id] for item_id in failed_ids if item_id in jobs_by_id]
                        if narrowed_retry_batch:
                            batch_retry_overrides[batch_index] = narrowed_retry_batch
                            _runtime_log(
                                "DEBUG",
                                f"batch {batch_index + 1}/{len(batches)} narrowed retry set to {len(narrowed_retry_batch)} rows",
                            )

                error_kind = result.get("error_kind", "service")
                retryable = bool(result.get("retryable", True))
                if error_kind == "rate_limit":
                    provider_rate_limit_streak[provider_name] = provider_rate_limit_streak.get(provider_name, 0) + 1
                    cooldown = min(
                        rate_limit_cooldown_max_seconds,
                        max(1, rate_limit_cooldown_seconds)
                        * (2 ** max(0, provider_rate_limit_streak[provider_name] - 1)),
                    )
                    temporarily_disabled_until[provider_name] = time.time() + float(cooldown)
                    disabled_providers[provider_name] = f"rate_limit_cooldown_{cooldown}s"
                    _log_step(
                        f"provider cooling down: {provider_name} (reason=rate_limit, cooldown={cooldown}s)"
                    )
                elif error_kind == "quota" or not retryable:
                    permanently_disabled_providers[provider_name] = error_kind
                    disabled_providers[provider_name] = error_kind
                    _log_step(f"provider disabled: {provider_name} (reason={error_kind})")

                attempt_count = batch_attempts.get(batch_index, 0) + 1
                batch_attempts[batch_index] = attempt_count
                available_after_disable = [
                    provider for provider in providers if provider.name not in permanently_disabled_providers
                ]
                active_after_disable = [
                    provider
                    for provider in available_after_disable
                    if provider.name not in temporarily_disabled_until
                ]
                tried_for_batch = batch_tried_providers.get(batch_index, set())
                untried_active_providers = [
                    provider for provider in active_after_disable if provider.name not in tried_for_batch
                ]
                has_attempt_budget = (max_batch_attempts <= 0) or (attempt_count < max_batch_attempts)
                if row_failures and len(available_after_disable) <= 1:
                    same_failure_streak = batch_same_failure_streak.get(batch_index, 0)
                    if same_failure_streak >= 3:
                        has_attempt_budget = False
                        _log_step(
                            f"batch {batch_index + 1}/{len(batches)} has unchanged row-level failures "
                            f"for {same_failure_streak} attempts with a single provider; marking rows failed"
                        )

                if has_attempt_budget and (untried_active_providers or available_after_disable):
                    retry_mode = "alternate provider" if untried_active_providers else "same provider"
                    _log_step(
                        f"retry scheduling batch {batch_index + 1}/{len(batches)} on {retry_mode} "
                        f"(attempt {attempt_count + 1})"
                    )
                    pending.append(batch_index)
                else:
                    batch = batches[batch_index]
                    if row_failures:
                        batch_retry_overrides.pop(batch_index, None)
                        batch_last_failure_ids.pop(batch_index, None)
                        batch_same_failure_streak.pop(batch_index, None)
                        failures.extend(row_failures)
                        if failures_output_path:
                            _append_jsonl(failures_output_path, row_failures)
                    else:
                        batch_retry_overrides.pop(batch_index, None)
                        batch_last_failure_ids.pop(batch_index, None)
                        batch_same_failure_streak.pop(batch_index, None)
                        with telemetry_lock:
                            stats = provider_stats[provider_name]
                            stats["rows_failed"] += len(batch)
                        failed_rows = [
                            {
                                "id": item["id"],
                                "ok": False,
                                "mt_error": f"provider_failed:{result.get('message', '')}",
                                "mt_error_kind": result.get("error_kind", "service"),
                                "mt_retryable": bool(result.get("retryable", True)),
                                "mt_error_message": result.get("message", ""),
                                "provider": provider_name,
                            }
                            for item in batch
                        ]
                        failures.extend(failed_rows)
                        if failures_output_path:
                            _append_jsonl(failures_output_path, failed_rows)

    flush_translation_checkpoint(force=True)

    if args.treat_remaining_failures_as_ok and failures:
        for failure in failures:
            item_id = failure.get("id")
            translation_masked = failure.get("translation_masked")
            if not isinstance(item_id, str) or not item_id:
                continue
            if not isinstance(translation_masked, str) or not translation_masked.strip():
                continue

            translated_rows[item_id] = {
                "translation_provider_raw": failure.get("translation_provider_raw", ""),
                "translation_masked": translation_masked,
                "provider": failure.get("provider", "accepted_failure"),
                "qa_status": "accepted_failure",
            }
            accepted_failures.append(failure)

        if accepted_failures:
            _log_step(
                "treat-remaining-failures-as-ok enabled: "
                f"promoted {len(accepted_failures)} failed rows into translated output (kept in failures report for inspection)"
            )

    ordered_out: List[dict] = []
    for job in all_jobs:
        translated_meta = translated_rows.get(job["id"])
        if translated_meta and translated_meta.get("translation_masked"):
            ordered_out.append(
                {
                    "id": job["id"],
                    "translation_provider_raw": translated_meta.get("translation_provider_raw", ""),
                    "translation_masked": translated_meta["translation_masked"],
                    "provider": translated_meta.get("provider", "unknown"),
                }
            )

    _write_jsonl(output_path, ordered_out)

    if args.failures_output:
        _write_jsonl(Path(args.failures_output), failures)

    _log_step("translation stage complete; writing summaries")

    print(f"[INFO] MT provider order: {', '.join(provider_order)}")
    if enabled_provider_order:
        print(
            "[INFO] MT enabled provider weights: "
            + ", ".join(f"{name}={provider_weights.get(name, 1)}" for name in enabled_provider_order)
        )
    if configured_disabled:
        print(
            "[INFO] Providers skipped by config: "
            + ", ".join(f"{name}({reason})" for name, reason in configured_disabled.items())
        )
    if disabled_providers:
        print(
            "[WARN] Disabled providers due to limits/errors: "
            + ", ".join(f"{name}({reason})" for name, reason in disabled_providers.items())
        )
    print(f"[INFO] Input rows considered: {len(jobs)}")
    print(f"[INFO] Successfully translated: {len(ordered_out)}")
    if args.treat_remaining_failures_as_ok:
        print(f"[INFO] Accepted failed rows as OK: {len(accepted_failures)}")
    print(f"[INFO] Failed rows: {len(failures)}")
    print(f"[INFO] Wrote translated JSONL: {args.output}")
    if args.failures_output:
        print(f"[INFO] Wrote failures JSONL: {args.failures_output}")

    review_output = Path(args.review_output) if args.review_output else Path(args.output).with_suffix(".review.md")
    failures_path_for_report = args.failures_output or "<not_written>"
    _write_mt_review_markdown(
        review_output,
        input_path=args.input,
        output_path=args.output,
        failures_path=failures_path_for_report,
        source_field=source_field,
        jobs=jobs,
        entries_by_id=entries_by_id,
        translated_rows=translated_rows,
        failures=failures,
        include_internal_masked_fields=args.include_internal_masked_fields,
    )
    print(f"[INFO] Wrote MT review markdown: {review_output}")

    failures_report_output = (
        Path(args.failures_output).with_suffix(".md")
        if args.failures_output
        else review_output.with_name(review_output.stem + ".failures.md")
    )
    _write_mt_failures_markdown(
        failures_report_output,
        input_path=args.input,
        output_path=args.output,
        failures_path=failures_path_for_report,
        source_field=source_field,
        jobs=jobs,
        entries_by_id=entries_by_id,
        translated_rows=translated_rows,
        failures=failures,
        include_internal_masked_fields=args.include_internal_masked_fields,
    )
    print(f"[INFO] Wrote MT failures markdown: {failures_report_output}")
    prep_context = {
        "mt_config": args.mt_config,
        "mt_local_config": args.mt_local_config,
        "providers_requested": ", ".join(provider_order),
        "providers_enabled": ", ".join(enabled_provider_order),
        "source_lang": source_lang,
        "target_lang": target_lang,
        "batch_size": str(batch_size),
        "max_parallel": str(max_parallel_per_provider),
        "parenthesized_transport_token_edges": str(parenthesized_transport_token_edges),
    }
    translation_success_report_output = Path(args.translation_success_report)
    _write_mt_success_markdown(
        translation_success_report_output,
        input_path=args.input,
        output_path=args.output,
        source_field=source_field,
        jobs=jobs,
        entries_by_id=entries_by_id,
        translated_rows=translated_rows,
        prep_context=prep_context,
        include_internal_masked_fields=args.include_internal_masked_fields,
    )
    print(f"[INFO] Wrote translation success trace markdown: {translation_success_report_output}")

    translation_failures_report_output = Path(args.translation_failures_report)
    _write_mt_failures_markdown(
        translation_failures_report_output,
        input_path=args.input,
        output_path=args.output,
        failures_path=failures_path_for_report,
        source_field=source_field,
        jobs=jobs,
        entries_by_id=entries_by_id,
        translated_rows=translated_rows,
        failures=failures,
        include_internal_masked_fields=args.include_internal_masked_fields,
    )
    print(f"[INFO] Wrote translation failures trace markdown: {translation_failures_report_output}")

    print("[INFO] MT request telemetry by provider:")
    for provider_name in provider_order:
        stats = provider_stats.get(provider_name)
        if not stats:
            continue
        calls = max(1, stats["calls"])
        avg_rows = stats["rows_total"] / calls
        avg_words = stats["words_total"] / calls
        avg_sec = stats["response_sec_total"] / calls
        print(
            f"[INFO]   {provider_name}: calls={stats['calls']} ok={stats['calls_ok']} error={stats['calls_error']} "
            f"rows_total={stats['rows_total']} rows/req={avg_rows:.2f} words_total={stats['words_total']} "
            f"words/req={avg_words:.2f} sec_total={stats['response_sec_total']:.3f} sec/req={avg_sec:.3f} "
            f"sec_min={stats['response_sec_min'] if stats['response_sec_min'] is not None else 0:.3f} "
            f"sec_max={stats['response_sec_max'] if stats['response_sec_max'] is not None else 0:.3f}"
        )
        if stats.get("last_error"):
            print(f"[WARN]   {provider_name} last_error={stats['last_error']}")

    print("[INFO] MT request call log:")
    for event in call_log:
        err = f" error={event['error']}" if event.get("error") else ""
        print(
            f"[INFO]   {event['ts']} provider={event['provider']} rows={event['rows']} words={event['words']} "
            f"duration_sec={event['duration_sec']:.3f} ok={event['ok']}{err}"
        )
    _log_step("translate-mt finished")


def _split_sentences(text: str) -> List[str]:
    if not text:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def _has_likely_german_verb(sentence: str) -> bool:
    words = _tokenize_words(sentence)
    if not words:
        return True

    verb_anchors = {
        "ist", "sind", "war", "waren", "wird", "werden", "hat", "haben", "kann", "können",
        "muss", "müssen", "soll", "sollen", "finden", "geschehen", "passiert", "untersuchen",
        "abgeschlossen", "zerstört", "verfolgen", "herausfinden",
    }
    if any(word in verb_anchors for word in words):
        return True

    # light heuristic for common finite/infinitive endings
    return any(
        word.endswith(("en", "st", "t"))
        for word in words
        if len(word) >= 4
    )


def _quality_score_and_flags(
    english: str,
    german: str,
    source_masked: str,
    translation_masked: str,
    baseline_masked: str | None,
) -> Tuple[int, List[str]]:
    score = 10
    flags: List[str] = []

    english_plain = _plain_for_quality(english)
    german_plain = _plain_for_quality(german)

    if baseline_masked is not None and baseline_masked == translation_masked:
        score -= 3
        flags.append("unchanged_vs_baseline")

    de_words = _tokenize_words(german_plain)
    en_hits = sum(1 for word in de_words if word in ENGLISH_STOPWORDS)
    de_hits = sum(1 for word in de_words if word in GERMAN_HINTS)
    if en_hits >= 3 and en_hits >= de_hits:
        score -= 2
        flags.append("english_residue")

    if re.search(r"\b(mit\s+dem\s+passiert\s+ist)\b", german_plain.lower()):
        score -= 2
        flags.append("awkward_literal_phrase")

    if re.search(r"\bmüssen\s+sie\s+die\s+finden\b", german_plain.lower()):
        score -= 2
        flags.append("broken_word_order")

    if re.search(r"\bder\s+kolonieschiff\b", german_plain.lower()):
        score -= 1
        flags.append("article_gender_mismatch")

    # high-risk structure: check sentence-level verb plausibility
    sentences = _split_sentences(german_plain)
    bad_sentences = [s for s in sentences if len(_tokenize_words(s)) >= 6 and not _has_likely_german_verb(s)]
    if bad_sentences:
        score -= 2
        flags.append("possible_missing_verb")

    # sanity check: source placeholders vs translation placeholders
    src_tokens = re.findall(r"__PH_\d+__", source_masked)
    dst_tokens = re.findall(r"__PH_\d+__", translation_masked)
    if src_tokens != dst_tokens:
        score -= 4
        flags.append("placeholder_sequence_mismatch")

    return max(0, min(10, score)), flags


def cmd_quality_audit(args: argparse.Namespace) -> None:
    export_file = Path(args.export_file)
    baseline_file = Path(args.baseline_translated_file)
    current_file = Path(args.current_translated_file)
    output = Path(args.output)
    report_csv = Path(args.report_csv)

    exported = {entry["id"]: entry for entry in _read_jsonl(export_file)}
    baseline = {entry.get("id"): entry for entry in _read_jsonl(baseline_file)}
    current = {entry.get("id"): entry for entry in _read_jsonl(current_file)}

    rows: List[dict] = []
    for item_id, exp in exported.items():
        risk_score = int(exp.get("risk_score", 0))
        if risk_score < int(args.min_risk_score):
            continue

        cur = current.get(item_id)
        if not cur:
            continue
        current_masked = cur.get("translation_masked")
        if not isinstance(current_masked, str) or not current_masked.strip():
            continue

        baseline_masked = None
        base_entry = baseline.get(item_id)
        if base_entry:
            baseline_masked = base_entry.get("translation_masked")

        current_restored = restore_text(current_masked, exp.get("protected", {}))
        current_restored = _compact_empyrion_tag_spacing(current_restored)
        quality_score, quality_flags = _quality_score_and_flags(
            english=exp.get("english", ""),
            german=current_restored,
            source_masked=exp.get("source_masked", ""),
            translation_masked=current_masked,
            baseline_masked=baseline_masked,
        )

        if quality_score > int(args.max_quality_score):
            continue

        rows.append(
            {
                "id": item_id,
                "file": exp.get("file", ""),
                "row": exp.get("row", ""),
                "key": exp.get("key", ""),
                "risk_score": risk_score,
                "risk_level": exp.get("risk_level", ""),
                "quality_score": quality_score,
                "quality_flags": quality_flags,
                "baseline_same": baseline_masked == current_masked if baseline_masked is not None else False,
                "english_plain": _plain_for_quality(exp.get("english", "")),
                "german_plain": _plain_for_quality(current_restored),
                "source_masked": exp.get("source_masked", ""),
                "current_translation_masked": current_masked,
            }
        )

    rows.sort(
        key=lambda item: (
            item["quality_score"],
            -int(item["risk_score"]),
            str(item["file"]),
            int(item["row"]),
        )
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report_csv.parent.mkdir(parents=True, exist_ok=True)
    with report_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "id", "file", "row", "key", "risk_score", "quality_score", "quality_flags", "baseline_same",
            "english_plain", "german_plain",
        ])
        for row in rows:
            writer.writerow([
                row["id"], row["file"], row["row"], row["key"], row["risk_score"], row["quality_score"],
                "|".join(row["quality_flags"]), row["baseline_same"], row["english_plain"], row["german_plain"],
            ])

    print(f"[INFO] Quality-audit candidates: {len(rows)}")
    print(f"[INFO] Wrote quality JSONL: {output}")
    print(f"[INFO] Wrote quality CSV: {report_csv}")


def cmd_quality_chunk(args: argparse.Namespace) -> None:
    candidates_file = Path(args.candidates_file)
    out_dir = Path(args.out_dir)
    size = max(1, int(args.size))
    max_chunks = int(args.max_chunks) if args.max_chunks else 0

    entries = _read_jsonl(candidates_file)
    if args.max_entries > 0:
        entries = entries[: args.max_entries]

    out_dir.mkdir(parents=True, exist_ok=True)
    chunks = [entries[i : i + size] for i in range(0, len(entries), size)]
    if max_chunks > 0:
        chunks = chunks[:max_chunks]

    index_rows: List[dict] = []
    for idx, chunk in enumerate(chunks, start=1):
        chunk_id = f"quality_chunk_{idx:04d}"
        jsonl_path = out_dir / f"{chunk_id}.jsonl"
        prompt_path = out_dir / f"{chunk_id}.prompt.txt"

        with jsonl_path.open("w", encoding="utf-8") as handle:
            for item in chunk:
                handle.write(
                    json.dumps(
                        {
                            "id": item["id"],
                            "source_masked": item["source_masked"],
                            "current_translation_masked": item["current_translation_masked"],
                            "risk_score": item.get("risk_score", 0),
                            "quality_score": item.get("quality_score", 0),
                            "quality_flags": item.get("quality_flags", []),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        prompt_path.write_text(
            "Revise each JSONL line to high-quality natural German while preserving markup placeholders.\n"
            "Input fields: id, source_masked, current_translation_masked.\n"
            "Rules:\n"
            "1) Keep id unchanged.\n"
            "2) Output ONLY fields: id, translation_masked.\n"
            "3) Preserve ALL __PH_n__ tokens exactly and in the same order.\n"
            "4) Improve grammar/word order/article cases; do not omit verbs.\n"
            "5) Keep concise in-game UI tone.\n",
            encoding="utf-8",
        )

        index_rows.append(
            {
                "chunk_id": chunk_id,
                "rows": len(chunk),
                "jsonl": str(jsonl_path),
                "prompt": str(prompt_path),
                "translated_output_suggestion": str(out_dir / f"{chunk_id}.translated.jsonl"),
            }
        )

    index_file = out_dir / "chunks_index.csv"
    with index_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["chunk_id", "rows", "jsonl", "prompt", "translated_output_suggestion"],
        )
        writer.writeheader()
        writer.writerows(index_rows)

    print(f"[INFO] Created quality chunks: {len(chunks)} in {out_dir}")
    print(f"[INFO] Wrote quality chunk index: {index_file}")


def cmd_apply(args: argparse.Namespace) -> None:
    base = Path(args.base_dir)
    export_file = Path(args.export_file)
    translated_file = Path(args.translated_file)
    out_dir = Path(args.out_dir)
    pattern_file = Path(args.pattern_file) if args.pattern_file else None
    glossary_file = Path(args.glossary_file) if args.glossary_file else None

    patterns = _build_patterns(pattern_file)
    glossary = load_glossary(glossary_file)

    exported = {entry["id"]: entry for entry in _read_jsonl(export_file)}
    translated = {entry["id"]: entry for entry in _read_jsonl(translated_file)}

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "applied_changes.csv"
    with report_path.open("w", encoding="utf-8", newline="") as rep_handle:
        rep_writer = csv.writer(rep_handle)
        rep_writer.writerow(["file", "row", "key", "status", "old_de", "new_de", "source_en"])

        for file_name in CSV_FILES:
            file_path = base / file_name
            rows = load_csv_rows(file_path)
            memory = build_translation_memory(rows)

            for idx, row in enumerate(rows, start=2):
                key = (row.get("KEY") or "").strip()
                if not key:
                    continue
                cid = make_id(file_name, key, idx)
                exp = exported.get(cid)
                if not exp:
                    continue

                current_de = row.get("Deutsch") or ""
                source_en = row.get("English") or ""
                source_norm = _normalize_text(source_en)

                new_de = None
                translated_entry = translated.get(cid)
                if translated_entry and translated_entry.get("translation_masked"):
                    masked = translated_entry["translation_masked"]
                    restored = restore_text(masked, exp.get("protected", {}))
                    restored = _compact_empyrion_tag_spacing(restored)
                    new_de = enforce_glossary(restored, glossary)
                elif source_norm in memory:
                    new_de = memory[source_norm]

                if new_de and new_de.strip() and new_de != current_de:
                    test_masked_old, old_tokens = protect_text(source_en, patterns)
                    test_masked_new, new_tokens = protect_text(new_de, patterns)
                    if set(old_tokens.values()) - set(new_tokens.values()):
                        continue
                    row["Deutsch"] = new_de
                    rep_writer.writerow([file_name, idx, key, exp["status"], current_de, new_de, source_en])

            output_path = out_dir / f"{Path(file_name).stem}.de.completed.csv"
            with output_path.open("w", encoding="utf-8", newline="") as out_handle:
                fieldnames = rows[0].keys() if rows else ["KEY", "English", "Deutsch"]
                writer = csv.DictWriter(out_handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            print(f"[INFO] Wrote {output_path}")

    print(f"[INFO] Wrote change report: {report_path}")


def cmd_build_stub(args: argparse.Namespace) -> None:
    export_file = Path(args.export_file)
    out_file = Path(args.output)
    entries = _read_jsonl(export_file)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(
                json.dumps(
                    {
                        "id": entry["id"],
                        "translation_masked": entry["source_masked"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"[INFO] Wrote translation stub template: {out_file}")


def cmd_chunk(args: argparse.Namespace) -> None:
    export_file = Path(args.export_file)
    out_dir = Path(args.out_dir)
    size = max(1, int(args.size))
    max_chunks = int(args.max_chunks) if args.max_chunks else 0

    entries = _read_jsonl(export_file)
    if args.high_risk_only and args.standard_only:
        raise ValueError("--high-risk-only and --standard-only cannot be used together")

    if args.skip_existing:
        entries = [entry for entry in entries if not (entry.get("deutsch_current") or "").strip()]

    out_dir.mkdir(parents=True, exist_ok=True)
    index_rows: List[dict] = []
    chunk_specs: List[Tuple[str, List[dict]]] = []
    if args.split_by_risk:
        high_entries = [entry for entry in entries if int(entry.get("risk_score", 0)) >= args.high_risk_min_score]
        high_entries = sorted(
            high_entries,
            key=lambda item: (
                -int(item.get("risk_score", 0)),
                str(item.get("file", "")),
                int(item.get("row", 0)),
                str(item.get("key", "")),
            ),
        )
        if args.high_risk_top_n > 0:
            high_entries = high_entries[: args.high_risk_top_n]

        normal_entries = [entry for entry in entries if int(entry.get("risk_score", 0)) < args.high_risk_min_score]

        high_chunks: List[List[dict]] = []
        if not args.standard_only:
            high_chunks = [high_entries[i : i + size] for i in range(0, len(high_entries), size)]

        normal_chunks: List[List[dict]] = []
        if not args.high_risk_only:
            normal_chunks = [normal_entries[i : i + size] for i in range(0, len(normal_entries), size)]

        if max_chunks > 0:
            high_chunks = high_chunks[:max_chunks]
            normal_chunks = normal_chunks[:max_chunks]

        for idx, chunk in enumerate(high_chunks, start=1):
            chunk_specs.append((f"highrisk_chunk_{idx:04d}", chunk))
        for idx, chunk in enumerate(normal_chunks, start=1):
            chunk_specs.append((f"chunk_{idx:04d}", chunk))
    else:
        chunks = [entries[i : i + size] for i in range(0, len(entries), size)]
        if max_chunks > 0:
            chunks = chunks[:max_chunks]
        for idx, chunk in enumerate(chunks, start=1):
            chunk_specs.append((f"chunk_{idx:04d}", chunk))

    for chunk_id, chunk in chunk_specs:
        jsonl_path = out_dir / f"{chunk_id}.jsonl"
        prompt_path = out_dir / f"{chunk_id}.prompt.txt"

        with jsonl_path.open("w", encoding="utf-8") as handle:
            for item in chunk:
                handle.write(
                    json.dumps(
                        {
                            "id": item["id"],
                            "source_masked": item["source_masked"],
                            "status": item.get("status", ""),
                            "key": item.get("key", ""),
                            "file": item.get("file", ""),
                            "risk_level": item.get("risk_level", ""),
                            "risk_score": item.get("risk_score", 0),
                            "risk_flags": item.get("risk_flags", []),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        is_high_risk = chunk_id.startswith("highrisk_chunk_")
        if is_high_risk:
            prompt_text = (
                "Translate each JSONL line from English to German with high grammar quality.\n"
                "Rules:\n"
                "1) Keep id unchanged.\n"
                "2) Translate ONLY source_masked to translation_masked.\n"
                "3) Preserve ALL __PH_n__ tokens exactly and do not reorder them.\n"
                "4) Produce fluent natural German sentence structure, especially around placeholders/tags.\n"
                "5) For short dialogue acts (e.g. Deal.), prefer idiomatic German, not literal word mapping.\n"
                "6) Return JSONL lines only, fields: id, translation_masked.\n"
            )
        else:
            prompt_text = (
                "Translate each JSONL line from English to German.\n"
                "Rules:\n"
                "1) Keep id unchanged.\n"
                "2) Translate ONLY source_masked to translation_masked.\n"
                "3) Preserve ALL __PH_n__ tokens exactly.\n"
                "4) Keep game/UI style concise and natural German.\n"
                "5) Return JSONL lines only, fields: id, translation_masked.\n"
            )
        prompt_path.write_text(prompt_text, encoding="utf-8")

        index_rows.append(
            {
                "chunk_id": chunk_id,
                "risk_bucket": "high" if is_high_risk else "standard",
                "rows": len(chunk),
                "jsonl": str(jsonl_path),
                "prompt": str(prompt_path),
                "translated_output_suggestion": str(out_dir / f"{chunk_id}.translated.jsonl"),
            }
        )

    index_file = out_dir / "chunks_index.csv"
    with index_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["chunk_id", "risk_bucket", "rows", "jsonl", "prompt", "translated_output_suggestion"],
        )
        writer.writeheader()
        writer.writerows(index_rows)

    print(f"[INFO] Created {len(chunk_specs)} chunk files in {out_dir}")
    print(f"[INFO] Wrote chunk index: {index_file}")


def cmd_merge(args: argparse.Namespace) -> None:
    in_dir = Path(args.in_dir)
    output = Path(args.output)
    pattern = args.pattern

    files = sorted(in_dir.glob(pattern))
    merged: Dict[str, str] = {}
    duplicates = 0
    invalid = 0

    for file_path in files:
        for entry in _read_jsonl(file_path):
            item_id = entry.get("id")
            translation = entry.get("translation_masked")
            if not item_id or not isinstance(translation, str):
                invalid += 1
                continue
            if item_id in merged and merged[item_id] != translation:
                duplicates += 1
            merged[item_id] = translation

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for item_id, translation in merged.items():
            handle.write(json.dumps({"id": item_id, "translation_masked": translation}, ensure_ascii=False) + "\n")

    print(f"[INFO] Merged files matched by '{pattern}': {len(files)}")
    print(f"[INFO] Wrote merged translations: {output}")
    print(f"[INFO] Entries: {len(merged)} | duplicate ids overwritten: {duplicates} | invalid lines: {invalid}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Empyrion German localization helper (MT-first workflow).\n"
            "Tip: use '<command> --help' to see command-specific options like --keys."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # 1) Audit\n"
            "  python3 empyrion_localize.py audit --base-dir ./input_data --report-dir ./reports\n"
            "\n"
            "  # 2) Export\n"
            "  python3 empyrion_localize.py export --base-dir ./input_data --output ./reports/translation_units.risk.v2.jsonl\n"
            "\n"
            "  # 3) MT translate (default flow)\n"
            "  python3 empyrion_localize.py translate-mt --input ./reports/translation_units.risk.v2.jsonl --output ./reports/translations.mt.jsonl --target-lang DE\n"
            "\n"
            "  # 4) MT translate selected keys\n"
            "  python3 empyrion_localize.py translate-mt --input ./reports/translation_units.risk.v2.jsonl --output ./reports/translations.mt.keys.jsonl --keys dialogue_iKK4CKC eden_pda_eGSGG\n"
            "\n"
            "  # 5) Apply\n"
            "  python3 empyrion_localize.py apply --base-dir ./input_data --export-file ./reports/translation_units.risk.v2.jsonl --translated-file ./reports/translations.mt.jsonl --out-dir ./output\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="Scan candidate rows")
    p_audit.add_argument("--base-dir", default="./input_data")
    p_audit.add_argument("--report-dir", default="./reports")
    p_audit.set_defaults(func=cmd_audit)

    p_export = sub.add_parser("export", help="Export translation units as JSONL")
    p_export.add_argument("--base-dir", default="./input_data")
    p_export.add_argument("--output", default="./reports/translation_units.jsonl")
    p_export.add_argument("--pattern-file", default="./protect_patterns.txt")
    p_export.add_argument(
        "--high-risk-report",
        default="./reports/high_risk_samples.csv",
        help="Optional CSV report of highest-risk entries for developer quality spot checks.",
    )
    p_export.add_argument(
        "--risk-medium-threshold",
        type=int,
        default=3,
        help="Risk score threshold for medium classification.",
    )
    p_export.add_argument(
        "--risk-high-threshold",
        type=int,
        default=6,
        help="Risk score threshold for high classification.",
    )
    p_export.add_argument(
        "--high-risk-min-score",
        type=int,
        default=6,
        help="Minimum risk score included in high-risk report selection.",
    )
    p_export.add_argument(
        "--high-risk-sample-size",
        type=int,
        default=300,
        help="Max rows to include in high-risk sample report.",
    )
    p_export.set_defaults(func=cmd_export)

    p_risk_report = sub.add_parser(
        "risk-report",
        help="Generate row distribution report by risk score and print level totals",
    )
    p_risk_report.add_argument("--export-file", required=True)
    p_risk_report.add_argument("--output-csv", default="./reports/risk_distribution.v2.csv")
    p_risk_report.set_defaults(func=cmd_risk_report)

    p_risk_sample = sub.add_parser(
        "risk-sample",
        help="Select a random sample from exported rows filtered by risk selectors",
    )
    p_risk_sample.add_argument("--export-file", required=True)
    p_risk_sample.add_argument("--output", required=True, help="Output JSONL sample file")
    p_risk_sample.add_argument(
        "--report-csv",
        default="",
        help="Optional CSV report for sampled rows (default: <output>.csv)",
    )
    p_risk_sample.add_argument(
        "--risk-levels",
        nargs="*",
        default=[],
        help="Risk levels to include (e.g. --risk-levels medium high)",
    )
    p_risk_sample.add_argument(
        "--risk-scores",
        nargs="*",
        type=int,
        default=[],
        help="Exact risk scores to include (e.g. --risk-scores 4 5 6)",
    )
    p_risk_sample.add_argument("--min-score", type=int, default=None)
    p_risk_sample.add_argument("--max-score", type=int, default=None)
    p_risk_sample.add_argument("--size", type=int, default=10)
    p_risk_sample.add_argument("--seed", type=int, default=None)
    p_risk_sample.set_defaults(func=cmd_risk_sample)

    p_stub = sub.add_parser("build-stub", help="Create translation JSONL stub")
    p_stub.add_argument("--export-file", required=True)
    p_stub.add_argument("--output", required=True)
    p_stub.set_defaults(func=cmd_build_stub)

    p_chunk = sub.add_parser("chunk", help="Split exported units into GPT/Copilot-sized JSONL batches")
    p_chunk.add_argument("--export-file", required=True)
    p_chunk.add_argument("--out-dir", required=True)
    p_chunk.add_argument("--size", type=int, default=200)
    p_chunk.add_argument("--max-chunks", type=int, default=0)
    p_chunk.add_argument(
        "--skip-existing",
        action="store_true",
        help="Only chunk rows where Deutsch is currently empty.",
    )
    p_chunk.add_argument(
        "--split-by-risk",
        action="store_true",
        help="Create dedicated high-risk chunks (highrisk_chunk_*) based on export risk metadata.",
    )
    p_chunk.add_argument(
        "--high-risk-min-score",
        type=int,
        default=6,
        help="Minimum risk score that goes to high-risk bucket when --split-by-risk is enabled.",
    )
    p_chunk.add_argument(
        "--high-risk-top-n",
        type=int,
        default=0,
        help="If >0, only keep the top-N high-risk entries by score for high-risk chunk output.",
    )
    p_chunk.add_argument(
        "--high-risk-only",
        action="store_true",
        help="When used with --split-by-risk, emit only high-risk chunks and skip standard chunks.",
    )
    p_chunk.add_argument(
        "--standard-only",
        action="store_true",
        help="When used with --split-by-risk, emit only standard (lower-risk) chunks and skip high-risk chunks.",
    )
    p_chunk.set_defaults(func=cmd_chunk)

    p_translate_mt = sub.add_parser(
        "translate-mt",
        help="Translate JSONL entries with the default pass-1 transport pipeline and write JSONL + reports",
        description=(
            "Default pipeline: source masking -> direct transport tokens -> MT -> restore placeholders -> "
            "placeholder-sequence QA -> report generation.\n"
            "Default reports: --review-output=<output>.review.md, --translation-success-report=./reports/translation-success.md, "
            "--translation-failures-report=./reports/translation-failures.md.\n\n"
            "Bootstrap from raw CSV first (if JSONL does not exist yet):\n"
            "  python3 empyrion_localize.py audit --base-dir ./input_data --report-dir ./reports\n"
            "  python3 empyrion_localize.py export --base-dir ./input_data --output ./reports/translation_units.risk.v2.jsonl\n\n"
            "Examples:\n"
            "  python3 empyrion_localize.py translate-mt --input ./reports/translation_units.risk.v2.jsonl --output ./reports/translations.mt.jsonl --target-lang DE --resume\n"
            "  python3 empyrion_localize.py translate-mt --input ./reports/translation_units.risk.v2.jsonl --output ./reports/translations.mt.keys.jsonl --keys dialogue_iKK4CKC eden_pda_eGSGG --resume\n"
            "  python3 empyrion_localize.py translate-mt --input ./reports/translation_units.risk.v2.jsonl --output ./reports/translations.mt.sample10.jsonl --sample-mode --sample-size 10 --resume"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p_translate_mt.add_argument("--input", required=True, help="Input JSONL containing id + source field")
    p_translate_mt.add_argument("--output", required=True, help="Output JSONL with id + translation_masked")
    p_translate_mt.add_argument(
        "--source-field",
        default="source_masked",
        help="Field name used as source text in input JSONL (default: source_masked)",
    )
    p_translate_mt.add_argument(
        "--mt-config",
        default="./mt.toml",
        help="Local TOML config with MT provider API keys and settings",
    )
    p_translate_mt.add_argument(
        "--mt-local-config",
        default="./mt.local.toml",
        help="Optional local TOML override merged on top of --mt-config",
    )
    p_translate_mt.add_argument(
        "--providers",
        nargs="*",
        default=[],
        help="Providers to use in parallel (e.g. --providers deepl google). If omitted, uses mt.providers.",
    )
    p_translate_mt.add_argument(
        "--provider",
        default="",
        help="Primary provider override (legacy single-provider mode)",
    )
    p_translate_mt.add_argument("--source-lang", default="")
    p_translate_mt.add_argument("--target-lang", default="")
    p_translate_mt.add_argument(
        "--keys",
        nargs="*",
        default=[],
        help=(
            "Optional CSV KEY filter(s) (first-column KEY values). "
            "Only matching rows are translated; accepts space- or comma-separated values."
        ),
    )
    p_translate_mt.add_argument("--batch-size", type=int, default=0)
    p_translate_mt.add_argument(
        "--max-parallel",
        type=int,
        default=0,
        help="Max parallel MT tasks per provider (default from TOML, recommended: 2)",
    )
    p_translate_mt.add_argument(
        "--sample-mode",
        action="store_true",
        help="Translate only a sample subset for inspection/validation",
    )
    p_translate_mt.add_argument(
        "--sample-size",
        type=int,
        default=200,
        help="Sample size when --sample-mode is enabled",
    )
    p_translate_mt.add_argument(
        "--sample-seed",
        type=int,
        default=None,
        help="Optional random seed for sample-mode selection (default: non-deterministic random).",
    )
    p_translate_mt.add_argument(
        "--parenthesized-transport-token-edges",
        dest="parenthesized_transport_token_edges",
        action="store_true",
        default=None,
        help=(
            "Override TOML and force parenthesized edge transport tokens during MT payload generation "
            "(default from mt.parenthesized_transport_token_edges, which defaults to true)."
        ),
    )
    p_translate_mt.add_argument(
        "--no-parenthesized-transport-token-edges",
        dest="parenthesized_transport_token_edges",
        action="store_false",
        help="Override TOML and disable parenthesized edge transport tokens.",
    )
    p_translate_mt.add_argument(
        "--failures-output",
        default="",
        help="Optional output JSONL for failed rows",
    )
    p_translate_mt.add_argument(
        "--review-output",
        default="",
        help="Optional markdown review output path (default: <output>.review.md)",
    )
    p_translate_mt.add_argument(
        "--translation-success-report",
        default="./reports/translation-success.md",
        help="Markdown trace report for successful translated rows (single canonical output).",
    )
    p_translate_mt.add_argument(
        "--translation-failures-report",
        default="./reports/translation-failures.md",
        help="Markdown trace report for failed translation rows.",
    )
    p_translate_mt.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing --output file if present; already translated rows are skipped.",
    )
    p_translate_mt.add_argument(
        "--resume-from-output",
        default="",
        help="Explicit JSONL translation file to resume from (same schema as --output).",
    )
    p_translate_mt.add_argument(
        "--include-internal-masked-fields",
        action="store_true",
        help="Include internal masked lifecycle fields in review output (source_masked_internal and translation_masked_final).",
    )
    p_translate_mt.add_argument(
        "--treat-remaining-failures-as-ok",
        action="store_true",
        help=(
            "Promote failed rows with available translation_masked into translated output and remove them from remaining failures. "
            "Use when you want full CSV coverage despite unresolved placeholder QA errors."
        ),
    )
    p_translate_mt.set_defaults(func=cmd_translate_mt)

    p_merge = sub.add_parser("merge", help="Merge translated chunk JSONL files")
    p_merge.add_argument("--in-dir", required=True)
    p_merge.add_argument("--pattern", default="*.translated.jsonl")
    p_merge.add_argument("--output", required=True)
    p_merge.set_defaults(func=cmd_merge)

    p_quality_audit = sub.add_parser(
        "quality-audit",
        help="Score high-risk translated rows for grammar/fluency issues and emit bulk refinement candidates",
    )
    p_quality_audit.add_argument("--export-file", required=True)
    p_quality_audit.add_argument(
        "--baseline-translated-file",
        default="./reports/translations.all.jsonl",
        help="Baseline translation file to detect unchanged rows.",
    )
    p_quality_audit.add_argument("--current-translated-file", required=True)
    p_quality_audit.add_argument("--min-risk-score", type=int, default=9)
    p_quality_audit.add_argument("--max-quality-score", type=int, default=7)
    p_quality_audit.add_argument(
        "--output",
        default="./reports/highrisk_quality_candidates.jsonl",
    )
    p_quality_audit.add_argument(
        "--report-csv",
        default="./reports/highrisk_quality_candidates.csv",
    )
    p_quality_audit.set_defaults(func=cmd_quality_audit)

    p_quality_chunk = sub.add_parser(
        "quality-chunk",
        help="Create chat refinement chunks from quality-audit candidates",
    )
    p_quality_chunk.add_argument("--candidates-file", required=True)
    p_quality_chunk.add_argument("--out-dir", required=True)
    p_quality_chunk.add_argument("--size", type=int, default=120)
    p_quality_chunk.add_argument("--max-chunks", type=int, default=0)
    p_quality_chunk.add_argument("--max-entries", type=int, default=0)
    p_quality_chunk.set_defaults(func=cmd_quality_chunk)

    p_apply = sub.add_parser("apply", help="Apply translated JSONL and generate completed CSV files")
    p_apply.add_argument("--base-dir", default="./input_data")
    p_apply.add_argument("--export-file", required=True)
    p_apply.add_argument("--translated-file", required=True)
    p_apply.add_argument("--out-dir", default="./output")
    p_apply.add_argument("--pattern-file", default="./protect_patterns.txt")
    p_apply.add_argument("--glossary-file", default="./glossary_de.csv")
    p_apply.set_defaults(func=cmd_apply)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
