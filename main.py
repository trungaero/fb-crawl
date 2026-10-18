"""Orchestrates the full crawl: auth -> search -> profiles -> images -> SQLite.

Usage:
    python main.py                 # normal run (login if needed, then crawl)
    python main.py --login-only    # only log in and save the session
    python main.py --headless      # headless browser (after first login)
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import config
from crawler import auth, images, profile as profile_mod, search
from crawler.db import Database
from crawler.utils import human_delay, log, setup_logging


def process_profile(
    context,
    page,
    db: Database,
    result: dict,
    redo: bool = False,
) -> None:
    user_id = result.get("user_id") or result.get("username")
    url = result["url"]

    # Resume: skip profiles we already finished.
    if not redo and db.profile_status(user_id) == "done":
        log().info("Skip %s (already done)", user_id)
        return

    log().info("Scraping %s (%s)", result.get("name"), user_id)
    try:
        data = profile_mod.extract_profile(
            page, url, extras=config.SCRAPE_ABOUT_EXTRAS
        )
    except Exception as exc:  # noqa: BLE001
        log().warning("Profile extraction failed for %s: %s", user_id, exc)
        db.upsert_profile(user_id, {"url": url, "status": "error"})
        return

    # Profile picture.
    photo_path = None
    try:
        photo_path = images.save_profile_picture(
            context,
            page,
            user_id,
            data.get("profile_photo_url"),
            config.PROFILE_PICS_DIR,
        )
    except Exception as exc:  # noqa: BLE001
        log().warning("Profile picture save failed for %s: %s", user_id, exc)

    data["profile_photo_path"] = photo_path
    data["status"] = "done"
    db.upsert_profile(user_id, data)
    log().info("Stored %s", user_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Facebook people crawler")
    parser.add_argument("--login-only", action="store_true",
                        help="Only log in and save the session, then exit")
    parser.add_argument("--headless", action="store_true",
                        help="Run headless (only after a session exists)")
    parser.add_argument("--query", default=None,
                        help="Override the search phrase")
    parser.add_argument("--browser", default=config.BROWSER,
                        choices=auth.SUPPORTED,
                        help="Browser backend to use (default: %(default)s)")
    parser.add_argument("--storage-state", default=None,
                        help="Path to a saved Playwright session file "
                             "(default: data/storage_state[_<browser>].json)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only scrape the first N profiles (0 = all)")
    parser.add_argument("--redo", action="store_true",
                        help="Re-scrape profiles already marked done")
    args = parser.parse_args()

    setup_logging()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.PROFILE_PICS_DIR.mkdir(parents=True, exist_ok=True)

    if args.query:
        config.SEARCH_QUERY = args.query

    browser = auth.normalize(args.browser)
    state_path = auth.resolve_storage_state(
        browser,
        config.STORAGE_STATE_PATH,
        Path(args.storage_state) if args.storage_state else None,
    )
    log().info("Using browser: %s", browser)
    if state_path.exists():
        log().info("Using saved session: %s", state_path)
    else:
        log().info("No saved session at %s", state_path)

    with sync_playwright() as p:
        if args.login_only or not state_path.exists():
            auth.login_interactively(
                p, state_path, headless=args.headless, browser=browser
            )
            if args.login_only:
                return 0

        context = auth.new_context(
            p, state_path, headless=args.headless, browser=browser
        )
        page = context.new_page()
        db = Database(config.DB_PATH)

        try:
            # Verify we're still logged in; if not, re-login.
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            if not auth.wait_for_login(page, context):
                log().warning("Session appears expired; please re-login.")
                context.close()
                auth.login_interactively(
                    p, state_path, headless=args.headless, browser=browser
                )
                context = auth.new_context(
                    p, state_path, headless=args.headless, browser=browser
                )
                page = context.new_page()

            # 1) Search and collect results.
            results = search.run_search(page)
            db.upsert_search_results(config.SEARCH_QUERY, results)
            log().info("Stored %d search results", len(results))

            # 2) Visit each unique profile.
            targets = results[: args.limit] if args.limit else results
            total = len(targets)
            for idx, r in enumerate(targets, start=1):
                log().info("[%d/%d] Processing", idx, total)
                process_profile(context, page, db, r, redo=args.redo)
                human_delay(config.MIN_DELAY, config.MAX_DELAY)
        finally:
            db.close()
            context.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
