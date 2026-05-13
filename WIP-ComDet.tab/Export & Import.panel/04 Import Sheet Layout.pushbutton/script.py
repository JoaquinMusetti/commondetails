# -*- coding: utf-8 -*-
__title__ = 'Import\nSheet Layout'
__doc__ = ('Reads a sheets_layout.json file and places dependent views and detail lines '
           'on destination model sheets. Supports position, label offset, viewport type, '
           'detail number, title on sheet, and detail line updates in two transactions per sheet.')

import json
import time
from pyrevit import revit, DB, script, forms

doc    = revit.doc
uidoc  = revit.uidoc
output = script.get_output()

# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick JSON file
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(
    file_ext="json",
    title="Select sheets_layout.json"
)
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    layout = json.load(f)

output.print_md("**Sheets in JSON:** {}".format(len(layout)))

# ─────────────────────────────────────────────────────────────────────────────
# 2. Filter sheets to import
# ─────────────────────────────────────────────────────────────────────────────

sheets_in_json = []
for e in layout:
    label = e["sheet_number"] + " --- " + e["sheet_name"]
    sheets_in_json.append(label)
sheets_in_json.sort()

chosen_sheets = forms.SelectFromList.show(
    sheets_in_json,
    title="Select Sheets to Import",
    prompt="Select one or more sheets:",
    multiselect=True
)
if not chosen_sheets:
    script.exit()

chosen_sheet_numbers = []
for s in chosen_sheets:
    parts = s.split(" --- ")
    num = parts[0].strip()
    if num not in chosen_sheet_numbers:
        chosen_sheet_numbers.append(num)

filtered_layout = []
for e in layout:
    if e["sheet_number"] in chosen_sheet_numbers:
        filtered_layout.append(e)
layout = filtered_layout
output.print_md("**Filtered sheets:** {}".format(len(layout)))

# ─────────────────────────────────────────────────────────────────────────────
# 3. Checklist — what to update
# ─────────────────────────────────────────────────────────────────────────────

options = [
    "VIEWPORTS | Position & Title location",
    "VIEWPORTS | Match viewport types",
    "VIEWPORTS | Detail number",
    "VIEWPORTS | Title on sheet",
    "SHEET ELEMENTS | Detail lines  (delete existing and redraw)",
]

chosen_options = forms.SelectFromList.show(
    options,
    title="Select What to Update",
    prompt="Choose what to update on existing viewports and sheet elements:",
    multiselect=True
)
if chosen_options is None:
    script.exit()

DO_POSITION   = any("Position & Title location" in o for o in chosen_options)
DO_VP_TYPE    = any("Match viewport types"      in o for o in chosen_options)
DO_DET_NUMBER = any("Detail number"             in o for o in chosen_options)
DO_TITLE      = any("Title on sheet"            in o for o in chosen_options)
DO_LINES      = any("Detail lines"              in o for o in chosen_options)

output.print_md("**Position & Title location:** {}".format("Yes" if DO_POSITION   else "No"))
output.print_md("**Match viewport types:** {}".format("Yes"      if DO_VP_TYPE    else "No"))
output.print_md("**Detail number:** {}".format("Yes"             if DO_DET_NUMBER else "No"))
output.print_md("**Title on sheet:** {}".format("Yes"            if DO_TITLE      else "No"))
output.print_md("**Detail lines:** {}".format("Yes"              if DO_LINES      else "No"))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Destination model prefix
# ─────────────────────────────────────────────────────────────────────────────

dest_prefix = forms.ask_for_string(
    prompt="Enter the 2-letter prefix of the destination model\n(e.g. AE, AB, AC...)",
    title="Destination Model Prefix",
)
if not dest_prefix:
    script.exit()

dest_prefix = dest_prefix.strip().upper()
output.print_md("**Destination prefix:** `{}`".format(dest_prefix))

# ─────────────────────────────────────────────────────────────────────────────
# 5. Index destination model resources
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("Indexing model resources...")

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

dep_view_by_name = {}
for v in all_views:
    try:
        primary_id = v.GetPrimaryViewId()
        if primary_id != DB.ElementId.InvalidElementId:
            dep_view_by_name[v.Name] = v
    except Exception:
        pass

all_sheets_dest = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
sheet_by_suffix = {}
for s in all_sheets_dest:
    suffix = s.SheetNumber[2:] if len(s.SheetNumber) > 2 else s.SheetNumber
    sheet_by_suffix[suffix] = s

vp_type_by_name = {}
vp_types_collector = DB.FilteredElementCollector(doc)\
    .OfClass(DB.ElementType)\
    .ToElements()
for t in vp_types_collector:
    try:
        p = t.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p:
            name = p.AsString()
            if name:
                family_p = t.get_Parameter(DB.BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
                if family_p and family_p.AsString() == "Viewport":
                    vp_type_by_name[name] = t.Id
    except Exception:
        pass

existing_viewports = DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements()
viewport_by_view_id = {}
for vp in existing_viewports:
    viewport_by_view_id[vp.ViewId.IntegerValue] = vp

all_detail_lines = DB.FilteredElementCollector(doc)\
    .OfClass(DB.CurveElement)\
    .ToElements()

existing_dls_by_sheet = {}
for dl in all_detail_lines:
    try:
        if not isinstance(dl, DB.DetailLine):
            continue
        owner_id = dl.OwnerViewId.IntegerValue
        if owner_id not in existing_dls_by_sheet:
            existing_dls_by_sheet[owner_id] = []
        existing_dls_by_sheet[owner_id].append(dl)
    except Exception:
        pass

line_style_by_name = {}
try:
    detail_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    for sub in detail_cat.SubCategories:
        gs = sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
        if gs:
            line_style_by_name[sub.Name] = gs
except Exception:
    pass

def get_line_style(name):
    if name and name in line_style_by_name:
        return line_style_by_name[name]
    if line_style_by_name:
        return list(line_style_by_name.values())[0]
    return None

output.print_md("**Viewport types indexed:** {}".format(len(vp_type_by_name)))
output.print_md("**Dependent views indexed:** {}".format(len(dep_view_by_name)))
output.print_md("**Sheets indexed:** {}".format(len(sheet_by_suffix)))

# ─────────────────────────────────────────────────────────────────────────────
# 6. Helpers
# ─────────────────────────────────────────────────────────────────────────────

def set_detail_number(vp, value):
    try:
        p = vp.get_Parameter(DB.BuiltInParameter.VIEWPORT_DETAIL_NUMBER)
        if p and not p.IsReadOnly:
            p.Set(value)
    except Exception:
        pass

def get_detail_number(vp):
    try:
        p = vp.get_Parameter(DB.BuiltInParameter.VIEWPORT_DETAIL_NUMBER)
        if p:
            return p.AsString() or ""
    except Exception:
        pass
    return ""

def update_position(vp, center, entry):
    if DO_POSITION:
        vp.SetBoxCenter(center)
        try:
            label_x = entry.get("label_offset_x", 0)
            label_y = entry.get("label_offset_y", 0)
            vp.LabelOffset = DB.XYZ(label_x, label_y, 0)
        except Exception:
            pass
    if DO_VP_TYPE:
        vp_type_name = entry.get("viewport_type", "")
        if vp_type_name and vp_type_name in vp_type_by_name:
            try:
                vp.ChangeTypeId(vp_type_by_name[vp_type_name])
            except Exception:
                pass

def update_title_on_sheet(view, entry):
    title = entry.get("title_on_sheet", "")
    if not title:
        return
    try:
        p = view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
        if p and not p.IsReadOnly:
            current = p.AsString() or ""
            if current != title:
                p.Set(title)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# 7. Process sheets — TWO transactions per sheet
# ─────────────────────────────────────────────────────────────────────────────

vp_created = 0
vp_updated = 0
dl_created = 0
dl_deleted = 0
not_placed = []
errors     = []

for sheet_data in layout:
    sheet_number = sheet_data["sheet_number"]
    sheet_name   = sheet_data["sheet_name"]
    suffix       = sheet_number[2:] if len(sheet_number) > 2 else sheet_number

    if suffix not in sheet_by_suffix:
        not_placed.append("Sheet not found: '{}{}' ({})".format(
            dest_prefix, suffix, sheet_name))
        continue

    target_sheet = sheet_by_suffix[suffix]
    output.print_md("### Sheet {}{}".format(dest_prefix, suffix))

    # ═══════════════════════════════════════════════════════════════════════
    # TRANSACTION 1 — move all to temp + create/update viewports + detail lines
    # ═══════════════════════════════════════════════════════════════════════

    t1 = DB.Transaction(doc, "Import Layout T1 - {}{}".format(dest_prefix, suffix))
    vp_id_to_detail_number = {}

    try:
        t1.Start()

        # ── Step 0: snapshot existing detail numbers ──
        all_sheet_vps = list(DB.FilteredElementCollector(doc, target_sheet.Id)
            .OfClass(DB.Viewport)
            .ToElements())

        original_numbers = {}
        for sv in all_sheet_vps:
            original_numbers[sv.Id.IntegerValue] = get_detail_number(sv)

        # ── Step 1: move ALL existing to temp ──
        timestamp = str(int(time.time()))
        for i, sv in enumerate(all_sheet_vps):
            set_detail_number(sv, "zzz{}_{}".format(timestamp, i))

        # ── Step 2: create/update viewports ──
        processed_ids = []

        for entry in sheet_data["viewports"]:
            view_name = entry["view_name"]
            center    = DB.XYZ(entry["viewport_center_x"], entry["viewport_center_y"], 0)

            if view_name not in dep_view_by_name:
                not_placed.append("View not found: '{}'".format(view_name))
                continue

            target_view    = dep_view_by_name[view_name]
            target_det_num = entry.get("detail_number", "")

            try:
                if target_view.Id.IntegerValue in viewport_by_view_id:
                    existing_vp = viewport_by_view_id[target_view.Id.IntegerValue]
                    if existing_vp.SheetId.IntegerValue == target_sheet.Id.IntegerValue:
                        anything = DO_POSITION or DO_VP_TYPE or DO_DET_NUMBER or DO_TITLE
                        if not anything:
                            not_placed.append("No update selected, skipped: '{}'".format(
                                view_name))
                            continue
                        update_position(existing_vp, center, entry)
                        if DO_TITLE:
                            update_title_on_sheet(target_view, entry)
                        if DO_DET_NUMBER and target_det_num:
                            vp_id_to_detail_number[existing_vp.Id.IntegerValue] = target_det_num
                        processed_ids.append(existing_vp.Id.IntegerValue)
                        output.print_md("  🔄 Updated: *{}*".format(view_name))
                        vp_updated += 1
                    else:
                        not_placed.append(
                            "'{}' is placed on a different sheet, not moved.".format(view_name))
                else:
                    vp = DB.Viewport.Create(doc, target_sheet.Id, target_view.Id, center)
                    update_position(vp, center, entry)
                    update_title_on_sheet(target_view, entry)
                    if DO_DET_NUMBER and target_det_num:
                        vp_id_to_detail_number[vp.Id.IntegerValue] = target_det_num
                    processed_ids.append(vp.Id.IntegerValue)
                    output.print_md("  ✅ Created: *{}*".format(view_name))
                    vp_created += 1

            except Exception as e:
                errors.append("Viewport *{}*: {}".format(view_name, str(e)))

        # Restore non-processed viewports to original numbers
        for sv in all_sheet_vps:
            if sv.Id.IntegerValue not in processed_ids:
                original = original_numbers.get(sv.Id.IntegerValue, "")
                if original:
                    set_detail_number(sv, original)

        # ── Detail Lines ──
        if DO_LINES:
            dl_data = sheet_data.get("detail_lines", [])
            if dl_data:
                sheet_id     = target_sheet.Id.IntegerValue
                existing_dls = existing_dls_by_sheet.get(sheet_id, [])

                deleted_count = 0
                for dl in existing_dls:
                    try:
                        doc.Delete(dl.Id)
                        deleted_count += 1
                    except Exception:
                        pass
                dl_deleted += deleted_count
                if deleted_count:
                    output.print_md("  🗑️ {} detail lines deleted".format(deleted_count))

                sheet_dl_created = 0
                for dl_entry in dl_data:
                    try:
                        p0  = dl_entry["p0"]
                        p1  = dl_entry["p1"]
                        pt0 = DB.XYZ(p0[0], p0[1], p0[2])
                        pt1 = DB.XYZ(p1[0], p1[1], p1[2])
                        if pt0.DistanceTo(pt1) < 1e-4:
                            continue
                        line   = DB.Line.CreateBound(pt0, pt1)
                        new_dl = doc.Create.NewDetailCurve(target_sheet, line)
                        style  = get_line_style(dl_entry.get("line_style", ""))
                        if style:
                            new_dl.LineStyle = style
                        sheet_dl_created += 1
                    except Exception as e:
                        errors.append("Detail line on sheet {}: {}".format(
                            sheet_number, str(e)))

                dl_created += sheet_dl_created
                output.print_md("  📐 {} detail lines created".format(sheet_dl_created))

        t1.Commit()
        output.print_md("  ✔ T1 committed")

    except Exception as e:
        try:
            if t1.HasStarted() and not t1.HasEnded():
                t1.RollBack()
        except Exception:
            pass
        errors.append("Sheet {}{} T1 rolled back: {}".format(dest_prefix, suffix, str(e)))
        output.print_md("  ❌ T1 rolled back: {}".format(str(e)))
        continue

    # ═══════════════════════════════════════════════════════════════════════
    # TRANSACTION 2 — assign final detail numbers
    # ═══════════════════════════════════════════════════════════════════════

    if not DO_DET_NUMBER or not vp_id_to_detail_number:
        continue

    t2 = DB.Transaction(doc, "Import Detail Numbers T2 - {}{}".format(dest_prefix, suffix))

    try:
        t2.Start()

        all_sheet_vps_t2 = list(DB.FilteredElementCollector(doc, target_sheet.Id)
            .OfClass(DB.Viewport)
            .ToElements())

        timestamp2 = str(int(time.time())) + "b"
        for i, sv in enumerate(all_sheet_vps_t2):
            set_detail_number(sv, "zzz{}_{}".format(timestamp2, i))

        for sv in all_sheet_vps_t2:
            vid = sv.Id.IntegerValue
            if vid in vp_id_to_detail_number:
                det_num = vp_id_to_detail_number[vid]
                set_detail_number(sv, det_num)
                output.print_md("  🔢 Detail number set: *{}*".format(det_num))

        t2.Commit()
        output.print_md("  ✔ T2 committed")

    except Exception as e:
        try:
            if t2.HasStarted() and not t2.HasEnded():
                t2.RollBack()
        except Exception:
            pass
        errors.append("Sheet {}{} T2 rolled back: {}".format(dest_prefix, suffix, str(e)))
        output.print_md("  ❌ T2 rolled back: {}".format(str(e)))

# ─────────────────────────────────────────────────────────────────────────────
# 8. Final report
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("✅ **{}** viewports created  |  🔄 **{}** updated".format(
    vp_created, vp_updated))
output.print_md("📐 **{}** detail lines created  |  🗑️ **{}** deleted".format(
    dl_created, dl_deleted))

if not_placed:
    output.print_md("⏭️ **{} skipped:**".format(len(not_placed)))
    for s in not_placed:
        output.print_md("  - {}".format(s))

if errors:
    output.print_md("❌ **{} errors:**".format(len(errors)))
    for e in errors:
        output.print_md("  - {}".format(e))