"""
live_demo.py
============
Live demo of MY part (Person 2: nutrition_analyzer.py) on REAL products.

Type a food name ("nutella") or a barcode ("3017624010701"). This script
fetches the product from the free Open Food Facts API, then hands it to
NutritionAnalyzer, which does the real work: high/medium/low for calories,
sugar, fat, saturated fat and salt, plus cleaned ingredient text.

    python live_demo.py              # keeps asking for foods until you type q
    python live_demo.py nutella      # analyze one food and stop

NOTE: fetching products properly is Person 1's job (OpenFoodFactsClient).
The small fetch functions below exist only so I can demo my analyzer on
live data. If there is no internet, it falls back to real products saved in
demo_products.json, so the demo still works offline.
"""

import json
import os
import re
import sys
from pathlib import Path

import requests

from nutrition_analyzer import NutritionAnalyzer, NutritionDataError

USER_AGENT = "FoodLabelAnalyzer/1.0 (student project)"
FIELDS = ("code,product_name,product_name_en,brands,nutriments,"
          "ingredients_text,ingredients_text_en,categories_tags,quantity")
OFFLINE_FILE = Path(__file__).with_name("demo_products.json")

# ANSI colour codes, so HIGH shows red, MEDIUM amber and LOW green.
COLOURS = {"high": "\033[91m", "medium": "\033[93m", "low": "\033[92m", "unknown": "\033[90m"}
BOLD, RESET = "\033[1m", "\033[0m"


# ----------------------------------------------------------------------
# Fetching (demo stand-in for Person 1's OpenFoodFactsClient)
# ----------------------------------------------------------------------
def get_json(url, params=None):
    response = requests.get(url, params=params, timeout=15,
                            headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.json()


def fetch_by_barcode(barcode):
    data = get_json(f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json",
                    {"fields": FIELDS})
    return [data["product"]] if data.get("status") == 1 else []


def search_by_name(name):
    """Try the fast search server first; if it is busy, try the classic one."""
    try:
        data = get_json("https://search.openfoodfacts.org/search",
                        {"q": name, "page_size": 15, "fields": FIELDS,
                         "sort_by": "-unique_scans_n"})      # most scanned first
        return data.get("hits", [])
    except requests.RequestException:
        data = get_json("https://world.openfoodfacts.org/cgi/search.pl",
                        {"search_terms": name, "search_simple": 1, "action": "process",
                         "json": 1, "page_size": 15, "fields": FIELDS,
                         "sort_by": "unique_scans_n"})
        return data.get("products", [])


def search_offline(text):
    products = json.loads(OFFLINE_FILE.read_text(encoding="utf-8"))
    text = text.lower()
    return [p for p in products
            if text in p.get("code", "") or text in display_name(p).lower()]


def find_products(text):
    """Return a list of matching products (dicts in Open Food Facts format)."""
    try:
        found = fetch_by_barcode(text) if text.isdigit() else search_by_name(text)
    except requests.RequestException:
        print("  (No internet / API busy: using saved products from demo_products.json)")
        found = search_offline(text)
    # Only keep products that have a name and some nutrition numbers.
    return [p for p in found if p.get("nutriments")
            and (p.get("product_name_en") or p.get("product_name"))][:5]


def display_name(product):
    name = product.get("product_name_en") or product.get("product_name") or "Unnamed product"
    brands = product.get("brands") or ""
    if isinstance(brands, list):            # the search server sends a list
        brands = ", ".join(brands)
    brand = brands.split(",")[0].strip()
    return f"{name} ({brand})" if brand else name


# ----------------------------------------------------------------------
# Showing the result (this is where MY NutritionAnalyzer is used)
# ----------------------------------------------------------------------
def show_analysis(product):
    is_drink = "en:beverages" in (product.get("categories_tags") or [])
    analyzer = NutritionAnalyzer(is_drink=is_drink)

    print(f"\n{BOLD}{display_name(product)}{RESET}")
    print(f"  Barcode: {product.get('code', '?')}   "
          f"Size: {product.get('quantity') or '?'}   "
          f"Type: {'drink (per 100 ml)' if is_drink else 'food (per 100 g)'}")

    try:
        report = analyzer.analyze(product["nutriments"])
    except NutritionDataError as error:
        print(f"  Sorry: {error}")
        return

    print()
    for nutrient, result in report.items():
        level = result["level"]
        amount = "-" if result["amount"] is None else f"{round(result['amount'], 2):g} {result['unit']}"
        print(f"  {nutrient.replace('_', ' ').title():<15}{amount:>12}   "
              f"{COLOURS[level]}{level.upper()}{RESET}")

    high = analyzer.high_nutrients(report)
    print(f"\n  {BOLD}High in:{RESET} {', '.join(n.replace('_', ' ') for n in high) or 'nothing'}")

    text = product.get("ingredients_text_en") or product.get("ingredients_text")
    ingredients = analyzer.split_ingredients(text)
    if ingredients:
        print(f"  {BOLD}Ingredients ({len(ingredients)}):{RESET}")
        for item in ingredients[:8]:
            print(f"    - {item}")
        if len(ingredients) > 8:
            print(f"    ... and {len(ingredients) - 8} more")
    else:
        print("  No ingredient list available.")


def choose(products):
    if len(products) == 1:
        return products[0]
    for number, product in enumerate(products, start=1):
        print(f"  {number}. {display_name(product)}")
    answer = input("  Pick a number (Enter = 1): ").strip() or "1"
    if answer.isdigit() and 1 <= int(answer) <= len(products):
        return products[int(answer) - 1]
    print("  Not a valid number, showing number 1.")
    return products[0]


def run_once(text):
    text = text.strip()
    if not text:
        return
    # Regex: a barcode is 8 to 14 digits and nothing else.
    if text.isdigit() and not re.fullmatch(r"\d{8,14}", text):
        print("  That is not a valid barcode: barcodes have 8 to 14 digits.")
        return
    print(f"\nSearching Open Food Facts for '{text}'...")
    products = find_products(text)
    if not products:
        print("  No product with nutrition data found. Try another name or barcode.")
        return
    product = choose(products)

    # Search results leave out the ingredients, so fetch the full product.
    if not text.isdigit() and product.get("code"):
        try:
            product = (fetch_by_barcode(product["code"]) or [product])[0]
        except requests.RequestException:
            pass                            # keep what we have
    show_analysis(product)


def main():
    os.system("")                           # turns on colours in Windows terminals
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) > 1:
        run_once(" ".join(sys.argv[1:]))
        return

    print(f"{BOLD}Food Label Analyzer: Nutrition Analyzer demo{RESET}")
    while True:
        text = input("\nType a food name or barcode (q to quit): ")
        if text.strip().lower() in ("q", "quit", "exit"):
            break
        run_once(text)


if __name__ == "__main__":
    main()
