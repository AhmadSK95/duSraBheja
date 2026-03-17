"""Lightweight local inventory for long-span profile/history sources."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import os
import re
from typing import Any

from src.config import settings

IMPORTABLE_SUFFIXES = {".md", ".txt", ".pdf", ".docx", ".xlsx", ".csv", ".pptx", ".json"}
INSTITUTION_TERMS = {
    "iitkgp": ("iitkgp", "iit kharagpur", "kharagpur", "iit"),
    "nyu": ("nyu", "tandon", "new york university"),
    "amazon": ("amazon", "aws"),
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


def _split_roots(raw: str | None) -> list[Path]:
    values = [item.strip() for item in (raw or "").split(",") if item.strip()]
    return [Path(value).expanduser() for value in values]


def discover_inventory_roots(*, extra_roots: list[str | Path] | None = None) -> list[Path]:
    candidates = [
        Path(settings.public_profile_seed_path).expanduser(),
        *_split_roots(settings.collector_project_roots),
        *_split_roots(settings.collector_bootstrap_roots),
        *_split_roots(settings.collector_daily_roots),
        Path("~/Desktop").expanduser(),
        Path("~/Documents").expanduser(),
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


def _matched_keys(path: Path, mapping: dict[str, tuple[str, ...]]) -> list[str]:
    lowered = _path_text(path)
    return [key for key, terms in mapping.items() if any(term in lowered for term in terms)]


def build_profile_inventory_payload(*, extra_roots: list[str | Path] | None = None) -> dict[str, Any]:
    roots = discover_inventory_roots(extra_roots=extra_roots)
    roots_scanned: list[str] = []
    roots_missing: list[str] = []
    institution_hits: dict[str, int] = defaultdict(int)
    era_hits: dict[str, int] = defaultdict(int)
    likely_importable_folders: list[dict[str, Any]] = []
    project_like_folders: list[dict[str, Any]] = []
    source_pack_signals: list[str] = []
    max_depth = max(2, int(settings.collector_scan_max_depth or 4))
    max_files = max(20, int(settings.collector_inventory_recent_files_limit or 50))
    scanned_files = 0

    for root in roots:
        if not root.exists():
            roots_missing.append(str(root))
            continue
        roots_scanned.append(str(root))
        if root.name == "CompanyInterviewPrep":
            source_pack_signals.append(str(root))

        root_depth = len(root.parts)
        for current_root, dirnames, filenames in os.walk(root):
            current_path = Path(current_root)
            depth = len(current_path.parts) - root_depth
            if depth > max_depth:
                dirnames[:] = []
                continue

            matched_institutions = _matched_keys(current_path, INSTITUTION_TERMS)
            matched_eras = _matched_keys(current_path, ERA_TERMS)
            for key in matched_institutions:
                institution_hits[key] += 1
            for key in matched_eras:
                era_hits[key] += 1

            importable_files = [name for name in filenames if Path(name).suffix.lower() in IMPORTABLE_SUFFIXES]
            if importable_files and len(likely_importable_folders) < 20:
                likely_importable_folders.append(
                    {
                        "path": str(current_path),
                        "importable_files": len(importable_files),
                        "matched_institutions": matched_institutions,
                        "matched_eras": matched_eras,
                    }
                )

            if (
                any(token in current_path.name.lower() for token in ("project", "course", "thesis", "research", "resume", "portfolio"))
                and len(project_like_folders) < 20
            ):
                project_like_folders.append(
                    {
                        "path": str(current_path),
                        "matched_institutions": matched_institutions,
                        "matched_eras": matched_eras,
                    }
                )

            scanned_files += len(filenames)
            if scanned_files >= max_files:
                break
        if scanned_files >= max_files:
            break

    recommended_next_imports = []
    for item in likely_importable_folders[:8]:
        institution_label = ", ".join(item.get("matched_institutions") or []) or "general history"
        recommended_next_imports.append(f"{item['path']} ({institution_label})")

    return {
        "roots_scanned": roots_scanned,
        "roots_missing": roots_missing,
        "scan_limits": {
            "max_depth": max_depth,
            "max_files": max_files,
        },
        "institution_hits": dict(institution_hits),
        "era_hits": dict(era_hits),
        "likely_importable_folders": likely_importable_folders,
        "project_like_folders": project_like_folders,
        "source_pack_signals": source_pack_signals,
        "recommended_next_imports": recommended_next_imports,
    }
