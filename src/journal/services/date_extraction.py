"""Extract dates from OCR text and filenames.

Handwritten journal pages typically begin with a date in formats like:
  - TUES 17 FEB 2026
  - Tuesday 17 February 2026
  - 17 Feb 2026
  - Feb 17, 2026
  - 17/02/2026
  - 2026-02-17

Uploaded filenames may also contain dates:
  - 2026-03-28_at_the_burrow.md
  - 2026-03-28.txt

This module searches for such patterns and returns an ISO 8601 date
string (YYYY-MM-DD) if found.
"""

from __future__ import annotations

import datetime
import logging
import re

log = logging.getLogger(__name__)

# Month name -> number mapping (case-insensitive, abbreviations OK)
_MONTHS: dict[str, int] = {}
for _i, _names in enumerate(
    [
        ("jan", "january"),
        ("feb", "february"),
        ("mar", "march"),
        ("apr", "april"),
        ("may",),
        ("jun", "june"),
        ("jul", "july"),
        ("aug", "august"),
        ("sep", "sept", "september"),
        ("oct", "october"),
        ("nov", "november"),
        ("dec", "december"),
    ],
    start=1,
):
    for _name in _names:
        _MONTHS[_name] = _i

# Pattern 1: "17 Feb 2026" or "17 February 2026" (with optional leading day name)
_PAT_DMY_NAMED = re.compile(
    r"(?:(?:mon|tue|wed|thu|fri|sat|sun)\w*[\s,.-]*)?(\d{1,2})\s+"
    r"([a-z]{3,9})\s+(\d{4})",
    re.IGNORECASE,
)

# Pattern 2: "Feb 17, 2026" or "February 17 2026"
_PAT_MDY_NAMED = re.compile(
    r"(?:(?:mon|tue|wed|thu|fri|sat|sun)\w*[\s,.-]*)?([a-z]{3,9})\s+"
    r"(\d{1,2})[,\s]+(\d{4})",
    re.IGNORECASE,
)

# Pattern 3: ISO-ish "2026-02-17"
_PAT_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# Pattern 4: "17/02/2026" or "17.02.2026" (DD/MM/YYYY)
_PAT_DMY_NUMERIC = re.compile(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})")


def _safe_date(year: int, month: int, day: int) -> str | None:
    """Validate and return ISO date string, or None if invalid."""
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def extract_date_from_text(text: str) -> str | None:
    """Try to extract a date from the first few lines of OCR text.

    Returns an ISO 8601 date string (YYYY-MM-DD) or None if no date
    is found. Only searches the first 500 characters to avoid false
    positives deeper in the text.
    """
    head = text[:500]

    # Try named-month patterns first (most common in handwritten journals)
    m = _PAT_DMY_NAMED.search(head)
    if m:
        day, month_str, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month = _MONTHS.get(month_str[:3])
        if month:
            result = _safe_date(year, month, day)
            if result:
                log.info("Extracted date %s from OCR text (DMY named)", result)
                return result

    m = _PAT_MDY_NAMED.search(head)
    if m:
        month_str, day, year = m.group(1).lower(), int(m.group(2)), int(m.group(3))
        month = _MONTHS.get(month_str[:3])
        if month:
            result = _safe_date(year, month, day)
            if result:
                log.info("Extracted date %s from OCR text (MDY named)", result)
                return result

    m = _PAT_ISO.search(head)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        result = _safe_date(year, month, day)
        if result:
            log.info("Extracted date %s from OCR text (ISO)", result)
            return result

    m = _PAT_DMY_NUMERIC.search(head)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        result = _safe_date(year, month, day)
        if result:
            log.info("Extracted date %s from OCR text (DMY numeric)", result)
            return result

    return None


# Filename patterns — match YYYY-MM-DD or YYYY_MM_DD at the start of the
# basename (before extension), with any separator (-, _, .).
_PAT_FILENAME_ISO = re.compile(r"(\d{4})[-_.](\d{2})[-_.](\d{2})")

# Named-month filename patterns like "28-March-2026" or "28_march_2026"
_PAT_FILENAME_DMY = re.compile(
    r"(\d{1,2})[-_.\s]([a-z]{3,9})[-_.\s](\d{4})", re.IGNORECASE
)
_PAT_FILENAME_MDY = re.compile(
    r"([a-z]{3,9})[-_.\s](\d{1,2})[-_.\s](\d{4})", re.IGNORECASE
)


def extract_date_from_filename(filename: str) -> str | None:
    """Try to extract a date from a filename (with or without extension).

    Strips the directory path and extension, then looks for date patterns.
    Returns an ISO 8601 date string (YYYY-MM-DD) or None.
    """
    import os

    stem = os.path.splitext(os.path.basename(filename))[0]

    # Try ISO-style first (most common for programmatic filenames)
    m = _PAT_FILENAME_ISO.search(stem)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        result = _safe_date(year, month, day)
        if result:
            log.info("Extracted date %s from filename '%s' (ISO)", result, filename)
            return result

    # Try DMY named: "28-March-2026" or "28_mar_2026"
    m = _PAT_FILENAME_DMY.search(stem)
    if m:
        day, month_str, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month = _MONTHS.get(month_str[:3])
        if month:
            result = _safe_date(year, month, day)
            if result:
                log.info("Extracted date %s from filename '%s' (DMY named)", result, filename)
                return result

    # Try MDY named: "March-28-2026" or "mar_28_2026"
    m = _PAT_FILENAME_MDY.search(stem)
    if m:
        month_str, day, year = m.group(1).lower(), int(m.group(2)), int(m.group(3))
        month = _MONTHS.get(month_str[:3])
        if month:
            result = _safe_date(year, month, day)
            if result:
                log.info("Extracted date %s from filename '%s' (MDY named)", result, filename)
                return result

    return None
