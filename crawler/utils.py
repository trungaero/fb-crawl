"""Small shared helpers: logging and polite random delays."""
import logging
import random
import time

_logger = logging.getLogger("fb-crawler")


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def log() -> logging.Logger:
    return _logger


def human_delay(min_s: float = 2.0, max_s: float = 6.0) -> None:
    """Sleep a random amount to avoid hammering Facebook."""
    wait = random.uniform(min_s, max_s)
    _logger.debug("Sleeping %.1fs", wait)
    time.sleep(wait)


def looks_like_person_name(name: str) -> bool:
    """Heuristic: a person name is non-empty and contains at least one letter."""
    if not name:
        return False
    return any(ch.isalpha() for ch in name)
