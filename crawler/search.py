"""People-search: build the URL, load results, and parse profile cards."""
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from playwright.sync_api import Page

import config

from .utils import log, looks_like_person_name

# Facebook usernames that are navigation destinations, not people.
NON_PEOPLE = {
    "search", "people", "home", "watch", "marketplace", "groups", "gaming",
    "messages", "notifications", "stories", "events", "friends", "saved",
    "pages", "reels", "permalink", "photo", "photos", "video", "videos",
    "about", "settings", "help", "privacy", "hashtag", "l.php",
    "login", "login.php", "checkpoint", "recover", "two_step_verification",
    "reg", "signup", "r.php", "home.php", "share", "sharer", "dialog",
    "logout", "plugins", "connect", "business", "developer", "apis",
    "legal", "policies", "communitystandards", "directory", "fundraisers",
    "bookmarks", "memories", "jobs", "offers", "live", "podcasts",
    "adsmanager", "ads", "weather", "games", "languages",
}

_PEOPLE_PATH = re.compile(r"^/people/([^/]+)/(\d+)/?$")
_SINGLE_SEGMENT_PATH = re.compile(r"^/([^/]+)/?$")
_PROFILE_PHOTO_PREFIXES = ("profile photo of ", "profile picture of ")

# Text that only appears on an actual person card (never on the nav/tab links).
_PERSON_HINTS = re.compile(
    r"add friend|follow|following|message|mutual friend|lives in|works at|"
    r"studied at|joined facebook",
    re.IGNORECASE,
)


def build_search_url(query: str, city_filters: Optional[str] = None) -> str:
    """Build the Facebook people-search URL, optionally with a location filter."""
    params = {"q": query}
    if city_filters:
        params["filters"] = city_filters
    return "https://www.facebook.com/search/people/?" + urlencode(params)


def parse_profile_href(href: str) -> Optional[Dict[str, Optional[str]]]:
    """Turn a profile link into ids plus a clean canonical URL.

    Handles the three shapes Facebook uses:
      /<username>                    /profile.php?id=<numeric id>
      /people/<slug>/<numeric id>/
    Tracking parameters (`__tn__`, `__cft__`, ...) are dropped.
    """
    if not href:
        return None
    parts = urlparse(href)
    host = (parts.netloc or "").lower()
    if not (host == "facebook.com" or host.endswith(".facebook.com")):
        return None

    path = parts.path
    if path.rstrip("/") == "/profile.php":
        uid = (parse_qs(parts.query).get("id") or [""])[0]
        if uid.isdigit():
            return {
                "user_id": uid,
                "username": None,
                "url": f"https://www.facebook.com/profile.php?id={uid}",
            }
        return None

    match = _PEOPLE_PATH.match(path)
    if match:
        slug, uid = unquote(match.group(1)), match.group(2)
        return {
            "user_id": uid,
            "username": slug,
            "url": f"https://www.facebook.com/people/{slug}/{uid}/",
        }

    match = _SINGLE_SEGMENT_PATH.match(path)
    if match:
        slug = unquote(match.group(1))
        if slug.isdigit():
            return {
                "user_id": slug,
                "username": None,
                "url": f"https://www.facebook.com/profile.php?id={slug}",
            }
        return {
            "user_id": None,
            "username": slug,
            "url": f"https://www.facebook.com/{slug}",
        }
    return None


def _strip_photo_prefix(value: str) -> str:
    """`aria-label="Profile photo of X"` -> `X`."""
    text = (value or "").strip()
    lowered = text.lower()
    for prefix in _PROFILE_PHOTO_PREFIXES:
        if lowered.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def _pick_name(item: Dict[str, Any]) -> str:
    """Best available display name: photo alt/label, link text, card text."""
    for field in ("photo_alt", "aria", "link_text"):
        name = _strip_photo_prefix(item.get(field) or "")
        if looks_like_person_name(name):
            return name

    lines = [
        line.strip()
        for line in (item.get("card_text") or "").splitlines()
        if line.strip()
    ]
    for line in lines[:3]:
        if _PERSON_HINTS.match(line) or "mutual friend" in line.lower():
            continue
        name = _strip_photo_prefix(line)
        if looks_like_person_name(name):
            return name
    return ""


def collect_results(page: Page) -> List[Dict[str, Any]]:
    """Extract name + profile URL + user id from the visible result cards.

    Facebook's DOM changes frequently; this uses a tolerant anchor-based scan
    rather than brittle class names. Two things the current layout does that a
    naive scan misses: avatars are inline `<svg><image>`, not `<img>`, and the
    person's name lives in the card's text (or the anchor's `aria-label`), not
    inside the link element.
    """
    js = r"""
    () => {
        const out = [];
        for (const a of document.querySelectorAll('a[href]')) {
            const abs = a.href || '';
            if (!abs) continue;
            if (!/(^|\.)facebook\.com/.test(new URL(abs).hostname)) continue;

            const photo = a.querySelector('img[alt], svg[aria-label]');
            const photoAlt = photo
                ? (photo.getAttribute('alt') || photo.getAttribute('aria-label') || '')
                : '';

            // The nearest ancestor with text is the result card.
            let card = a;
            let depth = 0;
            while (card && !(card.innerText || '').trim() && depth < 8) {
                card = card.parentElement;
                depth++;
            }
            const cardText = card ? (card.innerText || '').trim() : '';

            out.push({
                href: abs,
                aria: (a.getAttribute('aria-label') || '').trim(),
                photo_alt: photoAlt.trim(),
                link_text: ((a.innerText || '').trim().split('\n')[0] || '').trim(),
                card_text: cardText.slice(0, 400),
                card_links: card ? card.querySelectorAll('a[href]').length : 0
            });
        }
        return out;
    }
    """
    return page.evaluate(js) or []


def clean_results(raw: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Dedupe, drop nav links, and keep only plausible person entries."""
    cleaned: Dict[str, Dict[str, str]] = {}
    for item in raw:
        parsed = parse_profile_href(item.get("href") or "")
        if not parsed:
            continue
        username = (parsed["username"] or "").lower()
        if username in NON_PEOPLE:
            continue
        key = parsed["user_id"] or parsed["username"]
        if not key or key in cleaned:
            continue
        name = _pick_name(item)
        if not looks_like_person_name(name):
            continue
        # Nav/tab links have no avatar and no person hints in their container.
        if not item.get("photo_alt") and not _PERSON_HINTS.search(
            item.get("card_text") or ""
        ):
            continue
        cleaned[key] = {
            "name": name,
            "url": parsed["url"],
            "user_id": parsed["user_id"],
            "username": parsed["username"],
        }
    return list(cleaned.values())


def _wait_for_results(page: Page, timeout_s: float = 20.0) -> List[Dict[str, str]]:
    """Poll until at least one result card renders (or give up)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        results = clean_results(collect_results(page))
        if results:
            return results
        page.wait_for_timeout(1000)
    return []


def scroll_and_collect(
    page: Page,
    max_results: int = 0,
    scroll_passes: int = 40,
    idle_seconds: float = 2.0,
) -> List[Dict[str, str]]:
    """Keep scrolling the results feed, collecting until no new cards appear
    or max_results is reached."""
    all_results: Dict[str, Dict[str, str]] = {}
    previous_count = -1

    for _ in range(scroll_passes):
        for item in clean_results(collect_results(page)):
            key = item.get("user_id") or item.get("username")
            all_results[key] = item

        if max_results and len(all_results) >= max_results:
            break
        if len(all_results) == previous_count:
            break
        previous_count = len(all_results)

        # Scroll to bottom to trigger lazy-loading of more results.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(idle_seconds)

    log().info("Collected %d unique people results", len(all_results))
    return list(all_results.values())


def run_search(page: Page) -> List[Dict[str, str]]:
    """Navigate to the search page and collect all visible people results."""
    url = build_search_url(config.SEARCH_QUERY, config.CITY_FILTERS or None)
    log().info("Opening search: %s", url)
    page.goto(url, wait_until="domcontentloaded")

    if not _wait_for_results(page):
        log().warning(
            "No people results rendered (url=%s); the page may still be "
            "loading, filtered out, or showing a checkpoint.",
            page.url,
        )

    return scroll_and_collect(
        page,
        max_results=config.MAX_RESULTS,
        scroll_passes=config.SCROLL_PASSES,
        idle_seconds=config.SCROLL_IDLE_SECONDS,
    )
