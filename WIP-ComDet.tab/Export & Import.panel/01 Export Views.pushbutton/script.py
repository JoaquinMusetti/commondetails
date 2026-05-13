# -*- coding: utf-8 -*-
__title__ = 'Export\nViews'
__doc__ = ('Exports crop boundaries, view names, and title on sheet '
           'for dependent views of selected master views to a dated JSON file. '
           'Run this from the Common Details file. '
           'Coordinates are stored in the document\'s local space.')

import json
from datetime import date
from pyrevit import revit, DB, script, forms

doc    = revit.doc
output = script.get_output()

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def xyz_to_list(pt):
    return [pt.X, pt.Y, pt.Z]

# ─────────────────────────────────────────────────────────────────────────────
# 1. Select master views
# ─────────────────────────────────────────────────────────────────────────────

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

master_views = []
for v in all_views:
    try:
        if v.IsTemplate:
            continue
        if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
            continue
        if not v.CanViewBeDuplicated(DB.ViewDuplicateOption.AsDependent):
            continue
        master_views.append(v)
    except Exception:
        pass

master_views = sorted(master_views, key=lambda v: v.Name)
view_by_name = {v.Name: v for v in master_views}

chosen_views = forms.SelectFromList.show(
    [v.Name for v in master_views],
    title="Select Master Views to Export",
    prompt="Select one or more master views:",
    multiselect=True
)
if not chosen_views:
    script.exit()

selected_views = [view_by_name[n] for n in chosen_views]
output.print_md("**Master views selected:** {}".format(len(selected_views)))

# ─────────────────────────────────────────────────────────────────────────────
# 2. Choose output file
# ─────────────────────────────────────────────────────────────────────────────

today = date.today().strftime("%Y%m%d")

save_path = forms.save_file(
    file_ext="json",
    default_name="{}_Dependent views.json".format(today),
    title="Save Dependent Views"
)
if not save_path:
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 3. Collect dependent views per master view
# ─────────────────────────────────────────────────────────────────────────────

export_data = {
    "link_doc_name": doc.Title,
    "link_origin":   [0, 0, 0],
    "master_views":  []
}

for master_view in selected_views:
    output.print_md("### {}".format(master_view.Name))

    view_data = {
        "view_name":       master_view.Name,
        "view_scale":      master_view.Scale,
        "dependent_views": []
    }

    dep_ids = master_view.GetDependentViewIds()
    output.print_md("  Dependent views found: {}".format(len(dep_ids)))

    for vid in dep_ids:
        dep = doc.GetElement(vid)
        if dep is None:
            continue
        try:
            crop_box = dep.CropBox
            if crop_box is None:
                output.print_md("  ⚠️ No CropBox: *{}*".format(dep.Name))
                continue

            # Crop boundary — 4 corners in document local space
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
            rel_corners   = [xyz_to_list(c) for c in world_corners]

            # Title on Sheet
            title_on_sheet = ""
            try:
                p = dep.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
                if p:
                    title_on_sheet = p.AsString() or ""
            except Exception:
                pass

            # View template
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

        except Exception as e:
            output.print_md("  ❌ Error in '{}': {}".format(dep.Name, e))

    output.print_md("  **{}** dependent views exported".format(
        len(view_data["dependent_views"])))
    export_data["master_views"].append(view_data)

# ─────────────────────────────────────────────────────────────────────────────
# 4. Save JSON
# ─────────────────────────────────────────────────────────────────────────────

with open(save_path, "w") as f:
    json.dump(export_data, f, indent=2)

total_dv = sum(len(v["dependent_views"]) for v in export_data["master_views"])

output.print_md("\n---")
output.print_md("✅ **{} dependent views exported** from **{} master views**".format(
    total_dv, len(selected_views)))
output.print_md("📁 `{}`".format(save_path))
