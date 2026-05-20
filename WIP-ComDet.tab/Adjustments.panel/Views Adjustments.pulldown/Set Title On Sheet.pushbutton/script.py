# -*- coding: utf-8 -*-
__title__ = 'Set Title\nOn Sheet'
__doc__ = """Sets the Title on Sheet parameter for dependent views based on their view name.

If the view name starts with a numeric prefix (##_),
the title on sheet is set to the view name without the first 3 characters.
Example: 05_BACKING DETAILS AT PIPES -> BACKING DETAILS AT PIPES

If the view name has no prefix, the title on sheet is left unchanged.
This ensures that views without a prefix are not affected when
a prefix is added to the view name in the future.

Usage: Run the tool and select one or more master views from the list."""

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

master_views.sort(key=lambda v: v.Name)

view_by_name = {}
for v in master_views:
    view_by_name[v.Name] = v

chosen = ui.pick_list(
    [v.Name for v in master_views],
    "Select Master Views",
    button_name="Set Titles",
    context=u"Tick the masters whose dependents you want to affect. The tool reads "
            u"the numeric prefix of each dependent's name (e.g. '01_PoolDeck' → '01') "
            u"and stamps it into 'Title on Sheet'. Views without a numeric prefix "
            u"are left alone."
)
if not chosen:
    script.exit()

selected_views = [view_by_name[n] for n in chosen]

# ─────────────────────────────────────────────────────────────────────────────
# 2. Helper — detect ##_ prefix
# ─────────────────────────────────────────────────────────────────────────────

def has_prefix(name):
    """Returns True if name starts with exactly ##_ (2 digits + underscore)."""
    if len(name) < 3:
        return False
    return name[0].isdigit() and name[1].isdigit() and name[2] == "_"

def strip_prefix(name):
    return name[3:]

# ─────────────────────────────────────────────────────────────────────────────
# 3. Process dependent views
# ─────────────────────────────────────────────────────────────────────────────

lines   = []
updated = 0
skipped = 0
errors  = []

with revit.Transaction("Set Title On Sheet"):
    for master_view in selected_views:
        lines.append(u"")
        lines.append(u"{}".format(master_view.Name))

        dep_ids = master_view.GetDependentViewIds()
        if not dep_ids:
            lines.append(u"  (no dependent views)")
            continue

        for vid in dep_ids:
            dep = doc.GetElement(vid)
            if dep is None:
                continue
            try:
                view_name = dep.Name

                if has_prefix(view_name):
                    new_title = strip_prefix(view_name)
                    p = dep.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
                    if p and not p.IsReadOnly:
                        current = p.AsString() or ""
                        if current != new_title:
                            p.Set(new_title)
                            lines.append(u"  ✅ {}  →  {}".format(view_name, new_title))
                        else:
                            lines.append(u"  ✔  {} already set, no change".format(view_name))
                        updated += 1
                    else:
                        lines.append(u"  ⚠  {} — parameter is read-only".format(view_name))
                else:
                    lines.append(u"  ⏭  {} — no prefix, skipped".format(view_name))
                    skipped += 1

            except Exception as e:
                errors.append(u"{}: {}".format(dep.Name, str(e)))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Report
# ─────────────────────────────────────────────────────────────────────────────

lines.append(u"")
lines.append(SEP)
lines.append(u"✅  {} updated  |  ⏭️  {} skipped (no prefix)".format(updated, skipped))

if errors:
    lines.append(u"❌  {} error(s):".format(len(errors)))
    for e in errors:
        lines.append(u"  - {}".format(e))

ui.show_report(
    text     = u"\n".join(lines),
    title    = u"Set Title On Sheet",
    subtitle = u"{} master view(s) processed".format(len(selected_views)),
    summary  = u"✅ {} updated  ⏭️ {} skipped".format(updated, skipped),
)
