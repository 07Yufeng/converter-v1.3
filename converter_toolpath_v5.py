import re
import math
import argparse
from pathlib import Path

CONFIG = {
    "defaults": {
        "WELDFEED": 1200.0, "RAPIDFEED": 2000.0, "POWER": 300.0,
        "SPOT": 0.8, "CHARCURVE": 2, "HOPPER": 1, "DISKRPM": 1.0,
        "TRACKWIDTH": 0.9, "OVERLAP": 0.5, "LAYERHEIGHT": 0.33,
        "PAUSE_START": 1.0, "PAUSE": 1.0, "LENGTH": 70.0,
        "WIDTH": 5.0, "HEIGHT": 0.33, "CARRIERGAS": 4.0, "NOZZLEGAS": 10.0,
    },
    "power_formulas": {
        "10vx": lambda power_w: (power_w + 194.55) / 22.487,
        "24vx": lambda power_w: (power_w + 165.73) / 21.832,
    },
    "unit_conversions": {
        "rpm_to_stirrer_percent": lambda rpm: max(0, int(round(rpm * 5))),
        "rpm_to_turntable_percent": lambda rpm: max(0, int(round(rpm * 10))),
    },
    "beam": {
        "laser": {"mode": "MODE_LASER", "power": "PUIS_LASER", "speed": "VIT_TIR", "activate": "COMMANDE_LASER", "fire_on": "M110", "fire_off": "M111"},
        "gas": {"central_h": "H61", "secondary_h": "H62", "central_on": "M180", "central_off": "M181", "secondary_on": "M182", "secondary_off": "M183"},
        "hopper": {
            1: {"sel": "H21", "gas": "H31", "stir": "H41", "turn": "H51", "on": "M160", "off": "M161"},
            2: {"sel": "H22", "gas": "H32", "stir": "H42", "turn": "H52", "on": "M162", "off": "M163"},
            3: {"sel": "H23", "gas": "H33", "stir": "H43", "turn": "H53", "on": "M164", "off": "M165"},
            4: {"sel": "H24", "gas": "H34", "stir": "H44", "turn": "H54", "on": "M166", "off": "M167"},
            5: {"sel": "H25", "gas": "H35", "stir": "H45", "turn": "H55", "on": "M168", "off": "M169"},
        },
    },
    "block_numbers": {"start": 10, "step": 10},
}

class BlockWriter:
    def __init__(self, start=10, step=10):
        self.current = start
        self.step = step
        self.lines = []
    def add(self, code=None, comment=None, number=True):
        if code is None or code == "":
            self.lines.append(f"; {comment}" if comment else "")
            return
        line = f"N{self.current} {code}" if number else code
        if number:
            self.current += self.step
        if comment:
            line += f"    ; {comment}"
        self.lines.append(line)
    def section(self, title):
        self.lines += [";", ";============================================================", f"; {title}", ";============================================================"]
    def render(self):
        return "\n".join(self.lines)

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")

def strip_comment(line: str):
    if ";" in line:
        code, comment = line.split(";", 1)
        return code.rstrip(), comment.strip()
    return line.rstrip(), None

def strip_block_number(code: str) -> str:
    return re.sub(r"^\s*N\d+\s*", "", code, flags=re.IGNORECASE).strip()

def parse_def_vars(lines):
    vars_ = {}
    pat = re.compile(r"^\s*(?:N\d+\s+)?DEF\s+(REAL|INT)\s+([A-Za-z_]\w*)\s*=\s*([^\s;]+)", re.I)
    for line in lines:
        code, _ = strip_comment(line)
        m = pat.search(code)
        if not m:
            continue
        _, name, value = m.groups()
        name = name.upper()
        try:
            vars_[name] = float(value) if ("." in value or "E" in value.upper()) else int(value)
        except ValueError:
            vars_[name] = value
    return vars_

def parse_assignment_from_any_line(line):
    code, _ = strip_comment(line)
    code = strip_block_number(code)
    if not code or code.upper().startswith("DEF "):
        return None
    m = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+?)\s*$", code, re.I)
    if not m:
        return None
    return {"name": m.group(1).upper(), "expr": m.group(2).strip()}

def evaluate_simple_expr(expr, vars_):
    allowed = {k: v for k, v in vars_.items() if isinstance(v, (int, float))}
    allowed.update({"ROUND": round, "CEIL": math.ceil, "FLOOR": math.floor, "ABS": abs})
    try:
        return eval(expr.replace("^", "**"), {"__builtins__": {}}, allowed)
    except Exception:
        return None

def get_var(vars_, key):
    return vars_.get(key, CONFIG["defaults"][key])

def evaluate_shift(trackwidth, overlap):
    return (1.0 - overlap) * trackwidth

def evaluate_line_count(width, shift):
    return 0 if shift == 0 else math.ceil(width / shift)

def normalize_power_head(value):
    v = str(value).strip().lower().replace(" ", "")
    if v in ["10", "10v", "10vx"]:
        return "10vx"
    if v in ["24", "24v", "24vx"]:
        return "24vx"
    raise ValueError("Invalid power head. Use 10Vx or 24Vx.")

def get_power_formula_comment(power_head):
    return "(POWER+194.55)/22.487" if normalize_power_head(power_head) == "10vx" else "(POWER+165.73)/21.832"

def get_gas_settings(power_head):
    if normalize_power_head(power_head) == "10vx":
        return {"central_lpm": 3.0, "secondary_lpm": 6.0, "carrier_lpm": 6.0, "nozzle_lpm": 3.0}
    return {"central_lpm": 6.0, "secondary_lpm": 10.0, "carrier_lpm": 10.0, "nozzle_lpm": 6.0}

def convert_power_to_puis(power_w, power_head):
    return CONFIG["power_formulas"][normalize_power_head(power_head)](power_w)

def map_hopper(hopper_id):
    return CONFIG["beam"]["hopper"].get(hopper_id, CONFIG["beam"]["hopper"][1])

def detect_laser_off_method(lines):
    for line in lines:
        code, _ = strip_comment(line)
        m = re.search(r"TC_LASER_LMD_OFF\s*\((\d+)\)", code, re.I)
        if m:
            return int(m.group(1))
    return 2

def find_axis_numeric(code, axis):
    m = re.search(rf"\b{axis}\s*=?\s*([+-]?\d+(?:\.\d+)?)\b", code, re.I)
    return float(m.group(1)) if m else None

def find_toolpath_start_index(lines):
    """
    General source start used for detecting start position.
    This may begin at END_OF_HEADER because the start-positioning move is before
    the first Slicer layer.
    """
    for i, line in enumerate(lines):
        if "END_OF_HEADER" in line.upper():
            return i + 1
    for i, line in enumerate(lines):
        if re.search(r"Slicer\s+layer\s+0", line, re.I):
            return max(0, i - 3)
    for i, line in enumerate(lines):
        code, _ = strip_comment(line)
        if re.match(r"BLOCK_\d+\s*:", strip_block_number(code), re.I):
            return i
    return None


def find_deposition_start_index(lines):
    """
    Actual toolpath body starts at the first slicer layer, not at END_OF_HEADER.

    This prevents duplicated output:
      ORIGIN section -> POSITIONING section -> TOOLPATH containing Origin again.
    """
    for i, line in enumerate(lines):
        if re.search(r"Slicer\s+layer\s+0", line, re.I):
            return i
    for i, line in enumerate(lines):
        code, _ = strip_comment(line)
        if re.match(r"BLOCK_\d+\s*:", strip_block_number(code), re.I):
            return i
    return find_toolpath_start_index(lines)

def find_source_set_g54(lines):
    set_g54_lines = []
    for line in lines:
        code, _ = strip_comment(line)
        code_clean = strip_block_number(code)
        if re.search(r"^SET_G54\s*\(", code_clean, re.I):
            set_g54_lines.append(code_clean)
    return set_g54_lines[-1] if set_g54_lines else None

def extract_setup_commands(lines):
    """
    Convert the source setup area after '; end of declarations' / before '; END_OF_HEADER'.

    This is intentionally not hardcoded to line numbers. It scans the source setup
    section and applies command rules line-by-line.

    Converted/copied:
    - G71 -> G71
    - TC_TRAFO_ON(0) -> TRAORI
    - TRAFOOF -> TRAFOOF
    - SET_G54(...) -> SET_G54(...)
    - G54 -> G54
    - M0 -> M0
    - F = value -> F=value
    - G01/G02/G03/CIP -> copied with X=,Y=,Z= formatting
    - TC_ACL(1,...) -> M111
    - TC_ACL(2,...) -> M110

    Ignored:
    - TOOL_LEN(...)
    - G500
    - TC_LASER_REQUEST(...)
    - IsFirstBlock logic / GOTOF comments
    - TC_RESET, because no confirmed BEaM equivalent has been provided
    """
    extracted = []
    in_setup = False

    for line in lines:
        raw = line.strip()
        code, comment = strip_comment(line)
        code_clean = strip_block_number(code)

        # Start scanning at the real source setup area
        if raw.startswith(";") and re.search(r"end\s+of\s+declarations", raw, re.I):
            in_setup = True
            extracted.append({"code": None, "comment": "--------------- end of declarations ----------------"})
            continue

        # Stop at END_OF_HEADER
        if raw.startswith(";") and re.search(r"END_OF_HEADER", raw, re.I):
            break

        if not in_setup:
            continue

        if not raw:
            continue

        # Ignore comments in setup, except the end declaration marker above.
        if raw.startswith(";"):
            continue

        if not code_clean:
            continue

        # Explicit ignore list requested / already handled
        if re.match(r"^TOOL_LEN\s*\(", code_clean, re.I):
            continue
        if re.match(r"^G500\b", code_clean, re.I):
            continue
        if re.match(r"^TC_LASER_REQUEST\s*\(", code_clean, re.I):
            continue
        if re.match(r"^TC_RESET\b", code_clean, re.I):
            # No confirmed BEaM equivalent. Ignored for now instead of copying unsafe TRUMPF command.
            continue
        if re.match(r"^IsFirstBlock\s*=", code_clean, re.I):
            continue
        if re.match(r"^GOTOF\b", code_clean, re.I):
            continue

        # Known setup conversion/pass-through
        converted = convert_setup_command(code_clean)
        if converted:
            extracted.append({"code": converted, "comment": None})
            continue

        # M0 operator stop
        if re.match(r"^M0+\b", code_clean, re.I):
            extracted.append({"code": "M0", "comment": None})
            continue

        # Feed assignment such as F = 50000
        if re.match(r"^F\s*=", code_clean, re.I):
            extracted.append({"code": clean_motion_or_command(code_clean), "comment": None})
            continue

        # TC_ACL laser state in setup
        if re.search(r"\bTC_ACL\s*\(", code_clean, re.I):
            converted_acl = convert_tc_acl(code_clean)
            if converted_acl:
                extracted.append({"code": converted_acl, "comment": None})
            continue

        # Motion/setup moves
        if is_motion_to_copy(code_clean):
            extracted.append({"code": clean_motion_or_command(code_clean), "comment": None})
            continue

        # Any remaining setup command has no confirmed rule. Keep as review comment,
        # rather than silently losing it.
        extracted.append({"code": None, "comment": f"REVIEW source setup command not converted: {code_clean}"})

    # Remove consecutive duplicates only for simple string codes.
    deduped = []
    last_code = None
    for item in extracted:
        code = item.get("code")
        if code and code == last_code:
            continue
        deduped.append(item)
        if code:
            last_code = code
        else:
            last_code = None

    return deduped

def extract_origin_section(lines):
    """
    Extract the LST origin section:
      ;-* Origin
      N10 SET_G54(...)
      N11 G54

    The section ends when Start positioning/Slicer/BLOCK_START appears.
    """
    origin_items = []
    in_origin = False

    for line in lines:
        raw = line.strip()
        code, comment = strip_comment(line)
        code_clean = strip_block_number(code)

        if raw.startswith(";") and re.search(r"\bOrigin\b", raw, re.I):
            in_origin = True
            origin_items.append({"code": None, "comment": raw.lstrip(";").strip()})
            continue

        if in_origin and raw.startswith(";") and re.search(r"Start positioning|Slicer|BLOCK_START", raw, re.I):
            break

        if not in_origin:
            continue

        if not code_clean:
            continue

        converted = convert_setup_command(code_clean)
        if converted:
            origin_items.append({"code": converted, "comment": None})
            continue

        # Keep only origin-relevant setup lines here. Other lines will be handled
        # by the normal toolpath parser if needed.

    return origin_items

def is_power_ramp_motion(code):
    return re.search(r"\bPowerUpLen\b|\bPowerDownLen\b", code, re.I) is not None

def detect_start_position(lines):
    start_index = find_toolpath_start_index(lines)
    search_lines = lines[start_index:] if start_index is not None else lines
    for line in search_lines:
        code, _ = strip_comment(line)
        code = strip_block_number(code)
        if not re.search(r"\bG01\b", code, re.I) or is_power_ramp_motion(code):
            continue
        x, y, z = find_axis_numeric(code, "X"), find_axis_numeric(code, "Y"), find_axis_numeric(code, "Z")
        if x is not None or y is not None or z is not None:
            return x or 0.0, y or 0.0, z or 0.0
    return 0.0, 0.0, 0.0

def detect_layer_count(vars_, lines, layerheight):
    for key in ["LAYERS", "LAYER_COUNT", "TOTAL_LAYERS", "NUM_LAYERS", "NLAYERS", "LAYERSNUM"]:
        if key in vars_:
            try:
                value = int(math.ceil(float(vars_[key])))
                if value > 0:
                    return value
            except Exception:
                pass
    for key in ["HEIGHT", "TOTALHEIGHT", "PATCHHEIGHT", "BUILDHEIGHT"]:
        if key in vars_ and layerheight > 0:
            try:
                value = int(math.ceil(float(vars_[key]) / layerheight))
                if value > 0:
                    return value
            except Exception:
                pass
    slicer_layers = set()
    for line in lines:
        m = re.search(r"Slicer\s+layer\s+(\d+)", line, re.I)
        if m:
            slicer_layers.add(int(m.group(1)))
    return max(slicer_layers) + 1 if slicer_layers else 1

def is_ignored_logic_line(code):
    c = strip_block_number(code).strip()
    if not c:
        return True

    # Commands intentionally handled elsewhere or not needed in BEaM MPF body.
    # Do NOT ignore G71, G54, SET_G54, TC_TRAFO_ON, or TRAFOOF here because
    # they now have explicit conversion / pass-through rules.
    patterns = [
        r"^IF\s+IsFirstBlock\s*==\s*1\b",
        r"^IsFirstBlock\s*=",
        r"^ENDIF\b",
        r"^TC_LMD_POWDER\s*\(",
        r"^TC_LASER_LMD_ON\b",
        r"^TC_LASER_LMD_OFF\s*\(",
        r"^TC_LMD_ON\s*\(",
        r"^TC_TIMER\s*\(",
        r"^TC_RESET\b",
        r"^TC_LMD_FOCUSLINE_DATA\s*\(",
        r"^TC_LMD_NOZZLE_GAS\s*\(",
        r"^TC_LASER_REQUEST\s*\(",
        r"^G500\b",
        r"^TOOL_LEN\s*\(",
        r"^GOTOF\b",
    ]
    return any(re.search(p, c, re.I) for p in patterns)

def convert_tc_acl(code):
    m = re.search(r"TC_ACL\s*\((.*?)\)", code, re.I)
    if not m:
        return None
    args = [a.strip() for a in m.group(1).split(",")]
    if not args:
        return None
    laser = CONFIG["beam"]["laser"]
    if args[0] == "1":
        return laser["fire_off"]
    if args[0] == "2" or args[0] =="4":
        return laser["fire_on"]
    return None

def is_motion_to_copy(code):
    c = strip_block_number(code)
    if is_power_ramp_motion(c):
        return False
    return re.search(r"\b(G0[0123]|CIP)\b", c, re.I) is not None

def is_feed_assignment_to_copy(code):
    c = strip_block_number(code)
    return re.match(r"^F\s*=\s*(RAPIDFEED|WELDFEED|[+-]?\d+(?:\.\d+)?)\s*$", c, re.I) is not None

def is_label_to_copy(code):
    return re.match(r"^[A-Za-z_]\w*\s*:\s*$", strip_block_number(code)) is not None

def normalize_equals(code):
    """
    Normalise Siemens-style axis words so X0.201 becomes X=0.201.

    Important:
    - I1/J1/K1 must stay as I1/J1/K1.
    - Do NOT turn I1=40.463 into I=1=40.463.
    """
    c = strip_block_number(code).strip()

    # Handle numbered interpolation words first.
    for addr in ["I1", "J1", "K1"]:
        c = re.sub(rf"\b{addr}\s+(?=[+-]?\d)", f"{addr}=", c, flags=re.I)
        c = re.sub(rf"\b{addr}(?=[+-]?\d)", f"{addr}=", c, flags=re.I)

    # Single-letter axis words.
    for addr in ["X", "Y", "Z", "B", "C", "A", "F"]:
        c = re.sub(rf"\b{addr}\s+(?=[+-]?\d)", f"{addr}=", c, flags=re.I)
        c = re.sub(rf"\b{addr}(?=[+-]?\d)", f"{addr}=", c, flags=re.I)

    # Single-letter arc words must not match I1/J1/K1.
    for addr in ["I", "J", "K"]:
        c = re.sub(rf"\b{addr}(?!\d)\s+(?=[+-]?\d)", f"{addr}=", c, flags=re.I)
        c = re.sub(rf"\b{addr}(?!\d)(?=[+-]?\d)", f"{addr}=", c, flags=re.I)

    return c

def clean_motion_or_command(code):
    return normalize_equals(code)

def convert_setup_command(code):
    """
    Convert/pass through setup commands requested by user:
    - TC_TRAFO_ON(0) -> TRAORI
    - TRAFOOF -> TRAFOOF
    - G71 -> G71
    - G54 -> G54
    - SET_G54(...) -> SET_G54(...)
    - TOOL_LEN(0.0), G500, TC_LASER_REQUEST(1) are ignored elsewhere
    """
    c = strip_block_number(code).strip()

    if re.match(r"^TC_TRAFO_ON\s*\(\s*0\s*\)", c, re.I):
        return "TRAORI"

    if re.match(r"^TRAFOOF\b", c, re.I):
        return "TRAFOOF"

    if re.match(r"^G71\b", c, re.I):
        return "G71"

    if re.match(r"^G54\b", c, re.I):
        return "G54"

    if re.match(r"^SET_G54\s*\(", c, re.I):
        return c

    return None

def parse_toolpath_lines(lines):
    start = find_deposition_start_index(lines)
    selected_lines = lines[start:] if start is not None else lines
    output = []
    skip_origin_chunk = False
    report = {"copied_motion": 0, "laser_on": 0, "laser_off": 0, "ignored_power_ramp": 0, "ignored_tc_acl_other": 0, "ignored_logic": 0, "unhandled": []}
    for raw_line in selected_lines:
        code, comment = strip_comment(raw_line)
        stripped = raw_line.strip()
        code_clean = strip_block_number(code)
        if not stripped:
            continue
        if stripped.startswith(";"):
            if re.search(r"BLOCK_END", stripped, re.I):
                output.append({"code": None, "comment": stripped.lstrip(";").strip()})
                output.append({"blank": True})
            elif re.search(r"BLOCK_START", stripped, re.I):
                if output and not output[-1].get("blank"):
                    output.append({"blank": True})
                output.append({"code": None, "comment": stripped.lstrip(";").strip()})
            elif re.search(r"Slicer layer|Origin|Start positioning|Slicer", stripped, re.I):
                output.append({"code": None, "comment": stripped.lstrip(";").strip()})
            continue
        if not code_clean or re.match(r"^N\d+\s*$", code.strip(), re.I):
            if comment and re.search(r"BLOCK_END", comment, re.I):
                output.append({"code": None, "comment": comment.strip()})
                output.append({"blank": True})
            elif comment and re.search(r"BLOCK_START", comment, re.I):
                if output and not output[-1].get("blank"):
                    output.append({"blank": True})
                output.append({"code": None, "comment": comment.strip()})
            elif comment and re.search(r"Slicer layer|Origin|Start positioning|Slicer", comment, re.I):
                output.append({"code": None, "comment": comment.strip()})
            continue

        # Setup commands that appear inside the post-processed toolpath region
        # should be copied/converted, not reported as unhandled.
        converted_setup = convert_setup_command(code_clean)
        if converted_setup:
            output.append({"code": converted_setup, "comment": None})
            continue

        if is_ignored_logic_line(code_clean):
            report["ignored_logic"] += 1
            continue
        if re.search(r"\bTC_ACL\s*\(", code_clean, re.I):
            converted = convert_tc_acl(code_clean)
            if converted:
                output.append({"code": converted, "comment": None})
                if converted == CONFIG["beam"]["laser"]["fire_on"]: report["laser_on"] += 1
                if converted == CONFIG["beam"]["laser"]["fire_off"]: report["laser_off"] += 1
            else:
                report["ignored_tc_acl_other"] += 1
            continue
        if is_power_ramp_motion(code_clean):
            report["ignored_power_ramp"] += 1
            continue
        if is_feed_assignment_to_copy(code_clean):
            output.append({"code": clean_motion_or_command(code_clean), "comment": None})
            continue
        if is_label_to_copy(code_clean):
            output.append({"code": clean_motion_or_command(code_clean), "comment": None, "number": False})
            continue
        if is_motion_to_copy(code_clean):
            output.append({"code": clean_motion_or_command(code_clean), "comment": None})
            report["copied_motion"] += 1
            continue
        if re.match(r"^M0+\b", code_clean, re.I):
            output.append({"code": "M0", "comment": "Operator stop from source"})
            continue
        if re.match(r"^M30\b", code_clean, re.I):
            continue
        report["unhandled"].append(code_clean)
    return output, report

def parse_common(lines, source_name="uploaded.HP", power_head="24vx"):
    vars_ = parse_def_vars(lines)
    for line in lines:
        assign = parse_assignment_from_any_line(line)
        if assign:
            value = evaluate_simple_expr(assign["expr"], vars_)
            if value is not None:
                vars_[assign["name"]] = value
    parsed = {"source_name": source_name, "vars": vars_, "lines": lines, "power_head": normalize_power_head(power_head)}
    parsed["weldfeed"] = float(get_var(vars_, "WELDFEED"))
    parsed["rapidfeed"] = min(float(get_var(vars_, "RAPIDFEED")), 2000.0)
    parsed["power_w"] = float(get_var(vars_, "POWER"))
    parsed["spot"] = float(get_var(vars_, "SPOT"))
    parsed["charcurve"] = int(get_var(vars_, "CHARCURVE"))
    parsed["hopper"] = int(get_var(vars_, "HOPPER"))
    parsed["diskrpm"] = float(get_var(vars_, "DISKRPM"))
    parsed["trackwidth"] = float(get_var(vars_, "TRACKWIDTH"))
    parsed["overlap"] = float(get_var(vars_, "OVERLAP"))
    parsed["layerheight"] = float(get_var(vars_, "LAYERHEIGHT"))
    parsed["pause_start"] = float(vars_.get("PAUSE_START", vars_.get("PAUSE", CONFIG["defaults"]["PAUSE_START"])))
    parsed["length"] = float(get_var(vars_, "LENGTH"))
    parsed["width"] = float(get_var(vars_, "WIDTH"))
    parsed["shift"] = float(vars_.get("SHIFT", evaluate_shift(parsed["trackwidth"], parsed["overlap"])))
    parsed["line_count"] = int(vars_.get("LINE_COUNT", evaluate_line_count(parsed["width"], parsed["shift"])))
    parsed["layer_count"] = detect_layer_count(vars_, lines, parsed["layerheight"])
    parsed["laser_off_method"] = detect_laser_off_method(lines)
    parsed["source_set_g54"] = find_source_set_g54(lines)
    parsed["origin_section"] = extract_origin_section(lines)
    parsed["start_x"], parsed["start_y"], parsed["start_z"] = detect_start_position(lines)
    parsed["puis_laser"] = convert_power_to_puis(parsed["power_w"], parsed["power_head"])
    gas_settings = get_gas_settings(parsed["power_head"])
    parsed.update(gas_settings)
    parsed["stir_pct"] = CONFIG["unit_conversions"]["rpm_to_stirrer_percent"](parsed["diskrpm"])
    parsed["turn_pct"] = CONFIG["unit_conversions"]["rpm_to_turntable_percent"](parsed["diskrpm"])
    parsed["hopper_map"] = map_hopper(parsed["hopper"])
    parsed["toolpath_items"], parsed["toolpath_report"] = parse_toolpath_lines(lines)
    return parsed

def parse_hp_program(hp_path: Path, power_head="24vx"):
    return parse_common(read_text(hp_path).splitlines(), source_name=hp_path.name, power_head=power_head)

def build_hopper_block(bw, parsed):
    bw.section("POWDER FEEDER / HOPPER")

    bw.add(comment="HOPPER 1")
    bw.add(";H21=1                      ;Hopper 1 selected", number=True)
    bw.add(";H31=20                     ;Channel 1 carrier gas (%) 10% = 1l/min", number=True)
    bw.add(";H41=0                      ;Channel 1 stirrer speed (%)", number=True)
    bw.add(";H51=0                      ;Channel 1 turntable speed (%)\n", number=True)

    bw.add(comment="HOPPER 2")
    bw.add(";H22=2                      ;Hopper 2 selected", number=True)
    bw.add(";H32=20                     ;Channel 2 carrier gas (%) 10% = 1l/min", number=True)
    bw.add(";H42=0                      ;Channel 2 stirrer speed (%)", number=True)
    bw.add(";H52=0                      ;Channel 2 turntable speed (%)\n", number=True)

    bw.add(comment="HOPPER 3")
    bw.add(" H23=3                        ;Hopper 3 selected", number=True)
    bw.add(" H33=20                       ;Channel 3 carrier gas (%) 10% = 1l/min", number=True)
    bw.add(" H43=0                        ;Channel 3 stirrer speed (%)", number=True)
    bw.add(" H53=0                       ; Channel 3 turntable speed (%)\n", number=True)

    bw.add(comment="HOPPER 4")
    bw.add(";H24=4                      ;Hopper 4 selected", number=True)
    bw.add(";H34=20                     ;Channel 4 carrier gas (%) 10% = 1l/min", number=True)
    bw.add(";H44=0                      ;Channel 4 stirrer speed (%)", number=True)
    bw.add(";H54=0                      ;Channel 4 turntable speed (%)\n", number=True)

    bw.add(comment="HOPPER 5")
    bw.add(";H25=5                      ;Hopper 5 selected", number=True)
    bw.add(";H35=20                     ;Channel 5 carrier gas (%) 10% = 1l/min", number=True)
    bw.add(";H45=0                      ;Channel 5 stirrer speed (%)", number=True)
    bw.add(";H55=0                      ;Channel 5 turntable speed (%)\n", number=True)

    bw.add(comment="++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")
    bw.add(comment="+         /!\\ SELECT WHICH HOPPER(S) TO USE /!\\                                +")
    bw.add(comment="++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")

    bw.add(";M160                         ;Hopper 1 ON (turntable + stirrer + gas)")
    bw.add(";M162                         ;Hopper 2 ON (turntable + stirrer + gas)")
    bw.add(" M164                          Hopper 3 ON (turntable + stirrer + gas)")
    bw.add(";M166                         ;Hopper 4 ON (turntable + stirrer + gas)")
    bw.add(";M168                         ;Hopper 5 ON (turntable + stirrer + gas)")

    bw.add("G04 F=10", "Powder stabilization dwell")

def build_toolpath(bw, parsed):
    bw.section("TOOLPATH FROM HP/LST")
    for item in parsed["toolpath_items"]:
        if item.get("blank"):
            bw.add()
        elif item.get("code") is None:
            bw.add(comment=item.get("comment", ""))
        else:
            bw.add(item["code"], comment=item.get("comment"), number=item.get("number", True))

def build_mpf(parsed):
    bw = BlockWriter(start=CONFIG["block_numbers"]["start"], step=CONFIG["block_numbers"]["step"])
    laser, gas, hopper = CONFIG["beam"]["laser"], CONFIG["beam"]["gas"], parsed["hopper_map"]
    report = parsed["toolpath_report"]
    bw.add(comment="************************************************************")
    bw.add(comment=" CONVERTED FROM HP/LST TO MPF")
    bw.add(comment="************************************************************")
    bw.add(comment=f" Source file: {parsed['source_name']}")
    bw.add(comment=f" Power head selected: {parsed['power_head'].upper()}")
    bw.add()
    bw.section("DEFINITIONS")
    bw.add(f"DEF REAL WELDFEED = {parsed['weldfeed']:.3f}", "Deposition feedrate, mm/min")
    bw.add(f"DEF REAL RAPIDFEED = {parsed['rapidfeed']:.3f}", "Travel feedrate, mm/min")
    bw.add(f"DEF REAL POWER = {parsed['power_w']:.3f}", "Laser power from source, W")
    bw.add(f"DEF REAL PUIS_SET = {parsed['puis_laser']:.6f}", f"Calculated using {parsed['power_head'].upper()} formula")
    bw.add(f"DEF REAL SPOTSIZE = {parsed['spot']:.3f}", "Spot diameter, mm")
    
    bw.add(comment="Reference power equation used for PUIS_SET")
    bw.add(comment="***** 10Vx *****" if parsed["power_head"] == "10vx" else "***** 24Vx *****")
    bw.add(comment=f"PUIS_SET = {get_power_formula_comment(parsed['power_head'])}")

    bw.section("LASER MODE")
    bw.add(f"{laser['mode']} 1", "Fixed power mode")
    bw.add(f"{laser['power']} PUIS_SET", "Laser power command")
    bw.add(f"{laser['speed']} = WELDFEED", "Deposition speed")
    bw.add(f"{laser['activate']}", "Activate laser control")
    bw.section("GAS SETUP")
    bw.add(f"{gas['central_h']}={parsed['central_lpm']:.3f}", "Central gas from selected power head, L/min")
    bw.add(f"{gas['secondary_h']}={parsed['secondary_lpm']:.3f}", "Secondary gas, L/min")
    bw.add(f"{gas['central_on']}", "Central gas ON")
    bw.add(f"{gas['secondary_on']}", "Secondary gas ON")
    build_hopper_block(bw, parsed)

    if parsed.get("origin_section"):
        bw.section("ORIGIN (COMMENT OUT IF UNNECESSARY)")
        for item in parsed["origin_section"]:
            if item.get("code") is None:
                bw.add(comment=item.get("comment", ""))
            else:
                bw.add(item["code"])

    bw.section("POSITIONING")
    bw.add("G17 G54", "XY plane and work offset")
    bw.add("G90", "Absolute programming")
    bw.add(f"G01 F=RAPIDFEED X={parsed['start_x']:.3f} Y={parsed['start_y']:.3f}", "Move to detected source start XY")
    bw.add(f"G01 Z={parsed['start_z']:.3f}", "Move to detected source start Z")
    bw.add("M0", "Operator confirmation")
    build_toolpath(bw, parsed)
    bw.section("END PROGRAM")
    bw.add(f"{laser['fire_off']}")
    bw.add(";M161                ;Hopper 1 OFF (turntable + stirrer + gas")
    bw.add(";M163                ;Hopper 2 OFF (turntable + stirrer + gas")
    bw.add(";M165                ;Hopper 3 OFF (turntable + stirrer + gas")
    bw.add(";M167                ;Hopper 4 OFF (turntable + stirrer + gas")
    bw.add(";M169                ;Hopper 5 OFF (turntable + stirrer + gas")
    bw.add(f"{gas['secondary_off']}", "Secondary gas OFF")
    bw.add(f"{gas['central_off']}", "Central gas OFF")
    bw.add("M02", "Program end")
    if report["unhandled"]:
        bw.section("CONVERSION REVIEW")
        bw.add(comment="The following source lines were not converted and should be reviewed:")
        for line in report["unhandled"][:100]:
            bw.add(comment=line)
        if len(report["unhandled"]) > 100:
            bw.add(comment=f"... {len(report['unhandled']) - 100} more unhandled lines omitted from report")
    return bw.render()

def convert_hp_to_mpf_text(hp_text: str, source_name: str = "uploaded.HP", power_head="24vx") -> str:
    return build_mpf(parse_common(hp_text.splitlines(), source_name=Path(source_name).name, power_head=power_head))

def get_conversion_report(hp_text: str, source_name: str = "uploaded.HP", power_head="24vx") -> dict:
    parsed = parse_common(hp_text.splitlines(), source_name=Path(source_name).name, power_head=power_head)
    report = parsed["toolpath_report"]

    # Keep the UI report short. Detailed counters such as copied_motion,
    # laser_on, laser_off, ignored_power_ramp, ignored_tc_acl_other,
    # and ignored_logic are intentionally hidden.
    return {
        "source_name": parsed["source_name"],
        "power_head": parsed["power_head"],
        "puis_laser": parsed["puis_laser"],
        "unhandled_count": len(report["unhandled"]),
        "unhandled": report["unhandled"][:100],
    }

def convert_hp_to_mpf_file(hp_path: Path, out_path: Path, power_head="24vx"):
    out_path.write_text(build_mpf(parse_hp_program(hp_path, power_head=power_head)), encoding="utf-8")
    return out_path

def main():
    parser = argparse.ArgumentParser(description="Convert TRUMPF HP/LST to BEaM MPF")
    parser.add_argument("input", help="Path to source .HP or .LST file")
    parser.add_argument("-o", "--output", help="Output .MPF file path")
    parser.add_argument("--power-head", choices=["10vx", "24vx"], default="24vx", help="Laser head used for power conversion")
    args = parser.parse_args()
    source_path = Path(args.input)
    out_path = Path(args.output) if args.output else source_path.with_name(source_path.stem + "_converted.MPF")
    if not source_path.exists():
        raise FileNotFoundError(f"Input file not found: {source_path}")
    convert_hp_to_mpf_file(source_path, out_path, power_head=args.power_head)
    print(f"Converted: {source_path} -> {out_path}")
    print(f"Power head used: {args.power_head.upper()}")

if __name__ == "__main__":
    main()
