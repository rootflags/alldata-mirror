"""Shared settings for the alldata-mirror tools: where the config lives and how it is read."""
import configparser
import re
from pathlib import Path

CFG_DIR = Path.home() / ".alldata-mirror"
CFG_FILE = CFG_DIR / "alldata-mirror.conf"
PROJECT_DIR = Path(__file__).resolve().parent
SITE_HOST = "my.alldata.com"

# Never requested, even when the API links to them: logout, the account and profile pages, Help & Feedback
FORBIDDEN = re.compile(r"log-?out|sign-?out|profile|my-?account|feedback|contact-?us", re.I)


def parse_delay(text):
    """'5' or '5.01-6.99' -> (min, max) seconds."""
    lo, _, hi = str(text).strip().strip("\"'").partition("-")
    lo = float(lo)
    hi = float(hi) if hi else lo
    if lo < 0 or hi < lo:
        raise ValueError(f"bad delay {text!r}")
    return lo, hi


def load_crawl_config():
    """[crawl] settings: delay (seconds between pages, or a min-max range), output dir, cookies file."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    cfg.read(CFG_FILE)

    def path(key, default):
        return Path(cfg.get("crawl", key, fallback=default).strip().strip("\"'")).expanduser()
    return {
        "delay": parse_delay(cfg.get("crawl", "delay", fallback="5.01-6.99")),
        "output": path("output", "~/alldata-download"),
        "cookies": path("cookies", str(CFG_DIR / "cookies.txt")),
    }
