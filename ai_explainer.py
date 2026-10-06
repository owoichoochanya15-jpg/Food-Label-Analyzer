"""ai_explainer.py

The project asks for AI (Gemini) to EXPLAIN the label in simple terms. Nobody
owned that piece, so it lives here. If there is no API key (or Gemini fails)
it falls back to Person 2's plain sentences, so the app never breaks.

    explainer = LabelExplainer()
    result = explainer.explain(food, report, allergens=["milk", "soy"])
    result["text"]     # the explanation to show
    result["source"]   # "gemini" or "offline"
    result["error"]    # None, or why Gemini was not used
"""

import os

from nutrition_analyzer import NutritionAnalyzer

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


class LabelExplainer:

    def __init__(self, api_key=None, model=None, use_ai=True):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.use_ai = use_ai and bool(self.api_key)
        self.model = model or DEFAULT_MODEL

    @staticmethod
    def _facts(food, report, is_drink):
        per = "100 ml" if is_drink else "100 g"
        lines = []
        for nutrient, result in report.items():
            if result["level"] != "unknown":
                lines.append(f"- {nutrient.replace('_', ' ')}: {round(result['amount'], 2):g} "
                             f"{result['unit']} per {per} ({result['level']})")
        return "\n".join(lines)

    def explain(self, food, report, allergens=None):
        is_drink = bool(getattr(food, "is_drink", False))
        offline_text = " ".join(NutritionAnalyzer(is_drink=is_drink).summary(report))
        error = None

        if self.use_ai:
            try:
                from google import genai  # pip install google-genai
                prompt = (
                    f"Product: {food.name}\n"
                    f"Ingredients: {food.ingredients or 'not available'}\n"
                    f"Nutrition:\n{self._facts(food, report, is_drink)}\n"
                    f"Possible allergens: {', '.join(allergens) if allergens else 'none detected'}\n\n"
                    "Explain this food label in simple, friendly language for a non-expert. "
                    "Say clearly which nutrients are high, medium or low and what that means. "
                    "Keep it under 120 words. Do not give medical advice."
                )
                client = genai.Client(api_key=self.api_key)
                response = client.models.generate_content(model=self.model, contents=prompt)
                text = (response.text or "").strip()
                if text:
                    return {"text": text, "source": "gemini", "error": None}
                error = "Gemini returned an empty answer."
            except Exception as exc:        # keep the app alive, but remember why
                error = f"{type(exc).__name__}: {exc}"
        else:
            error = "No GEMINI_API_KEY set." if not self.api_key else "AI turned off."

        return {"text": offline_text, "source": "offline", "error": error}
