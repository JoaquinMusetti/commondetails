# -*- coding: utf-8 -*-
__title__ = 'Audit\nSource Views'
__doc__ = ('Compares details_geometry.json against the current model. '
           'Reports views in JSON no longer in the model, new views not yet exported, '
           'and detects possible renames by matching Title on Sheet. Generates renames.json if confirmed.')

import json
import os
from pyrevit import revit, DB, script, forms

doc    = revit.doc
output = script.get_output()

# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick JSON file
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(
    file_ext="json",
    title="Select details_geometry.json"
)
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    data = json.load(f)

output.print_md("**Linked model in JSON:** `{}`".format(data.get("link_doc_name", "")))
output.print_md("**Master views in JSON:** {}".format(len(data["master_views"])))

# ─────────────────────────────────────────────────────────────────────────────
# 2. Index current dependent views in model
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

master_by_name = {}
for v in master_views:
    master_by_name[v.Name] = v

# Build {master_view_name: [dep_view_names]}
model_deps_by_master = {}
for master in master_views:
    dep_names = []
    for vid in master.GetDependentViewIds():
        dep = doc.GetElement(vid)
        if dep:
            try:
                dep_names.append(dep.Name)
            except Exception:
                pass
    model_deps_by_master[master.Name] = dep_names

# Build {dep_view_name: title_on_sheet} for all dependent views
def get_title_on_sheet(view):
    try:
        p = view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
        if p:
            return p.AsString() or ""
    except Exception:
        pass
    return ""

dep_view_by_name = {}
for v in all_views:
    try:
        if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
            dep_view_by_name[v.Name] = v
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# 3. Compare JSON vs model
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")

total_missing  = 0
total_new      = 0
total_ok       = 0
master_missing = []

# Collect all missing and new views across all master views for rename detection
# {view_name: title_on_sheet}
all_missing = {}
all_new     = {}

for view_data in data["master_views"]:
    master_name = view_data["view_name"]
    json_views  = {dv["view_name"]: dv for dv in view_data["dependent_views"]}

    if master_name not in master_by_name:
        master_missing.append(master_name)
        output.print_md("### ❌ Master view not found: *{}*".format(master_name))
        continue

    model_names = model_deps_by_master.get(master_name, [])
    model_set   = set(model_names)
    json_set    = set(json_views.keys())

    missing = [n for n in json_views if n not in model_set]
    new     = [n for n in model_names if n not in json_set]
    ok      = [n for n in json_views if n in model_set]

    total_missing += len(missing)
    total_new     += len(new)
    total_ok      += len(ok)

    # Collect titles for rename detection
    for name in missing:
        title = json_views[name].get("title_on_sheet", "")
        if title:
            all_missing[name] = title

    for name in new:
        if name in dep_view_by_name:
            title = get_title_on_sheet(dep_view_by_name[name])
            if title:
                all_new[name] = title

    if missing or new:
        output.print_md("### ⚠️  {}".format(master_name))
        if missing:
            output.print_md("  **In JSON but NOT in model** — removed or renamed:")
            for name in sorted(missing):
                output.print_md("  🔴 *{}*".format(name))
        if new:
            output.print_md("  **In model but NOT in JSON** — new, not exported yet:")
            for name in sorted(new):
                output.print_md("  ➕ *{}*".format(name))
    else:
        output.print_md("### ✅ {}  — all {} views in sync".format(
            master_name, len(ok)))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Detect renames by title on sheet match
# ─────────────────────────────────────────────────────────────────────────────

# Build {title_on_sheet: old_name} and {title_on_sheet: new_name}
title_to_missing = {}
for name, title in all_missing.items():
    if title not in title_to_missing:
        title_to_missing[title] = []
    title_to_missing[title].append(name)

title_to_new = {}
for name, title in all_new.items():
    if title not in title_to_new:
        title_to_new[title] = []
    title_to_new[title].append(name)

# Find exact title matches — one missing + one new with same title
rename_suggestions = []
for title, missing_names in title_to_missing.items():
    if title in title_to_new:
        new_names = title_to_new[title]
        if len(missing_names) == 1 and len(new_names) == 1:
            rename_suggestions.append({
                "old_name":      missing_names[0],
                "new_name":      new_names[0],
                "title_on_sheet": title
            })

# ─────────────────────────────────────────────────────────────────────────────
# 5. Final summary
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## Summary")

if total_missing == 0 and total_new == 0 and not master_missing:
    output.print_md("✅ **Everything in sync.** JSON matches the model perfectly.")
    output.print_md("Safe to proceed with Import Dependent Views.")
    script.exit()

output.print_md("⚠️  **Sync issues detected — review before importing.**")
if total_missing:
    output.print_md("🔴 **{}** view(s) in JSON no longer exist in model".format(total_missing))
if total_new:
    output.print_md("➕ **{}** new view(s) in model not yet in JSON".format(total_new))
if master_missing:
    output.print_md("❌ **{}** master view(s) not found in model:".format(len(master_missing)))
    for name in master_missing:
        output.print_md("  - *{}*".format(name))

if rename_suggestions:
    output.print_md("\n---")
    output.print_md("## Possible Renames Detected")
    output.print_md("The following views have matching Title on Sheet — likely renamed:")
    for r in rename_suggestions:
        output.print_md("  🔄 *{}*  →  *{}*  (title: `{}`)".format(
            r["old_name"], r["new_name"], r["title_on_sheet"]))

# ─────────────────────────────────────────────────────────────────────────────
# 6. Confirm renames and generate renames.json
# ─────────────────────────────────────────────────────────────────────────────

if not rename_suggestions:
    output.print_md("\nRun **Export Dependent Views** to update the JSON before importing.")
    script.exit()

confirm = forms.alert(
    "{} possible rename(s) detected based on matching Title on Sheet.\n\n{}\n\n"
    "Confirm these renames and generate renames.json?".format(
        len(rename_suggestions),
        "\n".join(["  {} → {}".format(r["old_name"], r["new_name"])
                   for r in rename_suggestions])
    ),
    title="Confirm Renames",
    yes=True,
    no=True
)

if not confirm:
    output.print_md("\n⏭️  Renames not confirmed. Run **Export Dependent Views** to update the JSON.")
    script.exit()

save_path = forms.save_file(
    file_ext="json",
    default_name="renames.json",
    title="Save renames.json"
)
if not save_path:
    script.exit()

with open(save_path, "w") as f:
    json.dump(rename_suggestions, f, indent=2)

output.print_md("\n✅ **renames.json saved** → `{}`".format(save_path))
output.print_md("Run **Rename Dependent Views** in each destination model to apply the renames.")
output.print_md("Then run **Export Dependent Views** to update the JSON.")