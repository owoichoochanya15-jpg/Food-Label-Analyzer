from openfoodfacts import OpenFoodFactsClient


def main():

    print("=== FOOD LABEL ANALYZER ===")

    barcode = input("Enter food barcode: ").strip()

    client = OpenFoodFactsClient()

    food = client.get_product(barcode)

    if food:
        food.display_info()


if __name__ == "__main__":
    main()