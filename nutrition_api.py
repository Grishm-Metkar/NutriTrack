"""Small, defensive client for Open Food Facts' public read APIs."""

import json
import math
import re
import threading
import time
from collections import deque
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen


class NutritionServiceError(Exception):
    """Raised when a nutrition lookup cannot be completed safely."""


_USER_AGENT = "NutriTrack/1.0 (https://world.openfoodfacts.org/)"
_SEARCH_FIELDS = "code,product_name,generic_name,brands,nutriments,categories_tags,image_front_url"
_PRODUCT_FIELDS = "code,product_name,generic_name,brands,nutriments,categories_tags"
_CACHE_TTL_SECONDS = 300
_MAX_SEARCHES_PER_MINUTE = 8
_search_cache = {}
_request_times = deque()
_request_lock = threading.Lock()


def _read_json(url):
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=6) as response:
            payload = response.read(2_000_000)
        return json.loads(payload.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NutritionServiceError(
            "The food database is temporarily unavailable. Please try again shortly."
        ) from exc


def _reserve_request_slot():
    now = time.monotonic()
    with _request_lock:
        while _request_times and now - _request_times[0] >= 60:
            _request_times.popleft()
        if len(_request_times) >= _MAX_SEARCHES_PER_MINUTE:
            raise NutritionServiceError(
                "Too many food database lookups in a short time. Please wait a minute and try again."
            )
        _request_times.append(now)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _nutrition_per_100g(nutriments):
    if not isinstance(nutriments, dict):
        return None

    calories = _number(nutriments.get("energy-kcal_100g"))
    if calories is None:
        energy_kj = _number(
            nutriments.get("energy-kj_100g", nutriments.get("energy_100g"))
        )
        if energy_kj is not None:
            calories = energy_kj / 4.184

    protein = _number(nutriments.get("proteins_100g"))
    carbs = _number(nutriments.get("carbohydrates_100g"))
    fats = _number(nutriments.get("fat_100g"))
    if None in (calories, protein, carbs, fats):
        return None
    if calories > 1000 or max(protein, carbs, fats) > 100:
        return None

    return {
        "calories": round(calories, 1),
        "protein": round(protein, 1),
        "carbs": round(carbs, 1),
        "fats": round(fats, 1),
    }


def _normalise_product(product):
    if not isinstance(product, dict):
        return None

    code = str(product.get("code") or "").strip()
    name = str(product.get("product_name") or product.get("generic_name") or "").strip()
    if not code or not name:
        return None

    brands = product.get("brands") or ""
    if isinstance(brands, list):
        brands = ", ".join(str(brand).strip() for brand in brands if str(brand).strip())

    categories = product.get("categories_tags")
    category = "Packaged Foods"
    if isinstance(categories, list) and categories:
        last_category = str(categories[-1]).split(":")[-1]
        category = re.sub(r"[-_]+", " ", last_category).strip().title() or category

    return {
        "code": code,
        "name": name[:180],
        "brands": str(brands).strip()[:180],
        "category": category[:80],
        "nutrition": _nutrition_per_100g(product.get("nutriments")),
        "image_url": str(product.get("image_front_url") or ""),
    }


def search_foods(query):
    """Search Open Food Facts by text; results are cached to respect API limits."""
    query = " ".join((query or "").split())
    if len(query) < 2:
        return []

    cache_key = query.casefold()
    now = time.monotonic()
    cached = _search_cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    params = urlencode({
        "q": query[:80],
        "page_size": 8,
        "fields": _SEARCH_FIELDS,
        "langs": "en",
    })
    _reserve_request_slot()
    payload = _read_json(f"https://search.openfoodfacts.org/search?{params}")
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        raise NutritionServiceError(
            "The food database returned an unexpected response. Please try again later."
        )

    results = []
    for hit in hits:
        product = _normalise_product(hit)
        if product:
            results.append(product)

    _search_cache[cache_key] = (time.monotonic(), results)
    if len(_search_cache) > 128:
        expired_keys = [
            key for key, (created_at, _) in _search_cache.items()
            if time.monotonic() - created_at >= _CACHE_TTL_SECONDS
        ]
        for key in expired_keys:
            _search_cache.pop(key, None)
        if len(_search_cache) > 128:
            _search_cache.pop(next(iter(_search_cache)))
    return results


def get_food_by_code(code):
    """Fetch a product again by barcode so imported nutrition is server-verified."""
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{8,14}", code):
        raise NutritionServiceError("That product barcode is not valid.")

    params = urlencode({"fields": _PRODUCT_FIELDS})
    _reserve_request_slot()
    payload = _read_json(
        f"https://world.openfoodfacts.org/api/v3.6/product/{quote(code)}.json?{params}"
    )
    product = payload.get("product") if isinstance(payload, dict) else None
    status = payload.get("status") if isinstance(payload, dict) else None
    status_ok = (
        (type(status) is int and status == 1)
        or (isinstance(status, str) and status.casefold() == "success")
    )
    if not status_ok or not isinstance(product, dict):
        raise NutritionServiceError("That product was not found in the food database.")

    normalised = _normalise_product({**product, "code": code})
    if not normalised:
        raise NutritionServiceError("The product record is missing a usable name.")
    if not normalised["nutrition"]:
        raise NutritionServiceError(
            "This product does not have complete nutrition values per 100g yet."
        )
    return normalised