"""Lightweight local inventory for long-span profile/history sources."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import os
from typing import Any

from src.config import settings

IMPORTABLE_SUFFIXES = {".md", ".txt", ".pdf", ".docx", ".xlsx", ".csv", ".pptx", ".json"}
TEXT_SAMPLE_SUFFIXES = {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}
EXPECTED_INSTITUTIONS = ("iitkgp", "nyu", "amazon")
ROOT_DISCOVERY_TERMS = (
    "takeout",
    "google",
    "drive",
    "mail",
    "gmail",
    "keep",
    "notes",
    "course",
    "class",
    "semester",
    "iit",
    "kharagpur",
    "nyu",
    "tandon",
    "amazon",
    "aws",
    "project",
    "portfolio",
    "research",
    "resume",
    "archive",
)
PROJECT_SIGNAL_TERMS = ("project", "course", "thesis", "research", "resume", "portfolio", "capstone", "assignment")
INSTITUTION_TERMS = {
    "iitkgp": ("iitkgp", "iit kharagpur", "kharagpur", "indian institute of technology"),
    "nyu": ("nyu", "tandon", "new york university"),
    "amazon": ("amazon", "aws", "marketplace", "advertising services"),
    "citicorp": ("citicorp", "citi"),
    "loylty": ("loylty", "loylty rewardz"),
}
ERA_TERMS = {
    "undergrad": ("2013", "2014", "2015", "2016", "2017", "undergrad", "btech", "b.tech"),
    "mumbai_systems": ("2017", "2018", "2019", "2020", "mumbai", "loylty", "citicorp"),
    "nyu": ("2021", "2022", "nyu", "masters", "ms"),
    "amazon": ("2022", "2023", "2024", "2025", "amazon", "aws"),
    "builder": ("2025", "2026", "builder", "freelance", "dusrabheja", "datagenie"),
}
SOURCE_TYPE_TERMS = {
    "google_takeout": ("takeout",),
    "google_drive": ("google drive", "my drive", "drive"),
    "gmail_export": ("gmail", "mail"),
    "google_keep": ("google keep", "keep"),
    "apple_notes": ("notes", "apple notes"),
    "coursework": ("course", "class", "semester", "lecture", "assignment"),
    "project_archive": ("project", "portfolio", "capstone", "thesis", "research"),
    "work_history": ("amazon", "aws", "loylty", "citi", "citicorp", "resume"),
}


def _split_roots(raw: str | None) -> list[Path]:
    values = [item.strip() for item in (raw or "").split(",") if item.strip()]
    return [Path(value).expanduser() for value in values]


def _home_inventory_containers() -> list[Path]:
    return [
        Path("~/Desktop").expanduser(),
        Path("~/Documents").expanduser(),
        Path("~/Downloads").expanduser(),
    ]


def discover_inventory_roots(*, extra_roots: list[str | Path] | None = None) -> list[Path]:
    candidates = [
        Path(settings.public_profile_seed_path).expanduser(),
        *_split_roots(settings.collector_project_roots),
        *_split_roots(settings.collector_bootstrap_roots),
        *_split_roots(settings.collector_daily_roots),
        *_home_inventory_containers(),
    ]
    for value in extra_roots or []:
        candidates.append(Path(value).expanduser())

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _path_text(path: Path) -> str:
    return str(path).lower()


def _matched_keys_from_text(text: str, mapping: dict[str, tuple[str, ...]]) -> list[str]:
    lowered = text.lower()
    return [key for key, terms in mapping.items() if any(term in lowered for term in terms)]


def _read_text_sample(path: Path, *, sample_bytes: int) -> str:
    if path.suffix.lower() not in TEXT_SAMPLE_SUFFIXES:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:sample_bytes]
    except OSError:
        return ""


def _sample_directory_texts(current_path: Path, filenames: list[str], *, sample_bytes: int, sample_limit: int = 3) -> tuple[str, int]:
    samples: list[str] = []
    sampled_files = 0
    for name in filenames:
        if sampled_files >= sample_limit:
            break
        file_path = current_path / name
        sample = _read_text_sample(file_path, sample_bytes=sample_bytes)
        if not sample:
            continue
        samples.append(sample)
        sampled_files += 1
    return "\n".join(samples), sampled_files


def _folder_source_types(path_text: str, sample_text: str) -> list[str]:
    return _matched_keys_from_text(f"{path_text}\n{sample_text}", SOURCE_TYPE_TERMS)


def _path_has_project_signal(path: Path, sample_text: str) -> bool:
    lowered = f"{_path_text(path)}\n{sample_text.lower()}"
    return any(term in lowered for term in PROJECT_SIGNAL_TERMS)


def _priority_from_score(score: int) -> str:
    if score >= 12:
        return "high"
    if score >= 7:
        return "medium"
    return "low"


def _score_folder_candidate(candidate: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    source_types = set(candidate.get("matched_source_types") or [])
    institutions = list(candidate.get("matched_institutions") or [])
    eras = list(candidate.get("matched_eras") or [])
    importable_files = int(candidate.get("importable_files") or 0)

    if "google_takeout" in source_types:
        score += 6
        reasons.append("Looks like a Google Takeout export.")
    if "google_drive" in source_types:
        score += 5
        reasons.append("Looks like a Google Drive archive.")
    if "gmail_export" in source_types or "google_keep" in source_types:
        score += 4
        reasons.append("Contains likely Google history or note exports.")
    if "coursework" in source_types:
        score += 4
        reasons.append("Contains course or class history signals.")
    if "project_archive" in source_types:
        score += 4
        reasons.append("Looks like a project archive that can deepen case-study coverage.")
    if "work_history" in source_types:
        score += 4
        reasons.append("Contains work-history signals that can enrich the professional timeline.")
    if "apple_notes" in source_types:
        score += 2
        reasons.append("Contains notes-style material that is probably importable.")
    if importable_files >= 5:
        score += 4
        reasons.append("Contains several importable files.")
    elif importable_files > 0:
        score += 2
        reasons.append("Contains importable files.")
    if institutions:
        score += min(6, len(institutions) * 2)
        reasons.append(f"Institution signals: {', '.join(institutions)}.")
    if eras:
        score += min(4, len(eras))
        reasons.append(f"Era signals: {', '.join(eras)}.")
    if candidate.get("project_signal"):
        score += 2
        reasons.append("Folder name or sampled text looks project-oriented.")

    return score, reasons


def _trim_examples(examples: dict[str, list[str]], *, limit: int = 5) -> dict[str, list[str]]:
    return {key: values[:limit] for key, values in sorted(examples.items()) if values}


def _root_score(summary: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    importable_files = int(summary.get("importable_files") or 0)
    institutions = summary.get("matched_institutions") or []
    eras = summary.get("matched_eras") or []
    source_types = summary.get("matched_source_types") or []

    if summary.get("path", "").endswith("CompanyInterviewPrep"):
        score += 2
        reasons.append("Contains the current public-safe narrative seed pack.")
    if importable_files:
        score += min(4, max(1, importable_files // 2))
        reasons.append("Contains importable artifacts.")
    if institutions:
        score += min(6, len(institutions) * 2)
        reasons.append(f"Institution coverage detected: {', '.join(institutions)}.")
    if eras:
        score += min(4, len(eras))
        reasons.append(f"Era coverage detected: {', '.join(eras)}.")
    if source_types:
        score += min(6, len(source_types) * 2)
        reasons.append(f"Relevant source types: {', '.join(source_types)}.")
    return score, reasons


def _maybe_add_recommended_root(path: Path, bucket: list[dict[str, Any]], seen: set[str], *, score: int, reasons: list[str]) -> None:
    if score <= 0:
        return
    key = str(path)
    if key in seen:
        return
    seen.add(key)
    bucket.append(
        {
            "path": key,
            "score": score,
            "priority": _priority_from_score(score),
            "reasons": reasons,
        }
    )


def _should_recommend_folder(path: Path, candidate: dict[str, Any]) -> bool:
    if int(candidate.get("score") or 0) >= 7 and int(candidate.get("depth") or 0) <= 2:
        return True
    lowered = path.name.lower()
    return any(term in lowered for term in ROOT_DISCOVERY_TERMS)


def _nested_root_dirnames(current_path: Path, roots: list[Path], current_root: Path) -> set[str]:
    names: set[str] = set()
    for other_root in roots:
        if other_root == current_root:
            continue
        try:
            relative = other_root.relative_to(current_path)
        except ValueError:
            continue
        if not relative.parts:
            continue
        names.add(relative.parts[0])
    return names


def build_profile_inventory_payload(
    *,
    extra_roots: list[str | Path] | None = None,
    max_depth: int | None = None,
    max_files: int | None = None,
    sample_bytes: int = 4096,
) -> dict[str, Any]:
    roots = discover_inventory_roots(extra_roots=extra_roots)
    roots_scanned: list[str] = []
    roots_missing: list[str] = []
    institution_hits: dict[str, int] = defaultdict(int)
    era_hits: dict[str, int] = defaultdict(int)
    source_type_hits: dict[str, int] = defaultdict(int)
    institution_examples: dict[str, list[str]] = defaultdict(list)
    era_examples: dict[str, list[str]] = defaultdict(list)
    source_type_examples: dict[str, list[str]] = defaultdict(list)
    likely_importable_folders: list[dict[str, Any]] = []
    project_like_folders: list[dict[str, Any]] = []
    import_priorities: list[dict[str, Any]] = []
    recommended_roots: list[dict[str, Any]] = []
    recommended_roots_seen: set[str] = set()
    source_pack_signals: list[str] = []
    root_summaries: list[dict[str, Any]] = []

    resolved_max_depth = max(1, int(max_depth if max_depth is not None else settings.collector_scan_max_depth or 4))
    resolved_max_files = max(1, int(max_files if max_files is not None else settings.collector_inventory_recent_files_limit or 50))
    scanned_files = 0
    scanned_directories = 0

    for root in roots:
        if not root.exists():
            roots_missing.append(str(root))
            continue

        roots_scanned.append(str(root))
        if root.name == "CompanyInterviewPrep":
            source_pack_signals.append(str(root))

        root_depth = len(root.parts)
        root_institutions: set[str] = set()
        root_eras: set[str] = set()
        root_source_types: set[str] = set()
        root_importable_files = 0
        root_files_seen = 0
        root_directories_seen = 0
        root_candidates: list[str] = []

        for current_root, dirnames, filenames in os.walk(root, onerror=lambda _exc: None):
            current_path = Path(current_root)
            dirnames[:] = sorted(dirnames)
            filenames = sorted(filenames)
            depth = len(current_path.parts) - root_depth
            if depth > resolved_max_depth:
                dirnames[:] = []
                continue
            skip_dirnames = _nested_root_dirnames(current_path, roots, root)
            if skip_dirnames:
                dirnames[:] = [name for name in dirnames if name not in skip_dirnames]

            scanned_directories += 1
            root_directories_seen += 1
            root_files_seen += len(filenames)
            scanned_files += len(filenames)

            sample_text, sampled_files = _sample_directory_texts(current_path, filenames, sample_bytes=sample_bytes)
            combined_text = f"{_path_text(current_path)}\n{sample_text}"
            matched_institutions = _matched_keys_from_text(combined_text, INSTITUTION_TERMS)
            matched_eras = _matched_keys_from_text(combined_text, ERA_TERMS)
            matched_source_types = _folder_source_types(_path_text(current_path), sample_text)
            project_signal = _path_has_project_signal(current_path, sample_text)
            importable_files = sum(1 for name in filenames if Path(name).suffix.lower() in IMPORTABLE_SUFFIXES)

            for key in matched_institutions:
                institution_hits[key] += 1
                if str(current_path) not in institution_examples[key]:
                    institution_examples[key].append(str(current_path))
                root_institutions.add(key)
            for key in matched_eras:
                era_hits[key] += 1
                if str(current_path) not in era_examples[key]:
                    era_examples[key].append(str(current_path))
                root_eras.add(key)
            for key in matched_source_types:
                source_type_hits[key] += 1
                if str(current_path) not in source_type_examples[key]:
                    source_type_examples[key].append(str(current_path))
                root_source_types.add(key)

            root_importable_files += importable_files
            folder_candidate = {
                "path": str(current_path),
                "depth": depth,
                "importable_files": importable_files,
                "sampled_files": sampled_files,
                "matched_institutions": matched_institutions,
                "matched_eras": matched_eras,
                "matched_source_types": matched_source_types,
                "project_signal": project_signal,
            }
            score, reasons = _score_folder_candidate(folder_candidate)
            folder_candidate["score"] = score
            folder_candidate["priority"] = _priority_from_score(score)
            folder_candidate["reasons"] = reasons

            if importable_files > 0 and len(likely_importable_folders) < 40:
                likely_importable_folders.append(folder_candidate)
            if project_signal and len(project_like_folders) < 40:
                project_like_folders.append(folder_candidate)
            if score > 0:
                if len(import_priorities) < 80:
                    import_priorities.append(folder_candidate)
                if len(root_candidates) < 5:
                    root_candidates.append(str(current_path))
                if _should_recommend_folder(current_path, folder_candidate):
                    _maybe_add_recommended_root(
                        current_path,
                        recommended_roots,
                        recommended_roots_seen,
                        score=score,
                        reasons=reasons,
                    )
            if depth == 0:
                root_path_score, root_reasons = _root_score(
                    {
                        "path": str(current_path),
                        "importable_files": importable_files,
                        "matched_institutions": matched_institutions,
                        "matched_eras": matched_eras,
                        "matched_source_types": matched_source_types,
                    }
                )
                _maybe_add_recommended_root(
                    current_path,
                    recommended_roots,
                    recommended_roots_seen,
                    score=root_path_score,
                    reasons=root_reasons,
                )

            if scanned_files >= resolved_max_files:
                break

        root_summary = {
            "path": str(root),
            "folders_seen": max(1, root_directories_seen),
            "files_seen": root_files_seen,
            "importable_files": root_importable_files,
            "matched_institutions": sorted(root_institutions),
            "matched_eras": sorted(root_eras),
            "matched_source_types": sorted(root_source_types),
            "top_candidates": root_candidates,
        }
        root_score, root_reasons = _root_score(root_summary)
        root_summary["score"] = root_score
        root_summary["priority"] = _priority_from_score(root_score)
        root_summary["reasons"] = root_reasons
        root_summaries.append(root_summary)
        _maybe_add_recommended_root(
            root,
            recommended_roots,
            recommended_roots_seen,
            score=root_score,
            reasons=root_reasons,
        )

        if scanned_files >= resolved_max_files:
            break

    import_priorities.sort(key=lambda item: (-int(item["score"]), item["path"]))
    recommended_roots.sort(key=lambda item: (-int(item["score"]), item["path"]))
    likely_importable_folders.sort(key=lambda item: (-int(item["score"]), item["path"]))
    project_like_folders.sort(key=lambda item: (-int(item["score"]), item["path"]))
    root_summaries.sort(key=lambda item: (-int(item["score"]), item["path"]))

    recommended_next_imports = [f"{item['path']} ({item['priority']})" for item in import_priorities[:8]]
    missing_expected = [key for key in EXPECTED_INSTITUTIONS if institution_hits.get(key, 0) == 0]
    summary = {
        "roots_with_signal": len([item for item in root_summaries if int(item["score"]) > 0]),
        "high_priority_imports": len([item for item in import_priorities if item["priority"] == "high"]),
        "missing_expected_institutions": missing_expected,
        "top_priority_paths": [item["path"] for item in import_priorities[:5]],
        "source_types_detected": sorted(key for key, count in source_type_hits.items() if count > 0),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roots_scanned": roots_scanned,
        "roots_missing": roots_missing,
        "scan_limits": {
            "max_depth": resolved_max_depth,
            "max_files": resolved_max_files,
            "sample_bytes": sample_bytes,
        },
        "root_summaries": root_summaries,
        "recommended_roots": recommended_roots[:12],
        "institution_hits": dict(sorted(institution_hits.items())),
        "institution_examples": _trim_examples(institution_examples),
        "era_hits": dict(sorted(era_hits.items())),
        "era_examples": _trim_examples(era_examples),
        "source_type_hits": dict(sorted(source_type_hits.items())),
        "source_type_examples": _trim_examples(source_type_examples),
        "likely_importable_folders": likely_importable_folders[:25],
        "project_like_folders": project_like_folders[:25],
        "import_priorities": import_priorities[:20],
        "source_pack_signals": sorted(source_pack_signals),
        "recommended_next_imports": recommended_next_imports,
        "summary": summary,
        "scanned_directories": scanned_directories,
        "scanned_files": scanned_files,
    }
