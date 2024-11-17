
# AutoTrader AI Assistant

AutoTrader AI Assistant is a Python script that simplifies car searching and selection using data from AutoTrader and OpenAI's GPT-4. This tool helps you find the best-value cars based on your preferences and leverages AI to rank and provide recommendations with reasons for the top picks. It saves time and effort by automating car searches, filtering, and ranking.

---

## Features
- **Automated Car Search**: Searches AutoTrader for cars based on your input criteria like make, model, price range, mileage, and year range.
- **AI-Powered Recommendations**: Uses GPT-4 to rank the best-value cars and provide reasons for each recommendation.
- **Cached Results**: Saves and reuses data to avoid unnecessary API calls and reduce search time.

---

## Minimum Requirements
- Python 3.8 or above
- Dependencies listed in `requirements.txt`:
  - `openai`
  - `requests`
  - `bs4`
  - `prettytable`
- An OpenAI API Key (GPT-4 model)

---

## How to Use
1. **Clone the Repository**:
    ```bash
    git clone <your-repository-url>
    cd <your-repository-folder>
    ```

2. **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3. **Set Up OpenAI API Key**:
   - Open the `autotrader.py` file and replace the placeholder `OPENAI_API_KEY` with your OpenAI API key.

4. **Run the Script**:
    ```bash
    python autotrader.py
    ```

5. **Provide Search Parameters**:
   - The script will prompt you to enter details like postal code, search radius, maximum mileage, year range, maximum price, and the makes and models of the cars you're looking for.

6. **View Recommendations**:
   - The script will display a ranked table of the best cars with AI-provided reasons for each recommendation.

---

## Screenshots
*Add screenshots of the script in action here.*

---

## How This Script Makes Car Buying Easier
- **Time-Saving**: Automates the process of searching, filtering, and ranking cars.
- **AI Expertise**: Provides intelligent recommendations, ensuring you get the best value for your money.
- **Customizable**: Allows you to tailor search criteria to your specific needs.
- **Convenient Display**: Presents results in a user-friendly table format with direct URLs to car listings.

---

## License
This project is open-source and available under the [MIT License](LICENSE).

---

Happy car hunting!
