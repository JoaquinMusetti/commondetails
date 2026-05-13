# -*- coding: utf-8 -*-
__title__ = 'Import/Sync\nViews'
__doc__ = ('Reads details_geometry.json and creates or updates dependent views in the destination model. '
           'Applies crop boundaries from the exported JSON using the linked model offset. '
           'When importing into the Common Details file itself, choose "None (I\'m importing views into the Common Details file)" '
           'as the linked model. '
           'Choose to skip or update existing dependent views.')

import json
from pyrevit import revit, DB, script, forms
from Autodesk.Revit.DB import CurveLoop, Line, ViewDuplicateOption

doc    = revit.doc
output = script.get_output()

NONE_OPTION = "None (I'm importing views into the Common Details file)"

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def link_to_world(pt_list, link_transform):
    pt = DB.XYZ(pt_list[0], pt_list[1], pt_list[2])
    if link_transform is None:
        return pt
    return link_transform.OfPoint(pt)

def make_crop_loop(corners_world):
    loop = CurveLoop()
    for k in range(4):
        loop.Append(Line.CreateBound(
            corners_world[k],
            corners_world[(k + 1) % 4]
        ))
    return loop

# ─────────────────────────────────────────────────────────────────────────────
# 1. Select JSON
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(
    file_ext="json",
    title="Select details_geometry.json"
)
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    data = json.load(f)

output.print_md("**Source:** `{}`".format(data.get("link_doc_name", "unknown")))
output.print_md("**Master views in JSON:** {}".format(len(data["master_views"])))

# ─────────────────────────────────────────────────────────────────────────────
# 2. Strategy for existing dependent views
# ─────────────────────────────────────────────────────────────────────────────

strategy = forms.SelectFromList.show(
    [
        "Skip existing — do not touch dependent views that already exist",
        "Update existing — re-apply crop boundary to views that already exist",
    ],
    title="Strategy for Existing Dependent Views",
    prompt="What to do with dependent views that already exist?",
    multiselect=False
)
if not strategy:
    script.exit()

update_existing = "Update" in strategy
output.print_md("**Strategy:** {}".format(
    "Update existing" if update_existing else "Skip existing"
))

# ─────────────────────────────────────────────────────────────────────────────
# 3. Select linked model in destination (or None for local import)
# ─────────────────────────────────────────────────────────────────────────────

link_instances = DB.FilteredElementCollector(doc)\
    .OfClass(DB.RevitLinkInstance)\
    .ToElements()

link_by_name = {li.Name: li for li in link_instances}
link_options = [NONE_OPTION] + sorted(link_by_name.keys())

chosen_link = forms.SelectFromList.show(
    link_options,
    title="Reference Linked Model",
    prompt="Select the Common Details linked model, or choose 'None' if you are running this inside Common Details:",
    multiselect=False
)
if not chosen_link:
    script.exit()

if chosen_link == NONE_OPTION:
    link_transform = None
    output.print_md("**Mode:** Local coordinates (no linked model transform)")
else:
    link_transform = link_by_name[chosen_link].GetTotalTransform()
    output.print_md("**Destination linked model:** `{}`".format(chosen_link))
    output.print_md("**Origin:** X={:.4f} Y={:.4f} Z={:.4f}".format(
        link_transform.Origin.X,
        link_transform.Origin.Y,
        link_transform.Origin.Z
    ))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Index destination model views
# ─────────────────────────────────────────────────────────────────────────────

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()
view_by_name = {}
for v in all_views:
    try:
        view_by_name[v.Name] = v
    except Exception:
        pass

# View templates
template_by_name = {}
for v in all_views:
    try:
        if v.IsTemplate:
            template_by_name[v.Name] = v.Id
    except Exception:
        pass

# Existing names in the project
existing_proj_names = set(view_by_name.keys())

# ─────────────────────────────────────────────────────────────────────────────
# 5. Process each master view
# ─────────────────────────────────────────────────────────────────────────────

total_created = 0
total_updated = 0
total_skipped = 0

with revit.Transaction("Import Geometry — Dependent Views"):
    for view_data in data["master_views"]:
        view_name = view_data["view_name"]

        if view_name not in view_by_name:
            output.print_md("### ⏭️ *{}* — master view not found".format(view_name))
            continue

        master_view = view_by_name[view_name]
        output.print_md("### {}".format(view_name))

        # Existing dependent views in this master view
        existing_dep = {}
        for vid in master_view.GetDependentViewIds():
            dep = doc.GetElement(vid)
            if dep:
                existing_dep[dep.Name] = dep

        for dv_data in view_data["dependent_views"]:
            dv_name        = dv_data["view_name"]
            title_on_sheet = dv_data.get("title_on_sheet", "")
            template_name  = dv_data.get("view_template", "")
            scale          = dv_data.get("view_scale", master_view.Scale)

            # Convert corners to world space using destination transform (or identity)
            corners_world = [
                link_to_world(c, link_transform)
                for c in dv_data["crop_corners"]
            ]

            # ── Already exists ──
            if dv_name in existing_dep:
                if update_existing:
                    try:
                        dep_view   = existing_dep[dv_name]
                        crop_loop  = make_crop_loop(corners_world)
                        dep_view.CropBoxActive = True
                        dep_view.GetCropRegionShapeManager().SetCropShape(crop_loop)

                        if title_on_sheet:
                            try:
                                p = dep_view.get_Parameter(
                                    DB.BuiltInParameter.VIEW_DESCRIPTION)
                                if p and not p.IsReadOnly:
                                    p.Set(title_on_sheet)
                            except Exception:
                                pass

                        output.print_md("  🔄 Updated: *{}*".format(dv_name))
                        total_updated += 1
                    except Exception as e:
                        output.print_md("  ❌ Error updating '{}': {}".format(dv_name, e))
                else:
                    output.print_md("  ⏭️ Skipped: *{}*".format(dv_name))
                    total_skipped += 1
                continue

            # ── Create new ──
            try:
                crop_loop = make_crop_loop(corners_world)

                new_vid  = master_view.Duplicate(ViewDuplicateOption.AsDependent)
                new_view = doc.GetElement(new_vid)

                # Name — resolve name collision
                final_name = dv_name
                if dv_name in existing_proj_names:
                    n = 2
                    while "{} ({})".format(dv_name, n) in existing_proj_names:
                        n += 1
                    final_name = "{} ({})".format(dv_name, n)

                try:
                    new_view.Name = final_name
                except Exception:
                    pass

                # Crop boundary
                new_view.CropBoxActive  = True
                new_view.CropBoxVisible = True
                new_view.GetCropRegionShapeManager().SetCropShape(crop_loop)

                # Title on sheet
                if title_on_sheet:
                    try:
                        p = new_view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
                        if p and not p.IsReadOnly:
                            p.Set(title_on_sheet)
                    except Exception:
                        pass

                # Scale
                try:
                    new_view.Scale = scale
                except Exception:
                    pass

                # View template
                if template_name and template_name in template_by_name:
                    try:
                        new_view.ViewTemplateId = template_by_name[template_name]
                    except Exception:
                        pass

                output.print_md("  ✅ Created: *{}*  |  Title: *{}*".format(
                    final_name, title_on_sheet or "(no title)"))
                total_created += 1
                existing_proj_names.add(final_name)

            except Exception as e:
                output.print_md("  ❌ Error creating '{}': {}".format(dv_name, e))

# ─────────────────────────────────────────────────────────────────────────────
# 6. Final report
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("✅ **{}** created  |  🔄 **{}** updated  |  ⏭️ **{}** skipped".format(
    total_created, total_updated, total_skipped))
