"""Domain-only hints that a company site belongs to a multi-city chain or franchise."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

# Booking, video and site-builder hosts are shared by unrelated businesses.
PLATFORM_DOMAINS = {"rutube.ru", "taplink.cc", "taplink.ws", "dikidi.net", "dikidi.ru", "dikidi.app",
                    "yclients.com", "yclients.ru", "max.ru", "tilda.ws", "turbo.site", "wixsite.com", "clients.site", "business.site",
                    "ucoz.ru", "narod.ru", "flamp.ru", "zoon.ru", "prodoctorov.ru"}
TRANSLIT = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
                          "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
                          "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
                          "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
                          "я": "ya"})


def city_slug(city):
    return re.sub(r"[^a-z0-9]", "", str(city or "").casefold().translate(TRANSLIT))


def registered_domain(url):
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return ".".join(host.split(".")[-2:])


def is_city_code(value, slug):
    """Match a full city name or a short form such as oren or orb for orenburg."""
    if value == slug or (len(value) >= 4 and slug.startswith(value)):
        return True
    letters = iter(slug)
    return len(value) == 3 and value[0] == slug[0] and all(char in letters for char in value)


def city_section(url, city):
    """A brand site with a section per city: orb.brand.ru, oren.brand.ru or brand.ru/oren."""
    slug = city_slug(city)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    # A booking page with a city picker belongs to a multi-city network.
    if "/select-city/" in parts.path.lower():
        return True
    if not slug or registered_domain(url) in PLATFORM_DOMAINS:
        return False
    labels = host.split(".")
    if len(labels) >= 3:
        if is_city_code(re.sub(r"[^a-z0-9]", "", labels[0]), slug):
            return True
    segments = (re.sub(r"[^a-z0-9]", "", segment.lower()) for segment in parts.path.split("/"))
    return any(len(segment) >= 4 and is_city_code(segment, slug) for segment in segments)


def domain_cities(rows):
    cities = {}
    for row in rows:
        data = row["data"]
        for url in data.get("websites", []):
            domain = registered_domain(url)
            if domain and domain not in PLATFORM_DOMAINS and city_slug(data.get("city")):
                cities.setdefault(domain, set()).add(city_slug(data["city"]))
    return cities


def chain_reason(data, cities):
    """Explain why a company looks like a chain, or return an empty string."""
    for url in data.get("websites", []):
        if city_section(url, data.get("city")):
            return f"Похоже на сеть или франшизу: у сайта есть раздел города ({url})"
        domain = registered_domain(url)
        if len(cities.get(domain, ())) > 1:
            return f"Похоже на сеть или франшизу: домен {domain} встречается в разных городах"
    return ""
