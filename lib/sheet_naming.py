# -*- coding: utf-8 -*-
"""Sheet-number naming helpers shared across the Common Details workflow.

Custom sheet-number convention: <2-char prefix> + base + optional '.<Letter>'.
The base itself contains dots (e.g. '10.65'), and a trailing single-letter
segment is the BUILDING TAG ('AX10.65.E' -> building E). Shared sheets carry
no trailing tag ('AX10.65'). The CD model holds every building's custom sheets
(all AX-prefixed, distinguished only by their trailing tag) plus the shared
ones, so filtering to a destination building must key off the trailing tag, not
the leading prefix.

Single source of truth for both Pre-Import Audit and Import Details.
"""

SHARED_PREFIX = u"AX"


def building_tag(sheet_number):
    """Trailing building letter of a sheet number, or None for a shared sheet.

    Number = <2-char prefix> + base + optional '.<Letter>'. The base itself
    contains dots (e.g. '10.65'), so only a final segment that is exactly one
    alphabetic char counts as a building tag ('10.65.E' -> 'E', '10.65' -> None)."""
    if not sheet_number:
        return None
    suffix = sheet_number[2:] if len(sheet_number) > 2 else sheet_number
    parts = suffix.split(u".")
    last = parts[-1] if parts else u""
    if len(last) == 1 and last.isalpha():
        return last.upper()
    return None


def dest_building_letter(prefix):
    """Building letter implied by a 2-char destination prefix, else None.

    'AE'->'E', 'AS'->'S'. Shared 'AX' and Common Details 'CD' map to no single
    building -> None (keep everything, don't over-filter)."""
    if (len(prefix) == 2 and prefix[0] == u"A"
            and prefix != SHARED_PREFIX and prefix[1].isalpha()):
        return prefix[1]
    return None
