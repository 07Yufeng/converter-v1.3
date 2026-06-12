"""
CIP / G01 Length Calculator — Siemens Sinumerik
================================================
Parses NC code lines in the formats:
    N#### CIP X=... Y=... Z=... I1=... J1=... K1=... B=... C=...
    N#### G01 X=... Y=... Z=... B=... C=...

CIP parameters:
    X, Y, Z   — end point (absolute, relative to workpiece zero)
    I1, J1, K1 — intermediate point (absolute, relative to workpiece zero)
    B, C      — rotary axis angles (parsed but not used in geometry)

G01 parameters:
    X, Y, Z   — end point (absolute, relative to workpiece zero)
    B, C      — rotary axis angles (parsed but not used in geometry)

For both block types, the start point is the previous tool position
(must be provided, or tracked automatically by the CLI between lines).

- CIP arc length is calculated using the circumradius formula on the
  3-point circle defined by: start → intermediate → end.
- G01 length is simply the straight-line (Euclidean) distance between
  the previous position and the new end point.
"""

import re
import math


def parse_nc_line(line: str) -> dict:
    """
    Parse a CIP or G01 NC code line into a parameter dictionary.
    Handles formats like:
        N1020 G01 X=3.607 Y=0.200 Z=0.00 B=0.000 C=0.000
        CIP I1=44.65 J1=24.65 X80 Y120
        N100 CIP I1=44.65 J1=24.65 X=80 Y=120 Z=0 B=0 C=0

    Returns a dict with all matched axis values plus 'BLOCK_TYPE'
    ('CIP', 'G01', or None if not recognised) and 'N' (line number, if present).
    """
    params = {}
    line = line.strip()

    # Capture optional leading line number, e.g. N1020
    n_match = re.match(r'(?i)^N(\d+)\s*', line)
    if n_match:
        params['N'] = int(n_match.group(1))
        line = line[n_match.end():]

    # Identify block type
    if re.match(r'(?i)^CIP\b', line):
        params['BLOCK_TYPE'] = 'CIP'
        line = re.sub(r'(?i)^CIP\s*', '', line)
    elif re.match(r'(?i)^G0?1\b', line):
        params['BLOCK_TYPE'] = 'G01'
        line = re.sub(r'(?i)^G0?1\s*', '', line)
    else:
        params['BLOCK_TYPE'] = None

    # Match I1, J1, K1 first (multi-char keys), only relevant for CIP
    for key in ['I1', 'J1', 'K1']:
        match = re.search(rf'(?i){key}\s*=?\s*(-?\d+(?:\.\d+)?)', line)
        if match:
            params[key.upper()] = float(match.group(1))

    # Match single-char keys: X, Y, Z, B, C
    # Exclude positions already captured as I1/J1/K1
    remaining = re.sub(r'(?i)[IJK]1\s*=?\s*-?\d+(?:\.\d+)?', '', line)
    for key in ['X', 'Y', 'Z', 'B', 'C']:
        match = re.search(rf'(?i)(?<![A-Z]){key}\s*=?\s*(-?\d+(?:\.\d+)?)', remaining)
        if match:
            params[key.upper()] = float(match.group(1))

    return params


# Backwards-compatible alias
def parse_cip_line(cip_line: str) -> dict:
    """Alias for parse_nc_line, kept for backwards compatibility."""
    return parse_nc_line(cip_line)


def distance_3d(p1: tuple, p2: tuple) -> float:
    """Euclidean distance between two 3D points."""
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))


def triangle_area_3d(p1: tuple, p2: tuple, p3: tuple) -> float:
    """
    Area of triangle formed by three 3D points using cross product.
    Area = 0.5 * |AB × AC|
    """
    ax, ay, az = (p2[i] - p1[i] for i in range(3))
    bx, by, bz = (p3[i] - p1[i] for i in range(3))

    # Cross product AB × AC
    cx = ay * bz - az * by
    cy = az * bx - ax * bz
    cz = ax * by - ay * bx

    magnitude = math.sqrt(cx**2 + cy**2 + cz**2)
    return 0.5 * magnitude


def circumradius(p1: tuple, p2: tuple, p3: tuple) -> float:
    """
    Circumradius of triangle formed by three points.
    R = (a * b * c) / (4 * Area)
    """
    a = distance_3d(p1, p2)
    b = distance_3d(p2, p3)
    c = distance_3d(p1, p3)
    area = triangle_area_3d(p1, p2, p3)

    if area < 1e-12:
        raise ValueError(
            "The three points are collinear — no unique circle can be defined. "
            "Check that your intermediate point is not on the straight line "
            "between start and end."
        )

    return (a * b * c) / (4 * area)


def central_angle(p_start: tuple, p_mid: tuple, p_end: tuple, radius: float) -> float:
    """
    Calculate the central angle (in radians) swept by the arc.
    Uses the inscribed angle theorem:
        chord(start→end) = 2R * sin(θ/2)
    Then checks which side the midpoint is on to determine
    whether the arc is < 180° or > 180°.
    """
    chord = distance_3d(p_start, p_end)

    # Clamp to [-1, 1] to avoid floating point errors in arcsin
    sin_half = min(1.0, max(-1.0, chord / (2 * radius)))
    half_angle = math.asin(sin_half)
    theta = 2 * half_angle  # This gives the minor arc angle (≤ π)

    # Determine if the arc is major (> 180°) by checking if the
    # intermediate point is on the far side of the chord midpoint
    chord_mid = tuple((p_start[i] + p_end[i]) / 2 for i in range(3))
    dist_mid_to_chord_mid = distance_3d(p_mid, chord_mid)
    dist_start_to_chord_mid = distance_3d(p_start, chord_mid)

    # If the intermediate point is further from the chord midpoint
    # than the radius of the chord's semicircle, it's a major arc
    if dist_mid_to_chord_mid > dist_start_to_chord_mid:
        theta = 2 * math.pi - theta

    return theta


def calculate_cip_arc_length(
    cip_line: str,
    start_x: float,
    start_y: float,
    start_z: float = 0.0
) -> dict:
    """
    Main function: parse a CIP line and compute the arc length.

    Args:
        cip_line  : The NC code line, e.g. 'CIP X=80 Y=120 Z=0 I1=44.65 J1=24.65 K1=0'
        start_x   : X coordinate of the start point (machine position before CIP)
        start_y   : Y coordinate of the start point
        start_z   : Z coordinate of the start point (default 0)

    Returns:
        dict with all parsed values plus arc_length_mm and arc_angle_deg
    """
    params = parse_cip_line(cip_line)

    # Extract points — default Z/K1 to 0 if not provided
    p_start = (start_x, start_y, start_z)
    p_mid   = (params.get('I1', 0.0), params.get('J1', 0.0), params.get('K1', 0.0))
    p_end   = (params.get('X',  0.0), params.get('Y',  0.0), params.get('Z',  0.0))

    R     = circumradius(p_start, p_mid, p_end)
    theta = central_angle(p_start, p_mid, p_end, R)
    arc_length = R * theta

    return {
        'parsed_params':  params,
        'start_point':    p_start,
        'intermediate':   p_mid,
        'end_point':      p_end,
        'radius_mm':      round(R, 6),
        'diameter_mm':    round(2 * R, 6),
        'arc_angle_deg':  round(math.degrees(theta), 4),
        'arc_length_mm':  round(arc_length, 6),
        'rotary_B':       params.get('B', None),
        'rotary_C':       params.get('C', None),
    }


def calculate_g01_length(
    g01_line: str,
    start_x: float,
    start_y: float,
    start_z: float = 0.0
) -> dict:
    """
    Parse a G01 line and compute the straight-line move length.

    Args:
        g01_line  : The NC code line, e.g. 'N1020 G01 X=3.607 Y=0.200 Z=0.00 B=0.000 C=0.000'
        start_x   : X coordinate of the start point (previous tool position)
        start_y   : Y coordinate of the start point
        start_z   : Z coordinate of the start point (default 0)

    Returns:
        dict with parsed values plus length_mm
    """
    params = parse_nc_line(g01_line)

    p_start = (start_x, start_y, start_z)
    p_end = (
        params.get('X', start_x),
        params.get('Y', start_y),
        params.get('Z', start_z),
    )

    length = distance_3d(p_start, p_end)

    return {
        'parsed_params': params,
        'start_point':   p_start,
        'end_point':     p_end,
        'length_mm':     round(length, 6),
        'rotary_B':      params.get('B', None),
        'rotary_C':      params.get('C', None),
    }


def calculate_nc_line(
    nc_line: str,
    start_x: float,
    start_y: float,
    start_z: float = 0.0
) -> dict:
    """
    Unified entry point. Detects whether the line is CIP or G01
    and routes to the appropriate calculation. Returns a dict
    that always includes 'BLOCK_TYPE', 'end_point', and 'length_mm'
    (length_mm == arc_length_mm for CIP).
    """
    params = parse_nc_line(nc_line)
    block_type = params.get('BLOCK_TYPE')

    if block_type == 'CIP':
        result = calculate_cip_arc_length(nc_line, start_x, start_y, start_z)
        result['length_mm'] = result['arc_length_mm']
        result['BLOCK_TYPE'] = 'CIP'
    elif block_type == 'G01':
        result = calculate_g01_length(nc_line, start_x, start_y, start_z)
        result['BLOCK_TYPE'] = 'G01'
    else:
        raise ValueError(
            f"Unrecognised block type in line: '{nc_line}'. "
            "Expected the line to start with CIP or G01/G1 "
            "(optionally preceded by an N#### line number)."
        )

    return result



def print_result(result: dict) -> None:
    """Pretty-print the calculation result for CIP or G01."""
    block_type = result.get('BLOCK_TYPE', 'CIP')

    print("\n" + "=" * 52)
    if block_type == 'G01':
        print("  G01 Straight Line Length — Sinumerik")
        print("=" * 52)
        print(f"  Start point      : {result['start_point']}")
        print(f"  End point        : {result['end_point']}")
        print("-" * 52)
        print(f"  Length           : {result['length_mm']} mm")
    else:
        print("  CIP Arc Length Calculation — Sinumerik")
        print("=" * 52)
        print(f"  Start point      : {result['start_point']}")
        print(f"  Intermediate (I1/J1/K1) : {result['intermediate']}")
        print(f"  End point        : {result['end_point']}")
        print("-" * 52)
        print(f"  Radius           : {result['radius_mm']} mm")
        print(f"  Diameter         : {result['diameter_mm']} mm")
        print(f"  Arc angle        : {result['arc_angle_deg']}°")
        print(f"  Arc length       : {result['arc_length_mm']} mm")

    if result['rotary_B'] is not None:
        print(f"  Rotary B         : {result['rotary_B']}° (not used in geometry)")
    if result['rotary_C'] is not None:
        print(f"  Rotary C         : {result['rotary_C']}° (not used in geometry)")
    print("=" * 52 + "\n")


# ── Interactive CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  CIP / G01 Length Calculator — Siemens Sinumerik")
    print("  Enter NC lines one at a time (CIP or G01).")
    print("  The end point of each line becomes the start point of the next.")
    print("  Enter 'q' to quit, or 'r' to reset/re-enter the starting position.\n")

    # Initial position
    print("-" * 52)
    sx = float(input("  Starting X : "))
    sy = float(input("  Starting Y : "))
    sz_input = input("  Starting Z [0]: ").strip()
    sz = float(sz_input) if sz_input else 0.0
    current = (sx, sy, sz)

    total_length = 0.0

    while True:
        print("-" * 52)
        print(f"  Current position: {current}")
        nc_line = input("  NC line (CIP/G01, or 'q'/'r'): ").strip()

        if nc_line.lower() == 'q':
            break

        if nc_line.lower() == 'r':
            sx = float(input("  Starting X : "))
            sy = float(input("  Starting Y : "))
            sz_input = input("  Starting Z [0]: ").strip()
            sz = float(sz_input) if sz_input else 0.0
            current = (sx, sy, sz)
            total_length = 0.0
            continue

        try:
            result = calculate_nc_line(nc_line, *current)
            print_result(result)

            total_length += result['length_mm']
            print(f"  Running total length: {round(total_length, 6)} mm\n")

            # Advance current position to this block's end point
            current = result['end_point']

        except ValueError as e:
            print(f"\n  Error: {e}\n")
        except KeyError as e:
            print(f"\n  Missing parameter in line: {e}\n")
        except Exception as e:
            print(f"\n  Unexpected error: {e}\n")

