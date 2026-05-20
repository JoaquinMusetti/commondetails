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

# ─────────────────────────────────────────────────────────────────────────────
# Legacy guard — confirm before running. This tool was moved to the Legacy
# pulldown on 2026-05-19 because the combined "Sheets with Views" flow now
# covers its workflow. Kept here for backwards-compat with old JSONs.
# ─────────────────────────────────────────────────────────────────────────────
if not ui.confirm(
    u"This tool is part of the LEGACY workflow.\n\n"
    u"The current workflow uses 'Export Sheets with Views' + 'Import "
    u"Sheets with Views' (and their PRO variant) from the Export & Import "
    u"panel. The matching new Audit tools live under Audit > Audit Views / "
    u"Audit Sheets pulldowns.\n\n"
    u"Continue with the legacy tool anyway?",
    title=u"Legacy Tool",
    yes_text=u"Continue (legacy)",
    context=u"Common Details migrated to a combined JSON flow on 2026-05-19. "
            u"The legacy tools stay here in case you need to interop with old "
            u"JSONs or a feature not yet covered in the new flow."
):
    script.exit()

doc = revit.doc
SEP = u"─" * 55

# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick details_views.json
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(file_ext="json", title="Select details_views.json")
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    data = json.load(f)

lines = []
lines.append(u"Linked model: {}".format(data.get("link_doc_name", "")))
lines.append(u"Master views in JSON: {}".format(len(data["master_views"])))

# ─────────────────────────────────────────────────────────────────────────────
# 2. Select master views to audit
# ─────────────────────────────────────────────────────────────────────────────

master_options = sorted([mv["view_name"] for mv in data["master_views"]])

chosen_masters = ui.pick_list(
    master_options,
    "Audit Destination Views",
    subtitle="Select which master views to include in the audit:",
    multiselect=True,
    context=u"Audits the destination model (building) against the Common Details "
            u"master JSON. Reports orphans (views in the JSON that are missing), "
            u"scale/template mismatches, and incorrect detail numbers. Dependents "
            u"of masters you don't tick are excluded from the report."
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

lines.append(u"Master views selected: {}".format(len(chosen_masters_set)))
lines.append(u"Dependent views in scope: {}".format(len(json_view_names)))

# Warn if JSON has no scale/type data
sample_scales = [v for v in json_view_scale.values() if v is not None]
has_scale_data = len(sample_scales) > 0
if not has_scale_data:
    lines.append(u"⚠  No view_scale data in JSON — scale comparison skipped. Re-export JSON.")

has_type_data = any(t for t in json_view_type.values())
if not has_type_data:
    lines.append(u"⚠  No view_type data in JSON — view type comparison skipped. Re-export JSON.")

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

lines.append(u"Dependent views in model: {}".format(len(dep_views_in_model)))

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

not_in_json          = []
not_in_json_placed   = []
not_in_json_orphan   = []
scale_mismatches     = []
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
        if has_scale_data:
            json_scale = json_view_scale.get(v.Name)
            if json_scale is not None:
                try:
                    json_scale_int = int(json_scale)
                    model_scale    = v.Scale
                    if model_scale != json_scale_int:
                        scale_mismatches.append((v, model_scale, json_scale_int))
                except (TypeError, ValueError, Exception):
                    lines.append(u"  ⚠  Could not compare scale for {}".format(v.Name))

        if has_type_data:
            json_type  = json_view_type.get(v.Name, "")
            model_type = str(v.ViewType)
            if json_type and model_type != json_type:
                is_placed = v.Id.IntegerValue in placed_view_ids
                view_type_mismatches.append((v, model_type, json_type, is_placed))

# ─────────────────────────────────────────────────────────────────────────────
# 6. Report: Views NOT in JSON
# ─────────────────────────────────────────────────────────────────────────────

lines.append(u"")
lines.append(SEP)
lines.append(u"Views Not in JSON")
lines.append(SEP)

if not not_in_json:
    lines.append(u"✅  All dependent views accounted for in JSON.")
else:
    by_master = {}
    for v, is_placed in not_in_json:
        try:
            primary     = doc.GetElement(v.GetPrimaryViewId())
            master_name = primary.Name if primary else "Unknown"
        except Exception:
            master_name = "Unknown"
        by_master.setdefault(master_name, []).append((v, is_placed))

    for master_name, views in sorted(by_master.items()):
        lines.append(u"  {}".format(master_name))
        for v, is_placed in views:
            if is_placed:
                lines.append(u"    \U0001f4cc {}  — on sheet, not in JSON".format(v.Name))
            else:
                lines.append(u"    \U0001f47b {}  — not in JSON, not on any sheet".format(v.Name))

# ─────────────────────────────────────────────────────────────────────────────
# 7. Report: Scale mismatches
# ─────────────────────────────────────────────────────────────────────────────

lines.append(u"")
lines.append(SEP)
lines.append(u"View Scale Mismatches")
lines.append(SEP)

if not has_scale_data:
    lines.append(u"⚠  Skipped — no scale data in JSON.")
elif not scale_mismatches:
    lines.append(u"✅  All matched views have correct scales.")
else:
    by_master_scale = {}
    for v, model_scale, json_scale in scale_mismatches:
        try:
            primary     = doc.GetElement(v.GetPrimaryViewId())
            master_name = primary.Name if primary else "Unknown"
        except Exception:
            master_name = "Unknown"
        by_master_scale.setdefault(master_name, []).append((v, model_scale, json_scale))

    for master_name, views in sorted(by_master_scale.items()):
        lines.append(u"  {}".format(master_name))
        for v, model_scale, json_scale in views:
            is_placed = v.Id.IntegerValue in placed_view_ids
            placement = u"on a sheet" if is_placed else u"not on any sheet"
            lines.append(u"    ⚠  {}  — model: 1:{}  expected: 1:{}  ({})".format(
                v.Name, model_scale, json_scale, placement))

    scale_mismatch_placed = [(v, ms, js) for v, ms, js in scale_mismatches
                             if v.Id.IntegerValue in placed_view_ids]
    scale_mismatch_orphan = [(v, ms, js) for v, ms, js in scale_mismatches
                             if v.Id.IntegerValue not in placed_view_ids]

    msg_lines = [
        u"{} view(s) have a scale mismatch and need to be deleted so they can be re-imported correctly.\n".format(
            len(scale_mismatches))
    ]
    if scale_mismatch_orphan:
        msg_lines.append(u"  • {} not on any sheet".format(len(scale_mismatch_orphan)))
    if scale_mismatch_placed:
        msg_lines.append(u"  • {} placed on a sheet — will also be deleted".format(
            len(scale_mismatch_placed)))
    msg_lines.append(u"\nDelete all of them now?")

    confirm_scale = ui.confirm(u"\n".join(msg_lines), title="Audit Destination Views")

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
        lines.append(u"")
        lines.append(u"\U0001f5d1 {} scale-mismatch views deleted.".format(deleted_s))
        if failed_s:
            lines.append(u"❌ {} could not be deleted.".format(failed_s))
    else:
        lines.append(u"⏭  Scale-mismatch views kept — address manually.")

# ─────────────────────────────────────────────────────────────────────────────
# 8. Report: View type mismatches
# ─────────────────────────────────────────────────────────────────────────────

lines.append(u"")
lines.append(SEP)
lines.append(u"View Type Mismatches")
lines.append(SEP)

if not has_type_data:
    lines.append(u"⚠  Skipped — no view type data in JSON.")
elif not view_type_mismatches:
    lines.append(u"✅  All matched views have the correct view type.")
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
        lines.append(u"  {}".format(master_name))
        for v, model_type, json_type, is_placed in views:
            placement = u"on a sheet" if is_placed else u"not on any sheet"
            lines.append(u"    \U0001f504 {}  — model: {}  expected: {}  ({})".format(
                v.Name, model_type, json_type, placement))

    lines.append(u"")
    lines.append(u"  View type mismatches: delete these views and re-import.")

# ─────────────────────────────────────────────────────────────────────────────
# 9. Summary
# ─────────────────────────────────────────────────────────────────────────────

lines.append(u"")
lines.append(SEP)
lines.append(u"SUMMARY")
lines.append(SEP)
lines.append(u"\U0001f47b  {} view(s) not in JSON and not on any sheet — likely orphaned".format(
    len(not_in_json_orphan)))
lines.append(u"\U0001f4cc  {} view(s) not in JSON but placed on a sheet — review manually".format(
    len(not_in_json_placed)))
if has_scale_data:
    lines.append(u"📐  {} view(s) with scale mismatch".format(len(scale_mismatches)))
if has_type_data:
    lines.append(u"\U0001f504  {} view(s) with view type mismatch".format(len(view_type_mismatches)))
lines.append(u"Total unaccounted: {} view(s)".format(len(not_in_json)))

# ─────────────────────────────────────────────────────────────────────────────
# 10. Optional: delete orphaned views (not in JSON, not on sheet)
# ─────────────────────────────────────────────────────────────────────────────

if not_in_json_orphan:
    confirm = ui.confirm(
        u"{} orphaned dependent view(s) found — not in JSON and not on any sheet.\n\n"
        u"These are likely leftovers from old imports or renamed views.\n\n"
        u"Do you want to delete them now?".format(len(not_in_json_orphan)),
        title="Audit Destination Views",
    )

    lines.append(u"")
    lines.append(SEP)
    lines.append(u"Orphaned Views — Action Taken")
    lines.append(SEP)

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
        lines.append(u"\U0001f5d1 {} orphaned views deleted.".format(deleted))
        if failed:
            lines.append(u"❌ {} could not be deleted.".format(failed))
    else:
        lines.append(u"⏭  Orphaned views kept — address manually.")

# ─────────────────────────────────────────────────────────────────────────────
# 11. Optional: delete placed views not in JSON
# ─────────────────────────────────────────────────────────────────────────────

if not_in_json_placed:
    confirm_placed = ui.confirm(
        u"{} view(s) are not in the JSON but ARE placed on sheets.\n\n"
        u"These may be leftover views from a previous import or manually placed views "
        u"that are not part of the current layout.\n\n"
        u"WARNING: Deleting a view also removes it from its sheet.\n\n"
        u"Do you want to delete them now?".format(len(not_in_json_placed)),
        title="Audit Destination Views",
    )

    lines.append(u"")
    lines.append(SEP)
    lines.append(u"Placed Views Not in JSON — Action Taken")
    lines.append(SEP)

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
        lines.append(u"\U0001f5d1 {} placed view(s) deleted.".format(deleted_p))
        if failed_p:
            lines.append(u"❌ {} could not be deleted.".format(failed_p))
    else:
        lines.append(u"⏭  Placed views not in JSON kept — address manually.")

# ─────────────────────────────────────────────────────────────────────────────
# 12. Show result window
# ─────────────────────────────────────────────────────────────────────────────

ui.show_report(
    text     = u"\n".join(lines),
    title    = u"Audit Destination Views",
    subtitle = u"{} master view(s) audited".format(len(chosen_masters_set)),
    summary  = u"\U0001f47b {}  \U0001f4cc {}  📐 {}  \U0001f504 {}".format(
        len(not_in_json_orphan), len(not_in_json_placed),
        len(scale_mismatches), len(view_type_mismatches)),
)
