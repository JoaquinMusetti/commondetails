# -*- coding: utf-8 -*-
__title__ = 'Audit\nSheet Layout'
__doc__ = ('Compares sheets_layout.json against the destination model. '
           'Reports missing sheets, views not found, orphaned viewports, detail number mismatches, '
           'and viewport type mismatches. Warns about viewport types missing from the destination '
           'model and offers to remove or delete orphaned viewports.')

import sys
import os as _os
import json
from pyrevit import revit, DB, script, forms

sys.path.append(_os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))),
    'lib'
))
from magictools import ui

doc    = revit.doc
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
# 2. Destination model prefix
# ─────────────────────────────────────────────────────────────────────────────

dest_prefix = ui.ask_for_string(
    prompt="Enter the 2-letter prefix of the destination model\n(e.g. AE, AB, AC...)",
    title="Audit Sheet Layout",
)
if not dest_prefix:
    script.exit()

dest_prefix = dest_prefix.strip().upper()
output.print_md("**Destination prefix:** `{}`".format(dest_prefix))

# ─────────────────────────────────────────────────────────────────────────────
# 3. Index destination model resources
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

all_sheets = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
sheet_by_suffix = {}
for s in all_sheets:
    suffix = s.SheetNumber[2:] if len(s.SheetNumber) > 2 else s.SheetNumber
    sheet_by_suffix[suffix] = s

all_viewports = DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements()
viewport_by_view_id = {}
for vp in all_viewports:
    viewport_by_view_id[vp.ViewId.IntegerValue] = vp

viewports_by_sheet = {}
for vp in all_viewports:
    sid = vp.SheetId.IntegerValue
    if sid not in viewports_by_sheet:
        viewports_by_sheet[sid] = []
    viewports_by_sheet[sid].append(vp)

# Index viewport types available in the destination model
vp_type_by_name = {}
all_vp_types = DB.FilteredElementCollector(doc)\
    .OfClass(DB.ElementType)\
    .ToElements()
for t in all_vp_types:
    try:
        # Only keep Viewport family types
        if t.FamilyName == "Viewport":
            vp_type_by_name[t.Name] = t
    except Exception:
        pass

# Fallback: also index from existing viewport type IDs directly
for vp in all_viewports:
    try:
        type_id = vp.GetTypeId()
        vp_type = doc.GetElement(type_id)
        if vp_type is not None:
            p = vp_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
            name = (p.AsString() if p else None) or vp_type.Name
            if name and name not in vp_type_by_name:
                vp_type_by_name[name] = vp_type
    except Exception:
        pass

output.print_md("**Dependent views indexed:** {}".format(len(dep_view_by_name)))
output.print_md("**Sheets indexed:** {}".format(len(sheet_by_suffix)))
output.print_md("**Viewport types in model:** {}".format(len(vp_type_by_name)))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Pre-audit: check for missing viewport types
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## 🔍 Pre-Audit: Viewport Types")

json_vp_types = set()
for sheet_data in layout:
    for entry in sheet_data.get("viewports", []):
        vt = entry.get("viewport_type", "")
        if vt:
            json_vp_types.add(vt)

missing_types = sorted([t for t in json_vp_types if t not in vp_type_by_name])

if not missing_types:
    output.print_md("✅ All viewport types referenced in JSON are present in this model.")
else:
    output.print_md(
        "⚠️  **{} viewport type(s) referenced in JSON are NOT in this model:**".format(
            len(missing_types))
    )
    for mt in missing_types:
        output.print_md("  - `{}`".format(mt))
    output.print_md(
        "\n> **Action required before importing:** Transfer these types from the source "
        "model using **Manage → Transfer Project Standards → Viewport Types**."
    )
    ui.alert(
        "{} viewport type(s) from the JSON are missing in this model:\n\n{}\n\n"
        "Transfer them from the source model via:\n"
        "Manage > Transfer Project Standards > Viewport Types\n\n"
        "Then re-run this audit before importing.".format(
            len(missing_types),
            "\n".join("  - " + t for t in missing_types)
        ),
        title="Audit Sheet Layout",
    )

# ─────────────────────────────────────────────────────────────────────────────
# 5. Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_detail_number(vp):
    try:
        p = vp.get_Parameter(DB.BuiltInParameter.VIEWPORT_DETAIL_NUMBER)
        if p:
            return p.AsString() or ""
    except Exception:
        pass
    return ""

def get_view_name_from_vp(vp):
    try:
        v = doc.GetElement(vp.ViewId)
        if v:
            return v.Name
    except Exception:
        pass
    return ""

def get_viewport_type_name(vp):
    try:
        type_id = vp.GetTypeId()
        vp_type = doc.GetElement(type_id)
        if vp_type is None:
            return ""
        p = vp_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p:
            return p.AsString() or ""
        return vp_type.Name
    except Exception:
        return ""

# ─────────────────────────────────────────────────────────────────────────────
# 6. Audit
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")

sheets_not_found    = []
sheets_with_issues  = []
sheets_ok           = []
all_orphaned_vp_ids = []
orphaned_details    = []  # (sheet_number, view_name) for summary

for sheet_data in layout:
    sheet_number = sheet_data["sheet_number"]
    sheet_name   = sheet_data["sheet_name"]
    suffix       = sheet_number[2:] if len(sheet_number) > 2 else sheet_number

    dest_sheet_number = "{}{}".format(dest_prefix, suffix)

    if suffix not in sheet_by_suffix:
        sheets_not_found.append(dest_sheet_number)
        continue

    target_sheet = sheet_by_suffix[suffix]

    # json_vp_dict: view_name -> full entry dict
    json_vp_dict = {}
    for entry in sheet_data["viewports"]:
        json_vp_dict[entry["view_name"]] = entry

    sheet_vps     = viewports_by_sheet.get(target_sheet.Id.IntegerValue, [])
    # model_vp_dict: view_name -> (vp, det_num, vp_type_name, view_type_str)
    model_vp_dict = {}
    for vp in sheet_vps:
        vname = get_view_name_from_vp(vp)
        if vname:
            v_elem     = doc.GetElement(vp.ViewId)
            view_type  = str(v_elem.ViewType) if v_elem else ""
            model_vp_dict[vname] = (
                vp, get_detail_number(vp), get_viewport_type_name(vp), view_type)

    issues = []

    # Views in JSON not found in model
    for vname in json_vp_dict:
        if vname not in dep_view_by_name:
            issues.append("🔴 **NOT IN MODEL** — *{}* (renamed or deleted)".format(vname))

    # Views in JSON not placed in this sheet
    for vname in json_vp_dict:
        if vname in dep_view_by_name:
            target_view = dep_view_by_name[vname]
            vid = target_view.Id.IntegerValue
            if vid not in viewport_by_view_id:
                issues.append("➕ **NOT PLACED** — *{}* (exists in model but not on sheet)".format(
                    vname))

    # Orphaned viewports — on sheet but NOT in JSON
    for vname, (vp, det_num, type_name, _view_type) in model_vp_dict.items():
        if vname not in json_vp_dict:
            v = doc.GetElement(vp.ViewId)
            is_dependent = False
            try:
                if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
                    is_dependent = True
            except Exception:
                pass
            if is_dependent:
                issues.append(
                    "⚠️  **ORPHANED** — *{}* (on sheet, not in JSON — will cause conflicts)".format(
                        vname))
                all_orphaned_vp_ids.append(vp.Id)
                orphaned_details.append((dest_sheet_number, vname))

    # Detail number mismatches
    for vname, entry in json_vp_dict.items():
        json_det_num = entry.get("detail_number", "")
        if vname in model_vp_dict:
            model_det_num = model_vp_dict[vname][1]
            if json_det_num and model_det_num and json_det_num != model_det_num:
                issues.append(
                    "🔢 **DETAIL NUMBER MISMATCH** — *{}*  JSON: `{}`  Model: `{}`".format(
                        vname, json_det_num, model_det_num))

    # Viewport type mismatches
    for vname, entry in json_vp_dict.items():
        json_type = entry.get("viewport_type", "")
        if vname in model_vp_dict:
            model_type = model_vp_dict[vname][2]
            if json_type and model_type and json_type != model_type:
                issues.append(
                    "🖼️  **VIEWPORT TYPE MISMATCH** — *{}*  JSON: `{}`  Model: `{}`".format(
                        vname, json_type, model_type))
            elif json_type and not model_type:
                issues.append(
                    "🖼️  **VIEWPORT TYPE UNKNOWN** — *{}*  expected: `{}`".format(
                        vname, json_type))

    # View type mismatches (e.g. Section changed to FloorPlan)
    for vname, entry in json_vp_dict.items():
        json_view_type = entry.get("view_type", "")
        if json_view_type and vname in model_vp_dict:
            model_view_type = model_vp_dict[vname][3]
            if model_view_type and model_view_type != json_view_type:
                issues.append(
                    "🔄 **VIEW TYPE MISMATCH** — *{}*  JSON: `{}`  Model: `{}`  "
                    "— delete and re-import".format(
                        vname, json_view_type, model_view_type))

    if issues:
        output.print_md("### ⚠️  Sheet {}  —  {}".format(dest_sheet_number, sheet_name))
        for issue in issues:
            output.print_md("  {}".format(issue))
        sheets_with_issues.append(dest_sheet_number)
    else:
        output.print_md("### ✅ Sheet {}  —  {}".format(dest_sheet_number, sheet_name))
        sheets_ok.append(dest_sheet_number)

# ─────────────────────────────────────────────────────────────────────────────
# 7. Final summary
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## Summary")
output.print_md("✅ **{}** sheets in sync".format(len(sheets_ok)))
output.print_md("⚠️  **{}** sheets with issues".format(len(sheets_with_issues)))
output.print_md("❌ **{}** sheets not found in model".format(len(sheets_not_found)))

if sheets_not_found:
    output.print_md("\n**Sheets not found:**")
    for s in sheets_not_found:
        output.print_md("  - {}".format(s))

if sheets_with_issues:
    output.print_md("\n**Sheets to address before importing:**")
    for s in sheets_with_issues:
        output.print_md("  - {}".format(s))

if all_orphaned_vp_ids:
    output.print_md("\n**Orphaned viewports (not in JSON — will cause conflicts):**")
    current_sheet = None
    for sheet_num, vname in orphaned_details:
        if sheet_num != current_sheet:
            output.print_md("  **{}**".format(sheet_num))
            current_sheet = sheet_num
        output.print_md("    ⚠️  *{}*".format(vname))

# ─────────────────────────────────────────────────────────────────────────────
# 8. Handle orphaned viewports
# ─────────────────────────────────────────────────────────────────────────────

if all_orphaned_vp_ids:
    action = ui.pick_list(
        ["Remove from sheets (keep views)", "Delete viewports", "Do nothing"],
        "Audit Sheet Layout",
        prompt="{} orphaned viewport(s) found on sheets.\n\n"
        "These viewports are not in the JSON and will cause detail number "
        "conflicts during import.\n\n"
        "What do you want to do?".format(len(all_orphaned_vp_ids)),
    )

    if action == "Remove from sheets (keep views)":
        removed = 0
        failed  = 0
        with revit.Transaction("Remove Orphaned Viewports from Sheets"):
            for vp_id in all_orphaned_vp_ids:
                try:
                    doc.Delete(vp_id)
                    removed += 1
                except Exception:
                    failed += 1
        output.print_md("📌 **{}** viewports removed from sheets.".format(removed))
        if failed:
            output.print_md("❌ **{}** could not be removed.".format(failed))

    elif action == "Delete viewports":
        deleted = 0
        failed  = 0
        with revit.Transaction("Delete Orphaned Viewports"):
            for vp_id in all_orphaned_vp_ids:
                try:
                    doc.Delete(vp_id)
                    deleted += 1
                except Exception:
                    failed += 1
        output.print_md("🗑️ **{}** viewports deleted.".format(deleted))
        if failed:
            output.print_md("❌ **{}** could not be deleted.".format(failed))

    else:
        output.print_md("⏭️ Orphaned viewports kept — address manually.")
