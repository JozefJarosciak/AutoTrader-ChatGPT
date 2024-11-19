import unicodedata
import textwrap
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Union
import openai
import requests
from bs4 import BeautifulSoup
from prettytable import PrettyTable

# ======================== CONFIGURATION ========================

# OpenAI API Configuration
OPENAI_API_KEY = ""   # Replace with your actual API key from https://platform.openai.com/api-keys
OPENAI_MODEL = "gpt-4o-mini"
OPENAI_MAX_TOKENS = 2000
OPENAI_TEMPERATURE = 0  # Deterministic output

# OpenAI Prompt Template
OPENAI_PROMPT_TEMPLATE = (
    "As an expert car advisor, select the top 10 best-value cars from the list below. "
    "Consider car configuration, mileage, year, price, and everything you know about cars made in that year. "
    "Provide a one-sentence reason for each and include what configuration made you choose this car (e.g., platinum edition, panoramic roof, etc.). "
    "Each entry in your output should include 'id', 'Rk' (Rank), 'Rsn' (Reason), and the car attributes 'Mk', 'Md', 'Yr', 'Mi', 'Pr', 'Cfg'.\n\n"
    "Cars:\n{car_descriptions}\n\n"
    "Return the response as a plain JSON array, without any Markdown formatting, code blocks, or extra text. "
    "Do not include anything outside the JSON array."
)

# Script Configuration
MAX_CARS_TO_SEND = 100  # Limit the number of cars sent to OpenAI
CACHE_DAYS = 7
CACHE_FOLDER = "autotrader-cars"
SEARCH_DELAY = 2  # Delay between searches in seconds
TABLE_MAX_WIDTH = 75

# User Input Defaults
DEFAULT_POSTAL_CODE = "L4C 5G6"
DEFAULT_RADIUS_KM = 60
DEFAULT_MAX_MILEAGE_KM = 60000
DEFAULT_YEAR_RANGE = "2017-2024"
DEFAULT_MAX_PRICE = 25000
DEFAULT_SEARCH_PROMPT = "Nissan Murano, Toyota RAV4, Honda CR-V"

# URL and Headers
URL_BASE = "https://www.autotrader.ca"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        " AppleWebKit/537.36 (KHTML, like Gecko)"
        " Chrome/74.0.3729.169 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": URL_BASE,
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
        # Limit the number of cars sent to GPT
        cars = cars[:MAX_CARS_TO_SEND]

        # Assign unique IDs to cars
        for idx, car in enumerate(cars):
            car['id'] = idx

        # Prepare data for GPT with consistent formatting
        car_descriptions = []
        for car in cars:
            try:
                car_descriptions.append({
                    "id": car.get("id"),
                    "Mk": car.get("make", ""),
                    "Md": car.get("model", ""),
                    "Yr": int(car.get("year", 0)),  # Ensure year is numeric
                    "Mi": int(car.get("mileage", 0)),  # Ensure mileage is numeric
                    "Pr": float(car.get("price", 0)),  # Ensure price is numeric
                    "Cfg": car.get("trim", ""),  # Use 'trim' as configuration
                })
            except ValueError as e:
                logging.error(f"Error formatting car for GPT: {car} - {e}")
                continue

        # Log prepared data
        logging.debug(f"Formatted cars for GPT: {json.dumps(car_descriptions, indent=2)}")

        # Format the prompt
        prompt = OPENAI_PROMPT_TEMPLATE.format(car_descriptions=json.dumps(car_descriptions))

        # Send prompt to OpenAI API
        response = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=OPENAI_MAX_TOKENS,
            temperature=OPENAI_TEMPERATURE,
        )

        # Parse GPT-4 response
        gpt_output = response["choices"][0]["message"]["content"]
        gpt_output = unicodedata.normalize('NFKC', gpt_output).strip()
        logging.debug(f"Raw GPT Output (repr): {repr(gpt_output)}")
        logging.debug(f"GPT Output: {gpt_output}")
        try:
            sorted_cars = json.loads(gpt_output)
        except json.JSONDecodeError as e:
            logging.error(f"JSON decoding error: {e}")
            logging.error(f"Offending JSON: {repr(gpt_output)}")
            sorted_cars = []  # Or handle appropriately

        # Map GPT output back to original car data
        for car in sorted_cars:
            original_car = next((c for c in cars if c['id'] == car['id']), None)
            if original_car:
                # Map GPT fields to your car data fields
                car.update({
                    "Rank": car.get("Rk", "N/A"),
                    "ChatGPT Reason": car.get("Rsn", "N/A"),
                    "make": original_car.get("make"),
                    "model": original_car.get("model"),
                    "year": original_car.get("year"),
                    "mileage": original_car.get("mileage"),
                    "price": original_car.get("price"),
                    "trim": original_car.get("trim"),
                    #"transmission": original_car.get("transmission"),
                    "drivetrain": original_car.get("drivetrain"),
                    "body_type": original_car.get("body_type"),
                    "engine": original_car.get("engine"),
                    "interior_color": original_car.get("interior_color"),
                    #"warranty": original_car.get("warranty", "N/A"),
                    "url": original_car.get("url"),
                    "color": original_car.get("color"),
                })
            else:
                logging.warning(f"Original car not found for ID: {car['id']}")

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
        #print(response.content)
        return BeautifulSoup(response.content, "html.parser")
    except requests.RequestException as e:
        logging.error(f"Error fetching search results for {make} {model}: {e}")
        return None




def get_car_page_urls(search_page: BeautifulSoup) -> List[str]:
    car_page_urls = []
    for a_tag in search_page.find_all('a', href=True):
        href = a_tag['href']
        if href.startswith('/a/'):
            car_page_urls.append(URL_BASE + href)
    car_page_urls = list(set(car_page_urls))
    logging.info(f"Found {len(car_page_urls)} car page URLs.")
    return car_page_urls



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


def fetch_car_page(url: str) -> Optional[Dict]:
    try:
        if is_url_cached(url):
            return load_url_cache(url)

        response = requests.get(url, timeout=15, headers=HEADERS)
        response.raise_for_status()
        car_data = extract_car_data(response.text, url)
        if car_data:
            save_url_cache(url, car_data)
        return car_data
    except requests.RequestException as e:
        logging.error(f"Error fetching car page {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error in fetch_car_page for {url}: {e}")
        return None




def get_car_pages(car_page_urls: List[str]) -> List[Dict]:
    car_pages = []
    for url in car_page_urls:
        car_data = fetch_car_page(url)
        if car_data:
            car_pages.append(car_data)
    return car_pages





def extract_car_data(car_page_html: str, car_page_url: str) -> dict:
    try:
        soup = BeautifulSoup(car_page_html, "html.parser")

        # Find the script tag containing 'window['ngVdpModel']'
        script_tag = soup.find('script', string=re.compile(r"window\['ngVdpModel'\]\s*="))
        if not script_tag:
            logging.error(f"No ngVdpModel script tag found in {car_page_url}")
            return {}

        # Extract the text of the script tag
        script_content = script_tag.string

        # Extract the JSON assigned to window['ngVdpModel']
        match = re.search(r"window\['ngVdpModel'\]\s*=\s*(\{.*?\});\s*\n", script_content, re.DOTALL)
        if not match:
            logging.error(f"Could not extract JSON data from ngVdpModel in {car_page_url}")
            return {}

        car_data_json = match.group(1)

        # Fix the problematic strings by escaping inner double quotes
        # Replace the unescaped double quotes in specific fields
        car_data_json_fixed = car_data_json.replace(
            'vdpDataLayerManager.addDealerWebsiteClickEvent("seller section");',
            'vdpDataLayerManager.addDealerWebsiteClickEvent(\\"seller section\\");'
        ).replace(
            'vdpDataLayerManager.addDealerWebsiteClickEvent("dealer section");',
            'vdpDataLayerManager.addDealerWebsiteClickEvent(\\"dealer section\\");'
        )

        # Now parse using json5
        car_data = json.loads(car_data_json_fixed)

        # Now extract the details as before
        hero = car_data.get("hero", {})
        specifications = car_data.get("specifications", {}).get("specs", [])
        gallery = car_data.get("gallery", {}).get("items", [])

        # Create a dictionary of specifications
        specs_dict = {spec.get('key'): spec.get('value') for spec in specifications}

        # Extract relevant details
        car_details = {
            "url": car_page_url,
            "make": hero.get("make", ""),
            "model": hero.get("model", ""),
            "year": hero.get("year", ""),
            "trim": hero.get("trim", ""),
            "price": hero.get("price", "").replace(",", "").replace("$", ""),
            "mileage": hero.get("mileage", "").replace(",", "").replace(" km", ""),
            #"transmission": specs_dict.get("Transmission", ""),
            "drivetrain": specs_dict.get("Drivetrain", ""),
            "color": specs_dict.get("Exterior Colour", ""),
            "interior_color": specs_dict.get("Interior Colour", ""),
            #"body_type": specs_dict.get("Body Type", ""),
            "engine": specs_dict.get("Engine", ""),
            "doors": specs_dict.get("Doors", ""),
            "fuel_type": specs_dict.get("Fuel Type", ""),
            "gallery_urls": [item.get("galleryUrl") for item in gallery if item.get("type") == "Photo"],
        }

        return car_details

    except json.JSONDecodeError as e:
        logging.error(f"JSON parsing error for {car_page_url}: {e}")
        return {}
    except Exception as e:
        logging.error(f"Unexpected error while extracting car details from {car_page_url}: {e}")
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
            # Extract and clean data
            price_raw = car_data.get("price", "0").replace(",", "").replace("$", "")
            mileage_raw = car_data.get("mileage", "0").replace(",", "").replace(" km", "")
            price = float(price_raw) if price_raw else 0
            mileage = int(mileage_raw) if mileage_raw else 0
            year = int(car_data.get("year", 0))

            # Log the parsed data
            logging.debug(f"Evaluating car: {car_data.get('make')} {car_data.get('model')} - Price: {price}, Mileage: {mileage}, Year: {year}")

            # Filtering logic
            if price > max_price:
                logging.debug(f"Excluded due to price: {price} > {max_price}")
                continue
            if mileage > max_mileage_km:
                logging.debug(f"Excluded due to mileage: {mileage} > {max_mileage_km}")
                continue
            if not (year_start <= year <= year_end):
                logging.debug(f"Excluded due to year: {year} not in range {year_start}-{year_end}")
                continue

            # If all criteria match, add the car
            filtered_cars.append(car_data)
        except ValueError as e:
            logging.error(f"Error parsing car data: {car_data} - {e}")
            continue  # Skip cars with invalid data

    logging.info(f"Filtered {len(filtered_cars)} cars based on criteria.")
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
        "Trim",
        "Year",
        "Mileage",
        "Price",
        "Color",
        "Interior Color",
        #"Transmission",
        "Drivetrain",
        #"Body Type",
        "Engine",
        "ChatGPT Reason",
        "URL",
    ]

    # Set max width for "ChatGPT Reason" column to 200 characters
    table.max_width["ChatGPT Reason"] = TABLE_MAX_WIDTH

    for car in cars:
        mileage = car.get("mileage", "N/A")
        price = car.get("price", "N/A")
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

        # Wrap the "ChatGPT Reason" text to 200 characters
        chatgpt_reason = car.get("ChatGPT Reason", "N/A")
        if chatgpt_reason != "N/A":
            chatgpt_reason = textwrap.fill(chatgpt_reason, width=200)

        table.add_row(
            [
                car.get("Rank", "N/A"),
                car.get("make", "N/A"),
                car.get("model", "N/A"),
                car.get("trim", "N/A"),
                car.get("year", "N/A"),
                mileage,
                price,
                car.get("color", "N/A"),
                car.get("interior_color", "N/A"),
                #car.get("transmission", "N/A"),
                car.get("drivetrain", "N/A"),
                #car.get("body_type", "N/A"),
                car.get("engine", "N/A"),
                chatgpt_reason,
                car.get("url", "N/A"),
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

            for car_data in car_pages:
                if car_data and 'url' in car_data:
                    car_data_list.append(car_data)

        # Introduce a delay before the next search
        time.sleep(SEARCH_DELAY)

    if not car_data_list:
        logging.error("No car data was collected. Exiting.")
        return

    logging.info(f"Total cars collected before filtering: {len(car_data_list)}")
    #for car in car_data_list:
    #logging.info(f"Car data: {car}")

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
    exit(0)

    # Configure logging
    logging.basicConfig(level=logging.INFO)

    # Sample URL for testing
    sample_url = "https://www.autotrader.ca/a/nissan/murano/oakville/ontario/5_64261843_20170306181529965/"
    response = requests.get(sample_url, headers=HEADERS)
    if response.status_code == 200:
        car_data = extract_car_data(response.text, sample_url)
        print(json.dumps(car_data, indent=2))
    else:
        logging.error(f"Failed to fetch the page. Status code: {response.status_code}")
