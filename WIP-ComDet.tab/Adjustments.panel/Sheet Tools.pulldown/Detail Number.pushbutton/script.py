# -*- coding: utf-8 -*-
__title__ = 'Apply Detail\nNumbers'
__doc__ = ('Reads a positions.json file and assigns detail numbers to viewports on selected sheets. '
           'Uses zone matching (viewport bottom-right corner) as primary strategy '
           'and nearest neighbor as fallback.')

"""
APPLY DETAIL NUMBERS
=====================
Reads positions.json and assigns detail numbers to viewports.
- Primary: zone matching — viewport bottom-right corner (excluding title)
  with offset left and up
- Fallback: nearest neighbor
- Viewports already correct are restored after temp assignment
"""

import json
import math
import time
import os as _os
import sys
_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

from pyrevit import revit, DB, script, forms

doc    = revit.doc
output = script.get_output()

OFFSET_X = -2.5 / 12.0  # ft — offset left
OFFSET_Y =  1.0 / 12.0  # ft — offset up

# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick positions.json
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(
    file_ext="json",
    title="Select positions.json"
)
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    data = json.load(f)

zones = data["zones"]
output.print_md("**Reference sheet:** `{}`".format(data.get("reference_sheet", "")))
output.print_md("**Zones loaded:** {}".format(len(zones)))

# ─────────────────────────────────────────────────────────────────────────────
# 2. Select sheets
# ─────────────────────────────────────────────────────────────────────────────

all_sheets = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
all_sheets = list(all_sheets)
all_sheets.sort(key=lambda s: s.SheetNumber)

sheet_options   = []
sheet_by_option = {}
for s in all_sheets:
    label = "{} - {}".format(s.SheetNumber, s.Name)
    sheet_options.append(label)
    sheet_by_option[label] = s

chosen = ui.pick_list(
    sheet_options,
    "Apply Detail Numbers",
    multiselect=True
)
if not chosen:
    script.exit()

selected_sheets = [sheet_by_option[c] for c in chosen]
output.print_md("**Sheets selected:** {}".format(len(selected_sheets)))

# ─────────────────────────────────────────────────────────────────────────────
# 3. Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_detail_number(vp):
    try:
        p = vp.get_Parameter(DB.BuiltInParameter.VIEWPORT_DETAIL_NUMBER)
        if p:
            return p.AsString() or ""
    except Exception:
        pass
    return ""

def set_detail_number(vp, value):
    try:
        p = vp.get_Parameter(DB.BuiltInParameter.VIEWPORT_DETAIL_NUMBER)
        if p and not p.IsReadOnly:
            p.Set(value)
    except Exception:
        pass

def get_bottom_right(vp):
    try:
        outline = vp.GetBoxOutline()
        if outline:
            return DB.XYZ(
                outline.MaximumPoint.X + OFFSET_X,
                outline.MinimumPoint.Y + OFFSET_Y,
                0
            )
    except Exception:
        pass
    try:
        return vp.GetBoxCenter()
    except Exception:
        pass
    return None

def find_zone(x, y, zones):
    for zone in zones:
        if (zone["x_min"] <= x <= zone["x_max"] and
                zone["y_min"] <= y <= zone["y_max"]):
            return zone["detail_number"], "zone"
    return None, None

def find_nearest(x, y, zones):
    best_num  = None
    best_dist = float("inf")
    for zone in zones:
        dist = math.sqrt((x - zone["x_ref"]) ** 2 + (y - zone["y_ref"]) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_num  = zone["detail_number"]
    return best_num, best_dist

def find_best_matches(sheet_vps, zones):
    zone_candidates = []
    no_zone_vps     = []

    for vp in sheet_vps:
        br = get_bottom_right(vp)
        if br is None:
            continue
        det_num, method = find_zone(br.X, br.Y, zones)
        if det_num:
            zone_candidates.append((0.0, vp, det_num, br.X, br.Y, "zone"))
        else:
            no_zone_vps.append((vp, br))

    # Assign zone matches — one-to-one
    assigned_vp_ids   = []
    assigned_det_nums = []
    assignments       = []

    for dist, vp, det_num, x, y, method in zone_candidates:
        vid = vp.Id.IntegerValue
        if vid in assigned_vp_ids:
            continue
        if det_num in assigned_det_nums:
            continue
        assigned_vp_ids.append(vid)
        assigned_det_nums.append(det_num)
        assignments.append([vp, det_num, x, y, method])

    # Nearest neighbor fallback
    nn_candidates = []
    for vp, br in no_zone_vps:
        if vp.Id.IntegerValue in assigned_vp_ids:
            continue
        det_num, dist = find_nearest(br.X, br.Y, zones)
        if det_num:
            nn_candidates.append((dist, vp, det_num, br.X, br.Y))

    nn_candidates.sort(key=lambda c: c[0])

    for dist, vp, det_num, x, y in nn_candidates:
        vid = vp.Id.IntegerValue
        if vid in assigned_vp_ids:
            continue
        if det_num in assigned_det_nums:
            continue
        assigned_vp_ids.append(vid)
        assigned_det_nums.append(det_num)
        assignments.append([vp, det_num, x, y, "nearest ({:.3f} ft)".format(dist)])

    return assignments, assigned_vp_ids

# ─────────────────────────────────────────────────────────────────────────────
# 4. Process sheets — two transactions per sheet
# ─────────────────────────────────────────────────────────────────────────────

total_assigned  = 0
total_no_match  = 0
total_no_change = 0
errors          = []

for sheet in selected_sheets:
    output.print_md("### Sheet {} - {}".format(sheet.SheetNumber, sheet.Name))

    vp_ids = sheet.GetAllViewports()
    if not vp_ids:
        output.print_md("  *(no viewports)*")
        continue

    all_vps = []
    for vp_id in vp_ids:
        vp = doc.GetElement(vp_id)
        if vp:
            all_vps.append(vp)

    matched, matched_ids = find_best_matches(all_vps, zones)

    # Report unmatched
    for vp in all_vps:
        if vp.Id.IntegerValue not in matched_ids:
            br = get_bottom_right(vp)
            output.print_md("  ⚠️ No match — vp {} at ({:.3f}, {:.3f})".format(
                vp.Id.IntegerValue,
                br.X if br else 0,
                br.Y if br else 0))
            total_no_match += 1

    # Filter already correct
    assignments = []
    for vp, det_num, x, y, method in matched:
        current = get_detail_number(vp)
        if current == det_num:
            total_no_change += 1
        else:
            assignments.append([vp, det_num])
            output.print_md("  → ({:.3f}, {:.3f}) [{}]  → *{}*".format(
                x, y, method, det_num))

    if not assignments:
        output.print_md("  ✔ All detail numbers already correct")
        continue

    # ── T1: move all to temp + snapshot originals ──
    t1 = DB.Transaction(doc, "Apply Numbers T1 - {}".format(sheet.SheetNumber))
    vp_id_to_number  = {}
    vp_id_to_restore = {}

    try:
        t1.Start()
        all_sheet_vps = list(DB.FilteredElementCollector(doc, sheet.Id)
            .OfClass(DB.Viewport).ToElements())

        # Snapshot all current numbers before moving to temp
        for sv in all_sheet_vps:
            vp_id_to_restore[sv.Id.IntegerValue] = get_detail_number(sv)

        timestamp = str(int(time.time()))
        for i, sv in enumerate(all_sheet_vps):
            set_detail_number(sv, "zzz{}_{}".format(timestamp, i))

        for vp, det_num in assignments:
            vp_id_to_number[vp.Id.IntegerValue] = det_num

        t1.Commit()

    except Exception as e:
        try:
            if t1.HasStarted() and not t1.HasEnded():
                t1.RollBack()
        except Exception:
            pass
        errors.append("Sheet {} T1: {}".format(sheet.SheetNumber, str(e)))
        output.print_md("  ❌ T1 rolled back: {}".format(str(e)))
        continue

    # ── T2: assign final numbers + restore unchanged ──
    t2 = DB.Transaction(doc, "Apply Numbers T2 - {}".format(sheet.SheetNumber))

    try:
        t2.Start()
        all_sheet_vps_t2 = list(DB.FilteredElementCollector(doc, sheet.Id)
            .OfClass(DB.Viewport).ToElements())

        timestamp2 = str(int(time.time())) + "b"
        for i, sv in enumerate(all_sheet_vps_t2):
            set_detail_number(sv, "zzz{}_{}".format(timestamp2, i))

        for sv in all_sheet_vps_t2:
            vid = sv.Id.IntegerValue
            if vid in vp_id_to_number:
                set_detail_number(sv, vp_id_to_number[vid])
                total_assigned += 1
            elif vid in vp_id_to_restore:
                original = vp_id_to_restore[vid]
                if original:
                    set_detail_number(sv, original)

        t2.Commit()
        output.print_md("  ✔ Committed")

    except Exception as e:
        try:
            if t2.HasStarted() and not t2.HasEnded():
                t2.RollBack()
        except Exception:
            pass
        errors.append("Sheet {} T2: {}".format(sheet.SheetNumber, str(e)))
        output.print_md("  ❌ T2 rolled back: {}".format(str(e)))

# ─────────────────────────────────────────────────────────────────────────────
# 5. Final report
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("🔢 **{}** detail numbers assigned".format(total_assigned))
output.print_md("✔ **{}** already correct, no change".format(total_no_change))

if total_no_match:
    output.print_md("⚠️ **{}** viewport(s) with no match".format(total_no_match))

if errors:
    output.print_md("❌ **{} errors:**".format(len(errors)))
    for e in errors:
        output.print_md("  - {}".format(e))