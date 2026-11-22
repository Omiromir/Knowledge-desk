import json


def load_faq(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            faq_data = json.load(file)
        return faq_data
    except FileNotFoundError:
        print(f"Error: The file at {path} was not found.")
        return {}
    except json.JSONDecodeError:
        print(f"Error: The file at {path} is not a valid JSON file.")
        return {}


def save_faq(path, faq_data):
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(faq_data, file, indent=4)
        print(f"{len(faq_data)} FAQs successfully saved to {path}.")
        for i, faq in enumerate(faq_data):
            print(f"FAQ {i + 1}: {faq['question']} - {faq['answer']}")
    except Exception as e:
        print(f"Error: Could not save FAQ data to {path}. {e}")
