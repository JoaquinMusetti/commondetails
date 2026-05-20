# -*- coding: utf-8 -*-
__title__ = "View Name to Text Notes\n(archived)"
__doc__ = ('Creates a TextNote at the center of the CropBox of selected Dependent Views. '
           'The text content is the View Name of the dependent view. '
           'Select the crop boundary elements, choose a text type, and run the tool.')

import os as _os
import sys

sys.path.append(_os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))),
    'lib'
))
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

from pyrevit import revit, DB, script

doc   = revit.doc
uidoc = revit.uidoc
view  = uidoc.ActiveView

# --------------------------------------------------------------------------
# 1. Selection
# --------------------------------------------------------------------------
selection_ids = uidoc.Selection.GetElementIds()
if not selection_ids:
    ui.alert("Select the crop boundaries of the dependent views.", title="View Name to Text Notes")
    script.exit()

# --------------------------------------------------------------------------
# 2. Dependent views indexed by Id and by name
# --------------------------------------------------------------------------
dep_by_id   = {}
dep_by_name = {}

for vid in view.GetDependentViewIds():
    dep = doc.GetElement(vid)
    if dep is None:
        continue
    dep_by_id[vid.IntegerValue] = dep
    try:
        dep_by_name[dep.Name] = dep
    except Exception:
        pass

if not dep_by_id:
    ui.alert("The active view has no dependent views.", title="View Name to Text Notes")
    script.exit()

# --------------------------------------------------------------------------
# 3. Helper - Strategy C: read View Name parameter from element
# --------------------------------------------------------------------------
def get_view_name_param(elem):
    for p in elem.Parameters:
        try:
            if p.StorageType != DB.StorageType.String:
                continue
            if p.Definition.Name == "View Name":
                val = p.AsString()
                if val:
                    return val
        except Exception:
            continue
    return None

# --------------------------------------------------------------------------
# 4. Match element to dependent view
#    Strategy A (sections):    Id + 1
#    Strategy C (floor plans): View Name parameter == view name
# --------------------------------------------------------------------------
target_views = []
unresolved   = []

for eid in selection_ids:
    elem     = doc.GetElement(eid)
    resolved = None

    # Strategy A
    candidate_id = eid.IntegerValue + 1
    if candidate_id in dep_by_id:
        resolved = dep_by_id[candidate_id]

    # Strategy C
    if resolved is None:
        vn = get_view_name_param(elem)
        if vn and vn in dep_by_name:
            resolved = dep_by_name[vn]

    if resolved:
        target_views.append(resolved)
    else:
        unresolved.append(elem)

if not target_views:
    ui.alert("No matching dependent view found.", title="View Name to Text Notes")
    script.exit()

# --------------------------------------------------------------------------
# 5. Choose TextNoteType
# --------------------------------------------------------------------------
text_note_types = DB.FilteredElementCollector(doc)\
    .OfClass(DB.TextNoteType)\
    .ToElements()

type_dict = {}
for t in text_note_types:
    try:
        type_dict[t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString()] = t
    except Exception:
        type_dict[t.Name] = t

if not type_dict:
    ui.alert("No TextNote types found in the document.", title="View Name to Text Notes")
    script.exit()

chosen_type_name = ui.pick_list(
    sorted(type_dict.keys()),
    "Select Text Type",
    multiselect=False,
    context=u"Text type to use. The tool places a TextNote at the center of the "
            u"crop box of each selected view, using the view's name as the content. "
            u"Used to label dependent views on the master view."
)
if not chosen_type_name:
    script.exit()

text_note_type = type_dict[chosen_type_name]

# --------------------------------------------------------------------------
# 6. CropBox center in world space
# --------------------------------------------------------------------------
def get_cropbox_center(target_view):
    crop_box = target_view.CropBox
    if crop_box is None:
        raise Exception("CropBox is None.")

    min_pt    = crop_box.Min
    max_pt    = crop_box.Max
    transform = crop_box.Transform

    local_center = DB.XYZ(
        (min_pt.X + max_pt.X) / 2.0,
        (min_pt.Y + max_pt.Y) / 2.0,
        min_pt.Z
    )
    return transform.OfPoint(local_center)

# --------------------------------------------------------------------------
# 7. Create TextNotes
# --------------------------------------------------------------------------
created_count = 0
errors        = []

with revit.Transaction("Dependent Views -> Text Notes"):
    for v in target_views:
        try:
            center    = get_cropbox_center(v)
            view_name = v.Name

            opts = DB.TextNoteOptions(text_note_type.Id)
            opts.HorizontalAlignment = DB.HorizontalTextAlignment.Center

            DB.TextNote.Create(doc, view.Id, center, view_name, opts)
            created_count += 1

        except Exception as e:
            errors.append("View '{}' ({}): {}".format(v.Name, v.Id, str(e)))

msg = "Created {} text notes from {} view(s).".format(
    created_count, len(target_views)
)
if unresolved:
    msg += "\n\n{} element(s) could not be resolved.".format(len(unresolved))
if errors:
    msg += "\n\nErrors:\n" + "\n".join(errors)

ui.alert(msg, title="View Name to Text Notes")