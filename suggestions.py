"""
suggestions.py  (Person 3 - Suggestions & Allergens)

Gives the user recommendations about a scanned food product:
  1. Possible allergens (contains + "may contain" traces)
  2. Healthier alternatives
  3. Recipe suggestions using similar ingredients
  4. Optional AI (Gemini) ideas, with a safe offline fallback

How it fits with the rest of the team's code
--------------------------------------------
    from openfoodfacts import OpenFoodFactsClient       # Person 1
    from nutrition_analyzer import NutritionAnalyzer     # Person 2
    from suggestions import MealSuggestionGenerator      # Person 3

    food = OpenFoodFactsClient().get_product(barcode)    # FoodProduct
    analyzer = NutritionAnalyzer()
    report = analyzer.analyze(food.nutrition)            # NOTE: .nutrition, not the object
    ideas = MealSuggestionGenerator().generate(food, analysis=report,
                                               user_allergens=["peanuts"])

`product` can be Person 1's FoodProduct (name, barcode, ingredients, nutrition)
OR a raw Open Food Facts dict (product_name, ingredients_text, nutriments, ...).
Optional extras are used when present: categories / categories_tags,
allergens_tags, traces_tags, ingredients_text_en.

Optional Gemini setup:
    pip install google-genai
    set the environment variable GEMINI_API_KEY
"""

import os
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Person 2's analyzer decides high/medium/low. If the file is missing we fall
# back to simple thresholds below so this module still works on its own.
try:
    from nutrition_analyzer import NutritionAnalyzer, NutritionDataError
except ImportError:  # pragma: no cover
    NutritionAnalyzer = None
    NutritionDataError = Exception

# --------------------------------------------------------------------------
# Data tables (all keywords are lower-case and WITHOUT accents, because the
# ingredient text is accent-stripped before matching: "ble" for "blé")
# --------------------------------------------------------------------------

ALLERGEN_KEYWORDS: Dict[str, List[str]] = {
    "milk": ["milk", "milkfat", "milk fat", "butter", "butterfat", "buttermilk",
             "cheese", "cream", "whey", "casein", "caseinate", "lactose",
             "yogurt", "yoghurt", "ghee",
             "lait", "lactoserum", "beurre", "fromage", "creme", "yaourt",
             "leche", "queso", "mantequilla", "milch", "kase", "sahne", "joghurt"],
    "eggs": ["egg", "albumin", "albumen", "mayonnaise", "meringue",
             "oeuf", "huevo", "mayonesa", "eier", "eigelb", "eiweiss"],
    "peanuts": ["peanut", "groundnut", "arachis", "arachide", "cacahuete",
                "cacahuate", "erdnuss", "erdnusse"],
    "tree nuts": ["almond", "hazelnut", "walnut", "cashew", "pecan", "pistachio",
                  "macadamia", "brazil nut", "tree nut",
                  "amande", "noisette", "noix", "fruits a coque", "fruit a coque",
                  "almendra", "avellana", "nuez", "nueces", "frutos secos",
                  "mandel", "mandeln", "haselnuss", "haselnusse", "walnuss", "walnusse"],
    "gluten": ["wheat", "barley", "rye", "oat", "spelt", "semolina", "gluten",
               "malt", "couscous", "kamut", "triticale",
               "ble", "orge", "seigle", "avoine", "epeautre", "froment", "semoule",
               "trigo", "cebada", "centeno", "avena",
               "weizen", "gerste", "roggen", "hafer", "dinkel"],
    "soy": ["soy", "soya", "soybean", "tofu", "edamame", "miso", "soja", "soia"],
    "fish": ["fish", "salmon", "tuna", "cod", "anchovy", "sardine",
             "poisson", "saumon", "thon", "anchois", "pescado", "fisch"],
    "shellfish": ["shrimp", "prawn", "crab", "lobster", "crayfish", "scallop",
                  "mussel", "oyster", "clam", "crustacean", "mollusc", "mollusk",
                  "crevette", "crustace", "mollusque", "gamba", "langostino",
                  "camaron", "garnele"],
    "sesame": ["sesame", "tahini", "sesamo", "sesam"],
    "mustard": ["mustard", "moutarde", "mostaza", "senf"],
    "celery": ["celery", "celeriac", "celeri", "apio", "sellerie"],
    "sulphites": ["sulphite", "sulfite", "sulphur dioxide", "sulfur dioxide",
                  "sulfit", "anhydride sulfureux", "dioxyde de soufre",
                  "dioxido de azufre", "schwefeldioxid"],
    "lupin": ["lupin", "lupine", "altramuz"],
}

ALLERGEN_NAMES: List[str] = list(ALLERGEN_KEYWORDS)  # handy for a UI checklist

# Names a user might type -> our allergen names
ALLERGEN_ALIASES: Dict[str, str] = {
    "dairy": "milk", "lactose": "milk", "egg": "eggs", "peanut": "peanuts",
    "nuts": "tree nuts", "nut": "tree nuts", "tree nut": "tree nuts",
    "wheat": "gluten", "soya": "soy", "soybeans": "soy", "soybean": "soy",
    "crustaceans": "shellfish", "seafood": "shellfish",
    "sesame seeds": "sesame", "sulfites": "sulphites", "sulphite": "sulphites",
}

# Phrases that contain an allergen word but are NOT that allergen
# (or where the real allergen is something else).
TEXT_REPLACEMENTS: List[Tuple[str, str]] = [
    ("almond milk", "almond"), ("soy milk", "soy"), ("soya milk", "soya"),
    ("oat milk", "oat"), ("lait d'amande", "amande"), ("lait de soja", "soja"),
    ("lait d'avoine", "avoine"),
    ("coconut milk", " "), ("coconut cream", " "), ("lait de coco", " "),
    ("noix de coco", " "), ("leche de coco", " "),
    ("cocoa butter", " "), ("cacao butter", " "), ("beurre de cacao", " "),
    ("beurre de cacahuete", "cacahuete"), ("shea butter", " "),
    ("cream of tartar", " "),
]

# Text after these phrases lists TRACES ("may contain"), not real ingredients.
TRACE_PATTERN = re.compile(
    r"(may contain|might contain|can contain|traces? (?:of|d'|de)|made in a|"
    r"produced in a|manufactured in a|processed in a|packed in a|"
    r"peut contenir|fabrique dans|produit dans|puede contener|elaborado en|"
    r"kann spuren|kann .{0,40}enthalten)"
)

# OpenFoodFacts allergen tags -> our allergen names
TAG_MAP: Dict[str, str] = {
    "milk": "milk", "eggs": "eggs", "peanuts": "peanuts", "nuts": "tree nuts",
    "gluten": "gluten", "soybeans": "soy", "fish": "fish",
    "crustaceans": "shellfish", "molluscs": "shellfish",
    "sesame-seeds": "sesame", "mustard": "mustard", "celery": "celery",
    "sulphur-dioxide-and-sulphites": "sulphites", "lupin": "lupin",
}

# Ingredient values that mean "no data" (Person 1 uses "Not available").
MISSING_TEXT = {"", "not available", "unknown", "n/a", "none"}

# Fallback thresholds per 100 g, only used if nutrition_analyzer.py can't be imported.
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

DRINK_WORDS = ["beverage", "drink", "soda", "cola", "juice", "lemonade",
               "squash", "nectar"]

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

# (keywords found in name/categories, alternative text, allergens the alternative contains)
CATEGORY_ALTERNATIVES = [
    (["cereal", "granola", "muesli"], "Plain oats topped with fruit and a few seeds.", {"gluten"}),
    (["soda", "cola", "soft drink", "fizzy", "energy drink", "carbonated drink", "lemonade"],
     "Sparkling water with lemon, lime or mint.", set()),
    (["juice", "squash", "nectar"], "Whole fruit, or water infused with fruit slices.", set()),
    (["spread", "hazelnut spread", "nutella", "jam"],
     "Mashed banana or a 100% nut butter (no added sugar) on wholegrain toast.",
     {"peanuts", "tree nuts", "gluten"}),
    (["chocolate", "candy", "sweets", "confectionery"],
     "Dark chocolate (70% or more) or a handful of dried fruit.", set()),
    (["biscuit", "cookie", "cake", "pastry"],
     "Oat cakes, or a banana with a spoon of nut butter.", {"gluten", "peanuts", "tree nuts"}),
    (["yogurt", "yoghurt", "dessert"], "Plain yogurt with fresh fruit.", {"milk"}),
    (["ice cream"], "Frozen banana blended into 'nice cream' or frozen yogurt.", set()),
    (["bread", "bun", "roll"], "Wholegrain or seeded bread.", {"gluten"}),
    (["noodle", "pasta", "nouille", "nudel", "ramen", "indomie"],
     "Wholewheat pasta or noodles with extra vegetables, and only half the seasoning sachet.",
     {"gluten"}),
    (["ketchup", "sauce", "dressing"],
     "A homemade tomato sauce or a low-sugar, low-salt version.", set()),
    (["sausage", "bacon", "ham", "processed meat"],
     "Grilled chicken, fish, eggs or beans.", {"eggs", "fish"}),
    (["chips", "crisps", "snack"], "Air-popped popcorn or oven-roasted chickpeas.", set()),
]

# Used only when the name/categories didn't match anything (FoodProduct has no categories).
INGREDIENT_HINTS = [
    (["hazelnut"], "Mashed banana or a 100% nut butter (no added sugar) on wholegrain toast.",
     {"peanuts", "tree nuts", "gluten"}),
    (["chocolate", "cocoa"], "Dark chocolate (70% or more) or a handful of dried fruit.", set()),
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
     "ingredients": ["yogurt", "berry", "oat", "honey"],
     "steps": "Layer yogurt, berries and oats in a glass and finish with a little honey."},
    {"name": "Bean & Tomato Chilli",
     "ingredients": ["beans", "tomato", "onion", "pepper", "cumin", "garlic"],
     "steps": "Saute onion and garlic, add beans, tomato, pepper and cumin, simmer 20 min."},
    {"name": "Avocado Egg Toast",
     "ingredients": ["bread", "avocado", "egg", "lemon"],
     "steps": "Mash avocado with lemon, spread on toasted bread and top with a boiled or poached egg."},
    {"name": "Fruit Smoothie",
     "ingredients": ["banana", "berry", "yogurt", "milk"],
     "steps": "Blend all ingredients until smooth."},
    {"name": "Vegetable Jollof Rice",
     "ingredients": ["rice", "tomato", "onion", "pepper", "vegetable oil", "thyme"],
     "steps": "Blend tomato, pepper and onion, fry the mix in a little oil, add rice, stock and thyme, and steam until done."},
    {"name": "Egg & Tomato Scramble",
     "ingredients": ["egg", "tomato", "onion", "pepper"],
     "steps": "Saute onion and pepper, add chopped tomato, then stir in beaten eggs until just set."},
]


# --------------------------------------------------------------------------
# Small helpers (work with a dict OR an object like Person 1's FoodProduct)
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


def _strip_accents(text: str) -> str:
    text = text.lower().replace("\u0153", "oe").replace("\u00e6", "ae").replace("\u00df", "ss")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _clean(text: Any) -> str:
    """Lower-case, no accents, no OFF markup (_milk_ / *), single spaces, known false positives removed."""
    text = _strip_accents(str(text))
    text = re.sub(r"[_*]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    for phrase, replacement in TEXT_REPLACEMENTS:
        text = text.replace(phrase, replacement)
    return text


def _keyword_in(text: str, keyword: str) -> bool:
    """Whole-word match that also accepts simple plurals (oat/oats, tomato/tomatoes, berry/berries)."""
    if keyword.endswith("y"):
        core = re.escape(keyword[:-1]) + r"(?:y|ies|ys)"
    else:
        core = re.escape(keyword) + r"(?:e?s)?"
    return re.search(r"\b" + core + r"\b", text) is not None


def _ingredient_texts(product: Any) -> List[str]:
    """Every ingredient text we can find (FoodProduct has one, raw OFF dicts may have two languages)."""
    texts: List[str] = []
    for key in ("ingredients", "ingredients_text", "ingredients_text_en"):
        value = _get(product, [key])
        if isinstance(value, (list, tuple, set)):
            value = ", ".join(str(v) for v in value)
        if value and _clean(value) not in MISSING_TEXT:
            texts.append(_clean(value))
    return list(dict.fromkeys(texts))


def _ingredients_text(product: Any) -> str:
    return " | ".join(_ingredient_texts(product))


def _context(product: Any, categories: Any = None) -> str:
    """Name(s) + categories as one clean string (used to guess the product type)."""
    parts = [str(_get(product, [k], "")) for k in ("name", "product_name_en", "product_name")]
    cats = categories if categories is not None else _get(
        product, ["categories", "categories_tags", "categories_en"], "")
    if isinstance(cats, (list, tuple, set)):
        cats = " ".join(str(c) for c in cats)
    cats = re.sub(r"\b[a-z]{2}:", " ", str(cats).lower())
    parts.append(cats.replace("-", " ").replace(",", " "))
    return _clean(" ".join(parts))


def _is_drink(context: str) -> bool:
    return any(_keyword_in(context, w) for w in DRINK_WORDS)


def _normalise_nutrient_name(name: str) -> str:
    name = str(name).lower().replace("_", " ").replace("-", " ").strip()
    aliases = {"sugars": "sugar", "calorie": "calories", "energy": "calories",
               "sodium": "salt", "saturates": "saturated fat",
               "saturated": "saturated fat"}
    return aliases.get(name, name)


def _high_from_analysis(analysis: Any) -> Set[str]:
    """Accepts Person 2's report dict, or the list from analyzer.high_nutrients(report)."""
    high: Set[str] = set()
    if isinstance(analysis, dict):
        for key, value in analysis.items():
            level = value.get("level") if isinstance(value, dict) else value
            name = _normalise_nutrient_name(key)
            if str(level).lower() in ("high", "very high") and name in HIGH_THRESHOLDS:
                high.add(name)
    elif isinstance(analysis, (list, tuple, set)):
        for key in analysis:
            name = _normalise_nutrient_name(key)
            if name in HIGH_THRESHOLDS:
                high.add(name)
    return high


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


def _normalise_allergens(allergens: Optional[Iterable[str]]) -> Set[str]:
    result = set()
    for a in (allergens or []):
        a = str(a).lower().strip()
        result.add(ALLERGEN_ALIASES.get(a, a))
    return result


def _tag_allergens(tags: Any) -> Set[str]:
    if isinstance(tags, str):
        tags = re.split(r"[,;]", tags)
    found = set()
    for tag in (tags or []):
        clean = str(tag).lower().split(":")[-1].strip()
        if clean in TAG_MAP:
            found.add(TAG_MAP[clean])
        elif clean in ALLERGEN_KEYWORDS:
            found.add(clean)
    return found


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
    def _scan_allergens(self, product: Any) -> Tuple[Set[str], Set[str]]:
        """Return (contains, traces). Traces = 'may contain' / made-in-a-factory warnings."""
        contains = _tag_allergens(_get(product, ["allergens_tags", "allergens"], []))
        traces = _tag_allergens(_get(product, ["traces_tags", "traces"], []))

        for text in _ingredient_texts(product):
            match = TRACE_PATTERN.search(text)
            main, trace_part = (text[:match.start()], text[match.start():]) if match else (text, "")
            for allergen, keywords in ALLERGEN_KEYWORDS.items():
                if any(_keyword_in(main, kw) for kw in keywords):
                    contains.add(allergen)
                if trace_part and any(_keyword_in(trace_part, kw) for kw in keywords):
                    traces.add(allergen)
        return contains, traces - contains

    def detect_allergens(self, product: Any) -> List[str]:
        """Allergens that are IN the ingredients (sorted). Always verify the real label."""
        return sorted(self._scan_allergens(product)[0])

    def detect_trace_allergens(self, product: Any) -> List[str]:
        """Allergens the label only says it MAY contain (cross-contamination)."""
        return sorted(self._scan_allergens(product)[1])

    # ---- 2. Healthier alternatives ---------------------------------------
    def high_nutrients(self, product: Any, analysis: Any = None,
                       is_drink: Optional[bool] = None) -> List[str]:
        """Which nutrients are high? Uses Person 2's analysis (or runs it) for consistent limits."""
        if analysis is not None:
            return sorted(_high_from_analysis(analysis))

        if NutritionAnalyzer is not None:
            nutrition = _get(product, ["nutrition", "nutriments", "nutrition_info"], {})
            if is_drink is None:
                is_drink = _is_drink(_context(product))
            try:
                report = NutritionAnalyzer(is_drink=bool(is_drink)).analyze(nutrition)
            except NutritionDataError:
                return []
            return sorted(_high_from_analysis(report))

        return sorted(n for n, limit in HIGH_THRESHOLDS.items()
                      if (_nutrient_value(product, n) or 0) > limit)

    def healthier_alternatives(self, product: Any, analysis: Any = None,
                               user_allergens: Optional[Iterable[str]] = None,
                               limit: int = 6, categories: Any = None,
                               is_drink: Optional[bool] = None) -> List[str]:
        avoid = _normalise_allergens(user_allergens)

        # product-type swaps (from name / categories, else from ingredients)
        context = _context(product, categories)
        groups = [g for g in CATEGORY_ALTERNATIVES
                  if any(_keyword_in(context, k) for k in g[0])]
        tips = [text for _, text, allergens in groups if not (allergens & avoid)][:2]
        if not tips:
            ingredients = _ingredients_text(product)
            tips = [text for keywords, text, allergens in INGREDIENT_HINTS
                    if any(_keyword_in(ingredients, k) for k in keywords)
                    and not (allergens & avoid)][:1]

        # advice for whatever is high
        tips += [NUTRIENT_TIPS[n] for n in self.high_nutrients(product, analysis, is_drink)]

        # ingredient-level swaps for home cooking
        if is_drink is None:
            is_drink = _is_drink(context)
        if not is_drink:
            ingredients = _ingredients_text(product)
            for keyword, swap in INGREDIENT_SWAPS.items():
                if _keyword_in(ingredients, keyword):
                    tips.append(swap)

        unique = list(dict.fromkeys(tips))  # de-duplicate, keep order
        return unique[:limit] or ["This product looks fairly balanced. Enjoy it in sensible portions."]

    # ---- 3. Recipes using similar ingredients -----------------------------
    def recipe_suggestions(self, product: Any, user_allergens: Optional[Iterable[str]] = None,
                           max_results: int = 3) -> List[Dict[str, Any]]:
        avoid = _normalise_allergens(user_allergens)
        text = _ingredients_text(product) + " " + _context(product)

        scored = []
        for recipe in RECIPES:
            recipe_allergens = set(self.detect_allergens({"ingredients": recipe["ingredients"]}))
            if recipe_allergens & avoid:
                continue
            shared = [i for i in recipe["ingredients"] if _keyword_in(text, i)]
            scored.append((len(shared), -len(recipe["ingredients"]), recipe, shared, recipe_allergens))

        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [{
            "name": recipe["name"],
            "ingredients": recipe["ingredients"],
            "steps": recipe["steps"],
            "shared_ingredients": shared,
            "contains_allergens": sorted(rec_allergens),
        } for _, _, recipe, shared, rec_allergens in scored[:max_results]]

    # ---- 4. Optional Gemini ideas ------------------------------------------
    def ai_suggestions(self, product: Any, user_allergens: Optional[Iterable[str]] = None,
                       high: Optional[List[str]] = None) -> Optional[str]:
        """Ask Gemini for healthier ideas. Returns None if AI is off or the call fails."""
        if not self.use_ai:
            return None
        try:
            from google import genai  # pip install google-genai

            name = _get(product, ["name", "product_name_en", "product_name"], "this product")
            avoid = ", ".join(sorted(_normalise_allergens(user_allergens))) or "none"
            prompt = (
                f"Product: {name}\n"
                f"Ingredients: {_ingredients_text(product) or 'unknown'}\n"
                f"High in: {', '.join(high) if high else 'nothing notable'}\n"
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
    def generate(self, product: Any, analysis: Any = None,
                 user_allergens: Optional[Iterable[str]] = None,
                 categories: Any = None, is_drink: Optional[bool] = None) -> Dict[str, Any]:
        """
        product        FoodProduct or raw Open Food Facts dict
        analysis       Person 2's report (analyzer.analyze(food.nutrition)); optional
        user_allergens e.g. ["peanuts", "milk"] - recipes/alternatives avoid them
        categories     optional, e.g. a categories string/list if you have one
        is_drink       optional override for drink-vs-food limits
        """
        contains, traces = self._scan_allergens(product)
        avoid = _normalise_allergens(user_allergens)
        has_data = bool(_ingredient_texts(product)) or bool(contains or traces)
        high = self.high_nutrients(product, analysis, is_drink)

        return {
            "allergens": sorted(contains),
            "may_contain": sorted(traces),
            "allergen_warning": sorted(contains & avoid),
            "trace_warning": sorted(traces & avoid),
            "allergen_note": None if has_data else
                "No ingredient list is available, so allergens could not be checked.",
            "high_nutrients": high,
            "alternatives": self.healthier_alternatives(
                product, analysis, user_allergens, categories=categories, is_drink=is_drink),
            "recipes": self.recipe_suggestions(product, user_allergens),
            "ai_suggestions": self.ai_suggestions(product, user_allergens, high),
            "disclaimer": "Allergen detection is a guide only. Always check the product packaging.",
        }


# --------------------------------------------------------------------------
# Quick test:  python suggestions.py   (uses demo_products.json if it is next to this file)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    from pathlib import Path

    demo = Path(__file__).with_name("demo_products.json")
    products = json.loads(demo.read_text(encoding="utf-8")) if demo.exists() else [{
        "product_name": "Chocolate Oat Cookies",
        "ingredients_text": "Wheat flour, sugar, butter, oats, cocoa butter, egg, soy lecithin, salt",
        "nutriments": {"sugars_100g": 30, "salt_100g": 0.9, "fat_100g": 22,
                       "saturated-fat_100g": 11, "energy-kcal_100g": 480},
    }]

    generator = MealSuggestionGenerator()
    for item in products:
        result = generator.generate(item, user_allergens=["peanuts"])
        print(f"\n=== {item.get('product_name_en') or item.get('product_name')} ===")
        for key in ("allergens", "may_contain", "allergen_warning", "allergen_note",
                    "high_nutrients", "alternatives"):
            print(f"{key}: {result[key]}")
        print("recipes:", [r["name"] for r in result["recipes"]])
