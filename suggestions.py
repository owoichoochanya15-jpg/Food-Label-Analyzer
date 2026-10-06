"""
suggestions.py

Gives the user recommendations about a scanned food product:
  1. Possible allergens
  2. Healthier alternatives
  3. Recipe suggestions using similar ingredients
  4. Optional AI (Gemini) recipe ideas, with a safe offline fallback

Main entry point:  MealSuggestionGenerator.generate(product, ...)

The module works with Person 1's FoodProduct object OR a plain dict, as long as
it exposes (any of these names):
    name / product_name
    barcode / code
    ingredients / ingredients_text      (string or list of strings)
    nutrition / nutriments              (dict, values per 100 g)
    allergens_tags / allergens          (optional, OpenFoodFacts tags like "en:milk")
    categories                          (optional string)

Optional Gemini setup:
    pip install google-genai
    set the environment variable GEMINI_API_KEY
"""

import os
import re
from typing import Any, Dict, Iterable, List, Optional, Set

# --------------------------------------------------------------------------
# Data tables
# --------------------------------------------------------------------------

ALLERGEN_KEYWORDS: Dict[str, List[str]] = {
    "milk": ["milk", "butter", "cheese", "cream", "whey", "casein", "lactose",
             "yogurt", "yoghurt", "ghee", "milk powder"],
    "eggs": ["egg", "albumin", "mayonnaise", "meringue"],
    "peanuts": ["peanut", "groundnut", "arachis"],
    "tree nuts": ["almond", "hazelnut", "walnut", "cashew", "pecan",
                  "pistachio", "macadamia", "brazil nut"],
    "gluten": ["wheat", "barley", "rye", "oat", "spelt", "semolina",
               "gluten", "malt", "couscous"],
    "soy": ["soy", "soya", "soybean", "tofu", "edamame", "miso"],
    "fish": ["fish", "salmon", "tuna", "cod", "anchovy", "anchovies", "sardine"],
    "shellfish": ["shrimp", "prawn", "crab", "lobster", "crayfish",
                  "scallop", "mussel", "oyster", "clam"],
    "sesame": ["sesame", "tahini"],
    "mustard": ["mustard"],
    "celery": ["celery", "celeriac"],
    "sulphites": ["sulphite", "sulfite", "sulphur dioxide", "sulfur dioxide"],
    "lupin": ["lupin"],
}

# Phrases that contain an allergen word but are not that allergen.
FALSE_POSITIVE_PHRASES = ["coconut milk", "coconut cream", "cocoa butter",
                          "shea butter", "nutmeg"]

# OpenFoodFacts allergen tags -> our allergen names
TAG_MAP: Dict[str, str] = {
    "milk": "milk", "eggs": "eggs", "peanuts": "peanuts", "nuts": "tree nuts",
    "gluten": "gluten", "soybeans": "soy", "fish": "fish",
    "crustaceans": "shellfish", "molluscs": "shellfish",
    "sesame-seeds": "sesame", "mustard": "mustard", "celery": "celery",
    "sulphur-dioxide-and-sulphites": "sulphites", "lupin": "lupin",
}

# "High" thresholds per 100 g (sugar/fat/sat fat/salt follow UK traffic-light
# values; calories is a simple project choice).
HIGH_THRESHOLDS = {"sugar": 22.5, "salt": 1.5, "fat": 17.5,
                   "saturated fat": 5.0, "calories": 400.0}

NUTRIENT_KEYS: Dict[str, List[str]] = {
    "sugar": ["sugars_100g", "sugar_100g", "sugars", "sugar"],
    "salt": ["salt_100g", "salt"],
    "fat": ["fat_100g", "fat"],
    "saturated fat": ["saturated-fat_100g", "saturated_fat_100g",
                      "saturated_fat", "saturated-fat"],
    "calories": ["energy-kcal_100g", "energy_kcal_100g", "energy-kcal",
                 "energy_kcal", "calories"],
}

NUTRIENT_TIPS: Dict[str, str] = {
    "sugar": "Choose an unsweetened version and sweeten with fresh fruit or cinnamon.",
    "salt": "Look for a low-salt version, and add flavour with herbs, lemon, garlic or spices instead.",
    "fat": "Pick a lower-fat version, or bake/grill/air-fry instead of frying.",
    "saturated fat": "Swap butter and palm oil for olive or rapeseed oil where you can.",
    "calories": "Have a smaller portion and pair it with vegetables or fruit to stay full.",
}

# ingredient keyword -> lighter swap (used when cooking at home)
INGREDIENT_SWAPS: Dict[str, str] = {
    "sugar": "Use mashed banana, fruit puree or a little honey instead of added sugar.",
    "glucose": "Look for products without glucose or glucose-fructose syrup.",
    "palm oil": "Choose products made with olive or rapeseed oil instead of palm oil.",
    "butter": "Try olive oil, mashed avocado or Greek yogurt in place of butter.",
    "cream": "Use Greek yogurt or evaporated milk instead of cream.",
    "mayonnaise": "Mix Greek yogurt with lemon and mustard for a lighter dressing.",
    "white flour": "Use wholemeal flour for more fibre.",
    "wheat flour": "Use wholemeal flour for more fibre.",
    "white rice": "Brown rice keeps you full for longer.",
    "salt": "Season with herbs, garlic, lemon or spices to cut back on salt.",
}

# (keyword found in name/categories, alternative text, allergens it contains)
CATEGORY_ALTERNATIVES = [
    (["cereal", "granola", "muesli"], "Plain oats topped with fruit and a few seeds.", {"gluten"}),
    (["soda", "cola", "soft drink", "fizzy", "energy drink"], "Sparkling water with lemon, lime or mint.", set()),
    (["juice", "squash"], "Whole fruit, or water infused with fruit slices.", set()),
    (["chocolate", "candy", "sweets", "confection"], "Dark chocolate (70% or more) or a handful of dried fruit.", set()),
    (["chips", "crisps", "snack"], "Air-popped popcorn or oven-roasted chickpeas.", set()),
    (["biscuit", "cookie", "cake", "pastry"], "Oat cakes, or a banana with a spoon of nut butter.", {"gluten", "peanuts", "tree nuts"}),
    (["yogurt", "yoghurt", "dessert"], "Plain yogurt with fresh fruit.", {"milk"}),
    (["ice cream"], "Frozen banana blended into 'nice cream' or frozen yogurt.", set()),
    (["bread", "bun", "roll"], "Wholegrain or seeded bread.", {"gluten"}),
    (["noodle", "pasta"], "Wholewheat pasta/noodles with extra vegetables.", {"gluten"}),
    (["ketchup", "sauce", "dressing"], "A homemade tomato sauce or a low-sugar, low-salt version.", set()),
    (["sausage", "bacon", "ham", "processed meat"], "Grilled chicken, fish, eggs or beans.", {"eggs", "fish"}),
]

RECIPES: List[Dict[str, Any]] = [
    {"name": "Tomato & Veggie Pasta",
     "ingredients": ["pasta", "tomato", "onion", "garlic", "olive oil", "basil"],
     "steps": "Boil pasta. Saute onion and garlic in olive oil, add chopped tomato and simmer 10 min. Toss with pasta and basil."},
    {"name": "Veggie Egg Fried Rice",
     "ingredients": ["rice", "egg", "carrot", "peas", "onion", "soy sauce"],
     "steps": "Stir-fry onion and carrot, add peas and cooked rice, push aside and scramble the eggs, then mix with a splash of soy sauce."},
    {"name": "Oatmeal with Fruit",
     "ingredients": ["oat", "milk", "banana", "honey", "cinnamon"],
     "steps": "Simmer oats in milk for 5 min. Top with sliced banana, a drizzle of honey and cinnamon."},
    {"name": "Chicken & Veg Stir-fry",
     "ingredients": ["chicken", "bell pepper", "broccoli", "garlic", "ginger", "soy sauce"],
     "steps": "Cook chicken strips, add garlic and ginger, then vegetables. Finish with soy sauce and serve hot."},
    {"name": "Lentil Soup",
     "ingredients": ["lentil", "carrot", "onion", "celery", "garlic", "tomato", "cumin"],
     "steps": "Saute onion, carrot, celery and garlic. Add lentils, tomato, cumin and water; simmer 25 min."},
    {"name": "Banana Oat Pancakes",
     "ingredients": ["banana", "egg", "oat", "milk"],
     "steps": "Blend everything into a batter and cook small pancakes in a non-stick pan."},
    {"name": "Chickpea Salad",
     "ingredients": ["chickpea", "cucumber", "tomato", "olive oil", "lemon", "parsley"],
     "steps": "Combine chickpeas, chopped cucumber and tomato. Dress with olive oil, lemon juice and parsley."},
    {"name": "Baked Potato Wedges",
     "ingredients": ["potato", "olive oil", "paprika", "garlic"],
     "steps": "Cut potatoes into wedges, toss with oil, paprika and garlic, bake at 220C for 30 min."},
    {"name": "Yogurt Berry Parfait",
     "ingredients": ["yogurt", "berries", "oat", "honey"],
     "steps": "Layer yogurt, berries and oats in a glass and finish with a little honey."},
    {"name": "Bean & Tomato Chilli",
     "ingredients": ["beans", "tomato", "onion", "pepper", "cumin", "garlic"],
     "steps": "Saute onion and garlic, add beans, tomato, pepper and cumin, simmer 20 min."},
    {"name": "Avocado Egg Toast",
     "ingredients": ["bread", "avocado", "egg", "lemon"],
     "steps": "Mash avocado with lemon, spread on toasted bread and top with a boiled or poached egg."},
    {"name": "Fruit Smoothie",
     "ingredients": ["banana", "berries", "yogurt", "milk"],
     "steps": "Blend all ingredients until smooth."},
    {"name": "Vegetable Jollof Rice",
     "ingredients": ["rice", "tomato", "onion", "pepper", "vegetable oil", "thyme"],
     "steps": "Blend tomato, pepper and onion, fry the mix in a little oil, add rice, stock and thyme, and steam until done."},
    {"name": "Egg & Tomato Scramble",
     "ingredients": ["egg", "tomato", "onion", "pepper"],
     "steps": "Saute onion and pepper, add chopped tomato, then stir in beaten eggs until just set."},
]


# --------------------------------------------------------------------------
# Small helpers (work with a dict or any object, e.g. Person 1's FoodProduct)
# --------------------------------------------------------------------------

def _get(obj: Any, names: Iterable[str], default: Any = None) -> Any:
    for n in names:
        value = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if value not in (None, "", [], {}):
            return value
    return default


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d*\.?\d+", str(value).replace(",", "."))
    return float(match.group()) if match else None


def _ingredients_text(product: Any) -> str:
    value = _get(product, ["ingredients", "ingredients_text"], "")
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(v) for v in value)
    return str(value).lower()


def _keyword_in(text: str, keyword: str) -> bool:
    """Whole-word-start match so 'oat' matches 'oats' but 'pea' won't match 'peanut'."""
    return re.search(r"\b" + re.escape(keyword), text) is not None


def _nutrient_value(product: Any, nutrient: str) -> Optional[float]:
    nutrition = _get(product, ["nutrition", "nutriments", "nutrition_info"], {})
    if not isinstance(nutrition, dict):
        return None
    for key in NUTRIENT_KEYS[nutrient]:
        if key in nutrition:
            number = _to_float(nutrition[key])
            if number is not None:
                return number
    return None


def _normalise_nutrient_name(name: str) -> str:
    name = str(name).lower().replace("_", " ").replace("-", " ").strip()
    aliases = {"sugars": "sugar", "calorie": "calories", "energy": "calories",
               "sodium": "salt", "saturates": "saturated fat",
               "saturated": "saturated fat"}
    return aliases.get(name, name)


# --------------------------------------------------------------------------
# Main class
# --------------------------------------------------------------------------

class MealSuggestionGenerator:
    """Allergens, healthier alternatives and recipe ideas for a food product."""

    def __init__(self, api_key: Optional[str] = None, use_ai: bool = True,
                 model: str = "gemini-2.5-flash"):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.use_ai = use_ai and bool(self.api_key)
        self.model = model

    # ---- 1. Allergens -----------------------------------------------------
    def detect_allergens(self, product: Any) -> List[str]:
        """Return a sorted list of POSSIBLE allergens (always verify the real label)."""
        found: Set[str] = set()

        # a) official tags from OpenFoodFacts, if Person 1 stored them
        tags = _get(product, ["allergens_tags", "allergens"], [])
        if isinstance(tags, str):
            tags = re.split(r"[,;]", tags)
        for tag in tags:
            clean = str(tag).lower().split(":")[-1].strip()
            if clean in TAG_MAP:
                found.add(TAG_MAP[clean])
            elif clean in ALLERGEN_KEYWORDS:
                found.add(clean)

        # b) keyword scan of the ingredient text
        text = _ingredients_text(product)
        for phrase in FALSE_POSITIVE_PHRASES:
            text = text.replace(phrase, " ")
        for allergen, keywords in ALLERGEN_KEYWORDS.items():
            if any(_keyword_in(text, kw) for kw in keywords):
                found.add(allergen)

        return sorted(found)

    # ---- 2. Healthier alternatives ---------------------------------------
    def high_nutrients(self, product: Any, analysis: Optional[Dict] = None) -> List[str]:
        """Which nutrients are high? Uses Person 2's analysis if given, else thresholds."""
        high: Set[str] = set()
        if analysis:
            for key, value in analysis.items():
                level = value.get("level") if isinstance(value, dict) else value
                name = _normalise_nutrient_name(key)
                if str(level).lower() in ("high", "very high") and name in HIGH_THRESHOLDS:
                    high.add(name)
        else:
            for nutrient, limit in HIGH_THRESHOLDS.items():
                value = _nutrient_value(product, nutrient)
                if value is not None and value > limit:
                    high.add(nutrient)
        return sorted(high)

    def healthier_alternatives(self, product: Any, analysis: Optional[Dict] = None,
                               user_allergens: Optional[Iterable[str]] = None,
                               limit: int = 6) -> List[str]:
        avoid = {a.lower() for a in (user_allergens or [])}
        tips: List[str] = []

        # product-type swaps
        context = " ".join([str(_get(product, ["name", "product_name"], "")),
                            str(_get(product, ["categories"], ""))]).lower()
        for keywords, text, allergens in CATEGORY_ALTERNATIVES:
            if any(_keyword_in(context, k) for k in keywords) and not (allergens & avoid):
                tips.append(text)

        # advice for whatever is high
        tips += [NUTRIENT_TIPS[n] for n in self.high_nutrients(product, analysis)]

        # ingredient-level swaps for home cooking
        text = _ingredients_text(product)
        for keyword, swap in INGREDIENT_SWAPS.items():
            if _keyword_in(text, keyword):
                tips.append(swap)

        unique = list(dict.fromkeys(tips))  # de-duplicate, keep order
        return unique[:limit] or ["This product looks fairly balanced. Enjoy it in sensible portions."]

    # ---- 3. Recipes using similar ingredients -----------------------------
    def recipe_suggestions(self, product: Any, user_allergens: Optional[Iterable[str]] = None,
                           max_results: int = 3) -> List[Dict[str, Any]]:
        avoid = {a.lower() for a in (user_allergens or [])}
        text = _ingredients_text(product) + " " + str(_get(product, ["name", "product_name"], "")).lower()

        scored = []
        for recipe in RECIPES:
            recipe_allergens = set(self.detect_allergens({"ingredients": recipe["ingredients"]}))
            if recipe_allergens & avoid:
                continue
            shared = [i for i in recipe["ingredients"] if _keyword_in(text, i)]
            scored.append((len(shared), -len(recipe["ingredients"]), recipe, shared, recipe_allergens))

        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        results = []
        for score, _, recipe, shared, rec_allergens in scored[:max_results]:
            results.append({
                "name": recipe["name"],
                "ingredients": recipe["ingredients"],
                "steps": recipe["steps"],
                "shared_ingredients": shared,
                "contains_allergens": sorted(rec_allergens),
            })
        return results

    # ---- 4. Optional Gemini ideas ------------------------------------------
    def ai_suggestions(self, product: Any, user_allergens: Optional[Iterable[str]] = None) -> Optional[str]:
        """Ask Gemini for healthier ideas. Returns None if AI is off or the call fails."""
        if not self.use_ai:
            return None
        try:
            from google import genai  # pip install google-genai

            name = _get(product, ["name", "product_name"], "this product")
            avoid = ", ".join(user_allergens) if user_allergens else "none"
            prompt = (
                f"Product: {name}\n"
                f"Ingredients: {_ingredients_text(product) or 'unknown'}\n"
                f"Allergens the user must avoid: {avoid}\n\n"
                "In simple language, suggest 2 healthier alternatives and 2 quick recipes "
                "that use similar ingredients. Keep it under 150 words and never include "
                "the allergens listed above."
            )
            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(model=self.model, contents=prompt)
            return (response.text or "").strip() or None
        except Exception:
            return None  # the app still works without AI

    # ---- Everything in one call (for Person 4's app.py) -------------------
    def generate(self, product: Any, analysis: Optional[Dict] = None,
                 user_allergens: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        allergens = self.detect_allergens(product)
        avoid = {a.lower() for a in (user_allergens or [])}
        return {
            "allergens": allergens,
            "allergen_warning": sorted(set(allergens) & avoid),
            "alternatives": self.healthier_alternatives(product, analysis, user_allergens),
            "recipes": self.recipe_suggestions(product, user_allergens),
            "ai_suggestions": self.ai_suggestions(product, user_allergens),
            "disclaimer": "Allergen detection is a guide only. Always check the product packaging.",
        }


# --------------------------------------------------------------------------
# Quick test:  python suggestions.py
# --------------------------------------------------------------------------
if __name__ == "__main__":
    sample = {
        "name": "Chocolate Oat Cookies",
        "barcode": "1234567890123",
        "categories": "Biscuits and cakes",
        "ingredients": "Wheat flour, sugar, butter, oats, cocoa butter, egg, soy lecithin, salt",
        "nutrition": {"sugars_100g": 30, "salt_100g": 0.9, "fat_100g": 22,
                      "saturated-fat_100g": 11, "energy-kcal_100g": 480},
    }
    generator = MealSuggestionGenerator()
    result = generator.generate(sample, user_allergens=["peanuts"])
    for key, value in result.items():
        print(f"\n== {key} ==")
        print(value)
