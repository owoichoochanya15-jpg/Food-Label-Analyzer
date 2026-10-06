"""Tests for nutrition_analyzer.py. Run with:  python -m pytest -v"""

import pytest

from nutrition_analyzer import NutritionAnalyzer, NutritionDataError

# Real values for Nutella (barcode 3017624010701) from Open Food Facts.
NUTELLA = {
    "energy-kcal_100g": 539,
    "sugars_100g": 56.3,
    "fat_100g": 30.9,
    "saturated-fat_100g": 10.6,
    "salt_100g": 0.1075,
}


@pytest.fixture
def analyzer():
    return NutritionAnalyzer()


@pytest.mark.parametrize("value, expected", [
    (12.5, 12.5),
    (7, 7.0),
    ("12,5 g", 12.5),
    ("< 0.5 g", 0.5),
    ("539kcal", 539.0),
    ("n/a", None),
    ("", None),
    (None, None),
])
def test_extract_number(value, expected):
    assert NutritionAnalyzer.extract_number(value) == expected


def test_nutella_is_high_in_sugar_fat_and_calories(analyzer):
    report = analyzer.analyze(NUTELLA)
    assert report["sugar"] == {"amount": 56.3, "unit": "g", "level": "high"}
    assert report["salt"]["level"] == "low"
    assert analyzer.high_nutrients(report) == ["calories", "sugar", "fat", "saturated_fat"]


def test_boundaries(analyzer):
    assert analyzer.classify("sugar", 5.0) == "low"      # exactly the low limit
    assert analyzer.classify("sugar", 22.5) == "medium"  # exactly the high limit
    assert analyzer.classify("sugar", 22.6) == "high"
    assert analyzer.classify("sugar", None) == "unknown"


def test_drinks_use_stricter_limits():
    # 12 g sugar per 100 g/ml is medium for food but high for a drink
    assert NutritionAnalyzer().classify("sugar", 12) == "medium"
    assert NutritionAnalyzer(is_drink=True).classify("sugar", 12) == "high"


def test_missing_values_become_unknown(analyzer):
    report = analyzer.analyze({"sugars_100g": 3})
    assert report["sugar"]["level"] == "low"
    assert report["fat"]["level"] == "unknown"
    assert "No information on fat." in analyzer.summary(report)


def test_backup_calculations(analyzer):
    report = analyzer.analyze({"energy-kj_100g": 2228, "sodium_100g": 0.4})
    assert report["calories"]["amount"] == 532.5   # 2228 kJ / 4.184
    assert report["salt"]["amount"] == 1.0          # 0.4 g sodium x 2.5


@pytest.mark.parametrize("bad_data", [None, {}, "text", {"proteins_100g": 6}])
def test_no_usable_data_raises_error(analyzer, bad_data):
    with pytest.raises(NutritionDataError):
        analyzer.analyze(bad_data)


def test_accepts_object_with_nutriments_attribute(analyzer):
    class FakeProduct:
        nutriments = NUTELLA

    assert analyzer.analyze(FakeProduct())["sugar"]["level"] == "high"


def test_clean_ingredients(analyzer):
    messy = "Ingredients: Sugar, _milk_ powder*,  cocoa (12%) ."
    assert analyzer.clean_ingredients(messy) == "Sugar, milk powder, cocoa (12%)."
    assert analyzer.clean_ingredients(None) == ""


def test_split_ingredients_keeps_brackets_together(analyzer):
    text = "sugar, cocoa butter; emulsifier (soy lecithin, E476), vanillin."
    assert analyzer.split_ingredients(text) == [
        "sugar", "cocoa butter", "emulsifier (soy lecithin, E476)", "vanillin",
    ]


def test_split_ingredients_with_nested_brackets(analyzer):
    # Real text from Open Food Facts product 0737628064502
    text = "Rice Noodles (rice, water), seasoning packet (peanut, spices [chili, clove], salt)."
    assert analyzer.split_ingredients(text) == [
        "Rice Noodles (rice, water)",
        "seasoning packet (peanut, spices [chili, clove], salt)",
    ]


def test_summary_rounds_numbers(analyzer):
    assert "Low in salt (0.11 g per 100 g)." in analyzer.summary(analyzer.analyze(NUTELLA))
