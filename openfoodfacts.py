import requests
import re
from food_product import FoodProduct


class OpenFoodFactsClient:

    def get_product(self, barcode):

        # Validate barcode
        if not re.fullmatch(r"\d{8,14}", barcode):
            print("Invalid barcode. Please enter 8 to 14 digits.")
            return None

        url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"

        headers = {
            "User-Agent": "FoodLabelAnalyzer/1.0 (student project)"
        }

        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=10
            )

            if response.status_code == 404:
                print("Product not found.")
                return None

            response.raise_for_status()

            data = response.json()

            if data.get("status") != 1:
                print("Product not found.")
                return None

            product = data.get("product", {})

            name = product.get(
                "product_name",
                "Unknown"
            )

            ingredients = product.get(
                "ingredients_text",
                "Not available"
            )

            nutrition = product.get(
                "nutriments",
                {}
            )

            return FoodProduct(
                name=name,
                barcode=barcode,
                ingredients=ingredients,
                nutrition=nutrition
            )

        except requests.RequestException as e:
            print("Could not connect to OpenFoodFacts.")
            print("Error:", e)
            return None

        except ValueError:
            print("invalid data recieved from the API.")
            return None
            

