"""Extract publicly visible information from a Facebook profile page.

Work / Education / Bio / personal details live on the profile's "About" tabs,
not on the landing page, so each profile costs a few extra navigations:
`/about`, `/directory_work`, `/directory_education`, `/directory_personal_details`
(or `?sk=...` for `profile.php?id=` profiles).
"""
import re
import time
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page

from .utils import log

# About tabs we always visit. `links` / `contact_info` are opt-in (see config).
ABOUT_SECTIONS = ("about", "work", "education", "personal_details")
EXTRA_ABOUT_SECTIONS = ("details", "links", "contact_info")

_SECTION_PATH = {
    "about": "about",
    "details": "about_details",
}

# Reads each About card as {title, entries} where entries are the visual rows.
_SECTIONS_JS = r"""
() => {
    const main = document.querySelector("div[role='main']");
    if (!main) return [];
    const out = [];
    for (const section of main.querySelectorAll('section')) {
        const h2 = section.querySelector('h2');
        const title = h2 ? (h2.innerText || '').replace(/\s+/g, ' ').trim() : '';
        const entries = [];
        for (const child of section.querySelectorAll(':scope > div')) {
            const lines = (child.innerText || '')
                .split('\n').map(l => l.replace(/\s+/g, ' ').trim()).filter(Boolean);
            if (!lines.length) continue;
            if (lines.length === 1 && lines[0] === title) continue;  // heading row
            entries.push(lines);
        }
        if (entries.length) out.push({title: title, entries: entries});
    }
    return out;
}
"""

_SECTION_SUFFIXES = re.compile(r"/(about|about_details|directory_[a-z_]+)/?$")
_PEOPLE_PATH = re.compile(r"/people/[^/]+/(\d+)")

# Facebook's profile pages are client-rendered and carry no og:* meta tags, so
# the name/avatar come from the main column text plus the header avatar SVG.
# Reject first-line text that is page chrome rather than a person's name.
_NAME_REJECT = re.compile(
    r"^(log ?in|log into facebook|sign ?up|facebook|add friend|message|see all|"
    r"create new account|you must log in.*|personal details|intro|about|friends|"
    r"posts|photos|reels|videos|check-ins|music|likes|groups|reviews|followers|"
    r"following|menu|more|all|details|privacy|terms|search|profile|home)$",
    re.IGNORECASE,
)

_PROFILE_PROBE_JS = r"""
() => {
    const out = {name: null, avatar: null, photo: null};
    const main = document.querySelector("div[role='main']") || document.body;
    if (main) {
        const lines = (main.innerText || '')
            .split('\n')
            .map(l => l.replace(/\s+/g, ' ').trim())
            .filter(Boolean);
        if (lines.length) out.name = lines[0];
        for (const svg of main.querySelectorAll('svg[aria-label]')) {
            const label = (svg.getAttribute('aria-label') || '').trim();
            if (!label || /your profile|your story|create story/i.test(label)) continue;
            if (!out.avatar) out.avatar = label;
            const img = svg.querySelector('image');
            const href = img && (img.getAttribute('xlink:href') || img.getAttribute('href'));
            if (href && href.indexOf('http') === 0) { out.photo = href; break; }
        }
    }
    if (!out.photo) {
        const meta = document.querySelector("meta[property='og:image']");
        if (meta && meta.getAttribute('content')) out.photo = meta.getAttribute('content');
    }
    return out;
}
"""


def _plausible_name(text: Optional[str]) -> Optional[str]:
    candidate = (text or "").strip()
    if not 2 <= len(candidate) <= 80:
        return None
    return None if _NAME_REJECT.match(candidate) else candidate


def _wait_for_profile(page: Page, timeout_s: float = 10.0) -> Dict[str, Optional[str]]:
    """Poll until the profile header has rendered (name + avatar) or time runs out."""
    probe: Dict[str, Optional[str]] = {"name": None, "avatar": None, "photo": None}
    deadline = time.time() + timeout_s
    while True:
        try:
            fresh = page.evaluate(_PROFILE_PROBE_JS) or {}
        except Exception as exc:  # noqa: BLE001
            log().debug("profile probe failed: %s", exc)
            fresh = {}
        probe = {k: fresh.get(k) or probe.get(k) for k in probe}
        if _plausible_name(probe.get("name")) and (
            _plausible_name(probe.get("avatar")) or probe.get("photo")
        ):
            break
        if time.time() >= deadline:
            break
        page.wait_for_timeout(400)
    # The avatar label is the person's plain name; the first text line can hold
    # a nickname, so keep it only when both agree-ish, else fall back to a label.
    probe["name"] = _plausible_name(probe.get("name")) or _plausible_name(
        probe.get("avatar")
    )
    return probe


def _meta_content(page: Page, prop: str) -> Optional[str]:
    # Locators auto-wait for the element to appear (30s on Facebook), so probe
    # with a single evaluate instead: these pages usually have no meta tags.
    try:
        value = page.evaluate(
            "(p) => { const m = document.querySelector(`meta[property='${p}']`);"
            " return m ? m.getAttribute('content') : null; }",
            prop,
        )
    except Exception:
        return None
    return (value or "").strip() or None


def _first_text(page: Page, selector: str) -> Optional[str]:
    try:
        loc = page.locator(selector).first
        if loc.count() == 0:
            return None
        return (loc.inner_text() or "").strip() or None
    except Exception:
        return None


def _intro_items(page: Page) -> Dict[str, str]:
    """Fallback for city/relationship, read from the visible page text.

    Only prefixes that can't collide with sidebar friend cards are used; the
    About tabs provide the authoritative work/education data.
    """
    out: Dict[str, str] = {}

    js = r"""
    () => {
        const result = {};
        const text = document.body ? document.body.innerText : '';
        const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
        const prefixes = [
            ['lives_in', 'Lives in'],
            ['from_city', 'From'],
            ['relationship', 'Married to'],
            ['relationship', 'In a relationship with'],
        ];
        // Longest prefix first so "Works at X" isn't also matched by "Works".
        prefixes.sort((a, b) => b[1].length - a[1].length);
        for (let i = 0; i < lines.length; i++) {
            const l = lines[i];
            for (const [key, prefix] of prefixes) {
                if (l.startsWith(prefix)) {
                    // "Single" carries no value after the prefix.
                    const val = l.slice(prefix.length).trim() || prefix;
                    result[key] = result[key] ? result[key] + ' | ' + val : val;
                    break;
                }
            }
        }
        return result;
    }
    """
    try:
        out = page.evaluate(js)
    except Exception as exc:  # noqa: BLE001
        log().debug("intro extraction failed: %s", exc)
    return out


_EDUCATION_TITLES = {
    "education", "college", "high school", "graduate school", "university",
    "vocational school",
}

# About card title -> (data field, index of the value inside the entry row).
# index None means "join every line of the row".
_ABOUT_FIELDS = {
    "location": ("lives_in", 0),
    "current city": ("lives_in", 0),
    "hometown": ("from_city", 0),
    "birthday": ("birthday", 0),
    "gender": ("gender", 0),
    "languages": ("languages", 0),
    "family members": ("family", None),
    "relationship": ("relationship", 0),
    "bio": ("bio", None),
    "favorite quotes": ("quotes", None),
    "social media": ("contact_info", None),
    "contact info": ("contact_info", None),
    "links": ("links", None),
    "websites": ("links", None),
}

# Fields that legitimately hold multiple rows.
_LIST_FIELDS = {"work", "education", "family", "links", "contact_info"}


def _profile_base(url: str, current: str = "") -> str:
    """Return the profile URL without any About tab suffix."""
    for candidate in (current, url):
        candidate = (candidate or "").split("#")[0].strip()
        if not candidate:
            continue
        candidate = _SECTION_SUFFIXES.sub("", candidate)
        candidate = re.sub(r"[?&]sk=[^&]*", "", candidate).rstrip("?&")
        if candidate.rstrip("/") in (
            "https://www.facebook.com",
            "https://www.facebook.com/",
        ):
            continue
        people = _PEOPLE_PATH.search(candidate)
        if people:
            # /people/<slug>/<id>/ has no /about tabs; the id form does.
            return f"https://www.facebook.com/profile.php?id={people.group(1)}"
        if candidate.rstrip("/") != candidate:
            candidate = candidate.rstrip("/")
        return candidate
    return url.rstrip("/")


def _section_url(base: str, section: str) -> str:
    path = _SECTION_PATH.get(section, f"directory_{section}")
    if "?" in base:
        head, _, query = base.partition("?")
        params = [p for p in query.split("&") if p and not p.startswith("sk=")]
        params.append(f"sk={path}")
        return f"{head}?{'&'.join(params)}"
    return f"{base.rstrip('/')}/{path}"


_SETTLED_JS = r"""
() => {
    const main = document.querySelector("div[role='main']");
    return main ? (main.innerText || '').length : 0;
}
"""

# The About page's left nav lists only the sections that actually exist, which
# lets us skip tabs that would just fall back to the plain intro view.
_NAV_JS = r"""
() => {
    const main = document.querySelector("div[role='main']");
    if (!main) return [];
    const keys = [];
    for (const a of main.querySelectorAll("a[href]")) {
        const href = a.getAttribute('href') || '';
        let m = href.match(/directory_([a-z_]+)/);
        if (m) { keys.push(m[1]); continue; }
        if (/\/about_details(?:[?#]|$)/.test(href) || /[?&]sk=about_details/.test(href)) {
            keys.push('details');
        } else if (/\/about(?:[?#]|$)/.test(href) || /[?&]sk=about(?:&|$)/.test(href)) {
            keys.push('about');
        }
    }
    return keys;
}
"""


def _about_nav(page: Page) -> set:
    """Section keys offered by the current About page's nav (empty if unknown)."""
    try:
        return set(page.evaluate(_NAV_JS) or [])
    except Exception as exc:  # noqa: BLE001
        log().debug("about nav probe failed: %s", exc)
        return set()


def _load_sections(page: Page, url: str, timeout_s: float = 5.0) -> List[Dict[str, Any]]:
    """Open an About tab and return its rendered cards.

    Tabs with no data (e.g. an account with no workplace) silently fall back to
    the plain intro view, so the wait also stops once the main column has
    rendered content instead of burning the full timeout.
    """
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as exc:  # noqa: BLE001
        log().debug("about page failed (%s): %s", url, exc)
        return []

    deadline = time.time() + timeout_s
    grace_used = False
    while True:
        try:
            sections = page.evaluate(_SECTIONS_JS) or []
        except Exception as exc:  # noqa: BLE001
            log().debug("about parse failed (%s): %s", url, exc)
            sections = []
        if sections:
            return sections
        if time.time() >= deadline:
            return []
        try:
            rendered = (page.evaluate(_SETTLED_JS) or 0) > 200
        except Exception:  # noqa: BLE001
            rendered = False
        if rendered:
            # Page finished painting; give late cards one last chance.
            if grace_used:
                return []
            grace_used = True
            page.wait_for_timeout(200)
            continue
        page.wait_for_timeout(250)


def _entry_line(entry: List[str]) -> str:
    return " — ".join(part for part in entry if part)


def _set_field(data: Dict[str, Any], name: str, value: str) -> None:
    value = (value or "").strip()
    if not value:
        return
    if name in _LIST_FIELDS:
        data[name] = f"{data[name]}\n{value}" if data.get(name) else value
    elif not data.get(name):
        data[name] = value


def _apply_sections(
    data: Dict[str, Any], kind: str, sections: List[Dict[str, Any]]
) -> None:
    """Map one About tab's cards onto profile fields."""
    for section in sections:
        title = (section.get("title") or "").strip()
        key = title.lower()
        entries = section.get("entries") or []

        if kind == "work" or key == "work":
            for entry in entries:
                _set_field(data, "work", _entry_line(entry))
            continue

        if kind == "education" or key in _EDUCATION_TITLES:
            prefix = "" if key == "education" else f"{title}: "
            for entry in entries:
                _set_field(data, "education", prefix + _entry_line(entry))
            continue

        mapped = _ABOUT_FIELDS.get(key)
        if not mapped:
            continue
        field, index = mapped
        if key in ("links", "social media", "contact info"):
            for entry in entries:
                for i in range(0, len(entry), 2):
                    label = entry[i + 1] if i + 1 < len(entry) else ""
                    _set_field(data, field, f"{entry[i]} ({label})" if label else entry[i])
            continue
        for entry in entries:
            value = _entry_line(entry) if index is None else (
                entry[index] if len(entry) > index else ""
            )
            if value.strip().lower() != key:
                _set_field(data, field, value)


def _follower_count_from_text(page: Page) -> Optional[str]:
    """Fallback: pull "1.2K followers" out of the page's visible text."""
    try:
        text = page.evaluate(
            "() => document.body ? document.body.innerText : ''"
        )
    except Exception:
        return None
    match = re.search(
        r"([\d.,]+\s*[KMB]?)\s+(followers|friends)", text or "", re.IGNORECASE
    )
    if not match:
        return None
    return f"{match.group(1).strip()} {match.group(2).lower()}"


def extract_profile(page: Page, url: str, extras: bool = False) -> Dict[str, Any]:
    """Visit a profile plus its About tabs and pull every public field we can."""
    data: Dict[str, Any] = {
        "url": url,
        "name": None,
        "profile_photo_url": None,
        "bio": None,
        "intro": None,
        "lives_in": None,
        "from_city": None,
        "birthday": None,
        "gender": None,
        "languages": None,
        "work": None,
        "education": None,
        "family": None,
        "relationship": None,
        "quotes": None,
        "links": None,
        "contact_info": None,
        "follower_count": None,
    }

    page.goto(url, wait_until="domcontentloaded")
    probe = _wait_for_profile(page)
    base = _profile_base(url, page.url)

    data["name"] = (
        probe.get("name")
        or _meta_content(page, "og:title")
        or _first_text(page, "h1")
    )
    data["profile_photo_url"] = probe.get("photo") or _meta_content(page, "og:image")

    # Follower / friend count (public pages/profiles only).
    follower = _first_text(
        page,
        "a[href*='followers'] span, div[role='main'] span:has-text('followers')",
    ) or _follower_count_from_text(page)
    if follower:
        # Drop trailing " • 1.7K following" style noise.
        follower = follower.split("•")[0].strip() or None
    data["follower_count"] = follower

    # Work / education / bio / personal details live on the About tabs. The
    # About page itself also carries some cards and its nav lists which tabs
    # exist, so tabs that would only fall back to the intro view are skipped.
    wanted = list(ABOUT_SECTIONS) + (list(EXTRA_ABOUT_SECTIONS) if extras else [])
    first, rest = wanted[0], wanted[1:]
    _apply_sections(data, first, _load_sections(page, _section_url(base, first)))

    nav = _about_nav(page)
    if nav and "work_and_education" in nav and not {"work", "education"} & nav:
        # Some accounts only offer the combined "Work and education" tab.
        rest = [
            "work_and_education" if kind in ("work", "education") else kind
            for kind in rest
        ]
        seen = set()
        rest = [k for k in rest if not (k in seen or seen.add(k))]
    for kind in rest:
        if nav and kind not in nav:
            continue
        _apply_sections(data, kind, _load_sections(page, _section_url(base, kind)))

    # Landing-page intro card as a last resort for city / relationship.
    for key, value in _intro_items(page).items():
        if value and not data.get(key):
            data[key] = value

    data["intro"] = data["bio"]
    return data
