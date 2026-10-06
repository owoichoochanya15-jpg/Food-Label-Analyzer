"""
nutrition_analyzer.py
=====================
Person 2's part of the Food Label Analyzer project.

Job of this file: take the nutrition numbers of a food product and decide
whether it is LOW, MEDIUM or HIGH in calories, sugar, fat, saturated fat
and salt. It also cleans up messy ingredient text using regular expressions.

How the rest of the team uses it
--------------------------------
    from nutrition_analyzer import NutritionAnalyzer, NutritionDataError

    analyzer = NutritionAnalyzer()
    try:
        report = analyzer.analyze(product)        # product = FoodProduct or a dict
    except NutritionDataError as error:
        print("Could not analyze:", error)

    analyzer.summary(report)          # list of simple English sentences
    analyzer.high_nutrients(report)   # e.g. ["sugar", "fat"]
    analyzer.clean_ingredients(text)  # tidy ingredient text
    analyzer.split_ingredients(text)  # ingredient text -> list of ingredients

`product` can be:
  * the "nutriments" dictionary returned by the Open Food Facts API, or
  * any object (like a FoodProduct class) that has a `.nutriments` attribute.

Every result is a normal Python dictionary, so it can be saved straight into
a JSON/CSV food log or pasted into the Gemini AI prompt.

Where the HIGH / LOW limits come from
-------------------------------------
Sugar, fat, saturated fat and salt use the UK Food Standards Agency
"traffic light" front-of-pack labelling limits (per 100 g, or per 100 ml
for drinks). Calories have no official traffic light, so we use:
  * LOW  = 40 kcal or less per 100 g (EU "low energy" claim rule; 20 for drinks)
  * HIGH = more than 400 kcal per 100 g (our team rule: 20% of a 2000 kcal day
    in just 100 g; 70 kcal per 100 ml for drinks)
"""

import re


class NutritionDataError(Exception):
    """Raised when a product has no usable nutrition information at all.

    We make our OWN exception type so the person building the app can catch
    exactly this problem (``except NutritionDataError``) and show a friendly
    message, instead of the program crashing.
    """


class NutritionAnalyzer:
    """Analyzes nutrition values and ingredient text of a food product."""

    # ------------------------------------------------------------------
    # CLASS ATTRIBUTES: settings shared by every NutritionAnalyzer object.
    # ------------------------------------------------------------------

    # For each nutrient: which Open Food Facts keys to look in (in order),
    # its unit, and the (low, high) limits for foods and for drinks.
    # amount <= low  -> "low"
    # amount >  high -> "high"
    # anything between -> "medium"
    NUTRIENTS = {
        "calories": {
            "keys": ["energy-kcal_100g", "energy-kcal"],
            "unit": "kcal",
            "food_limits": (40, 400),
            "drink_limits": (20, 70),
        },
        "sugar": {
            "keys": ["sugars_100g", "sugars"],
            "unit": "g",
            "food_limits": (5.0, 22.5),
            "drink_limits": (2.5, 11.25),
        },
        "fat": {
            "keys": ["fat_100g", "fat"],
            "unit": "g",
            "food_limits": (3.0, 17.5),
            "drink_limits": (1.5, 8.75),
        },
        "saturated_fat": {
            "keys": ["saturated-fat_100g", "saturated-fat"],
            "unit": "g",
            "food_limits": (1.5, 5.0),
            "drink_limits": (0.75, 2.5),
        },
        "salt": {
            "keys": ["salt_100g", "salt"],
            "unit": "g",
            "food_limits": (0.3, 1.5),
            "drink_limits": (0.3, 0.75),
        },
    }

    KJ_PER_KCAL = 4.184       # 1 kcal = 4.184 kJ
    SALT_PER_SODIUM = 2.5     # salt = sodium x 2.5

    # Regular expressions are "compiled" once here so they are not rebuilt
    # every time a method runs.
    # A number like 12, 12.5, 12,5 (European comma), -3 or .5
    NUMBER_PATTERN = re.compile(r"-?\d*[.,]?\d+")

    def __init__(self, is_drink=False):
        """Create an analyzer.

        is_drink: set True for drinks so the stricter per-100 ml limits are used.
        """
        self.is_drink = is_drink

    # ------------------------------------------------------------------
    # 1. Extracting numbers (regular expressions)
    # ------------------------------------------------------------------
    @staticmethod
    def extract_number(value):
        """Turn a nutrition value into a float, or None if there is no number.

        Examples:
            12.5        -> 12.5
            "12,5 g"    -> 12.5   (European decimal comma)
            "< 0.5 g"   -> 0.5
            "539kcal"   -> 539.0
            "n/a", None -> None
        """
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)

        match = NutritionAnalyzer.NUMBER_PATTERN.search(str(value))
        if match is None:
            return None
        return float(match.group().replace(",", "."))

    # ------------------------------------------------------------------
    # 2. Reading one nutrient from the product data
    # ------------------------------------------------------------------
    def get_amount(self, nutriments, nutrient):
        """Return the amount of one nutrient per 100 g/ml, or None if missing.

        It tries each key listed in NUTRIENTS. If calories or salt are missing
        it works them out from kilojoules or sodium instead.
        """
        for key in self.NUTRIENTS[nutrient]["keys"]:
            amount = self.extract_number(nutriments.get(key))
            if amount is not None:
                return amount

        # Backup plan 1: calories missing, but energy in kJ is present.
        if nutrient == "calories":
            for key in ("energy-kj_100g", "energy_100g"):
                kilojoules = self.extract_number(nutriments.get(key))
                if kilojoules is not None:
                    return round(kilojoules / self.KJ_PER_KCAL, 1)

        # Backup plan 2: salt missing, but sodium is present.
        if nutrient == "salt":
            sodium = self.extract_number(nutriments.get("sodium_100g"))
            if sodium is not None:
                return round(sodium * self.SALT_PER_SODIUM, 2)

        return None

    # ------------------------------------------------------------------
    # 3. Deciding high / medium / low
    # ------------------------------------------------------------------
    def classify(self, nutrient, amount):
        """Return "low", "medium", "high", or "unknown" when amount is None."""
        if amount is None:
            return "unknown"

        limits_name = "drink_limits" if self.is_drink else "food_limits"
        low_limit, high_limit = self.NUTRIENTS[nutrient][limits_name]

        if amount <= low_limit:
            return "low"
        if amount > high_limit:
            return "high"
        return "medium"

    # ------------------------------------------------------------------
    # 4. The main method: analyze a whole product
    # ------------------------------------------------------------------
    def analyze(self, product):
        """Analyze every nutrient and return a report dictionary.

        Example result:
            {"sugar": {"amount": 56.3, "unit": "g", "level": "high"},
             "salt":  {"amount": None, "unit": "g", "level": "unknown"}, ...}

        Missing values do NOT crash the program; they become "unknown".
        Raises NutritionDataError if there is no nutrition data at all.
        """
        # Accept a FoodProduct-style object OR a plain dictionary.
        nutriments = getattr(product, "nutriments", product)

        if not isinstance(nutriments, dict) or not nutriments:
            raise NutritionDataError("This product has no nutrition information.")

        report = {}
        for nutrient, info in self.NUTRIENTS.items():
            amount = self.get_amount(nutriments, nutrient)
            report[nutrient] = {
                "amount": amount,
                "unit": info["unit"],
                "level": self.classify(nutrient, amount),
            }

        if all(result["amount"] is None for result in report.values()):
            raise NutritionDataError(
                "Nutrition data is incomplete: no calories, sugar, fat or salt values found."
            )

        return report

    # ------------------------------------------------------------------
    # 5. Helpers that turn the report into something people can read
    # ------------------------------------------------------------------
    def summary(self, report):
        """Turn a report into simple sentences, e.g. "High in sugar (56.3 g per 100 g)"."""
        per = "100 ml" if self.is_drink else "100 g"
        sentences = []
        for nutrient, result in report.items():
            name = nutrient.replace("_", " ")
            if result["level"] == "unknown":
                sentences.append(f"No information on {name}.")
            else:
                amount = round(result["amount"], 2)   # 0.1075 -> 0.11
                sentences.append(
                    f"{result['level'].capitalize()} in {name} "
                    f"({amount:g} {result['unit']} per {per})."
                )
        return sentences

    @staticmethod
    def high_nutrients(report):
        """Return the names of nutrients that are high, e.g. ["sugar", "fat"]."""
        return [name for name, result in report.items() if result["level"] == "high"]

    # ------------------------------------------------------------------
    # 6. Cleaning ingredient text (regular expressions)
    # ------------------------------------------------------------------
    @staticmethod
    def clean_ingredients(text):
        """Tidy messy ingredient text from the API.

        "Ingredients: Sugar, _milk_ powder*,  cocoa (12%) ."
            -> "Sugar, milk powder, cocoa (12%)."
        """
        if not text:
            return ""
        text = re.sub(r"^\s*ingr[eé]dients?\s*:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"[_*]", "", text)        # OFF marks allergens like _milk_
        text = re.sub(r"\s+", " ", text)        # many spaces/newlines -> one space
        text = re.sub(r"\s+([,.;)])", r"\1", text)  # no space before , . ; )
        return text.strip()

    def split_ingredients(self, text):
        """Split ingredient text into a list, keeping brackets together.

        "sugar, cocoa butter, emulsifier (soy lecithin, E476)"
            -> ["sugar", "cocoa butter", "emulsifier (soy lecithin, E476)"]

        A regular expression cannot count brackets inside brackets, e.g.
        "seasoning (spices [chili, clove], salt)", so we walk through the text
        and keep a `depth` counter: only split on , or ; when depth is 0.
        """
        cleaned = self.clean_ingredients(text).rstrip(".")
        ingredients = []
        current = ""
        depth = 0
        for char in cleaned:
            if char in "([":
                depth += 1
            elif char in ")]":
                depth = max(depth - 1, 0)

            if char in ",;" and depth == 0:
                ingredients.append(current.strip())
                current = ""
            else:
                current += char
        ingredients.append(current.strip())
        return [item for item in ingredients if item]


# Run "python nutrition_analyzer.py" to see a quick demo with Nutella's real
# Open Food Facts values. This block does not run when another file imports us.
if __name__ == "__main__":
    nutella = {
        "energy-kcal_100g": 539,
        "sugars_100g": 56.3,
        "fat_100g": 30.9,
        "saturated-fat_100g": 10.6,
        "salt_100g": "0,1075 g",
    }
    analyzer = NutritionAnalyzer()
    nutella_report = analyzer.analyze(nutella)
    for line in analyzer.summary(nutella_report):
        print("-", line)
    print("High in:", analyzer.high_nutrients(nutella_report))
    print(analyzer.split_ingredients(
        "Ingredients: Sugar, palm oil, _hazelnuts_ (13%), emulsifier (lecithins, soy)."
    ))
