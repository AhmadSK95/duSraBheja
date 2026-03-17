from __future__ import annotations

import json
import sys
from pathlib import Path

import scripts.profile_inventory_report as report_script

from src.services import profile_inventory


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_bytes(path: Path, payload: bytes = b"stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_build_profile_inventory_payload_ranks_google_and_historical_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    seed_pack = tmp_path / "Desktop" / "CompanyInterviewPrep"
    google_takeout = tmp_path / "Downloads" / "Google Takeout" / "Drive" / "NYU Tandon Fall 2021"
    iit_root = tmp_path / "Documents" / "IITKGP" / "BTech" / "Algorithms"
    amazon_root = tmp_path / "Documents" / "Amazon" / "Ads Systems"

    _write_text(seed_pack / "resume.md", "Public-safe source pack for Ahmad.")
    _write_text(
        google_takeout / "notes.md",
        "New York University Tandon distributed systems notes from 2021.",
    )
    _write_bytes(iit_root / "project_report.pdf")
    _write_text(
        amazon_root / "design.md",
        "Amazon marketplace advertising services notes from 2023.",
    )

    monkeypatch.setattr(profile_inventory.settings, "public_profile_seed_path", str(seed_pack))
    monkeypatch.setattr(profile_inventory.settings, "collector_project_roots", "")
    monkeypatch.setattr(profile_inventory.settings, "collector_bootstrap_roots", "")
    monkeypatch.setattr(profile_inventory.settings, "collector_daily_roots", "")

    payload = profile_inventory.build_profile_inventory_payload(max_depth=4, max_files=200)

    assert payload["institution_hits"]["iitkgp"] >= 1
    assert payload["institution_hits"]["nyu"] >= 1
    assert payload["institution_hits"]["amazon"] >= 1
    assert payload["source_type_hits"]["google_takeout"] >= 1
    assert payload["source_type_hits"]["google_drive"] >= 1
    assert payload["summary"]["missing_expected_institutions"] == []
    assert any(item["path"].endswith("Google Takeout") for item in payload["recommended_roots"])
    assert any("IITKGP" in item["path"] for item in payload["import_priorities"])
    assert any("Amazon" in item["path"] for item in payload["import_priorities"])


def test_build_profile_inventory_payload_uses_text_samples_for_hidden_institution_hits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    seed_pack = tmp_path / "Desktop" / "CompanyInterviewPrep"
    hidden_root = tmp_path / "Documents" / "archive-box" / "semester-material"

    _write_text(seed_pack / "resume.md", "Seed pack")
    _write_text(
        hidden_root / "weekly_notes.txt",
        "New York University Tandon capstone notes from 2022 on distributed systems.",
    )

    monkeypatch.setattr(profile_inventory.settings, "public_profile_seed_path", str(seed_pack))
    monkeypatch.setattr(profile_inventory.settings, "collector_project_roots", "")
    monkeypatch.setattr(profile_inventory.settings, "collector_bootstrap_roots", "")
    monkeypatch.setattr(profile_inventory.settings, "collector_daily_roots", "")

    payload = profile_inventory.build_profile_inventory_payload(extra_roots=[hidden_root.parent], max_depth=3, max_files=100)

    assert payload["institution_hits"]["nyu"] >= 1
    assert payload["era_hits"]["nyu"] >= 1
    assert any(item["path"].endswith("semester-material") for item in payload["import_priorities"])
    assert any(item["path"].endswith("semester-material") for item in payload["recommended_roots"])


def test_profile_inventory_report_main_outputs_json_and_passes_args(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_build_profile_inventory_payload(**kwargs):
        captured.update(kwargs)
        return {
            "recommended_roots": [],
            "import_priorities": [],
            "summary": {"missing_expected_institutions": []},
        }

    monkeypatch.setattr(report_script, "build_profile_inventory_payload", fake_build_profile_inventory_payload)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "profile_inventory_report.py",
            "--root",
            "/tmp/google",
            "--max-depth",
            "3",
            "--max-files",
            "25",
            "--sample-bytes",
            "128",
        ],
    )

    report_script.main()

    payload = json.loads(capsys.readouterr().out)
    assert captured == {
        "extra_roots": ["/tmp/google"],
        "max_depth": 3,
        "max_files": 25,
        "sample_bytes": 128,
    }
    assert payload["recommended_roots"] == []
    assert payload["import_priorities"] == []
