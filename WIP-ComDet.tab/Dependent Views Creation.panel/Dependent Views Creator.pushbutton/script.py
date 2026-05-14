# -*- coding: utf-8 -*-
__title__ = 'Dependent\nViews Creator'
__doc__ = ('Creates dependent views from Detail Line rectangles drawn in the active view. '
           'Before running: draw rectangles using a reserved LineStyle (e.g. "_CropBox"), '
           'and place a TextNote of a reserved TextNoteType (e.g. "_CropName") inside each '
           'rectangle to name the dependent view. '
           'The tool will ask you to pick both the LineStyle and the TextNoteType at startup.')

import os as _os
import sys

sys.path.append(_os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))),
    'lib'
))
from magictools import ui

from pyrevit import revit, DB, script
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewDuplicateOption,
    CurveLoop,
    Line,
    XYZ,
    Transform,
    Transaction,
    DetailLine,
    TextNote,
    TextNoteType,
)

doc    = revit.doc
uidoc  = revit.uidoc
output = script.get_output()


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — coordinate transforms
# ─────────────────────────────────────────────────────────────────────────────

def get_view_transform(view):
    t = Transform.Identity
    t.BasisX = view.RightDirection
    t.BasisY = view.UpDirection
    t.BasisZ = view.ViewDirection
    t.Origin = view.Origin
    return t


def world_to_view(pt, view_transform):
    return view_transform.Inverse.OfPoint(pt)


def view_to_world(pt, view_transform):
    return view_transform.OfPoint(pt)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — endpoints and loops
# ─────────────────────────────────────────────────────────────────────────────

def get_endpoints(detail_line):
    crv = detail_line.GeometryCurve
    return crv.GetEndPoint(0), crv.GetEndPoint(1)


def pts_close(a, b, tol=1e-4):
    return a.DistanceTo(b) < tol


def build_loops(segments):
    segs = [[e, p0, p1] for e, p0, p1 in segments]
    remaining = list(range(len(segs)))
    loops = []

    while remaining:
        start_idx = remaining[0]
        loop_indices = [start_idx]
        remaining.remove(start_idx)

        loop_start_pt = segs[start_idx][1]
        current_end   = segs[start_idx][2]

        for _ in range(len(segs)):
            found = False
            for r in list(remaining):
                p0, p1 = segs[r][1], segs[r][2]
                if pts_close(current_end, p0):
                    loop_indices.append(r)
                    remaining.remove(r)
                    current_end = p1
                    found = True
                    break
                elif pts_close(current_end, p1):
                    segs[r][1], segs[r][2] = p1, p0
                    loop_indices.append(r)
                    remaining.remove(r)
                    current_end = p0
                    found = True
                    break
            if not found:
                break
            if pts_close(current_end, loop_start_pt):
                break

        if pts_close(current_end, loop_start_pt) and len(loop_indices) >= 3:
            loops.append([tuple(segs[i]) for i in loop_indices])
        else:
            output.print_md(
                "⚠️ {} line(s) do not form a closed loop and will be ignored.".format(
                    len(loop_indices)
                )
            )

    return loops


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — bounding box in view-space
# ─────────────────────────────────────────────────────────────────────────────

def loop_bbox_viewspace(loop_segs, view_transform):
    view_pts = []
    for _, p0, p1 in loop_segs:
        view_pts.append(world_to_view(p0, view_transform))
        view_pts.append(world_to_view(p1, view_transform))

    xs = [p.X for p in view_pts]
    ys = [p.Y for p in view_pts]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_bbox(pt_view, bbox, margin=1e-6):
    min_x, min_y, max_x, max_y = bbox
    return (min_x - margin <= pt_view.X <= max_x + margin and
            min_y - margin <= pt_view.Y <= max_y + margin)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — pickers for linestyle and textnote type
# ─────────────────────────────────────────────────────────────────────────────

def get_linestyle_names(doc):
    names = []
    try:
        detail_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
        for sub in detail_cat.SubCategories:
            gs = sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
            if gs:
                names.append(sub.Name)
    except Exception:
        pass
    return sorted(names)


def get_textnote_type_names(doc):
    names = []
    for t in FilteredElementCollector(doc).OfClass(TextNoteType).ToElements():
        try:
            names.append(t.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString())
        except Exception:
            try:
                names.append(t.Name)
            except Exception:
                pass
    return sorted(set(n for n in names if n))


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — TextNotes filtered by type
# ─────────────────────────────────────────────────────────────────────────────

def get_textnotes_in_view(view, textnote_type_name):
    collector = (
        FilteredElementCollector(doc, view.Id)
        .OfClass(TextNote)
        .ToElements()
    )
    result = []
    for tn in collector:
        try:
            # Filter by TextNoteType name
            tn_type_name = ""
            tn_type = doc.GetElement(tn.GetTypeId())
            if tn_type:
                p = tn_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
                tn_type_name = p.AsString() if p else tn_type.Name
            if tn_type_name != textnote_type_name:
                continue

            text = tn.Text.strip()
            origin = tn.Coord
            if text:
                result.append((text, origin))
        except Exception:
            pass
    return result


def find_name_for_loop(bbox_viewspace, all_textnotes, view_transform):
    found = []
    for text, origin_world in all_textnotes:
        origin_view = world_to_view(origin_world, view_transform)
        if point_in_bbox(origin_view, bbox_viewspace):
            found.append(text)

    if len(found) == 0:
        return None
    if len(found) > 1:
        output.print_md(
            "  ⚠️ {} name TextNotes found inside the rectangle. Using the first one: *{}*".format(
                len(found), found[0]
            )
        )
    return found[0]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — crop region
# ─────────────────────────────────────────────────────────────────────────────

def make_crop_loop(bbox_viewspace, view_transform):
    min_x, min_y, max_x, max_y = bbox_viewspace

    w = max_x - min_x
    h = max_y - min_y

    if w < 0.01 or h < 0.01:
        raise ValueError(
            "Bounding box too small: {:.4f} x {:.4f} ft. "
            "Check that the lines are in the active view.".format(w, h)
        )

    corners_view = [
        XYZ(min_x, min_y, 0.0),
        XYZ(max_x, min_y, 0.0),
        XYZ(max_x, max_y, 0.0),
        XYZ(min_x, max_y, 0.0),
    ]
    corners_world = [view_to_world(c, view_transform) for c in corners_view]

    loop = CurveLoop()
    for k in range(4):
        loop.Append(Line.CreateBound(corners_world[k], corners_world[(k + 1) % 4]))

    return loop


def next_available_name(base, existing):
    if base not in existing:
        return base
    n = 2
    while True:
        candidate = "{} ({})".format(base, n)
        if candidate not in existing:
            return candidate
        n += 1


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    active_view = uidoc.ActiveView

    if not active_view.CanViewBeDuplicated(ViewDuplicateOption.AsDependent):
        ui.alert(
            "The active view does not support dependent views.\n"
            "Switch to a floor plan, elevation, or section view.",
            title="Dependent Views Creator",
        )
        script.exit()

    # ── Pick crop box linestyle ──
    linestyle_names = get_linestyle_names(doc)
    if not linestyle_names:
        ui.alert("No line styles found in document.", title="Dependent Views Creator")
        script.exit()

    chosen_linestyle = ui.pick_list(
        linestyle_names,
        "Crop Box LineStyle",
        prompt="Select the LineStyle used for crop box rectangles:",
        multiselect=False,
    )
    if not chosen_linestyle:
        script.exit()

    # ── Pick view name TextNoteType ──
    textnote_type_names = get_textnote_type_names(doc)
    if not textnote_type_names:
        ui.alert("No TextNote types found in document.", title="Dependent Views Creator")
        script.exit()

    chosen_tn_type = ui.pick_list(
        textnote_type_names,
        "View Name TextNoteType",
        prompt="Select the TextNoteType used for view name labels:",
        multiselect=False,
    )
    if not chosen_tn_type:
        script.exit()

    output.print_md("**Crop box LineStyle:** `{}`".format(chosen_linestyle))
    output.print_md("**View name TextNoteType:** `{}`".format(chosen_tn_type))

    # ── Collect and filter selection by linestyle ──
    sel_ids = list(uidoc.Selection.GetElementIds())
    if not sel_ids:
        ui.alert(
            "Nothing selected.\n"
            "Select the Detail Lines that form the crop box rectangles.",
            title="Dependent Views Creator",
        )
        script.exit()

    detail_lines = []
    for eid in sel_ids:
        el = doc.GetElement(eid)
        if not isinstance(el, DetailLine):
            continue
        try:
            if el.LineStyle.Name == chosen_linestyle:
                detail_lines.append(el)
        except Exception:
            pass

    if not detail_lines:
        ui.alert(
            "No Detail Lines with LineStyle '{}' in selection.\n\n"
            "Make sure:\n"
            "  - Lines are DetailLines (not ModelLines)\n"
            "  - Lines have the correct LineStyle assigned".format(chosen_linestyle),
            title="Dependent Views Creator",
        )
        script.exit()

    output.print_md("**Detail Lines with correct LineStyle:** {}".format(len(detail_lines)))

    segments = [(dl,) + get_endpoints(dl) for dl in detail_lines]
    loops = build_loops(segments)

    if not loops:
        ui.alert(
            "No closed loops detected.\n\n"
            "Make sure that:\n"
            "  - Each rectangle is fully closed\n"
            "  - Endpoints touch each other (use Snap to Endpoint)\n"
            "  - No loose lines are mixed in",
            title="Dependent Views Creator",
        )
        script.exit()

    output.print_md("**Loops detected:** {}  \n".format(len(loops)))

    view_transform = get_view_transform(active_view)
    all_textnotes  = get_textnotes_in_view(active_view, chosen_tn_type)

    output.print_md(
        "*Name TextNotes found in view (type '{}'): {}*  \n".format(
            chosen_tn_type, len(all_textnotes))
    )

    # Names of all views in the project
    all_views = FilteredElementCollector(doc).OfClass(DB.View).ToElements()
    existing_names = set()
    for v in all_views:
        try:
            existing_names.add(v.Name)
        except Exception:
            pass

    # Names of existing dependent views from the active view
    existing_dep_names = set()
    for vid in active_view.GetDependentViewIds():
        dep = doc.GetElement(vid)
        if dep:
            try:
                existing_dep_names.add(dep.Name)
            except Exception:
                pass

    master_name = active_view.Name
    default_zone_counter = [0]

    with Transaction(doc, "Create Dependent Views from Detail Lines") as t:
        t.Start()

        created  = 0
        skipped  = 0

        for i, loop_segs in enumerate(loops):

            bbox = loop_bbox_viewspace(loop_segs, view_transform)
            min_x, min_y, max_x, max_y = bbox
            w_ft = max_x - min_x
            h_ft = max_y - min_y

            output.print_md("**Loop {}** — {:.3f} x {:.3f} ft  ({:.0f} x {:.0f} mm)".format(
                i + 1, w_ft, h_ft, w_ft * 304.8, h_ft * 304.8
            ))

            found_name = find_name_for_loop(bbox, all_textnotes, view_transform)

            if found_name:
                output.print_md("  📝 Name detected: *{}*".format(found_name))
                base_name = found_name
            else:
                default_zone_counter[0] += 1
                base_name = "{} - Zone {}".format(master_name, default_zone_counter[0])
                output.print_md(
                    "  ℹ️ No name TextNote inside → default name: *{}*".format(base_name)
                )

            if base_name in existing_dep_names:
                output.print_md(
                    "  ⏭️ *{}* already exists as a dependent view → skipped".format(base_name)
                )
                skipped += 1
                continue

            final_name = next_available_name(base_name, existing_names)
            if final_name != base_name:
                output.print_md(
                    "  ⚠️ *{}* already exists → renamed to *{}*".format(base_name, final_name)
                )
            existing_names.add(final_name)

            try:
                crop_loop = make_crop_loop(bbox, view_transform)
            except ValueError as e:
                output.print_md("  ❌ {}".format(e))
                continue

            new_view_id = active_view.Duplicate(ViewDuplicateOption.AsDependent)
            new_view    = doc.GetElement(new_view_id)

            try:
                new_view.Name = final_name
            except Exception:
                try:
                    param = new_view.get_Parameter(DB.BuiltInParameter.VIEW_NAME)
                    if param and not param.IsReadOnly:
                        param.Set(final_name)
                except Exception:
                    pass

            new_view.CropBoxActive  = True
            new_view.CropBoxVisible = True
            crop_manager = new_view.GetCropRegionShapeManager()
            crop_manager.SetCropShape(crop_loop)

            output.print_md("  ✅ View created: *{}*  \n".format(final_name))
            created += 1

        t.Commit()

    output.print_md(
        "\n---\n✅ **{} dependent views created** — ⏭️ **{} skipped** (already existed) — in *{}*".format(
            created, skipped, master_name
        )
    )


main()
