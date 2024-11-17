import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Union
from concurrent.futures import ThreadPoolExecutor, as_completed

import openai
import requests
from bs4 import BeautifulSoup
from prettytable import PrettyTable

# ======================== CONFIGURATION ========================

# OpenAI API Configuration
OPENAI_API_KEY = ""  # Replace with your actual API key from https://platform.openai.com/api-keys
OPENAI_MODEL = "gpt-4"
OPENAI_MAX_TOKENS = 1500
OPENAI_TEMPERATURE = 0  # Deterministic output

# OpenAI Prompt Template
OPENAI_PROMPT_TEMPLATE = (
    "As an expert car advisor, select the top 10 best-value cars from the list below. Consider car configuration, mileage, year, price and everything you know about the car made in that year."
    "Provide a one-sentence reason for each and include what configuration made you choose this car (e.g., platinum edition, panoramic roof, etc.). Each entry in your output should include 'id', 'Rk' (Rank), 'Rsn' (Reason), "
    "and the car attributes 'Mk', 'Md', 'Yr', 'Mi', 'Pr', 'Cfg'.\n\n"
    "Cars:\n{car_descriptions}\n\n"
    "Use the same field names as provided, including 'id'. Do not modify the 'id'. Provide your output as a JSON array."
)


# Script Configuration
MAX_CARS_TO_SEND = 50  # Limit the number of cars sent to OpenAI
CACHE_DAYS = 7
CACHE_FOLDER = "autotrader-cars"
SEARCH_DELAY = 2  # Delay between searches in seconds

# User Input Defaults
DEFAULT_POSTAL_CODE = "M5G 1N8"
DEFAULT_RADIUS_KM = 50
DEFAULT_MAX_MILEAGE_KM = 50000
DEFAULT_YEAR_RANGE = "2019-2024"
DEFAULT_MAX_PRICE = 25000
DEFAULT_SEARCH_PROMPT = "Nissan Murano, Mazda CX-5, Toyota RAV4, Honda CR-V"

# URL and Headers
URL_BASE = "https://www.autotrader.ca"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        " AppleWebKit/537.36 (KHTML, like Gecko)"
        " Chrome/74.0.3729.169 Safari/537.36"
    )
}

# Logging Configuration
LOG_LEVEL = logging.INFO  # Set to logging.DEBUG for more verbose output
logging.basicConfig(level=LOG_LEVEL)

# Create cache directory if it doesn't exist
os.makedirs(CACHE_FOLDER, exist_ok=True)

# ================================================================


def configure_openai_api():
    if OPENAI_API_KEY:
        openai.api_key = OPENAI_API_KEY
    else:
        raise ValueError("OpenAI API key not provided. Please set the 'OPENAI_API_KEY' in the configuration section.")


def sort_cars_with_gpt(cars: List[Dict]) -> List[Dict]:
    try:
        cars = cars[:MAX_CARS_TO_SEND]  # Limit the number of cars

        # Assign unique IDs to cars
        for idx, car in enumerate(cars):
            car['id'] = idx

        # Prepare the data for GPT without URLs and with shortened field names
        car_descriptions = [
            {
                "id": car.get("id"),
                "Mk": car.get("make", ""),
                "Md": car.get("model", ""),
                "Yr": car.get("year", ""),
                "Mi": car.get("mileage", ""),
                "Pr": car.get("price", ""),
                "Cfg": car.get("vehicle_configuration", "")  # Add configuration field
            }
            for car in cars
        ]

        # Format the prompt with car descriptions
        prompt = OPENAI_PROMPT_TEMPLATE.format(car_descriptions=json.dumps(car_descriptions))

        # Send the prompt to OpenAI API
        response = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=OPENAI_MAX_TOKENS,
            temperature=OPENAI_TEMPERATURE
        )

        # Parse GPT-4 response
        gpt_output = response["choices"][0]["message"]["content"]
        logging.debug(f"GPT Output: {gpt_output}")  # For debugging
        sorted_cars = json.loads(gpt_output)  # Expecting JSON output

        # Merge URLs and other fields back into the sorted cars and map back field names
        for car in sorted_cars:
            car["id"] = car.get("id", "")
            car["Make"] = car.pop("Mk", "")
            car["Model"] = car.pop("Md", "")
            car["Year"] = car.pop("Yr", "")
            car["Mileage"] = car.pop("Mi", "")
            car["Price"] = car.pop("Pr", "")
            car["Configuration"] = car.pop("Cfg", "")  # Map configuration field
            car["Rank"] = car.pop("Rk", "")
            car["ChatGPT Reason"] = car.pop("Rsn", "")


    # Find the original car data based on 'id'
            original_car = next((c for c in cars if c['id'] == car['id']), None)
            if original_car:
                # Merge additional fields back into car data
                car["URL"] = original_car.get("url")
                car["Color"] = original_car.get("color")
                car["Configuration"] = original_car.get("vehicle_configuration")
            else:
                logging.warning(f"Could not find original car data for id {car['id']}")

        return sorted_cars
    except Exception as e:
        logging.error(f"Error in sorting cars with GPT: {e}")
        return []


def search_autotrader(make: str, model: str, postal_code: str, radius_km: int = 100, display_results: int = 100) -> Optional[BeautifulSoup]:
    try:
        url = (
                f"{URL_BASE}/cars/?"
                + "&".join(
            [
                f"loc={postal_code}",
                f"make={make}",
                f"mdl={model}",
                f"prx={radius_km}",
                f"rcp={display_results}"
            ]
        ).replace(" ", "%20")
        )
        response = requests.get(url, timeout=15, headers=HEADERS)
        response.raise_for_status()
        return BeautifulSoup(response.content, "html.parser")
    except requests.RequestException as e:
        logging.error(f"Error fetching search results for {make} {model}: {e}")
        return None


def get_car_page_urls(search_page: BeautifulSoup) -> List[str]:
    tags = search_page.find_all("a", attrs={"class": ["detail-price-area", "inner-link"]})
    car_page_urls = [URL_BASE + tag.get("href") for tag in tags if tag.get("href")]
    return list(set(car_page_urls))


def is_url_cached(url: str) -> bool:
    cache_file = os.path.join(CACHE_FOLDER, re.sub(r'[^\w]', '_', url) + ".json")
    if os.path.exists(cache_file):
        last_modified_time = datetime.fromtimestamp(os.path.getmtime(cache_file))
        if datetime.now() - last_modified_time < timedelta(days=CACHE_DAYS):
            return True
    return False


def save_url_cache(url: str, car_data: Dict):
    cache_file = os.path.join(CACHE_FOLDER, re.sub(r'[^\w]', '_', url) + ".json")
    with open(cache_file, 'w') as f:
        json.dump(car_data, f)


def load_url_cache(url: str) -> Optional[Dict]:
    cache_file = os.path.join(CACHE_FOLDER, re.sub(r'[^\w]', '_', url) + ".json")
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            return json.load(f)
    return None


def fetch_car_page(url: str) -> Optional[Union[BeautifulSoup, Dict]]:
    if is_url_cached(url):
        return load_url_cache(url)

    try:
        response = requests.get(url, timeout=15, headers=HEADERS)
        response.raise_for_status()
        return BeautifulSoup(response.content, "html.parser")
    except requests.RequestException as e:
        logging.error(f"Error fetching car page {url}: {e}")
        return None


def get_car_pages(car_page_urls: List[str]) -> List[Union[BeautifulSoup, Dict]]:
    car_pages = []
    with ThreadPoolExecutor() as executor:
        future_to_url = {executor.submit(fetch_car_page, url): url for url in car_page_urls}
        for future in as_completed(future_to_url):
            car_page = future.result()
            if car_page:
                car_pages.append(car_page)
    return car_pages


def extract_car_data(car_page: Union[BeautifulSoup, Dict]) -> Dict:
    if isinstance(car_page, Dict):
        return car_page

    scripts = car_page.find_all("script", {"type": "application/ld+json"})
    if not scripts:
        return {}

    # Find the script that contains the car data
    for script in scripts:
        try:
            json_data = json.loads(script.string)
            if json_data.get("@type") == "Car":
                car_data = {
                    "url": json_data.get("url"),
                    "name": json_data.get("name"),
                    "make": json_data.get("brand", {}).get("name", ""),
                    "model": json_data.get("model"),
                    "year": json_data.get("vehicleModelDate"),
                    "color": json_data.get("color"),
                    "mileage": json_data.get("mileageFromOdometer", {}).get("value"),
                    "price": json_data.get("offers", {}).get("price"),
                    "vehicle_configuration": json_data.get("vehicleConfiguration"),
                }
                return {k: v for k, v in car_data.items() if v is not None}
        except json.JSONDecodeError:
            continue
    return {}


def filter_and_rank_cars(car_data_list: List[Dict], max_mileage_km: int, year_range: str, max_price: int) -> List[Dict]:
    try:
        if '-' in year_range:
            year_start, year_end = map(int, year_range.split('-'))
        else:
            year_start = year_end = int(year_range)
    except ValueError:
        logging.error("Invalid year range format. Please enter a valid range like '2019-2024'.")
        return []

    filtered_cars = []

    for car_data in car_data_list:
        try:
            mileage = int(car_data.get("mileage", 0))
            year = int(car_data.get("year", 0))
            price = float(car_data.get("price", 0))
        except ValueError:
            continue  # Skip cars with invalid data

        if (
                (max_mileage_km == 0 or mileage <= max_mileage_km)
                and (year_start <= year <= year_end)
                and (max_price == 0 or price <= max_price)
        ):
            filtered_cars.append(car_data)

    return sorted(filtered_cars, key=lambda car: (car.get("price", 0), car.get("mileage", 0)))


def display_cars_table_with_reasons(cars: List[Dict], title: str):
    print()
    print(f"{title}:")
    print()

    if not cars:
        print("No results found.")
        return

    table = PrettyTable()
    table.field_names = [
        "Rank",
        "Make",
        "Model",
        "Year",
        "Mileage",
        "Price",
        "Color",
        "ChatGPT Reason",
        "Configuration",
        "URL",
    ]

    for car in cars:
        mileage = car.get("Mileage", "N/A")
        price = car.get("Price", "N/A")
        if isinstance(mileage, (int, float, str)) and mileage != "N/A":
            try:
                mileage = f"{float(mileage):,.0f} km"
            except ValueError:
                mileage = str(mileage)
        else:
            mileage = str(mileage)
        if isinstance(price, (int, float, str)) and price != "N/A":
            try:
                price = f"${float(price):,.2f}"
            except ValueError:
                price = str(price)
        else:
            price = str(price)

        table.add_row(
            [
                car.get("Rank", "N/A"),
                car.get("Make", "N/A"),
                car.get("Model", "N/A"),
                car.get("Year", "N/A"),
                mileage,
                price,
                car.get("Color", "N/A"),
                car.get("ChatGPT Reason", "N/A"),
                car.get("Configuration", "N/A"),
                car.get("URL", "N/A"),
            ]
        )

    print(table)


def main():
    configure_openai_api()  # Configure OpenAI API

    # User input with default fallback
    postal_code = input(f"Enter your postal code [{DEFAULT_POSTAL_CODE}]: ") or DEFAULT_POSTAL_CODE
    radius_km = input(f"Enter the search radius in kilometers [{DEFAULT_RADIUS_KM}]: ") or DEFAULT_RADIUS_KM
    radius_km = int(radius_km)
    max_mileage_km = input(f"Enter the maximum mileage in kilometers [{DEFAULT_MAX_MILEAGE_KM}]: ") or DEFAULT_MAX_MILEAGE_KM
    max_mileage_km = int(max_mileage_km)
    year_range = input(f"Enter the year range [{DEFAULT_YEAR_RANGE}]: ") or DEFAULT_YEAR_RANGE
    max_price = input(f"Enter the maximum price in CAD [{DEFAULT_MAX_PRICE}]: ") or DEFAULT_MAX_PRICE
    max_price = int(max_price)
    search_prompt = input(f"Enter the makes and models you want to search for (comma-separated) [{DEFAULT_SEARCH_PROMPT}]: ") or DEFAULT_SEARCH_PROMPT

    car_search_terms = [term.strip() for term in search_prompt.split(',')]
    car_data_list = []

    for term in car_search_terms:
        make_model = term.split(maxsplit=1)
        if len(make_model) == 2:
            make, model = make_model
        else:
            make = make_model[0]
            model = ''

        logging.info(f"Searching for {make} {model} within {radius_km} km of {postal_code}")
        search_page = search_autotrader(make, model, postal_code, radius_km)

        if search_page:
            car_page_urls = get_car_page_urls(search_page)

            car_pages = get_car_pages(car_page_urls)
            if not car_pages:
                logging.warning(f"No pages found for {make} {model}. Skipping to next term.")
                continue

            for car_page in car_pages:
                car_data = extract_car_data(car_page)
                if 'url' in car_data:
                    save_url_cache(car_data['url'], car_data)
                    car_data_list.append(car_data)

        # Introduce a delay before the next search
        time.sleep(SEARCH_DELAY)

    if not car_data_list:
        logging.error("No car data was collected. Exiting.")
        return

    # Filter and rank cars based on user criteria
    filtered_cars = filter_and_rank_cars(car_data_list, max_mileage_km, year_range, max_price)

    if not filtered_cars:
        logging.error("No cars match your criteria. Exiting.")
        return

    # Limit the number of cars to prevent exceeding context length
    cars_to_send = filtered_cars[:MAX_CARS_TO_SEND]

    # Send cars to GPT for sorting and reasons
    sorted_cars_with_reasons = sort_cars_with_gpt(cars_to_send)

    # Display the sorted cars with reasons
    display_cars_table_with_reasons(sorted_cars_with_reasons, "Top Car Recommendations")


if __name__ == "__main__":
    main()
