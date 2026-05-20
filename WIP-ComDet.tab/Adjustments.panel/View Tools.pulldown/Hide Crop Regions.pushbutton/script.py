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

doc = revit.doc
SEP = u"─" * 55

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
    button_name="Hide Crops",
    context=u"Tick the masters whose dependents you want to affect. The tool hides "
            u"the crop region (not the annotation crop) on every dependent view "
            u"under the selected masters. The masters themselves are not touched."
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

lines = []
lines.append(u"Master views selected: {}".format(len(selected)))
lines.append(u"Dependent views found: {}".format(len(dep_views)))
lines.append(u"")
lines.append(SEP)

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
                lines.append(u"  ✅ {}".format(v.Name))
            else:
                skipped += 1
        except Exception as e:
            errors.append(u"{}: {}".format(v.Name, str(e)))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Report
# ─────────────────────────────────────────────────────────────────────────────

lines.append(u"")
lines.append(SEP)
lines.append(u"✅  {} crop regions hidden".format(hidden))
lines.append(u"⏭️  {} already hidden, skipped".format(skipped))

if errors:
    lines.append(u"❌  {} error(s):".format(len(errors)))
    for e in errors:
        lines.append(u"  - {}".format(e))

ui.show_report(
    text     = u"\n".join(lines),
    title    = u"Hide Crop Regions",
    subtitle = u"{} views processed".format(len(dep_views)),
    summary  = u"✅ {}  ⏭️ {}  ❌ {}".format(hidden, skipped, len(errors)),
)
