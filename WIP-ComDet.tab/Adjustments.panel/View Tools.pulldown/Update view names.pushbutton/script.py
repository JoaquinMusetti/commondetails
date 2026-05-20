# -*- coding: utf-8 -*-
__title__ = 'Rename\nViews'
__doc__ = ('Reads a renames.json file and renames dependent views in the destination model. '
           'Run this before Import Dependent Views when view names have changed in the source model.')

"""
RENAME DEPENDENT VIEWS
=======================
Reads renames.json and renames dependent views in the destination model.
Run this before Import Dependent Views when view names have changed in the
source model.
"""

import json
import os as _os
import sys
_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

from pyrevit import revit, DB, script, forms

doc    = revit.doc
output = script.get_output()

# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick renames.json
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(
    file_ext="json",
    title="Select renames.json"
)
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    renames = json.load(f)

output.print_md("**Renames in JSON:** {}".format(len(renames)))

# ─────────────────────────────────────────────────────────────────────────────
# 2. Index dependent views by name
# ─────────────────────────────────────────────────────────────────────────────

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

dep_view_by_name = {}
for v in all_views:
    try:
        if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
            dep_view_by_name[v.Name] = v
    except Exception:
        pass

output.print_md("**Dependent views indexed:** {}".format(len(dep_view_by_name)))

# ─────────────────────────────────────────────────────────────────────────────
# 3. Preview renames
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")

can_rename  = []
not_found   = []
name_clash  = []

for r in renames:
    old_name = r["old_name"]
    new_name = r["new_name"]

    if old_name not in dep_view_by_name:
        not_found.append(old_name)
        output.print_md("  ⏭️ Not found: *{}*".format(old_name))
        continue

    if new_name in dep_view_by_name:
        name_clash.append(new_name)
        output.print_md("  ❌ Name already exists: *{}*  (cannot rename *{}*)".format(
            new_name, old_name))
        continue

    can_rename.append(r)
    output.print_md("  🔄 *{}*  →  *{}*".format(old_name, new_name))

if not can_rename:
    output.print_md("\n❌ No renames can be applied.")
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 4. Confirm and apply
# ─────────────────────────────────────────────────────────────────────────────

confirm = ui.confirm(
    "{} view(s) will be renamed.\n{} not found.\n{} name clash(es).\n\n"
    "Proceed?".format(len(can_rename), len(not_found), len(name_clash)),
    title="Rename Views"
)
if not confirm:
    script.exit()

renamed = 0
errors  = []

with revit.Transaction("Rename Dependent Views"):
    for r in can_rename:
        old_name = r["old_name"]
        new_name = r["new_name"]
        try:
            view = dep_view_by_name[old_name]
            view.Name = new_name
            output.print_md("  ✅ Renamed: *{}*  →  *{}*".format(old_name, new_name))
            renamed += 1
        except Exception as e:
            errors.append("*{}*: {}".format(old_name, str(e)))

# ─────────────────────────────────────────────────────────────────────────────
# 5. Report
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("✅ **{}** view(s) renamed".format(renamed))

if not_found:
    output.print_md("⏭️ **{} not found:**".format(len(not_found)))
    for n in not_found:
        output.print_md("  - *{}*".format(n))

if name_clash:
    output.print_md("❌ **{} name clash(es):**".format(len(name_clash)))
    for n in name_clash:
        output.print_md("  - *{}*".format(n))

if errors:
    output.print_md("❌ **{} errors:**".format(len(errors)))
    for e in errors:
        output.print_md("  - {}".format(e))