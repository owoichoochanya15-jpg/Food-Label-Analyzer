class FoodProduct:
    def __init__(self, name="", barcode="", ingredients="", nutrition=None):
        self.name = name
        self.barcode = barcode
        self.ingredients = ingredients
        self.nutrition = nutrition or {}

    def display_info(self):
        print("\n--- Food Product ---")
        print("Name:", self.name)
        print("Barcode:", self.barcode)
        print("Ingredients:", self.ingredients)

        print("\n--- Nutrition Information ---")

        important_nutrients = [
            "energy-kcal_100g",
            "fat_100g",
            "saturated-fat_100g",
            "carbohydrates_100g",
            "sugars_100g",
            "fiber_100g",
            "proteins_100g",
            "salt_100g",
            "sodium_100g"
        ]

        for nutrient in important_nutrients:
            if nutrient in self.nutrition:
                print(nutrient, ":", self.nutrition[nutrient])
