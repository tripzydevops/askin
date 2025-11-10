"""
FLEXIBLE HOTEL COMPARISON APP
==============================

QUICK START:
1. Install: pip install flask requests pandas
2. Run: python app.py
3. Open: http://localhost:5000

Your SerpAPI Key is already configured!
"""

import os
import re
import unicodedata
from functools import lru_cache
from flask import Flask, render_template, request, send_file, session
import uuid
import pandas as pd
import asyncio
import aiohttp

app = Flask(__name__)
app.secret_key = os.urandom(24)

# SerpAPI Key
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
if not SERPAPI_KEY:
    raise ValueError("SERPAPI_KEY environment variable must be set!")

SERPAPI_URL = "https://serpapi.com/search"

async def get_try_usd_rate():
    try:
        url = "https://api.exchangerate-api.com/v4/latest/TRY"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as r:
                r.raise_for_status()
                data = await r.json()
                return float(data["rates"]["USD"])
    except Exception:
        return 0.029

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
    except Exception:
        return None

async def fetch_hotel_data(session, hotel_name, check_in, check_out, location):
    """Fetch hotel data from SerpAPI for a single hotel."""
    hotel_title = hotel_name.strip()
    price_try = None

    serpapi_params = {
        "engine": "google_hotels",
        "q": f"{hotel_name} {location}",
        "check_in_date": check_in,
        "check_out_date": check_out,
        "adults": "2",
        "currency": "TRY",
        "gl": "tr",
        "hl": "en",
        "api_key": SERPAPI_KEY,
    }

    try:
        async with session.get(SERPAPI_URL, params=serpapi_params, timeout=25) as resp:
            if resp.status == 200:
                data = await resp.json()
                props = data.get('properties') or []

                if props:
                    best = sorted(
                        props,
                        key=lambda p: name_match_score(p.get('name') or '', hotel_name),
                        reverse=True
                    )[0]
                    hotel_title = best.get('name') or hotel_title

                    candidates = [
                        best.get('total_rate', {}).get('lowest') if isinstance(best.get('total_rate'), dict) else None,
                        best.get('rate_per_night', {}).get('lowest') if isinstance(best.get('rate_per_night'), dict) else None,
                        best.get('price'),
                        best.get('hotel_price'),
                    ]

                    for off in (best.get('offers') or []):
                        candidates.append(off.get('price'))
                        candidates.append(off.get('rate'))

                    for cand in candidates:
                        price_try = extract_try_price(cand)
                        if price_try:
                            break
    except Exception as e:
        print(f"Error fetching {hotel_name}: {e}")

    return {'name': hotel_title, 'price_try': price_try}


async def compare_hotels(hotels, check_in, check_out, location):
    """Compare hotel prices using SerpAPI asynchronously."""
    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_hotel_data(session, hotel, check_in, check_out, location)
            for hotel in hotels if hotel and hotel.strip()
        ]
        scraped_results = await asyncio.gather(*tasks)

    fx_rate = await get_try_usd_rate()
    final_results = []

    for res in scraped_results:
        price_try = res['price_try']
        if price_try is not None:
            price_usd = price_try * fx_rate if fx_rate else None
            final_results.append({
                'Hotel': res['name'],
                'Price per Night (₺)': f"₺{price_try:.2f}",
                'Price per Night ($)': f"${price_usd:.2f}" if price_usd is not None else 'N/A',
                'Status': '✓ Available'
            })
        else:
            final_results.append({
                'Hotel': res['name'],
                'Price per Night (₺)': 'N/A',
                'Price per Night ($)': 'N/A',
                'Status': '✗ No price'
            })

    return final_results

@app.route('/', methods=['GET', 'POST'])
def index():
    results = None
    best = None
    check_in = None
    check_out = None
    location = None
    hotels = ['', '', '', '']
    error = None

    if request.method == 'POST':
        check_in = request.form.get('check_in')
        check_out = request.form.get('check_out')
        location = request.form.get('location', '').strip()

        # Get up to 4 hotels
        hotels = [
            request.form.get('hotel1', '').strip(),
            request.form.get('hotel2', '').strip(),
            request.form.get('hotel3', '').strip(),
            request.form.get('hotel4', '').strip(),
        ]

        # Filter out empty hotels
        hotels_to_search = [h for h in hotels if h]

        # Basic input validation
        if not all([check_in, check_out, location, hotels_to_search]):
            error = "⚠️ Please fill in all required fields: dates, location, and at least one hotel."
        elif len(hotels_to_search) > 4:
            error = "⚠️ Maximum 4 hotels allowed."
        else:
            try:
                results = asyncio.run(compare_hotels(hotels_to_search, check_in, check_out, location))

                # Ensure 'temp' directory exists
                temp_dir = os.path.join(os.path.dirname(__file__), 'temp')
                os.makedirs(temp_dir, exist_ok=True)

                # Generate unique filename and save to session
                session['results_file'] = os.path.join(temp_dir, f'results_{uuid.uuid4()}.csv')
                pd.DataFrame(results).to_csv(session['results_file'], index=False, encoding='utf-8')

                best = get_best_hotel(results)
            except aiohttp.ClientError as e:
                error = f"⚠️ Network error: Could not connect to the hotel comparison service. {e}"
            except Exception as e:
                error = f"⚠️ An unexpected error occurred: {e}"

    return _render_index(
        results=results,
        best=best,
        check_in=check_in,
        check_out=check_out,
        location=location,
        hotels=hotels,
        error=error
    )


def _render_index(**kwargs):
    """Render the index template with common context."""
    return render_template('index.html', **kwargs)


def get_best_hotel(results):
    """Find the best hotel from a list of results."""
    avail = [r for r in results if r['Price per Night (₺)'] != 'N/A']
    if not avail:
        return None
    return min(avail, key=lambda x: float(x['Price per Night (₺)'].replace('₺', '').replace(',', '')))

@app.route('/download')
def download():
    results_file = session.get('results_file')
    if results_file and os.path.exists(results_file):
        try:
            return send_file(results_file, as_attachment=True, download_name='hotel_comparison.csv')
        except Exception as e:
            return str(e), 500
    return "No results available to download. Please run a comparison first.", 404

if __name__ == '__main__':
    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = "YOUR_IP"

    print("\n" + "="*60)
    print("🏨 FLEXIBLE HOTEL COMPARISON APP")
    print("="*60)
    print(f"✅ Server starting...")
    print(f"\n📱 ACCESS FROM THIS COMPUTER:")
    print(f"   http://localhost:5000")
    print(f"\n📱 ACCESS FROM YOUR PHONE (same WiFi):")
    print(f"   http://{local_ip}:5000")
    print(f"\n⚠️  Make sure your phone and computer are on the same WiFi!")
    print("="*60 + "\n")

    app.run(host='0.0.0.0', port=5000, debug=True)
