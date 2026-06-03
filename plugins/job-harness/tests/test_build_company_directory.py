from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def _load_builder_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build-company-directory.py"
    spec = importlib.util.spec_from_file_location("build_company_directory", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load build-company-directory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildCompanyDirectoryTest(unittest.TestCase):
    def test_import_date_is_supplied_by_caller_not_hardcoded(self) -> None:
        builder = _load_builder_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "companies.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=[
                        "Company",
                        "Job site",
                        "Linkedin",
                        "Vacancies on Linkedin",
                        "Description",
                        "Industry",
                        "Headcount",
                        "Remote",
                        "Types of jobs for hire",
                        "Stack",
                        "Country",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "Company": "Alpha",
                        "Job site": "https://alpha.test/careers",
                        "Types of jobs for hire": "QA",
                        "Stack": "Python",
                        "Country": "Armenia",
                    }
                )

            companies = {}
            builder._merge_hellonewjob(companies, csv_path, import_date="2026-06-03")
            public_cache_path = Path(tmpdir) / "cache.json"
            builder._write_public_cache(public_cache_path, list(companies.values()), import_date="2026-06-03")

            cache = json.loads(public_cache_path.read_text(encoding="utf-8"))

        profile = companies["alpha"]
        self.assertEqual("2026-06-03", profile["last_checked"])
        self.assertEqual("2026-06-03", cache["alpha"]["last_checked"])

    def test_import_date_must_be_iso_date(self) -> None:
        builder = _load_builder_module()

        self.assertEqual("2026-06-03", builder._iso_date("2026-06-03"))
        with self.assertRaises(ValueError):
            builder._iso_date("yesterday")


if __name__ == "__main__":
    unittest.main()
