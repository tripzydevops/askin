**Bug Report**

**File:** `app.py`
**Line:** 91

**Description:**
The original query construction in the `compare_hotels` function concatenated the hotel name and location with a simple space (e.g., `f"{hotel_name} {location}"`). This could lead to ambiguous search queries for the SerpAPI. For example, searching for "Grand Hotel" in "New York" would produce "Grand Hotel New York," which is acceptable, but searching for "Grand Hotel New York" in "New York" would result in the redundant and potentially confusing "Grand Hotel New York New York."

**Impact:**
This could lead to inaccurate or irrelevant search results from the SerpAPI, degrading the user experience.

**Fix:**
The fix is to change the query to a more specific format: `f"{hotel_name} in {location}"`. This provides clearer instructions to the API and improves the quality of the search results.

---

**File:** `templates/index.html`
**Lines:** 5, 131-132

**Description:**
The title and header of the main page were hardcoded to "Balıkesir Hotel Comparison," which did not reflect the user's actual search query.

**Impact:**
This created a confusing and unprofessional user experience, as the page title and header did not match the user's input.

**Fix:**
The fix involves replacing the hardcoded text with a dynamic Jinja2 expression (`{{ location.title() if location else 'Hotel' }}`) that displays the user's searched location. Additionally, the form was missing input fields for the location and hotels, which have now been added.
