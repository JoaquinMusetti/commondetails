# -*- coding: utf-8 -*-
__title__ = "View Crop\nto Lines"
__doc__ = ('Creates Detail Lines from the CropBox of selected Dependent Views. '
           'Works with sections, elevations, and floor plans. '
           'Select the crop boundary elements, choose a line style, and run the tool.')

import sys
import os as _os
from pyrevit import revit, DB, script

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

doc   = revit.doc
uidoc = revit.uidoc
view  = uidoc.ActiveView

# --------------------------------------------------------------------------
# 1. Selection
# --------------------------------------------------------------------------
selection_ids = uidoc.Selection.GetElementIds()
if not selection_ids:
    ui.alert("Select the crop boundaries of the dependent views.",
             title="View Crop to Lines")
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
    ui.alert("The active view has no dependent views.", title="View Crop to Lines")
    script.exit()

# --------------------------------------------------------------------------
# 3. Helper - Strategy C
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
    ui.alert("No matching dependent view found.", title="View Crop to Lines")
    script.exit()

# --------------------------------------------------------------------------
# 5. Line style picker
# --------------------------------------------------------------------------
def get_all_line_styles(document):
    detail_cat = document.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    styles = {}
    for sub in detail_cat.SubCategories:
        gs = sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
        if gs:
            styles[sub.Name] = gs
    return styles

all_styles = get_all_line_styles(doc)
if not all_styles:
    ui.alert("No line styles found in the document.", title="View Crop to Lines")
    script.exit()

chosen = ui.pick_list(
    sorted(all_styles.keys()),
    "Select Line Style",
    button_name="Create Lines",
    multiselect=False,
    context=u"Line style used to draw the crop box rectangles. The tool reads the "
            u"crop box of each selected dependent view and draws 4 detail lines of "
            u"this style forming the rectangle in the parent view. Useful for "
            u"visualizing crops on the master view."
)
if not chosen:
    script.exit()

line_style = all_styles[chosen]

# --------------------------------------------------------------------------
# 6. CropBox to 4 lines in world space
# --------------------------------------------------------------------------
def get_crop_box_lines(target_view):
    crop_box = target_view.CropBox
    if crop_box is None:
        raise Exception("CropBox is None.")

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

    lines = []
    for i in range(4):
        p1 = world_corners[i]
        p2 = world_corners[(i + 1) % 4]
        try:
            lines.append(DB.Line.CreateBound(p1, p2))
        except Exception as e:
            script.get_logger().warning(
                "Segment skipped for '{}': {}".format(target_view.Name, e)
            )
    return lines

# --------------------------------------------------------------------------
# 7. Create detail lines
# --------------------------------------------------------------------------
created_count = 0
errors        = []

with revit.Transaction("Dependent Views -> Detail Lines"):
    for v in target_views:
        try:
            for curve in get_crop_box_lines(v):
                dl = doc.Create.NewDetailCurve(view, curve)
                dl.LineStyle = line_style
                created_count += 1
        except Exception as e:
            errors.append("View '{}' ({}): {}".format(v.Name, v.Id, str(e)))

msg = "Created {} detail lines with style '{}' from {} view(s).".format(
    created_count, chosen, len(target_views)
)
if unresolved:
    msg += "\n\n{} element(s) could not be resolved.".format(len(unresolved))
if errors:
    msg += "\n\nErrors:\n" + "\n".join(errors)

ui.alert(msg, title="Dependent Views -> Detail Lines")