"""Capability-matrix completeness test.

Every registered scraper must declare a `capabilities` ClassVar with an
explicit FilterSupport value for every flag in CAPABILITY_FLAGS. The
sentinel default in base.py raises this test loudly if a new scraper is
added without an honest declaration.

This is the test the user explicitly asked for: "проверь реально ли
под капотом у этого инструмента это работает как ожидается".
"""

from __future__ import annotations

import unittest

import job_harness.scrapers  # noqa: F401  — register all scrapers
import job_harness.scrapers.career  # noqa: F401
from job_harness import registry
from job_harness.base import BaseScraper
from job_harness.types import (
    CAPABILITY_FLAGS,
    FilterSupport,
    Transport,
)


class CapabilityMatrixTest(unittest.TestCase):
    def test_every_registered_scraper_declares_capabilities(self):
        """Each scraper class explicitly sets its own capabilities ClassVar.

        Using `declares_full_capabilities` rather than `is BaseScraper.capabilities`
        catches both "forgot to override" and "overrode with the wrong shape".
        """
        for name, cls in registry.iter_registered():
            self.assertTrue(
                cls.declares_full_capabilities(),
                f"scraper {name!r} ({cls.__name__}) is missing a capability declaration; "
                "add a `capabilities` ClassVar with one FilterSupport value per "
                f"key in {CAPABILITY_FLAGS}",
            )

    def test_capabilities_use_valid_filter_support_values(self):
        for name, cls in registry.iter_registered():
            for flag, value in cls.capabilities.items():
                self.assertIn(
                    flag,
                    CAPABILITY_FLAGS,
                    f"{name}: unknown capability flag {flag!r}",
                )
                self.assertIsInstance(
                    value,
                    FilterSupport,
                    f"{name}.capabilities[{flag!r}]={value!r} must be a FilterSupport",
                )

    def test_transport_classification_matches_requires_browser(self):
        for name, cls in registry.iter_registered():
            expected = Transport.BROWSER if cls.requires_browser else Transport.HTTP
            self.assertEqual(
                cls.transport(),
                expected,
                f"{name}: transport() inconsistent with requires_browser",
            )

    def test_metadata_exposes_capabilities(self):
        meta = registry.get_scraper_metadata()
        for name in meta:
            caps = meta[name]["capabilities"]
            self.assertEqual(
                set(caps),
                set(CAPABILITY_FLAGS),
                f"{name}: metadata capabilities keys mismatch",
            )
            for value in caps.values():
                self.assertIn(value, {fs.value for fs in FilterSupport})

    def test_base_default_is_marker_not_a_real_declaration(self):
        """The default sentinel must read as 'unsupported' for every flag."""
        self.assertFalse(BaseScraper.declares_full_capabilities())
        defaults = dict(BaseScraper.capabilities)
        for flag in CAPABILITY_FLAGS:
            self.assertEqual(
                defaults.get(flag),
                FilterSupport.UNSUPPORTED,
                f"BaseScraper default for {flag} must be UNSUPPORTED",
            )


class CapabilityCoverageTest(unittest.TestCase):
    """Spot-check honesty: a scraper that claims SERVER for `remote_only`
    must actually expose `remote_only` in its build/_build URL builder.

    This is a regression net against capability drift: if a scraper's URL
    builder loses the remote_only branch but the capability declaration
    stays SERVER, this test fails. The mechanism: we render the URL with
    remote_only=True and remote_only=False and check the two outputs
    differ. Implemented for the HH family; other server-capable scrapers
    are covered by the matrix flag-enforcement test in Phase 4.
    """

    def test_hh_remote_only_actually_changes_url(self):
        from job_harness.models import SearchParams
        from job_harness.scrapers.hh_ru import HHRuScraper

        scraper = HHRuScraper(context=None, max_results=1)
        with_remote = scraper._build_search_url(
            SearchParams(query="QA", remote_only=True, country="RU")
        )
        without_remote = scraper._build_search_url(
            SearchParams(query="QA", remote_only=False, country="RU")
        )
        self.assertNotEqual(
            with_remote,
            without_remote,
            "hh_ru declares remote_only=SERVER but URL doesn't change with remote_only",
        )
        self.assertIn("schedule=remote", with_remote)
        self.assertNotIn("schedule=remote", without_remote)


if __name__ == "__main__":
    unittest.main()
