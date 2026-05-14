# -*- coding: utf-8 -*-
__title__ = 'Hide Crop\nRegions'
__doc__ = ('Turns off the visible crop region on all dependent views '
           'of the selected master views.')

import sys
import os as _os
from pyrevit import revit, DB, script

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

doc    = revit.doc
output = script.get_output()

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

chosen = ui.pick_list(
    [v.Name for v in master_views],
    "Select Master Views",
    button_name="Hide Crops"
)
if not chosen:
    script.exit()

chosen_set   = set(chosen)
view_by_name = {v.Name: v for v in master_views}
selected     = [view_by_name[n] for n in chosen]

# ─────────────────────────────────────────────────────────────────────────────
# 2. Collect dependent views
# ─────────────────────────────────────────────────────────────────────────────

dep_views = []
for master in selected:
    for vid in master.GetDependentViewIds():
        v = doc.GetElement(vid)
        if v:
            dep_views.append((master.Name, v))

output.print_md("**Dependent views found:** {}".format(len(dep_views)))

# ─────────────────────────────────────────────────────────────────────────────
# 3. Hide crop regions
# ─────────────────────────────────────────────────────────────────────────────

hidden  = 0
skipped = 0
errors  = []

with revit.Transaction("Hide Crop Regions"):
    for master_name, v in dep_views:
        try:
            if v.CropBoxVisible:
                v.CropBoxVisible = False
                hidden += 1
                output.print_md("  ✅ *{}*".format(v.Name))
            else:
                skipped += 1
        except Exception as e:
            errors.append("{}: {}".format(v.Name, str(e)))

output.print_md("\n---")
output.print_md("✅ **{}** crop regions hidden".format(hidden))
output.print_md("⏭️ **{}** already hidden, skipped".format(skipped))

if errors:
    output.print_md("❌ **{} errors:**".format(len(errors)))
    for e in errors:
        output.print_md("  - {}".format(e))