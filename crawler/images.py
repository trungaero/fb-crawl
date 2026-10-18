"""Save each account's profile picture, with a screenshot fallback."""
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page

from .utils import log

# Candidate selectors for the profile picture element (used by the screenshot
# fallback). Facebook renders avatars in several ways; we try them in order.
PROFILE_PIC_SELECTORS = [
    "div[role='main'] svg[aria-label] image",
    "div[role='main'] image[preserveAspectRatio='xMidYMid meet']",
    "div[role='main'] img[alt]",
    "svg[aria-label] image",
]

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _pick_extension(url: Optional[str], content_type: Optional[str]) -> str:
    if url:
        lower = url.lower().split("?")[0]
        for ext in _IMAGE_EXTENSIONS:
            if ext in lower:
                return ".jpg" if ext == ".jpeg" else ext
    ct = (content_type or "").lower()
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    return ".jpg"


def _download(context, url: str, dest: Path) -> bool:
    """Download the image using the logged-in context's cookies."""
    try:
        resp = context.request.get(url, timeout=30_000)
        if not resp.ok:
            return False
        body = resp.body()
        if not body or len(body) < 1000:  # likely a placeholder/tracker pixel
            return False
        dest.write_bytes(body)
        return True
    except Exception as exc:  # noqa: BLE001
        log().debug("download failed for %s: %s", url, exc)
        return False


def _screenshot(page: Page, dest: Path) -> bool:
    """Capture the profile picture element as a screenshot."""
    for selector in PROFILE_PIC_SELECTORS:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue
            loc.scroll_into_view_if_needed(timeout=5000)
            loc.screenshot(path=str(dest))
            if dest.exists() and dest.stat().st_size > 1000:
                return True
        except Exception as exc:  # noqa: BLE001
            log().debug("screenshot failed for %s: %s", selector, exc)
    return False


def save_profile_picture(
    context,
    page: Page,
    user_id: str,
    photo_url: Optional[str],
    out_dir: Path,
) -> Optional[str]:
    """Return the saved file path, or None if nothing could be captured."""
    out_dir.mkdir(parents=True, exist_ok=True)

    if photo_url:
        ext = _pick_extension(photo_url, None)
        dest = out_dir / f"{user_id}{ext}"
        # Skip if we already have a non-empty file for this account.
        if dest.exists() and dest.stat().st_size > 0:
            return str(dest)
        if _download(context, photo_url, dest):
            return str(dest)

    # Fallback: capture the avatar element on the currently-open profile page.
    fallback = out_dir / f"{user_id}.png"
    if fallback.exists() and fallback.stat().st_size > 0:
        return str(fallback)
    if _screenshot(page, fallback):
        log().info("Captured profile picture screenshot for %s", user_id)
        return str(fallback)

    log().warning("Could not obtain profile picture for %s", user_id)
    return None
