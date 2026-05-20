# -*- coding: utf-8 -*-
__title__ = 'Audit\nSource Views'
__doc__ = ('Compares details_geometry.json against the current model. '
           'Reports views in JSON no longer in the model, new views not yet exported, '
           'and detects possible renames by matching Title on Sheet. Generates renames.json if confirmed.')

import json
import os
import sys as _sys
import os as _os
from pyrevit import revit, DB, script, forms

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
_sys.path.append(_os.path.join(_ext_dir, 'lib'))
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

lines = []
lines.append(u"Linked model: {}".format(data.get("link_doc_name", "")))
lines.append(u"Master views in JSON: {}".format(len(data["master_views"])))

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

lines.append(u"")
lines.append(SEP)
lines.append(u"Comparison: JSON vs Model")
lines.append(SEP)

total_missing  = 0
total_new      = 0
total_ok       = 0
master_missing = []

all_missing = {}
all_new     = {}

for view_data in data["master_views"]:
    master_name = view_data["view_name"]
    json_views  = {dv["view_name"]: dv for dv in view_data["dependent_views"]}

    if master_name not in master_by_name:
        master_missing.append(master_name)
        lines.append(u"❌ Master view not found: {}".format(master_name))
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
        lines.append(u"⚠   {}".format(master_name))
        if missing:
            lines.append(u"  In JSON but NOT in model (removed or renamed):")
            for name in sorted(missing):
                lines.append(u"    \U0001f534 {}".format(name))
        if new:
            lines.append(u"  In model but NOT in JSON (new, not exported yet):")
            for name in sorted(new):
                lines.append(u"    ➕ {}".format(name))
    else:
        lines.append(u"✅  {} — all {} views in sync".format(master_name, len(ok)))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Detect renames by title on sheet match
# ─────────────────────────────────────────────────────────────────────────────

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

rename_suggestions = []
for title, missing_names in title_to_missing.items():
    if title in title_to_new:
        new_names = title_to_new[title]
        if len(missing_names) == 1 and len(new_names) == 1:
            rename_suggestions.append({
                "old_name":       missing_names[0],
                "new_name":       new_names[0],
                "title_on_sheet": title
            })

# ─────────────────────────────────────────────────────────────────────────────
# 5. Summary
# ─────────────────────────────────────────────────────────────────────────────

lines.append(u"")
lines.append(SEP)
lines.append(u"SUMMARY")
lines.append(SEP)

if total_missing == 0 and total_new == 0 and not master_missing:
    lines.append(u"✅  Everything in sync. JSON matches the model perfectly.")
    lines.append(u"Safe to proceed with Import Dependent Views.")
    ui.show_report(
        text     = u"\n".join(lines),
        title    = u"Audit Source Views",
        subtitle = u"{} master views checked".format(len(data["master_views"])),
        summary  = u"✅ all in sync",
    )
    script.exit()

lines.append(u"⚠   Sync issues detected — review before importing.")
if total_missing:
    lines.append(u"\U0001f534 {} view(s) in JSON no longer exist in model".format(total_missing))
if total_new:
    lines.append(u"➕ {} new view(s) in model not yet in JSON".format(total_new))
if master_missing:
    lines.append(u"❌ {} master view(s) not found in model:".format(len(master_missing)))
    for name in master_missing:
        lines.append(u"  - {}".format(name))

if rename_suggestions:
    lines.append(u"")
    lines.append(SEP)
    lines.append(u"Possible Renames Detected")
    lines.append(SEP)
    lines.append(u"Views with matching Title on Sheet — likely renamed:")
    for r in rename_suggestions:
        lines.append(u"  \U0001f504 {}  →  {}  (title: {})".format(
            r["old_name"], r["new_name"], r["title_on_sheet"]))

# ─────────────────────────────────────────────────────────────────────────────
# 6. Confirm renames and generate renames.json
# ─────────────────────────────────────────────────────────────────────────────

if not rename_suggestions:
    lines.append(u"")
    lines.append(u"Run Export Dependent Views to update the JSON before importing.")
    ui.show_report(
        text     = u"\n".join(lines),
        title    = u"Audit Source Views",
        subtitle = u"{} master views checked".format(len(data["master_views"])),
        summary  = u"\U0001f534 {}  ➕ {}  ❌ {}".format(
            total_missing, total_new, len(master_missing)),
    )
    script.exit()

confirm = ui.confirm(
    u"{} possible rename(s) detected based on matching Title on Sheet.\n\n{}\n\n"
    u"Confirm these renames and generate renames.json?".format(
        len(rename_suggestions),
        u"\n".join([u"  {} -> {}".format(r["old_name"], r["new_name"])
                    for r in rename_suggestions])
    ),
    title="Confirm Renames",
    yes_text="Confirm"
)

if not confirm:
    lines.append(u"")
    lines.append(u"⏭  Renames not confirmed. Run Export Dependent Views to update the JSON.")
    ui.show_report(
        text     = u"\n".join(lines),
        title    = u"Audit Source Views",
        subtitle = u"{} master views checked".format(len(data["master_views"])),
        summary  = u"\U0001f534 {}  ➕ {}  \U0001f504 {} possible renames".format(
            total_missing, total_new, len(rename_suggestions)),
    )
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

lines.append(u"")
lines.append(SEP)
lines.append(u"✅  renames.json saved → {}".format(save_path))
lines.append(u"Run Rename Dependent Views in each destination model to apply the renames.")
lines.append(u"Then run Export Dependent Views to update the JSON.")

ui.show_report(
    text     = u"\n".join(lines),
    title    = u"Audit Source Views",
    subtitle = u"{} master views checked".format(len(data["master_views"])),
    summary  = u"\U0001f534 {}  ➕ {}  \U0001f504 {} renames saved".format(
        total_missing, total_new, len(rename_suggestions)),
)
