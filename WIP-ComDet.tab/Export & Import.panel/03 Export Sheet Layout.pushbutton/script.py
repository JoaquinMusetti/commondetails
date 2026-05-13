# -*- coding: utf-8 -*-
__title__ = 'Export\nSheet Layout'
__doc__ = ('Exports viewport positions, detail numbers, and detail lines '
           'from selected sheets to a dated Sheet layout JSON file.')

import json
from datetime import date
from pyrevit import revit, DB, script, forms

doc    = revit.doc
output = script.get_output()

# ─────────────────────────────────────────────────────────────────────────────
# 1. Collect all sheets and show selection list
# ─────────────────────────────────────────────────────────────────────────────

all_sheets = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
all_sheets = list(all_sheets)
all_sheets.sort(key=lambda s: s.SheetNumber)

if not all_sheets:
    forms.alert("No sheets found in the model.", exitscript=True)

sheet_options   = []
sheet_by_option = {}
for s in all_sheets:
    label = "{} - {}".format(s.SheetNumber, s.Name)
    sheet_options.append(label)
    sheet_by_option[label] = s

chosen_options = forms.SelectFromList.show(
    sheet_options,
    title="Select Sheets to Export",
    prompt="Select one or more sheets:",
    multiselect=True
)
if not chosen_options:
    script.exit()

selected_sheets = [sheet_by_option[o] for o in chosen_options]
output.print_md("**Sheets selected:** {}".format(len(selected_sheets)))

# ─────────────────────────────────────────────────────────────────────────────
# 2. Choose output file
# ─────────────────────────────────────────────────────────────────────────────

today = date.today().strftime("%Y%m%d")

save_path = forms.save_file(
    file_ext="json",
    default_name="{}_Sheet layout.json".format(today),
    title="Save Sheet Layout"
)
if not save_path:
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 3. Index dependent views by Id
# ─────────────────────────────────────────────────────────────────────────────

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

dep_views_by_id = {}
for v in all_views:
    try:
        primary_id = v.GetPrimaryViewId()
        if primary_id != DB.ElementId.InvalidElementId:
            dep_views_by_id[v.Id.IntegerValue] = v
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# 4. Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_viewport_type_name(vp):
    try:
        type_id = vp.GetTypeId()
        vp_type = doc.GetElement(type_id)
        if vp_type is None:
            return ""
        # Try Name parameter first
        p = vp_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p:
            return p.AsString() or ""
        # Fallback to Name property
        return vp_type.Name
    except Exception as e:
        return ""

def xyz_to_list(pt):
    return [pt.X, pt.Y, pt.Z]

# ─────────────────────────────────────────────────────────────────────────────
# 5. Build the JSON
# ─────────────────────────────────────────────────────────────────────────────

layout = []

for sheet in selected_sheets:
    output.print_md("### Sheet {} - {}".format(sheet.SheetNumber, sheet.Name))

    sheet_entry = {
        "sheet_number": sheet.SheetNumber,
        "sheet_name":   sheet.Name,
        "viewports":    [],
        "detail_lines": []
    }

    # ── Viewports ──
    vp_ids = sheet.GetAllViewports()
    for vp_id in vp_ids:
        vp = doc.GetElement(vp_id)
        if vp is None:
            continue

        view_id = vp.ViewId.IntegerValue
        if view_id not in dep_views_by_id:
            continue

        v      = dep_views_by_id[view_id]
        center = vp.GetBoxCenter()

        try:
            label_offset = vp.LabelOffset
            label_x = label_offset.X
            label_y = label_offset.Y
        except Exception:
            label_x = 0
            label_y = 0

        template_name = ""
        tid = v.ViewTemplateId
        if tid != DB.ElementId.InvalidElementId:
            tmpl = doc.GetElement(tid)
            if tmpl:
                template_name = tmpl.Name

        scale = 1
        try:
            scale = v.Scale
        except Exception:
            pass

        title_on_sheet = ""
        try:
            p = v.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
            if p:
                title_on_sheet = p.AsString() or ""
        except Exception:
            pass

        detail_number = ""
        try:
            p = vp.get_Parameter(DB.BuiltInParameter.VIEWPORT_DETAIL_NUMBER)
            if p:
                detail_number = p.AsString() or ""
        except Exception:
            pass

        sheet_entry["viewports"].append({
            "view_name":         v.Name,
            "viewport_center_x": center.X,
            "viewport_center_y": center.Y,
            "label_offset_x":    label_x,
            "label_offset_y":    label_y,
            "viewport_type":     get_viewport_type_name(vp),
            "view_scale":        scale,
            "view_template":     template_name,
            "title_on_sheet":    title_on_sheet,
            "detail_number":     detail_number,
            "view_type":         str(v.ViewType),
        })
        output.print_md("  ✅ Viewport: *{}*  |  Detail: *{}*".format(
            v.Name, detail_number or "-"))

    # ── Detail Lines ──
    dl_collector = DB.FilteredElementCollector(doc, sheet.Id)\
        .OfClass(DB.CurveElement)\
        .ToElements()

    for dl in dl_collector:
        try:
            if not isinstance(dl, DB.DetailLine):
                continue
            crv = dl.GeometryCurve
            p0  = crv.GetEndPoint(0)
            p1  = crv.GetEndPoint(1)

            line_style = ""
            try:
                line_style = dl.LineStyle.Name
            except Exception:
                pass

            sheet_entry["detail_lines"].append({
                "p0":         xyz_to_list(p0),
                "p1":         xyz_to_list(p1),
                "line_style": line_style
            })
        except Exception as e:
            output.print_md("  ⚠️ Detail line skipped: {}".format(e))

    output.print_md("  📐 Detail lines: {}".format(
        len(sheet_entry["detail_lines"])))
    layout.append(sheet_entry)

# ─────────────────────────────────────────────────────────────────────────────
# 6. Save JSON
# ─────────────────────────────────────────────────────────────────────────────

with open(save_path, "w") as f:
    json.dump(layout, f, indent=2)

output.print_md("\n---")
output.print_md("✅ **{} sheets exported** → `{}`".format(len(layout), save_path))