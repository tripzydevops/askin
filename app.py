"""
FLEXIBLE HOTEL COMPARISON APP
==============================

QUICK START:
1. Install: pip install -r requirements.txt
2. Create .env file with SERPAPI_KEY
3. Run: python app.py
4. Open: http://localhost:5000
"""

import os
import re
import asyncio
import logging
import unicodedata
from functools import lru_cache
from flask import Flask, render_template, request, send_file
import aiohttp
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SERPAPI_KEY = os.environ.get("SERPAPI_KEY")
if not SERPAPI_KEY:
    logging.critical("FATAL: SERPAPI_KEY environment variable not set.")
    raise ValueError("SERPAPI_KEY environment variable not set.")

SERPAPI_URL = "https://serpapi.com/search"

@lru_cache(maxsize=16)
def get_try_usd_rate():
    try:
        import requests
        url = "https://api.exchangerate-api.com/v4/latest/TRY"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        return float(data["rates"]["USD"])
    except requests.exceptions.RequestException as e:
        logging.error(f"Could not fetch currency conversion rate: {e}")
        return 0.029 # Fallback rate

def normalize_txt(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().strip()
    return re.sub(r"\s+", " ", s)

def name_match_score(candidate: str, target: str) -> int:
    c = normalize_txt(candidate)
    t = normalize_txt(target)
    score = 0
    for tok in t.split():
        if tok and tok in c:
            score += 1
    return score

def extract_try_price(value):
    if value is None:
        return None
    s = str(value)
    s = s.replace("₺", "").replace("$", "").replace(",", "").replace("TL", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    try:
        return float(m.group(1)) if m else None
    except (ValueError, TypeError):
        return None

async def fetch_hotel_data(session, hotel_name, check_in, check_out, location):
    price_try = None
    hotel_title = hotel_name.strip()
    status = '✗ Error'

    serpapi_params = {
        "engine": "google_hotels",
        "q": f"{hotel_name} in {location}",
        "check_in_date": check_in,
        "check_out_date": check_out,
        "adults": "2",
        "currency": "TRY",
        "gl": "tr",
        "hl": "en",
        "api_key": SERPAPI_KEY,
    }

    try:
        async with session.get(SERPAPI_URL, params=serpapi_params, timeout=30) as resp:
            resp.raise_for_status()
            data = await resp.json()

            if data.get("error"):
                logging.error(f"SerpAPI error for '{hotel_name}': {data['error']}")
                status = '✗ API Error'
            else:
                props = data.get('properties') or []
                if props:
                    best = sorted(
                        props,
                        key=lambda p: name_match_score(p.get('name') or '', hotel_name),
                        reverse=True
                    )[0]
                    hotel_title = best.get('name') or hotel_title

                    candidates = [
                        best.get('total_rate', {}).get('lowest'),
                        best.get('rate_per_night', {}).get('lowest'),
                        best.get('price'),
                        best.get('hotel_price'),
                    ]
                    candidates.extend(off.get('price') for off in best.get('offers', []))

                    for cand in candidates:
                        price_try = extract_try_price(cand)
                        if price_try:
                            status = '✓ Available'
                            break
                    else:
                        status = '✗ No price'
                else:
                    status = '✗ Not found'

    except aiohttp.ClientError as e:
        logging.error(f"Network error fetching '{hotel_name}': {e}")
        status = '✗ Network Error'
    except asyncio.TimeoutError:
        logging.error(f"Timeout fetching '{hotel_name}'")
        status = '✗ Timeout'
    except Exception as e:
        logging.error(f"Unexpected error fetching '{hotel_name}': {e}", exc_info=True)

    result = {
        'Hotel': hotel_title,
        'Price per Night (₺)': 'N/A',
        'Price per Night ($)': 'N/A',
        'Status': status
    }

    if price_try is not None:
        fx = get_try_usd_rate()
        price_usd = price_try * fx if fx else None
        result.update({
            'Price per Night (₺)': f"₺{price_try:.2f}",
            'Price per Night ($)': f"${price_usd:.2f}" if price_usd else 'N/A',
        })

    return result

async def compare_hotels_async(hotels, check_in, check_out, location):
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_hotel_data(session, name, check_in, check_out, location) for name in hotels if name and name.strip()]
        return await asyncio.gather(*tasks)

@app.route('/', methods=['GET', 'POST'])
def index():
    results, best, check_in, check_out, location, error = None, None, None, None, None, None
    hotels = ['', '', '', '']

    if request.method == 'POST':
        check_in = request.form.get('check_in')
        check_out = request.form.get('check_out')
        location = request.form.get('location', '').strip()
        hotels = [request.form.get(f'hotel{i}', '').strip() for i in range(1, 5)]
        hotels_to_search = [h for h in hotels if h]

        if not all([check_in, check_out]):
            error = "⚠️ Please provide both check-in and check-out dates."
        elif not location:
            error = "⚠️ Please provide a location."
        elif not hotels_to_search:
            error = "⚠️ Please enter at least one hotel name."
        else:
            try:
                results = asyncio.run(compare_hotels_async(hotels_to_search, check_in, check_out, location))
                if results:
                    pd.DataFrame(results).to_csv('latest_results.csv', index=False, encoding='utf-8')
                    avail = [r for r in results if r['Status'] == '✓ Available']
                    if avail:
                        best = min(avail, key=lambda x: float(re.sub(r'[^\d.]', '', x['Price per Night (₺)'])))
            except Exception as e:
                logging.error(f"Error during hotel comparison: {e}", exc_info=True)
                error = "⚠️ An unexpected error occurred. Please try again."

    return render_template('index.html', **locals())

@app.route('/download')
def download():
    try:
        return send_file('latest_results.csv', as_attachment=True, download_name='hotel_comparison.csv')
    except FileNotFoundError:
        return "No results available to download. Please run a comparison first.", 404

if __name__ == '__main__':
    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        local_ip = "127.0.0.1"

    logging.info("Starting Flexible Hotel Comparison App")
    print("\n" + "="*60)
    print("🏨 FLEXIBLE HOTEL COMPARISON APP")
    print("="*60)
    print("✅ Server starting...")
    print("\n➡️  Access from this computer: http://localhost:5000")
    print(f"➡️  Access from your phone (on the same WiFi): http://{local_ip}:5000")
    print("\n" + "="*60 + "\n")

    app.run(host='0.0.0.0', port=5000, debug=False)
