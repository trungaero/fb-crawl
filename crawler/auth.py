"""Login / session handling via a persistent Playwright storage state."""
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from .utils import log

# Browser backends we know how to launch.
BUNDLED = ("chromium", "firefox", "webkit")
CHANNELS = ("chrome", "msedge")  # use the copy installed on this machine
SUPPORTED = BUNDLED + CHANNELS

# URL path fragments Facebook uses for "you are not (fully) logged in" pages.
LOGGED_OUT_PATHS = (
    "/login",
    "/checkpoint",
    "/two_step_verification",
    "/recover",
)


def normalize(browser: str) -> str:
    name = (browser or "chromium").strip().lower()
    if name not in SUPPORTED:
        raise ValueError(
            f"Unsupported browser {browser!r}. Choose one of: {', '.join(SUPPORTED)}"
        )
    return name


def launch_browser(playwright: Playwright, browser: str, headless: bool = False) -> Browser:
    """Launch the requested browser backend."""
    name = normalize(browser)
    log().debug("Launching %s (headless=%s)", name, headless)
    try:
        if name in CHANNELS:
            return playwright.chromium.launch(channel=name, headless=headless)
        return getattr(playwright, name).launch(headless=headless)
    except Exception as exc:  # noqa: BLE001
        if name in CHANNELS:
            hint = f"Make sure {name} is installed on this machine."
        else:
            hint = f"Install it first: playwright install {name}"
        raise RuntimeError(f"Could not launch {name}: {exc}. {hint}") from exc


def storage_state_path(browser: str, default: Path) -> Path:
    """Give each backend its own session file: cookies/localStorage are not
    portable between engines, so switching browsers should not silently reuse
    (or clobber) another browser's login."""
    name = normalize(browser)
    if name == "chromium":
        return default
    return default.with_name(f"{default.stem}_{name}{default.suffix}")


def resolve_storage_state(
    browser: str, default: Path, override: Path | None = None
) -> Path:
    """Pick the session file to use.

    Precedence: an explicit path, then this browser's own file, then the shared
    default file (e.g. one produced by `playwright codegen`). Falling back keeps
    a hand-made session usable without forcing a browser-specific filename.
    """
    if override is not None:
        return override
    preferred = storage_state_path(browser, default)
    if preferred.exists() or not default.exists():
        return preferred
    log().info(
        "No %s session at %s; reusing %s", normalize(browser), preferred.name, default
    )
    return default


def has_session_cookie(context: BrowserContext) -> bool:
    """`c_user` is the cookie Facebook sets only for an authenticated session."""
    try:
        cookies = context.cookies("https://www.facebook.com/")
    except Exception:  # noqa: BLE001 - context/browser already closed
        return False
    return any(c.get("name") == "c_user" and c.get("value") for c in cookies)


def login_interactively(
    playwright: Playwright,
    storage_state_path: Path,
    headless: bool = False,
    browser: str = "chromium",
) -> None:
    """Launch a visible browser, let the user log in manually, then persist
    the session so future runs don't need credentials again."""
    if headless:
        log().warning("Login requires a visible browser; ignoring --headless.")
        headless = False
    backend = launch_browser(playwright, browser, headless=headless)
    context = backend.new_context(locale="en-US")
    page = context.new_page()
    page.goto("https://www.facebook.com/", wait_until="domcontentloaded")

    log().info("Log in manually (including any 2FA / checkpoint).")
    input(">>> When you are logged in, press Enter here to save the session... ")

    storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(storage_state_path))
    log().info("Saved %s session to %s", normalize(browser), storage_state_path)

    backend.close()


def new_context(
    playwright: Playwright,
    storage_state_path: Path,
    headless: bool = False,
    browser: str = "chromium",
) -> BrowserContext:
    """Create a context, loading the saved session if present."""
    backend = launch_browser(playwright, browser, headless=headless)
    if storage_state_path.exists():
        # storage_state must be the file path (or a parsed dict), not raw JSON.
        context = backend.new_context(
            storage_state=str(storage_state_path),
            locale="en-US",
        )
    else:
        context = backend.new_context(locale="en-US")
    return context


def is_logged_in(page: Page, context: BrowserContext | None = None) -> bool:
    """True when we're on Facebook with a valid session cookie.

    Cookies are a far better signal than the DOM: the logged-in page is built
    client-side and its markup changes often, so the old "profile link exists"
    check reported a false negative and triggered pointless re-logins.
    """
    path = urlparse(page.url or "").path.lower()
    if any(marker in path for marker in LOGGED_OUT_PATHS) or "login.php" in path:
        return False
    return has_session_cookie(context or page.context)


def wait_for_login(
    page: Page, context: BrowserContext, timeout_s: float = 15.0
) -> bool:
    """Poll until navigation settles into a logged-in page (or timeout)."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if is_logged_in(page, context):
                return True
        except Exception:  # noqa: BLE001 - page/context closed mid-check
            return False
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(500)
