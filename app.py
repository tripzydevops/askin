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
import time
import re
import unicodedata
import uuid
from datetime import datetime
from functools import lru_cache
from flask import Flask, render_template, request, send_file, jsonify, session, after_this_request
import asyncio
import aiohttp
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ✅ YOUR API KEY (already configured)
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
if not SERPAPI_KEY:
    raise ValueError("SERPAPI_KEY not found in .env. Did you create a .env file?")

SERPAPI_URL = "https://serpapi.com/search"

@lru_cache(maxsize=16)
def get_try_usd_rate():
    try:
        url = "https://api.exchangerate-api.com/v4/latest/TRY"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
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
    """Asynchronously fetch data for a single hotel."""
    if not hotel_name or not hotel_name.strip():
        return None

    price_try = None
    hotel_title = hotel_name.strip()

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

    if price_try is not None:
        try:
            fx = get_try_usd_rate()
            price_usd = price_try * fx
        except Exception:
            price_usd = None

        return {
            'Hotel': hotel_title,
            'Price per Night (₺)': f"₺{price_try:.2f}",
            'Price per Night ($)': f"${price_usd:.2f}" if price_usd is not None else 'N/A',
            'Status': '✓ Available'
        }
    else:
        return {
            'Hotel': hotel_title,
            'Price per Night (₺)': 'N/A',
            'Price per Night ($)': 'N/A',
            'Status': '✗ No price'
        }

async def compare_hotels_async(hotels, check_in, check_out, location):
    """Compare hotel prices asynchronously."""
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_hotel_data(session, hotel, check_in, check_out, location) for hotel in hotels]
        results = await asyncio.gather(*tasks)
        return [res for res in results if res]

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

        if not check_in or not check_out:
            error = "⚠️ Please provide both check-in and check-out dates."
        elif not location:
            error = "⚠️ Please provide a location."
        elif not hotels_to_search:
            error = "⚠️ Please enter at least one hotel name."
        elif len(hotels_to_search) > 4:
            error = "⚠️ Maximum 4 hotels allowed."
        else:
            try:
                if 'session_id' not in session:
                    session['session_id'] = str(uuid.uuid4())

                results = asyncio.run(compare_hotels_async(hotels_to_search, check_in, check_out, location))

                session['results_df'] = pd.DataFrame(results).to_dict('records')

                avail = [r for r in results if r['Price per Night (₺)'] != 'N/A']
                if avail:
                    best = min(
                        avail,
                        key=lambda x: float(x['Price per Night (₺)'].replace('₺', '').replace(',', ''))
                    )
            except Exception as e:
                error = f"⚠️ Error: {str(e)}"

    return render_template(
        'index.html',
        results=results,
        best=best,
        check_in=check_in,
        check_out=check_out,
        location=location,
        hotels=hotels,
        error=error
    )

@app.route('/download')
def download():
    if 'results_df' not in session:
        return "No results available to download. Please run a comparison first.", 404

    df_data = session.pop('results_df', None)
    if not df_data:
        return "No results data found in session.", 404

    df = pd.DataFrame(df_data)

    # Generate a temporary, unique filename
    session_id = session.get('session_id', 'default')
    filename = f"hotel_comparison_{session_id}_{int(time.time())}.csv"
    filepath = os.path.join('/tmp', filename)

    df.to_csv(filepath, index=False, encoding='utf-8')

    @after_this_request
    def remove_file(response):
        try:
            os.remove(filepath)
        except Exception as e:
            app.logger.error(f"Error removing temporary file {filepath}: {e}")
        return response

    return send_file(filepath, as_attachment=True, download_name='hotel_comparison.csv')

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
