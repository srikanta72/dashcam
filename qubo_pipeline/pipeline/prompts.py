"""
pipeline/prompts.py

Ports the input prompts needed by the normal Windows run. Pressing Enter
accepts the displayed default; typed text overrides that one value.
"""

from pipeline import state as state_mod
from pathlib import Path


def _ask(label, default):
    try:
        value = input(f"{label} [{default}]: ").strip()
    except EOFError:
        value = ""
    return value or default


def ask_source(default=None):
    return _ask("Source folder", default or state_mod.default_source())


def ask_destination(default=None):
    return _ask("Destination root", default or state_mod.default_destination())


def ask_time_range(start=None, end=None):
    print("  Examples: 2057   or   2026-09-06 2057   or   202609062057")
    print("  Use ALL for every video. For a cross-day range, enter the date on both ends.")
    start_value = _ask(
        "Start [HHMM / YYYY-MM-DD HHMM / YYYYMMDDHHMM / ALL]", start or "ALL"
    )
    end_value = _ask(
        "End [HHMM / YYYY-MM-DD HHMM / YYYYMMDDHHMM / ALL]", end or "ALL"
    )
    return start_value, end_value


def default_video_title(source=None, target=None):
    """Return the date-folder title used when no custom title is supplied."""
    if target:
        target_name = Path(target).name
        if target_name and not target_name.startswith("qubo_target_"):
            return target_name
    if source:
        date_folder = Path(source).parent.name
        try:
            day, month, year = date_folder.split("-")
            return f"{year}-{month}-{day}-ALL"
        except ValueError:
            pass
    return "QUBO dashcam"


def ask_title(default=None):
    return _ask("YouTube title (cabin and final video)", default or "QUBO dashcam")

