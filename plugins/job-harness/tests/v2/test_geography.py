from __future__ import annotations

import unittest

from job_harness.v2.geography import (
    geography_matches_any,
    listing_country_codes,
    normalize_request_geography,
    normalize_source_geographies,
    normalize_source_geography,
)


class GeographyTest(unittest.TestCase):
    def test_street_number_is_not_interpreted_as_a_country(self) -> None:
        self.assertEqual(
            ("RU",),
            normalize_source_geographies("Казань, улица Некрасова, 28"),
        )

    def test_normalizes_country_names_and_aliases(self) -> None:
        cases = (
            ("Кипр", "CY"),
            ("Турция", "TR"),
            ("UK", "GB"),
            ("United Kingdom", "GB"),
            ("The Netherlands", "NL"),
            ("EU", "EU"),
            ("Europe", "EU"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                # Arrange / Act
                normalized = normalize_request_geography(raw)

                # Assert
                self.assertEqual(expected, normalized)

    def test_ignores_non_country_source_markers(self) -> None:
        cases = ("global", "iberia", "remote", "worldwide")
        for raw in cases:
            with self.subTest(raw=raw):
                # Arrange / Act
                normalized = normalize_source_geography(raw)

                # Assert
                self.assertIsNone(normalized)

    def test_normalizes_country_from_delimited_source_location(self) -> None:
        # Arrange / Act
        normalized = normalize_source_geography(
            "Boston, Massachusetts; Foster City, California; Marlton, New Jersey; Remote, United States"
        )

        # Assert
        self.assertEqual("US", normalized)

    def test_normalizes_multiple_countries_from_delimited_source_location(self) -> None:
        # Arrange / Act
        normalized = normalize_source_geographies("Россия, Беларусь")

        # Assert
        self.assertEqual(("RU", "BY"), normalized)

    def test_does_not_treat_iberia_region_marker_as_us_city(self) -> None:
        # Arrange / Act
        normalized = normalize_source_geographies("Iberia, Spain")

        # Assert
        self.assertEqual(("ES",), normalized)

    def test_explicit_country_suppresses_city_name_country_fallback(self) -> None:
        cases = (
            ("Alexandria, Australia (Hybrid)", ("AU",)),
            ("Cambridge, United States", ("US",)),
            ("Medellín, Antioquia, CO", ("CO",)),
            ("Medellín, Antioquia, CO / Remote (Medellín, Antioquia, CO)", ("CO",)),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                # Arrange / Act
                normalized = normalize_source_geographies(raw)

                # Assert
                self.assertEqual(expected, normalized)

    def test_normalizes_country_suffix_in_branded_location_labels(self) -> None:
        cases = (
            ("Mapbox Japan", ("JP",)),
            ("Mapbox US", ("US",)),
            ("Remote role United States", ("US",)),
            ("Remote role Europe", ("EU",)),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                # Arrange / Act
                normalized = normalize_source_geographies(raw)

                # Assert
                self.assertEqual(expected, normalized)

    def test_listing_country_prefers_explicit_country_over_city_fallback(self) -> None:
        # Arrange
        listing: dict[str, object] = {
            "location_text": "Alexandria, Australia (Hybrid)",
            "country": "Australia",
            "city": "Alexandria",
        }

        # Act
        countries = listing_country_codes(listing)

        # Assert
        self.assertEqual(("AU",), countries)

    def test_normalizes_source_locations_with_workplace_descriptors(self) -> None:
        # Arrange / Act
        normalized = normalize_source_geographies("EU, Remote US, London Office, Multiple locations")

        # Assert
        self.assertEqual(("EU", "US", "GB"), normalized)

    def test_does_not_treat_us_state_abbreviations_as_countries_in_us_office_names(self) -> None:
        cases = (
            "US - Cambridge, MA",
            "US - Palo Alto CA",
            "San Mateo, CA, US",
        )
        for raw in cases:
            with self.subTest(raw=raw):
                # Arrange / Act
                normalized = normalize_source_geographies(raw)

                # Assert
                self.assertEqual(("US",), normalized)

    def test_infers_us_from_city_state_name_source_location(self) -> None:
        # Arrange / Act
        normalized = normalize_source_geographies("San Mateo, California / Remote")

        # Assert
        self.assertEqual(("US",), normalized)

    def test_normalizes_country_from_known_source_city(self) -> None:
        cases = (
            ("Москва", ("RU",)),
            ("Санкт-Петербург", ("RU",)),
            ("Минск", ("BY",)),
            ("Ереван", ("AM",)),
            ("London", ("GB",)),
            ("Barcelona", ("ES",)),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                # Arrange / Act
                normalized = normalize_source_geographies(raw)

                # Assert
                self.assertEqual(expected, normalized)

    def test_does_not_normalize_ambiguous_city_without_dominant_match(self) -> None:
        cases = ("Cambridge", "Valencia")
        for raw in cases:
            with self.subTest(raw=raw):
                # Arrange / Act
                normalized = normalize_source_geographies(raw)

                # Assert
                self.assertEqual((), normalized)

    def test_does_not_infer_geography_from_remote_timezone_text(self) -> None:
        # Arrange / Act
        normalized = normalize_source_geographies("remote from GMT-7 to GMT+4 timezones")

        # Assert
        self.assertEqual((), normalized)

    def test_normalizes_multiple_source_cities(self) -> None:
        # Arrange / Act
        normalized = normalize_source_geographies("Warsaw, Bucharest, Lisbon")

        # Assert
        self.assertEqual(("PL", "RO", "PT"), normalized)

    def test_matches_regions_through_member_countries(self) -> None:
        # Arrange / Act / Assert
        self.assertTrue(geography_matches_any("CY", ("europe",)))
        self.assertTrue(geography_matches_any("CY", ("EU",)))
        self.assertFalse(geography_matches_any("RU", ("europe",)))
        self.assertFalse(geography_matches_any("GB", ("europe",)))
        self.assertFalse(geography_matches_any("europe", ("GB",)))
        self.assertFalse(geography_matches_any("TR", ("EU",)))


if __name__ == "__main__":
    unittest.main()
