"""Tests for the reference data layer.

Tier 1 (contract) covers module loadability and shape of the provider records.
Tier 2 (behavior) exercises canonicalization, unit conversion (including the
incompatible-unit failure path), and regional rules.
"""

from __future__ import annotations

import pytest

from src.services.reference import (
    PROVIDERS,
    Provider,
    ReferenceRegion,
    RegionalRules,
    canonicalize_provider,
    convert_unit,
    get_regional_rules,
    is_known_provider,
)


# ---------------------------------------------------------------------------
# Tier 1 — contract
# ---------------------------------------------------------------------------


def test_module_imports_cleanly():
    # If the module-level pydantic construction or index build had failed,
    # we would not have reached this test. Assertions below just pin the
    # post-import surface.
    assert callable(canonicalize_provider)
    assert callable(convert_unit)
    assert callable(get_regional_rules)
    assert callable(is_known_provider)


def test_all_providers_load_with_required_fields():
    assert len(PROVIDERS) >= 10
    for provider in PROVIDERS:
        assert isinstance(provider, Provider)
        assert provider.name
        assert isinstance(provider.region, ReferenceRegion)
        assert len(provider.typical_units) >= 1
        # aliases and quirks may be empty in principle, but every entry in
        # the curated library has at least one quirk to satisfy the spec.
        assert len(provider.quirks) >= 1


def test_provider_library_covers_all_three_regions():
    regions = {p.region for p in PROVIDERS}
    assert regions == {
        ReferenceRegion.US_EAST,
        ReferenceRegion.US_WEST,
        ReferenceRegion.EU,
    }


# ---------------------------------------------------------------------------
# Tier 2 — behavior
# ---------------------------------------------------------------------------


def test_canonicalize_provider_matches_alias_and_case_insensitive():
    by_canonical = canonicalize_provider("Consolidated Edison")
    by_alias = canonicalize_provider("ConEd")
    by_alias_spaced = canonicalize_provider("Con Edison")
    by_alias_lowercase = canonicalize_provider("con edison")

    assert by_canonical is not None
    assert by_alias is not None
    assert by_alias.name == by_canonical.name == "Consolidated Edison"
    assert by_alias_spaced is by_alias
    assert by_alias_lowercase is by_alias


def test_canonicalize_provider_returns_none_for_unknown():
    assert canonicalize_provider("Totally Fake Power Co") is None
    assert canonicalize_provider("") is None


def test_is_known_provider_matches_canonicalize_provider():
    assert is_known_provider("ConEd") is True
    assert is_known_provider("Totally Fake Power Co") is False


def test_convert_unit_therms_to_kwh():
    # 1 therm = 29.3001 kWh, per the standard natural-gas heat-content factor.
    assert convert_unit(1.0, "therms", "kWh") == pytest.approx(29.3001, abs=0.01)
    assert convert_unit(10.0, "therms", "kWh") == pytest.approx(293.001, abs=0.1)


def test_convert_unit_identity_returns_value_unchanged():
    assert convert_unit(42.0, "kWh", "kWh") == 42.0


def test_convert_unit_inverse_lookup():
    # kWh -> therms is not stored directly; it's the reverse of therms -> kWh.
    # 100 kWh = 100 / 29.3001 therms.
    assert convert_unit(100.0, "kWh", "therms") == pytest.approx(
        100.0 / 29.3001, abs=0.001
    )


def test_convert_unit_ccf_to_gallons_water():
    # Pure-volume: 1 ccf = 748.052 US gallons.
    assert convert_unit(1.0, "ccf", "gallons") == pytest.approx(748.052, abs=0.01)


def test_convert_unit_raises_on_incompatible_units():
    with pytest.raises(ValueError):
        convert_unit(1.0, "kWh", "gallons")


def test_get_regional_rules_returns_expected_currency_per_region():
    assert get_regional_rules("US-East").currency_default == "USD"
    assert get_regional_rules("US-West").currency_default == "USD"
    assert get_regional_rules("EU").currency_default == "GBP"


def test_get_regional_rules_returns_expected_separators_and_date_formats():
    us_east = get_regional_rules("US-East")
    eu = get_regional_rules("EU")
    assert us_east.decimal_separator == "."
    assert us_east.date_format == "MM/DD/YYYY"
    assert eu.decimal_separator == ","
    assert eu.date_format == "DD/MM/YYYY"


def test_get_regional_rules_accepts_enum_or_string():
    by_string = get_regional_rules("EU")
    by_enum = get_regional_rules(ReferenceRegion.EU)
    assert isinstance(by_string, RegionalRules)
    assert by_string == by_enum


def test_get_regional_rules_raises_on_unknown_region():
    with pytest.raises(ValueError):
        get_regional_rules("Antarctica")
