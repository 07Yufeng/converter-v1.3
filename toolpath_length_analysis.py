"""
Toolpath Length Analysis — for HP/LST -> BEaM MPF converted output
====================================================================

This module walks through a generated MPF text, tracks the tool's
position through G01 (straight line) and CIP (3-point arc) blocks,
and computes the move length for each block.

Only moves executed while the active feed is WELDFEED are measured
against the short-move threshold. Moves under RAPIDFEED (or any other
feed) still update the tracked position (so later WELDFEED moves are
measured correctly), but are not reported.

This module does NOT modify cip_arc_length.py — it imports the
geometry primitives from it (distance_3d, circumradius, central_angle,
parse_nc_line) and applies its own "missing axis = hold previous
position" rule, which matches how modal NC axis words behave (an axis
word that isn't repeated keeps its last commanded value).
"""

import re
import math

from cip_arc_length import (
    parse_nc_line,
    distance_3d,
    circumradius,
    central_angle,
)

# Matches F=WELDFEED, F=RAPIDFEED, or F=<number>, anywhere on the line.
_FEED_PATTERN = re.compile(r'(?i)\bF\s*=\s*(WELDFEED|RAPIDFEED|[+-]?\d+(?:\.\d+)?)')

_GEOMETRY_KEYS = ("X", "Y", "Z", "I1", "J1", "K1")


def _compute_move(params: dict, position: tuple) -> tuple:
    """
    Given parsed NC params and the current tool position, compute the
    move length and the resulting end position.

    Missing axis words inherit the current position (modal behaviour),
    NOT zero. This is the key difference from calculate_cip_arc_length(),
    which is designed for fully-specified CIP lines.

    Returns:
        (length_mm, end_point) or (None, position) if the block has no
        geometry (e.g. a bare 'G01' used only to deselect CIP).
    """
    block_type = params.get("BLOCK_TYPE")

    if not any(k in params for k in _GEOMETRY_KEYS):
        # No axis words at all -> not a real move (e.g. bare 'G01' / 'CIP')
        return None, position

    if block_type == "G01":
        end_point = (
            params.get("X", position[0]),
            params.get("Y", position[1]),
            params.get("Z", position[2]),
        )
        return distance_3d(position, end_point), end_point

    if block_type == "CIP":
        mid_point = (
            params.get("I1", position[0]),
            params.get("J1", position[1]),
            params.get("K1", position[2]),
        )
        end_point = (
            params.get("X", position[0]),
            params.get("Y", position[1]),
            params.get("Z", position[2]),
        )
        try:
            radius = circumradius(position, mid_point, end_point)
            theta = central_angle(position, mid_point, end_point, radius)
            length = radius * theta
        except ValueError:
            # Start/intermediate/end are collinear -> treat as a straight move
            length = distance_3d(position, end_point)
        return length, end_point

    return None, position


def analyze_toolpath_lengths(mpf_text: str, threshold: float = 0.7) -> dict:
    """
    Walk through MPF text and compute move lengths for G01/CIP blocks.

    Args:
        mpf_text  : Full text of the generated MPF program.
        threshold : Minimum acceptable move length, in mm, for moves made
                    under F=WELDFEED. Moves shorter than this are flagged.

    Returns:
        dict with:
            'short_moves'   : list of flagged WELDFEED moves below threshold,
                              each {'n', 'block_type', 'code', 'length_mm', 'feed'}
            'weld_moves'    : list of ALL measured WELDFEED moves (for reference)
            'total_weld_length_mm' : sum of all WELDFEED move lengths
            'threshold'     : threshold used
            'moves_checked' : total number of G01/CIP moves measured under WELDFEED
    """
    current_feed = None
    position = (0.0, 0.0, 0.0)

    short_moves = []
    weld_moves = []

    for raw_line in mpf_text.splitlines():
        # Drop inline comments (everything after the first ';')
        code_part = raw_line.split(";", 1)[0].strip()
        if not code_part:
            continue

        # Track feed mode (WELDFEED / RAPIDFEED / numeric), can appear
        # standalone (e.g. "N120 F=WELDFEED") or inline with a motion block.
        feed_match = _FEED_PATTERN.search(code_part)
        if feed_match:
            current_feed = feed_match.group(1).upper()

        params = parse_nc_line(code_part)
        block_type = params.get("BLOCK_TYPE")
        if block_type not in ("G01", "CIP"):
            continue

        length, end_point = _compute_move(params, position)
        if length is None:
            # No axis words -> not a real move, position unchanged
            continue

        if current_feed == "WELDFEED":
            entry = {
                "n": params.get("N"),
                "block_type": block_type,
                "code": code_part,
                "length_mm": round(length, 4),
                "feed": current_feed,
            }
            weld_moves.append(entry)
            if length < threshold:
                short_moves.append(entry)

        position = end_point

    total_weld_length = sum(m["length_mm"] for m in weld_moves)

    return {
        "short_moves": short_moves,
        "weld_moves": weld_moves,
        "total_weld_length_mm": round(total_weld_length, 4),
        "threshold": threshold,
        "moves_checked": len(weld_moves),
    }
