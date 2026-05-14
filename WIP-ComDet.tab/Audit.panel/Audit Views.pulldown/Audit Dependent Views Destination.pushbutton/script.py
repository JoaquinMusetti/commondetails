# -*- coding: utf-8 -*-
__title__ = 'Audit\nDestination Views'
__doc__ = ('Compares details_views.json against the destination model. '
           'Reports dependent views not accounted for in the JSON, orphaned views, '
           'and view scale mismatches. Offers to delete orphaned or scale-mismatch views.')

import sys
import json
import os as _os
from pyrevit import revit, DB, script, forms

sys.path.append(_os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))),
    'lib'
))
from magictools import ui

doc    = revit.doc
output = script.get_output()

# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick details_views.json
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(file_ext="json", title="Select details_views.json")
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    data = json.load(f)

output.print_md("**Linked model in JSON:** `{}`".format(data.get("link_doc_name", "")))
output.print_md("**Master views in JSON:** {}".format(len(data["master_views"])))

# ─────────────────────────────────────────────────────────────────────────────
# 2. Select master views to audit
# ─────────────────────────────────────────────────────────────────────────────

master_options = sorted([mv["view_name"] for mv in data["master_views"]])

chosen_masters = ui.pick_list(
    master_options,
    "Audit Destination Views",
    prompt="Select which master views to include in the audit:",
    multiselect=True,
)
if not chosen_masters:
    script.exit()

chosen_masters_set = set(chosen_masters)

# Build lookups from JSON
json_view_scale = {}
json_view_type  = {}
for mv in data["master_views"]:
    if mv["view_name"] in chosen_masters_set:
        for dv in mv["dependent_views"]:
            json_view_scale[dv["view_name"]] = dv.get("view_scale")
            json_view_type[dv["view_name"]]  = dv.get("view_type", "")

json_view_names = set(json_view_scale.keys())

output.print_md("**Master views selected:** {}".format(len(chosen_masters_set)))
output.print_md("**Dependent views in scope:** {}".format(len(json_view_names)))

# Warn if JSON has no scale data
sample_scales = [v for v in json_view_scale.values() if v is not None]
has_scale_data = len(sample_scales) > 0
if not has_scale_data:
    output.print_md("⚠️ **JSON has no `view_scale` data** — scale comparison will be skipped. "
                    "Re-export the JSON from the source model to include scale.")

has_type_data = any(t for t in json_view_type.values())
if not has_type_data:
    output.print_md("⚠️ **JSON has no `view_type` data** — view type comparison will be skipped. "
                    "Re-export the JSON from the source model to include view type.")

# ─────────────────────────────────────────────────────────────────────────────
# 3. Index dependent views in destination model
# ─────────────────────────────────────────────────────────────────────────────

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

dep_views_in_model = []
for v in all_views:
    try:
        if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
            dep_views_in_model.append(v)
    except Exception:
        pass

output.print_md("**Dependent views in model:** {}".format(len(dep_views_in_model)))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Check which views are placed on sheets
# ─────────────────────────────────────────────────────────────────────────────

all_viewports = DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements()
placed_view_ids = set()
for vp in all_viewports:
    placed_view_ids.add(vp.ViewId.IntegerValue)

# ─────────────────────────────────────────────────────────────────────────────
# 5. Classify each dependent view in scope
# ─────────────────────────────────────────────────────────────────────────────

not_in_json        = []
not_in_json_placed = []
not_in_json_orphan = []
scale_mismatches   = []
view_type_mismatches = []

for v in dep_views_in_model:
    try:
        primary = doc.GetElement(v.GetPrimaryViewId())
        if primary is None or primary.Name not in chosen_masters_set:
            continue
    except Exception:
        continue

    if v.Name not in json_view_names:
        is_placed = v.Id.IntegerValue in placed_view_ids
        not_in_json.append((v, is_placed))
        if is_placed:
            not_in_json_placed.append(v)
        else:
            not_in_json_orphan.append(v)
    else:
        # View exists in JSON — check scale and view type
        if has_scale_data:
            json_scale = json_view_scale.get(v.Name)
            if json_scale is not None:
                try:
                    json_scale_int = int(json_scale)
                    model_scale    = v.Scale
                    if model_scale != json_scale_int:
                        scale_mismatches.append((v, model_scale, json_scale_int))
                except (TypeError, ValueError, Exception):
                    output.print_md("  ⚠️ Could not compare scale for *{}*".format(v.Name))

        if has_type_data:
            json_type  = json_view_type.get(v.Name, "")
            model_type = str(v.ViewType)
            if json_type and model_type != json_type:
                is_placed = v.Id.IntegerValue in placed_view_ids
                view_type_mismatches.append((v, model_type, json_type, is_placed))

# ─────────────────────────────────────────────────────────────────────────────
# 6. Report: Views NOT in JSON
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## 🔍 Views Not in JSON")

if not not_in_json:
    output.print_md("✅ All dependent views in model are accounted for in the JSON.")
else:
    by_master = {}
    for v, is_placed in not_in_json:
        try:
            primary = doc.GetElement(v.GetPrimaryViewId())
            master_name = primary.Name if primary else "Unknown"
        except Exception:
            master_name = "Unknown"
        by_master.setdefault(master_name, []).append((v, is_placed))

    for master_name, views in sorted(by_master.items()):
        output.print_md("### {}".format(master_name))
        for v, is_placed in views:
            if is_placed:
                output.print_md("  📌 *{}*  — on a sheet but not in JSON".format(v.Name))
            else:
                output.print_md("  👻 *{}*  — not in JSON, not on any sheet".format(v.Name))

# ─────────────────────────────────────────────────────────────────────────────
# 7. Report: Scale mismatches
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## 📐 View Scale Mismatches")

if not has_scale_data:
    output.print_md("⚠️ Skipped — no scale data in JSON.")
elif not scale_mismatches:
    output.print_md("✅ All matched views have correct scales.")
else:
    by_master_scale = {}
    for v, model_scale, json_scale in scale_mismatches:
        try:
            primary = doc.GetElement(v.GetPrimaryViewId())
            master_name = primary.Name if primary else "Unknown"
        except Exception:
            master_name = "Unknown"
        by_master_scale.setdefault(master_name, []).append((v, model_scale, json_scale))

    for master_name, views in sorted(by_master_scale.items()):
        output.print_md("### {}".format(master_name))
        for v, model_scale, json_scale in views:
            is_placed  = v.Id.IntegerValue in placed_view_ids
            placement  = "📌 on a sheet" if is_placed else "not on any sheet"
            output.print_md(
                "  ⚠️ *{}*  — model: **1:{}**  |  expected: **1:{}**  |  {}".format(
                    v.Name, model_scale, json_scale, placement)
            )

    scale_mismatch_placed = [(v, ms, js) for v, ms, js in scale_mismatches
                              if v.Id.IntegerValue in placed_view_ids]
    scale_mismatch_orphan = [(v, ms, js) for v, ms, js in scale_mismatches
                              if v.Id.IntegerValue not in placed_view_ids]

    msg_lines = [
        "{} view(s) have a scale mismatch and need to be deleted so they can be re-imported correctly.\n".format(
            len(scale_mismatches))
    ]
    if scale_mismatch_orphan:
        msg_lines.append("  • {} not on any sheet".format(len(scale_mismatch_orphan)))
    if scale_mismatch_placed:
        msg_lines.append("  • {} placed on a sheet — will also be deleted".format(
            len(scale_mismatch_placed)))
    msg_lines.append("\nDelete all of them now?")

    confirm_scale = ui.confirm(
        "\n".join(msg_lines),
        title="Audit Destination Views",
    )

    if confirm_scale:
        deleted_s = 0
        failed_s  = 0
        with revit.Transaction("Delete Scale-Mismatch Dependent Views"):
            for v, _, _ in scale_mismatches:
                try:
                    doc.Delete(v.Id)
                    deleted_s += 1
                except Exception:
                    failed_s += 1

        output.print_md("🗑️ **{}** scale-mismatch views deleted.".format(deleted_s))
        if failed_s:
            output.print_md("❌ **{}** could not be deleted.".format(failed_s))
    else:
        output.print_md("⏭️ Scale-mismatch views kept — address manually.")

# ─────────────────────────────────────────────────────────────────────────────
# 8. Report: View type mismatches
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## 🔄 View Type Mismatches")

if not has_type_data:
    output.print_md("⚠️ Skipped — no view type data in JSON.")
elif not view_type_mismatches:
    output.print_md("✅ All matched views have the correct view type.")
else:
    by_master_type = {}
    for v, model_type, json_type, is_placed in view_type_mismatches:
        try:
            primary     = doc.GetElement(v.GetPrimaryViewId())
            master_name = primary.Name if primary else "Unknown"
        except Exception:
            master_name = "Unknown"
        by_master_type.setdefault(master_name, []).append(
            (v, model_type, json_type, is_placed))

    for master_name, views in sorted(by_master_type.items()):
        output.print_md("### {}".format(master_name))
        for v, model_type, json_type, is_placed in views:
            placement = "📌 on a sheet" if is_placed else "not on any sheet"
            output.print_md(
                "  🔄 *{}*  — model: **{}**  |  expected: **{}**  |  {}".format(
                    v.Name, model_type, json_type, placement))

    output.print_md(
        "\n> View type mismatches mean the view was converted to a different type "
        "(e.g. Section → FloorPlan). These views must be **deleted and re-imported** "
        "so the import can create them with the correct type.")

# ─────────────────────────────────────────────────────────────────────────────
# 9. Summary
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## Summary")
output.print_md("👻 **{}** view(s) not in JSON and not on any sheet — likely orphaned".format(
    len(not_in_json_orphan)))
output.print_md("📌 **{}** view(s) not in JSON but placed on a sheet — review manually".format(
    len(not_in_json_placed)))
if has_scale_data:
    output.print_md("📐 **{}** view(s) with scale mismatch".format(len(scale_mismatches)))
if has_type_data:
    output.print_md("🔄 **{}** view(s) with view type mismatch".format(
        len(view_type_mismatches)))
output.print_md("**Total unaccounted:** {} view(s)".format(len(not_in_json)))

# ─────────────────────────────────────────────────────────────────────────────
# 10. Optional: delete orphaned views (not in JSON, not on sheet)
# ─────────────────────────────────────────────────────────────────────────────

if not_in_json_orphan:
    confirm = ui.confirm(
        "{} orphaned dependent view(s) found -- not in JSON and not on any sheet.\n\n"
        "These are likely leftovers from old imports or renamed views.\n\n"
        "Do you want to delete them now?".format(len(not_in_json_orphan)),
        title="Audit Destination Views",
    )

    if confirm:
        deleted = 0
        failed  = 0
        with revit.Transaction("Delete Orphaned Dependent Views"):
            for v in not_in_json_orphan:
                try:
                    doc.Delete(v.Id)
                    deleted += 1
                except Exception:
                    failed += 1

        output.print_md("🗑️ **{}** orphaned views deleted.".format(deleted))
        if failed:
            output.print_md("❌ **{}** could not be deleted.".format(failed))
    else:
        output.print_md("⏭️ Orphaned views kept — address manually.")

# ─────────────────────────────────────────────────────────────────────────────
# 11. Optional: delete placed views not in JSON
# ─────────────────────────────────────────────────────────────────────────────

if not_in_json_placed:
    confirm_placed = ui.confirm(
        "{} view(s) are not in the JSON but ARE placed on sheets.\n\n"
        "These may be leftover views from a previous import or manually placed views "
        "that are not part of the current layout.\n\n"
        "WARNING: Deleting a view also removes it from its sheet.\n\n"
        "Do you want to delete them now?".format(len(not_in_json_placed)),
        title="Audit Destination Views",
    )

    if confirm_placed:
        deleted_p = 0
        failed_p  = 0
        with revit.Transaction("Delete Placed Dependent Views Not in JSON"):
            for v in not_in_json_placed:
                try:
                    doc.Delete(v.Id)
                    deleted_p += 1
                except Exception:
                    failed_p += 1

        output.print_md("🗑️ **{}** placed view(s) deleted.".format(deleted_p))
        if failed_p:
            output.print_md("❌ **{}** could not be deleted.".format(failed_p))
    else:
        output.print_md("⏭️ Placed views not in JSON kept — address manually.")