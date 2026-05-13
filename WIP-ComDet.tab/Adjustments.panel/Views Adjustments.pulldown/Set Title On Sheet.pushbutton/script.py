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

from pyrevit import revit, DB, script, forms

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

master_views.sort(key=lambda v: v.Name)

view_by_name = {}
for v in master_views:
    view_by_name[v.Name] = v

chosen = forms.SelectFromList.show(
    [v.Name for v in master_views],
    title="Select Master Views",
    prompt="Select one or more master views:",
    multiselect=True
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

updated  = 0
skipped  = 0
errors   = []

with revit.Transaction("Set Title On Sheet"):
    for master_view in selected_views:
        output.print_md("### {}".format(master_view.Name))

        dep_ids = master_view.GetDependentViewIds()
        if not dep_ids:
            output.print_md("  *(no dependent views)*")
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
                            output.print_md("  ✅ *{}*  →  `{}`".format(
                                view_name, new_title))
                        else:
                            output.print_md("  ✔ *{}*  already set, no change".format(
                                view_name))
                        updated += 1
                    else:
                        output.print_md("  ⚠️ *{}*  parameter is read-only".format(
                            view_name))
                else:
                    output.print_md("  ⏭️ *{}*  no prefix, skipped".format(view_name))
                    skipped += 1

            except Exception as e:
                errors.append("*{}*: {}".format(dep.Name, str(e)))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Report
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("✅ **{}** updated  |  ⏭️ **{}** skipped (no prefix)".format(
    updated, skipped))

if errors:
    output.print_md("❌ **{} errors:**".format(len(errors)))
    for e in errors:
        output.print_md("  - {}".format(e))