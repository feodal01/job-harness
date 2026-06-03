#!/usr/bin/env python3
"""Build bundled company directory from external company lists."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from job_harness.company_directory import normalize_company_key  # noqa: E402
from job_harness.employer_resolver import classify_careers_url  # noqa: E402

SOURCE_HELLONEWJOB = "hellonewjob"
SOURCE_PUBLIC_CACHE = "company-careers-public"
IMPORT_DATE = "2026-06-02"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build company directory artifacts")
    parser.add_argument("--hellonewjob-csv", required=True, help="CSV exported from HelloNewJob Airtable")
    parser.add_argument(
        "--directory-json",
        default=str(ROOT / "data" / "company-directory.json"),
        help="Output rich company directory JSON",
    )
    parser.add_argument(
        "--public-cache-json",
        default=str(ROOT / "data" / "company-careers-public.json"),
        help="Bundled employer cache JSON to read and update",
    )
    parser.add_argument("--merged-csv", help="Optional CSV export of the merged company directory")
    args = parser.parse_args()

    public_cache_path = Path(args.public_cache_json)
    companies = _load_public_cache(public_cache_path)
    _merge_hellonewjob(companies, Path(args.hellonewjob_csv))

    directory = sorted(companies.values(), key=lambda item: item["name"].casefold())
    _write_json(Path(args.directory_json), directory)
    _write_public_cache(public_cache_path, directory)
    if args.merged_csv:
        _write_csv(Path(args.merged_csv), directory)

    print(f"companies={len(directory)}")
    print(f"directory_json={args.directory_json}")
    print(f"public_cache_json={public_cache_path}")
    if args.merged_csv:
        print(f"merged_csv={args.merged_csv}")


def _load_public_cache(path: Path) -> dict[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Public cache must be a JSON object")

    companies: dict[str, dict] = {}
    for entry in raw.values():
        if not isinstance(entry, dict):
            raise ValueError("Public cache entries must be objects")
        name = _required_str(entry, "company")
        careers_url = _optional_str(entry.get("careers_url"))
        profile = _empty_profile(name)
        profile.update(
            {
                "careers_url": careers_url,
                "ats_type": _optional_str(entry.get("ats_type")) or "unknown",
                "scraper_name": _optional_str(entry.get("scraper_name")),
                "last_checked": _optional_str(entry.get("last_checked")),
                "last_found_roles": bool(entry.get("last_found_roles")),
                "sources": [SOURCE_PUBLIC_CACHE],
            }
        )
        companies[normalize_company_key(name)] = profile
    return companies


def _merge_hellonewjob(companies: dict[str, dict], path: Path) -> None:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            name = _required_str(row, "Company")
            key = normalize_company_key(name)
            profile = companies.get(key, _empty_profile(name))
            careers_url = _optional_str(row.get("Job site"))
            profile.update(
                {
                    "linkedin_url": _optional_str(row.get("Linkedin")) or profile["linkedin_url"],
                    "linkedin_jobs_url": _optional_str(row.get("Vacancies on Linkedin"))
                    or profile["linkedin_jobs_url"],
                    "description": _optional_str(row.get("Description")) or profile["description"],
                    "industry": _optional_str(row.get("Industry")) or profile["industry"],
                    "headcount": _optional_str(row.get("Headcount")) or profile["headcount"],
                    "remote": _parse_remote(row.get("Remote")),
                    "job_types": _split_multiline(row.get("Types of jobs for hire")),
                    "stack": _split_multiline(row.get("Stack")),
                    "countries": _split_multiline(row.get("Country")),
                    "last_checked": IMPORT_DATE,
                }
            )
            if careers_url:
                profile["careers_url"] = careers_url
                profile["ats_type"] = classify_careers_url(careers_url)
                profile["last_found_roles"] = False
            if SOURCE_HELLONEWJOB not in profile["sources"]:
                profile["sources"].append(SOURCE_HELLONEWJOB)
            companies[key] = profile


def _empty_profile(name: str) -> dict:
    return {
        "name": name,
        "careers_url": None,
        "linkedin_url": None,
        "linkedin_jobs_url": None,
        "description": None,
        "industry": None,
        "headcount": None,
        "remote": False,
        "job_types": [],
        "stack": [],
        "countries": [],
        "ats_type": "unknown",
        "scraper_name": None,
        "last_checked": None,
        "last_found_roles": False,
        "sources": [],
    }


def _write_json(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_public_cache(path: Path, directory: list[dict]) -> None:
    cache = {}
    for company in directory:
        careers_url = company["careers_url"]
        if not careers_url:
            continue
        cache[normalize_company_key(company["name"])] = {
            "company": company["name"],
            "careers_url": careers_url,
            "ats_type": company["ats_type"],
            "scraper_name": company["scraper_name"],
            "last_checked": company["last_checked"] or IMPORT_DATE,
            "last_found_roles": company["last_found_roles"],
            "ignored": False,
        }
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, directory: list[dict]) -> None:
    fieldnames = [
        "name",
        "careers_url",
        "linkedin_url",
        "linkedin_jobs_url",
        "description",
        "industry",
        "headcount",
        "remote",
        "job_types",
        "stack",
        "countries",
        "ats_type",
        "scraper_name",
        "last_checked",
        "last_found_roles",
        "sources",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for company in directory:
            row = company.copy()
            for field in ("job_types", "stack", "countries", "sources"):
                row[field] = "\n".join(row[field])
            writer.writerow(row)


def _required_str(row: dict, field: str) -> str:
    value = _optional_str(row.get(field))
    if value is None:
        raise ValueError(f"Missing required field: {field}")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected string value, got {type(value).__name__}")
    stripped = value.strip()
    return stripped or None


def _split_multiline(value: object) -> list[str]:
    text = _optional_str(value)
    if text is None:
        return []
    return [part.strip() for part in text.splitlines() if part.strip()]


def _parse_remote(value: object) -> bool:
    text = _optional_str(value)
    if text is None:
        return False
    return text.casefold() in {"1", "true", "yes", "remote", "да"}


if __name__ == "__main__":
    main()
