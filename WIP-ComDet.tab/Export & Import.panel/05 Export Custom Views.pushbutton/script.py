# -*- coding: utf-8 -*-
__title__ = 'Export\nCustom Views'
__doc__ = ('Migration tool — run once per building to export custom dependent views '
           'that exist only in this building (placed on custom sheets not shared with Common Details). '
           'Select the custom sheets and the Common Details linked model. '
           'Generates a JSON compatible with Import/Sync Views so the custom views '
           'can be imported directly into Common Details.')

import json
import sys
import os as _os
from datetime import date
from pyrevit import revit, DB, script, forms

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

doc    = revit.doc
output = script.get_output()

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def xyz_to_list(pt):
    return [pt.X, pt.Y, pt.Z]

def world_to_link(pt, link_transform):
    return link_transform.Inverse.OfPoint(pt)

# ─────────────────────────────────────────────────────────────────────────────
# 1. Select Common Details linked model
# ─────────────────────────────────────────────────────────────────────────────

link_instances = DB.FilteredElementCollector(doc)\
    .OfClass(DB.RevitLinkInstance)\
    .ToElements()

if not link_instances:
    ui.alert("No linked models found in this document.", title="Export Custom Views")
    script.exit()

link_by_name = {li.Name: li for li in link_instances}

chosen_link = ui.pick_list(
    sorted(link_by_name.keys()),
    "1 of 3 - Reference Linked Model",
    button_name="Next",
    multiselect=False
)
if not chosen_link:
    script.exit()

link_instance  = link_by_name[chosen_link]
link_transform = link_instance.GetTotalTransform()

output.print_md("**Linked model:** `{}`".format(chosen_link))
output.print_md("**Origin:** X={:.4f} Y={:.4f} Z={:.4f}".format(
    link_transform.Origin.X,
    link_transform.Origin.Y,
    link_transform.Origin.Z
))

# ─────────────────────────────────────────────────────────────────────────────
# 2. Select custom sheets
# ─────────────────────────────────────────────────────────────────────────────

all_sheets = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
all_sheets = sorted(all_sheets, key=lambda s: s.SheetNumber)

if not all_sheets:
    ui.alert("No sheets found in the model.", title="Export Custom Views")
    script.exit()

sheet_options   = []
sheet_by_option = {}
for s in all_sheets:
    label = "{} - {}".format(s.SheetNumber, s.Name)
    sheet_options.append(label)
    sheet_by_option[label] = s

chosen_options = ui.pick_list(
    sheet_options,
    "2 of 3 - Select Custom Sheets",
    button_name="Next"
)
if not chosen_options:
    script.exit()

selected_sheets = [sheet_by_option[o] for o in chosen_options]
output.print_md("**Custom sheets selected:** {}".format(len(selected_sheets)))

# ─────────────────────────────────────────────────────────────────────────────
# 3. Choose output file
# ─────────────────────────────────────────────────────────────────────────────

today = date.today().strftime("%Y%m%d")
doc_title = doc.Title.split(".")[0] if doc.Title else "Building"

save_path = forms.save_file(
    file_ext="json",
    default_name="{}_Custom views_{}.json".format(today, doc_title),
    title="3 of 3 — Save Custom Views JSON"
)
if not save_path:
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 4. Collect dependent views from custom sheets
# ─────────────────────────────────────────────────────────────────────────────

# Index all dependent views by Id
all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

dep_view_by_id  = {}
master_view_by_id = {}
for v in all_views:
    try:
        pid = v.GetPrimaryViewId()
        if pid != DB.ElementId.InvalidElementId:
            dep_view_by_id[v.Id.IntegerValue] = v
        else:
            master_view_by_id[v.Id.IntegerValue] = v
    except Exception:
        pass

# Group custom views by master view
master_to_deps = {}  # master_name -> list of dep views

for sheet in selected_sheets:
    vp_ids = sheet.GetAllViewports()
    for vp_id in vp_ids:
        vp = doc.GetElement(vp_id)
        if vp is None:
            continue
        view_id = vp.ViewId.IntegerValue
        if view_id not in dep_view_by_id:
            continue
        dep = dep_view_by_id[view_id]
        try:
            primary_id = dep.GetPrimaryViewId().IntegerValue
            primary    = master_view_by_id.get(primary_id)
            master_name = primary.Name if primary else "Unknown Master"
        except Exception:
            master_name = "Unknown Master"

        master_to_deps.setdefault(master_name, [])
        # Avoid duplicates (same view on multiple sheets)
        if dep not in master_to_deps[master_name]:
            master_to_deps[master_name].append(dep)

if not master_to_deps:
    ui.alert(
        "No dependent views found on the selected sheets.\n"
        "Make sure the sheets contain dependent views (not master views or schedules).",
        title="Export Custom Views"
    )
    script.exit()

total_found = sum(len(deps) for deps in master_to_deps.values())
output.print_md("**Custom dependent views found:** {}".format(total_found))

# ─────────────────────────────────────────────────────────────────────────────
# 5. Build export JSON (same schema as Export Views)
# ─────────────────────────────────────────────────────────────────────────────

export_data = {
    "link_doc_name": chosen_link,
    "link_origin":   xyz_to_list(link_transform.Origin),
    "master_views":  []
}

exported = 0
errors   = 0

for master_name, dep_views in sorted(master_to_deps.items()):
    output.print_md("### {}".format(master_name))

    view_data = {
        "view_name":       master_name,
        "view_scale":      dep_views[0].Scale if dep_views else 1,
        "dependent_views": []
    }

    for dep in dep_views:
        try:
            crop_box = dep.CropBox
            if crop_box is None:
                output.print_md("  ⚠️ No CropBox: *{}*".format(dep.Name))
                continue

            min_pt    = crop_box.Min
            max_pt    = crop_box.Max
            transform = crop_box.Transform

            local_corners = [
                DB.XYZ(min_pt.X, min_pt.Y, min_pt.Z),
                DB.XYZ(max_pt.X, min_pt.Y, min_pt.Z),
                DB.XYZ(max_pt.X, max_pt.Y, min_pt.Z),
                DB.XYZ(min_pt.X, max_pt.Y, min_pt.Z),
            ]
            world_corners = [transform.OfPoint(c) for c in local_corners]

            # Convert to Common Details local space via link transform inverse
            rel_corners = [xyz_to_list(world_to_link(c, link_transform))
                           for c in world_corners]

            title_on_sheet = ""
            try:
                p = dep.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
                if p:
                    title_on_sheet = p.AsString() or ""
            except Exception:
                pass

            template_name = ""
            tid = dep.ViewTemplateId
            if tid != DB.ElementId.InvalidElementId:
                tmpl = doc.GetElement(tid)
                if tmpl:
                    template_name = tmpl.Name

            view_data["dependent_views"].append({
                "view_name":      dep.Name,
                "title_on_sheet": title_on_sheet,
                "crop_corners":   rel_corners,
                "view_template":  template_name,
                "view_scale":     dep.Scale,
                "view_type":      str(dep.ViewType),
            })

            output.print_md("  ✅ *{}*  |  Title: *{}*".format(
                dep.Name, title_on_sheet or "(no title)"))
            exported += 1

        except Exception as e:
            output.print_md("  ❌ Error in '{}': {}".format(dep.Name, e))
            errors += 1

    export_data["master_views"].append(view_data)

# ─────────────────────────────────────────────────────────────────────────────
# 6. Save JSON
# ─────────────────────────────────────────────────────────────────────────────

with open(save_path, "w") as f:
    json.dump(export_data, f, indent=2)

output.print_md("\n---")
output.print_md("✅ **{} custom views exported**{}".format(
    exported,
    "  ·  ❌ {} errors".format(errors) if errors else ""))
output.print_md("📁 `{}`".format(save_path))
output.print_md("\n**Next step:** Open Common Details and run *Import/Sync Views* with "
                "this JSON, selecting **'None (I\\'m importing views into the Common Details file)'** as the linked model.")
