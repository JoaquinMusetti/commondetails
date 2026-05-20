# -*- coding: utf-8 -*-
__title__ = 'Import\nSheets with Views'
__doc__ = ('Reads a Sheets with Views JSON (exported by "Export Sheets with Views") '
           'and in a single run: creates or updates dependent views, then places '
           'them on the destination model sheets. '
           'When importing into the Common Details file itself, choose '
           '"None (I\'m importing views into the Common Details file)" '
           'as the linked model.')

import json
import sys
import os as _os
import time
from pyrevit import revit, DB, script, forms, HOST_APP
from Autodesk.Revit.DB import (CurveLoop, Line, ViewDuplicateOption,
                                BuiltInParameterGroup, BuiltInCategory)

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

doc   = revit.doc
output = script.get_output()
output.close()

NONE_OPTION = "None (I'm importing views into the Common Details file)"
_SPF_PATH   = _os.path.join(_ext_dir, 'lib', 'cucosync_shared_params.txt')

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _patch_spf_varies(spf_path, param_name):
    """Set VARIES_ACROSS_GROUPS=1 in the shared params text file."""
    try:
        with open(spf_path, 'r') as f:
            lines = f.readlines()
        new_lines = []
        changed = False
        for line in lines:
            if line.startswith('PARAM\t'):
                parts = line.rstrip('\r\n').split('\t')
                if len(parts) >= 3 and parts[2] == param_name:
                    while len(parts) < 10:
                        parts.append('0')
                    if parts[9] != '1':
                        parts[9] = '1'
                        line = '\t'.join(parts) + '\n'
                        changed = True
            new_lines.append(line)
        if changed:
            with open(spf_path, 'w') as f:
                f.writelines(new_lines)
        return changed
    except Exception:
        return False


def ensure_detail_id_param(doc, app):
    _patch_spf_varies(_SPF_PATH, "Detail ID")
    orig = app.SharedParametersFilename
    app.SharedParametersFilename = _SPF_PATH
    try:
        spf = app.OpenSharedParameterFile()
        if spf is None:
            return False
        grp = spf.Groups.get_Item("CucoSync") or spf.Groups.Create("CucoSync")
        defn = grp.Definitions.get_Item("Detail ID")
        if defn is None:
            opts = DB.ExternalDefinitionCreationOptions("Detail ID", DB.SpecTypeId.String.Text)
            opts.UserModifiable = True
            opts.VariesAcrossGroups = True
            defn = grp.Definitions.Create(opts)
        cat_set = app.Create.NewCategorySet()
        cat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Views)
        if cat:
            cat_set.Insert(cat)
        binding = app.Create.NewInstanceBinding(cat_set)
        existing_key = None
        is_instance   = False
        it = doc.ParameterBindings.ForwardIterator()
        while it.MoveNext():
            if it.Key.Name == "Detail ID":
                existing_key = it.Key
                is_instance  = isinstance(it.Current, DB.InstanceBinding)
                break
        if existing_key is None:
            doc.ParameterBindings.Insert(defn, binding, BuiltInParameterGroup.PG_IDENTITY_DATA)
        elif is_instance:
            doc.ParameterBindings.ReInsert(defn, binding, BuiltInParameterGroup.PG_IDENTITY_DATA)
        else:
            doc.ParameterBindings.Remove(existing_key)
            doc.ParameterBindings.Insert(defn, binding, BuiltInParameterGroup.PG_IDENTITY_DATA)
        return True
    finally:
        app.SharedParametersFilename = orig


def get_detail_id(view):
    p = view.LookupParameter("Detail ID")
    return (p.AsString() or "") if p else ""


def set_detail_id(view, value):
    try:
        p = view.LookupParameter("Detail ID")
        if p and not p.IsReadOnly:
            p.Set(value)
    except Exception:
        pass


def link_to_world(pt_list, link_transform):
    pt = DB.XYZ(pt_list[0], pt_list[1], pt_list[2])
    if link_transform is None:
        return pt
    return link_transform.OfPoint(pt)


def make_crop_loop(corners_world):
    loop = CurveLoop()
    for k in range(4):
        loop.Append(Line.CreateBound(
            corners_world[k],
            corners_world[(k + 1) % 4]
        ))
    return loop


def set_detail_number(vp, value):
    try:
        p = vp.get_Parameter(DB.BuiltInParameter.VIEWPORT_DETAIL_NUMBER)
        if p and not p.IsReadOnly:
            p.Set(value)
    except Exception:
        pass


def get_detail_number(vp):
    try:
        p = vp.get_Parameter(DB.BuiltInParameter.VIEWPORT_DETAIL_NUMBER)
        if p:
            return p.AsString() or ""
    except Exception:
        pass
    return ""


def get_line_style(name, line_style_by_name):
    if name and name in line_style_by_name:
        return line_style_by_name[name]
    if line_style_by_name:
        return list(line_style_by_name.values())[0]
    return None

# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick JSON file
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(
    file_ext="json",
    title="Select Sheets with Views JSON"
)
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    data = json.load(f)

if data.get("format") != "sheets_with_views":
    ui.alert(
        u"Wrong file selected.\n\n"
        u"Import Sheets with Views expects a file exported by "
        u"\"Export Sheets with Views\" (format: sheets_with_views).\n\n"
        u"The selected file has format: \"{}\".\n\n"
        u"Please select the correct JSON file.".format(
            data.get("format", "unknown")),
        title=u"Import Sheets with Views"
    )
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 1b. Select linked model (determines master_map direction)
# ─────────────────────────────────────────────────────────────────────────────

link_instances = DB.FilteredElementCollector(doc)\
    .OfClass(DB.RevitLinkInstance)\
    .ToElements()

link_by_name = {li.Name: li for li in link_instances}
link_options = [NONE_OPTION] + sorted(link_by_name.keys())

chosen_link = ui.pick_list(
    link_options,
    "1 of 5 — Reference Linked Model",
    button_name="Next",
    multiselect=False,
    context=u"Pick 'None' if you're working inside Common Details (migrating custom "
            u"details from a building into CD — the master_map auto-inverts). Pick the "
            u"Common Details link if you're in a BUILDING importing content from CD."
)
if not chosen_link:
    script.exit()

if chosen_link == NONE_OPTION:
    link_transform = None
else:
    link_transform = link_by_name[chosen_link].GetTotalTransform()

# ─────────────────────────────────────────────────────────────────────────────
# 1c. Apply master_map.json
# ─────────────────────────────────────────────────────────────────────────────

map_path       = _os.path.join(_os.path.dirname(json_path), "master_map.json")
master_map_info = u""
if _os.path.isfile(map_path):
    with open(map_path, "r") as f:
        raw_map = json.load(f)
    if link_transform is None:
        # Importing into CD → reverse (building name → CD name)
        master_map = {v: k for k, v in raw_map.items()}
        map_dir    = u"reversed"
    else:
        # Importing into a building → forward (CD name → building name)
        master_map = raw_map
        map_dir    = u"forward"
    remapped = 0
    for mv in data["master_views"]:
        if mv["view_name"] in master_map:
            mv["view_name"] = master_map[mv["view_name"]]
            remapped += 1
    master_map_info = u"master_map.json: {} remapped ({})".format(remapped, map_dir)

# ─────────────────────────────────────────────────────────────────────────────
# 1d. Select which sheets to import
#     The JSON's natural unit is the sheet — the user picks which sheets they
#     want to bring across; the tool then derives the set of dependent views
#     each picked sheet references and creates only those (under their
#     matching masters).
# ─────────────────────────────────────────────────────────────────────────────

sheets_in_json = data.get("sheets", [])
if not sheets_in_json:
    ui.alert(
        u"This JSON has no 'sheets' section — nothing to import.\n\n"
        u"Make sure it was exported with 'Export Sheets with Views' and "
        u"that at least one sheet was selected during the export.",
        title=u"Import Sheets with Views"
    )
    script.exit()

sheet_options = [
    u"{} - {}  ({} views)".format(
        sh["sheet_number"], sh.get("sheet_name", ""),
        len(sh.get("viewports", [])))
    for sh in sheets_in_json
]
chosen_sheet_opts = ui.pick_list(
    sheet_options,
    "2 of 5 — Select Sheets to Import",
    multiselect=True,
    context=u"Tick the sheets you want to bring into the active model. The tool "
            u"figures out which dependent views each sheet needs and creates them "
            u"automatically under their matching master views. Sheets you don't "
            u"tick (and any views unique to them) are skipped entirely."
)
if not chosen_sheet_opts:
    script.exit()

# Filter data["sheets"] down to the chosen ones
chosen_sheet_numbers = {opt.split(" - ", 1)[0] for opt in chosen_sheet_opts}
data["sheets"] = [sh for sh in sheets_in_json
                  if sh["sheet_number"] in chosen_sheet_numbers]

# Derive the set of view_names referenced by viewports on chosen sheets
required_view_names = set()
for sh in data["sheets"]:
    for vp in sh.get("viewports", []):
        vn = vp.get("view_name")
        if vn:
            required_view_names.add(vn)

# Filter master_views: keep only deps whose view_name is required, drop
# masters that end up with no remaining deps. Orphans (placed_on_sheet=False)
# don't appear in any sheet's viewports → naturally excluded.
filtered_masters = []
for mv in data["master_views"]:
    kept_deps = [dv for dv in mv["dependent_views"]
                 if dv["view_name"] in required_view_names]
    if kept_deps:
        filtered_masters.append({
            "view_name":       mv["view_name"],
            "view_scale":      mv.get("view_scale"),
            "dependent_views": kept_deps,
        })
data["master_views"] = filtered_masters

# ─────────────────────────────────────────────────────────────────────────────
# 2. Strategy for existing dependent views
# ─────────────────────────────────────────────────────────────────────────────

strategy = ui.pick_list(
    [
        "Skip existing — do not touch dependent views that already exist",
        "Update existing — re-apply crop boundary to views that already exist",
    ],
    "3 of 5 — Strategy for Existing Dependent Views",
    button_name="Next",
    multiselect=False,
    context=u"If a dependent already exists: 'Skip' leaves it untouched. 'Update' "
            u"overwrites the crop geometry with what's in the JSON. Detail ID and "
            u"name are preserved in both cases."
)
if not strategy:
    script.exit()

update_existing = "Update" in strategy

# ─────────────────────────────────────────────────────────────────────────────
# 3. Sheet update options
# ─────────────────────────────────────────────────────────────────────────────

sheet_options_list = [
    "VIEWPORTS | Position & Title location",
    "VIEWPORTS | Match viewport types",
    "VIEWPORTS | Detail number",
    "VIEWPORTS | Title on sheet",
    "SHEET ELEMENTS | Detail lines  (delete existing and redraw)",
]

chosen_sheet_opts = ui.pick_list(
    sheet_options_list,
    "4 of 5 — Sheet Update Options",
    button_name="Next",
    context=u"If the sheets already exist: pick what to overwrite (viewport position, "
            u"viewport type, detail number, title on sheet, detail lines). The 5 "
            u"options are independent."
)
if chosen_sheet_opts is None:
    script.exit()

DO_POSITION   = any("Position & Title location" in o for o in chosen_sheet_opts)
DO_VP_TYPE    = any("Match viewport types"      in o for o in chosen_sheet_opts)
DO_DET_NUMBER = any("Detail number"             in o for o in chosen_sheet_opts)
DO_TITLE      = any("Title on sheet"            in o for o in chosen_sheet_opts)
DO_LINES      = any("Detail lines"              in o for o in chosen_sheet_opts)

# ─────────────────────────────────────────────────────────────────────────────
# 4. Destination model prefix
# ─────────────────────────────────────────────────────────────────────────────

dest_prefix = ui.ask_for_string(
    prompt="Enter the 2-letter prefix of the destination model\n(e.g. AE, AB, AC...)",
    title="5 of 5 — Destination Model Prefix",
    context=u"2-letter prefix of the destination building (AE/AB/AC/AD/AF/AG/AK/AS). "
            u"If you're importing into Common Details itself, use 'CD'. The JSON's "
            u"sheet names get rewritten with this prefix."
)
if not dest_prefix:
    script.exit()

dest_prefix = dest_prefix.strip().upper()

# ─────────────────────────────────────────────────────────────────────────────
# 5. Index destination model resources
# ─────────────────────────────────────────────────────────────────────────────

all_views_dest = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

view_by_name     = {}
template_by_name = {}
dep_view_by_name = {}

for v in all_views_dest:
    try:
        view_by_name[v.Name] = v
        if v.IsTemplate:
            template_by_name[v.Name] = v.Id
        else:
            pid = v.GetPrimaryViewId()
            if pid != DB.ElementId.InvalidElementId:
                dep_view_by_name[v.Name] = v
    except Exception:
        pass

existing_proj_names = set(view_by_name.keys())

all_sheets_dest = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
sheet_by_suffix = {}
for s in all_sheets_dest:
    suffix = s.SheetNumber[2:] if len(s.SheetNumber) > 2 else s.SheetNumber
    sheet_by_suffix[suffix] = s

vp_type_by_name = {}
for t in DB.FilteredElementCollector(doc).OfClass(DB.ElementType).ToElements():
    try:
        p = t.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p:
            name = p.AsString()
            if name:
                family_p = t.get_Parameter(DB.BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
                if family_p and family_p.AsString() == "Viewport":
                    vp_type_by_name[name] = t.Id
    except Exception:
        pass

existing_viewports_coll = DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements()
viewport_by_view_id = {vp.ViewId.IntegerValue: vp for vp in existing_viewports_coll}

existing_dls_by_sheet = {}
for dl in DB.FilteredElementCollector(doc).OfClass(DB.CurveElement).ToElements():
    try:
        if not isinstance(dl, DB.DetailLine):
            continue
        owner_id = dl.OwnerViewId.IntegerValue
        existing_dls_by_sheet.setdefault(owner_id, []).append(dl)
    except Exception:
        pass

line_style_by_name = {}
try:
    detail_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    for sub in detail_cat.SubCategories:
        gs = sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
        if gs:
            line_style_by_name[sub.Name] = gs
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# 5b. Ensure Detail ID parameter
# ─────────────────────────────────────────────────────────────────────────────

with revit.Transaction("Add Detail ID parameter"):
    ensure_detail_id_param(doc, HOST_APP.app)

# ─────────────────────────────────────────────────────────────────────────────
# 6. Import views (two-pass: all Duplicate calls, then all SetCropShape calls)
# ─────────────────────────────────────────────────────────────────────────────

total_v_created     = 0
total_v_updated     = 0
total_v_skipped     = 0
total_id_stamped    = 0
total_crop_adjusted = 0   # Pass 3: AnnotationCropOffset 1/8" + CropBoxVisible=False
view_results        = []   # (status, master_name, view_name, detail_str)
cancelled           = False

# 1/8" expressed in Revit internal units (feet). 1/8 in ÷ 12 in/ft = 1/96 ft.
ANNOTATION_CROP_OFFSET = 1.0 / 96.0

total_views = sum(len(mv["dependent_views"]) for mv in data["master_views"])
processed   = 0

sheets_data = data.get("sheets", [])

with ui.ProgressBar(title=u"Import Sheets with Views", cancellable=True, step=5) as pb:
    pb.update_progress(0, total_views + len(sheets_data))

    # ── Phase A: Views ──
    with revit.Transaction("Import Sheets with Views — Views"):
        for view_data in data["master_views"]:
            view_name = view_data["view_name"]

            if view_name not in view_by_name:
                view_results.append(("skip_master", view_name, "", ""))
                continue

            master_view = view_by_name[view_name]
            view_results.append(("master", view_name, "", ""))

            existing_by_id   = {}
            existing_by_name = {}
            for vid in master_view.GetDependentViewIds():
                dep = doc.GetElement(vid)
                if dep:
                    existing_by_name[dep.Name] = dep
                    did = get_detail_id(dep)
                    if did:
                        existing_by_id[did] = dep

            pending_crops = []

            for dv_data in view_data["dependent_views"]:
                if pb.cancelled:
                    cancelled = True
                    break

                dv_name        = dv_data["view_name"]
                dv_id          = dv_data.get("detail_id", "")
                title_on_sheet = dv_data.get("title_on_sheet", "")
                template_name  = dv_data.get("view_template", "")
                scale          = dv_data.get("view_scale", master_view.Scale)

                corners_world = [
                    link_to_world(c, link_transform)
                    for c in dv_data["crop_corners"]
                ]

                existing_view = existing_by_id.get(dv_id) if dv_id else None
                if existing_view is None:
                    existing_view = existing_by_name.get(dv_name)

                processed += 1
                pb.title = u"Importing views — {}/{} — {}".format(
                    processed, total_views + len(sheets_data), view_name)
                pb.update_progress(processed, total_views + len(sheets_data))

                # Already exists
                if existing_view is not None:
                    if dv_id and not get_detail_id(existing_view):
                        set_detail_id(existing_view, dv_id)
                        total_id_stamped += 1

                    if update_existing:
                        try:
                            crop_loop = make_crop_loop(corners_world)
                            existing_view.CropBoxActive = True
                            existing_view.GetCropRegionShapeManager().SetCropShape(crop_loop)
                            if title_on_sheet:
                                try:
                                    p = existing_view.get_Parameter(
                                        DB.BuiltInParameter.VIEW_DESCRIPTION)
                                    if p and not p.IsReadOnly:
                                        p.Set(title_on_sheet)
                                except Exception:
                                    pass
                            view_results.append(("updated", view_name, existing_view.Name, ""))
                            total_v_updated += 1
                        except Exception as e:
                            view_results.append(("error", view_name, dv_name, str(e)))
                    else:
                        view_results.append(("skipped", view_name, existing_view.Name, ""))
                        total_v_skipped += 1
                    continue

                # Create new — Pass 1: Duplicate + metadata
                try:
                    new_vid  = master_view.Duplicate(ViewDuplicateOption.AsDependent)
                    new_view = doc.GetElement(new_vid)

                    final_name = dv_name
                    if dv_name in existing_proj_names:
                        n = 2
                        while "{} ({})".format(dv_name, n) in existing_proj_names:
                            n += 1
                        final_name = "{} ({})".format(dv_name, n)

                    try:
                        new_view.Name = final_name
                    except Exception:
                        pass

                    if title_on_sheet:
                        try:
                            p = new_view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
                            if p and not p.IsReadOnly:
                                p.Set(title_on_sheet)
                        except Exception:
                            pass

                    try:
                        new_view.Scale = scale
                    except Exception:
                        pass

                    if template_name and template_name in template_by_name:
                        try:
                            new_view.ViewTemplateId = template_by_name[template_name]
                        except Exception:
                            pass

                    if dv_id:
                        set_detail_id(new_view, dv_id)

                    pending_crops.append((new_view, make_crop_loop(corners_world)))

                    view_results.append(("created", view_name, final_name,
                                        u"ID: {}  |  Title: {}".format(
                                            dv_id or u"—",
                                            title_on_sheet or u"(no title)")))
                    total_v_created += 1
                    existing_proj_names.add(final_name)
                    dep_view_by_name[final_name] = new_view   # make available for sheet phase

                except Exception as e:
                    view_results.append(("error", view_name, dv_name, str(e)))

            # Pass 2: apply crop shapes for this master
            for new_view, crop_loop in pending_crops:
                try:
                    new_view.CropBoxActive  = True
                    new_view.CropBoxVisible = True
                    new_view.GetCropRegionShapeManager().SetCropShape(crop_loop)
                except Exception as e:
                    view_results.append(("error", view_name, new_view.Name,
                                        "SetCropShape: " + str(e)))

            # Pass 3: annotation crop offset 1/8" on all 4 sides + hide crop region.
            # Replicates manual workflow of Annotation Crop Offset + Hide Crop Regions
            # so the operator doesn't have to run them after every import.
            for new_view, _ in pending_crops:
                try:
                    if not new_view.CropBoxActive:
                        continue
                    rm = new_view.GetCropRegionShapeManager()
                    try:
                        rm.TopAnnotationCropOffset    = ANNOTATION_CROP_OFFSET
                        rm.BottomAnnotationCropOffset = ANNOTATION_CROP_OFFSET
                        rm.LeftAnnotationCropOffset   = ANNOTATION_CROP_OFFSET
                        rm.RightAnnotationCropOffset  = ANNOTATION_CROP_OFFSET
                    except Exception:
                        # Fallback for versions where the single setter exists
                        rm.SetAnnotationCropOffset(
                            ANNOTATION_CROP_OFFSET, ANNOTATION_CROP_OFFSET,
                            ANNOTATION_CROP_OFFSET, ANNOTATION_CROP_OFFSET)
                    if new_view.CropBoxVisible:
                        new_view.CropBoxVisible = False
                    total_crop_adjusted += 1
                except Exception as e:
                    view_results.append(("error", view_name, new_view.Name,
                                        "CropOffset/Hide: " + str(e)))

            if cancelled:
                break

    # ── Phase B: Sheets ──

    vp_created = 0
    vp_updated = 0
    dl_created = 0
    dl_deleted = 0
    sheet_results = []   # (status, sheet_label, view_name, detail_str)

    sheet_offset = total_views

    for i, sheet_data in enumerate(sheets_data):
        if pb.cancelled:
            cancelled = True
            break

        sheet_number = sheet_data["sheet_number"]
        sheet_name   = sheet_data["sheet_name"]
        suffix       = sheet_number[2:] if len(sheet_number) > 2 else sheet_number
        sheet_label  = u"{}{}".format(dest_prefix, suffix)

        pb.title = u"Importing sheets — {}/{} — {}".format(
            i + 1, len(sheets_data), sheet_label)
        pb.update_progress(sheet_offset + i + 1, total_views + len(sheets_data))

        if suffix not in sheet_by_suffix:
            sheet_results.append(("skipped", sheet_label, u"—",
                                  u"Sheet not found in model"))
            continue

        target_sheet     = sheet_by_suffix[suffix]
        vp_id_to_det_num = {}

        # ══ T1: viewports + lines ══
        t1 = DB.Transaction(doc, "Import Sheets with Views T1 - {}".format(sheet_label))
        try:
            t1.Start()

            all_sheet_vps_t1 = list(DB.FilteredElementCollector(doc, target_sheet.Id)
                                    .OfClass(DB.Viewport).ToElements())
            original_numbers = {sv.Id.IntegerValue: get_detail_number(sv)
                                 for sv in all_sheet_vps_t1}

            # Move all to temp numbers to free up values
            timestamp = str(int(time.time()))
            for idx, sv in enumerate(all_sheet_vps_t1):
                set_detail_number(sv, "zzz{}_{}".format(timestamp, idx))

            processed_vp_ids = []

            for entry in sheet_data["viewports"]:
                view_name   = entry["view_name"]
                center      = DB.XYZ(entry["viewport_center_x"],
                                     entry["viewport_center_y"], 0)
                target_det  = entry.get("detail_number", "")

                if view_name not in dep_view_by_name:
                    sheet_results.append(("skipped", sheet_label, view_name,
                                          u"View not found in model"))
                    continue

                target_view = dep_view_by_name[view_name]

                try:
                    if target_view.Id.IntegerValue in viewport_by_view_id:
                        existing_vp = viewport_by_view_id[target_view.Id.IntegerValue]
                        if existing_vp.SheetId.IntegerValue == target_sheet.Id.IntegerValue:
                            if DO_POSITION:
                                existing_vp.SetBoxCenter(center)
                                try:
                                    existing_vp.LabelOffset = DB.XYZ(
                                        entry.get("label_offset_x", 0),
                                        entry.get("label_offset_y", 0), 0)
                                except Exception:
                                    pass
                            if DO_VP_TYPE:
                                vt = entry.get("viewport_type", "")
                                if vt and vt in vp_type_by_name:
                                    try:
                                        existing_vp.ChangeTypeId(vp_type_by_name[vt])
                                    except Exception:
                                        pass
                            if DO_TITLE:
                                title = entry.get("title_on_sheet", "")
                                if title:
                                    try:
                                        p = target_view.get_Parameter(
                                            DB.BuiltInParameter.VIEW_DESCRIPTION)
                                        if p and not p.IsReadOnly:
                                            p.Set(title)
                                    except Exception:
                                        pass
                            if DO_DET_NUMBER and target_det:
                                vp_id_to_det_num[existing_vp.Id.IntegerValue] = target_det
                            processed_vp_ids.append(existing_vp.Id.IntegerValue)
                            sheet_results.append(("updated", sheet_label, view_name,
                                                  u"det. {}".format(target_det) if target_det else u""))
                            vp_updated += 1
                        else:
                            sheet_results.append(("skipped", sheet_label, view_name,
                                                  u"Already on a different sheet"))
                    else:
                        vp = DB.Viewport.Create(doc, target_sheet.Id, target_view.Id, center)
                        if DO_POSITION:
                            vp.SetBoxCenter(center)
                            try:
                                vp.LabelOffset = DB.XYZ(
                                    entry.get("label_offset_x", 0),
                                    entry.get("label_offset_y", 0), 0)
                            except Exception:
                                pass
                        if DO_VP_TYPE:
                            vt = entry.get("viewport_type", "")
                            if vt and vt in vp_type_by_name:
                                try:
                                    vp.ChangeTypeId(vp_type_by_name[vt])
                                except Exception:
                                    pass
                        title = entry.get("title_on_sheet", "")
                        if title:
                            try:
                                p = target_view.get_Parameter(
                                    DB.BuiltInParameter.VIEW_DESCRIPTION)
                                if p and not p.IsReadOnly:
                                    p.Set(title)
                            except Exception:
                                pass
                        if DO_DET_NUMBER and target_det:
                            vp_id_to_det_num[vp.Id.IntegerValue] = target_det
                        processed_vp_ids.append(vp.Id.IntegerValue)
                        sheet_results.append(("created", sheet_label, view_name,
                                              u"det. {}".format(target_det) if target_det else u""))
                        vp_created += 1

                except Exception as e:
                    sheet_results.append(("error", sheet_label, view_name, str(e)))

            # Restore non-processed viewports to their original detail numbers
            for sv in all_sheet_vps_t1:
                if sv.Id.IntegerValue not in processed_vp_ids:
                    orig = original_numbers.get(sv.Id.IntegerValue, "")
                    if orig:
                        set_detail_number(sv, orig)

            # ── Detail lines ──
            if DO_LINES:
                dl_data = sheet_data.get("detail_lines", [])
                if dl_data:
                    sheet_id     = target_sheet.Id.IntegerValue
                    existing_dls = existing_dls_by_sheet.get(sheet_id, [])
                    deleted = 0
                    for dl in existing_dls:
                        try:
                            doc.Delete(dl.Id)
                            deleted += 1
                        except Exception:
                            pass
                    dl_deleted += deleted
                    created = 0
                    for dl_entry in dl_data:
                        try:
                            p0  = dl_entry["p0"]
                            p1  = dl_entry["p1"]
                            pt0 = DB.XYZ(p0[0], p0[1], p0[2])
                            pt1 = DB.XYZ(p1[0], p1[1], p1[2])
                            if pt0.DistanceTo(pt1) < 1e-4:
                                continue
                            line   = DB.Line.CreateBound(pt0, pt1)
                            new_dl = doc.Create.NewDetailCurve(target_sheet, line)
                            style  = get_line_style(dl_entry.get("line_style", ""),
                                                    line_style_by_name)
                            if style:
                                new_dl.LineStyle = style
                            created += 1
                        except Exception as e:
                            sheet_results.append(("error", sheet_label,
                                                  u"Detail line", str(e)))
                    dl_created += created
                    sheet_results.append(("lines", sheet_label,
                                          u"{} created".format(created),
                                          u"{} deleted".format(deleted)))

            t1.Commit()

        except Exception as e:
            try:
                if t1.HasStarted() and not t1.HasEnded():
                    t1.RollBack()
            except Exception:
                pass
            sheet_results.append(("error", sheet_label, u"T1 rolled back", str(e)))
            continue

        # ══ T2: assign final detail numbers ══
        if not DO_DET_NUMBER or not vp_id_to_det_num:
            continue

        t2 = DB.Transaction(doc, "Import Sheets with Views T2 - {}".format(sheet_label))
        try:
            t2.Start()
            all_sheet_vps_t2 = list(DB.FilteredElementCollector(doc, target_sheet.Id)
                                    .OfClass(DB.Viewport).ToElements())
            ts2 = str(int(time.time())) + "b"
            for idx, sv in enumerate(all_sheet_vps_t2):
                set_detail_number(sv, "zzz{}_{}".format(ts2, idx))
            for sv in all_sheet_vps_t2:
                det_num = vp_id_to_det_num.get(sv.Id.IntegerValue)
                if det_num:
                    set_detail_number(sv, det_num)
            t2.Commit()
        except Exception as e:
            try:
                if t2.HasStarted() and not t2.HasEnded():
                    t2.RollBack()
            except Exception:
                pass
            sheet_results.append(("error", sheet_label, u"T2 rolled back", str(e)))

# ─────────────────────────────────────────────────────────────────────────────
# 7. Noir results window
# ─────────────────────────────────────────────────────────────────────────────

import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('PresentationCore')
from System.Windows import Visibility
from System.Windows.Forms import Clipboard as WinFormsClipboard
from System.Collections.ObjectModel import ObservableCollection

class _Row(object):
    _VIEW_LABELS = {
        "created":     u"✅  View Created",
        "updated":     u"🔄  View Updated",
        "skipped":     u"⏭️  View Skipped",
        "skip_master": u"⏭️  Master Not Found",
        "error":       u"❌  Error",
    }
    _SHEET_LABELS = {
        "created": u"✅  VP Created",
        "updated": u"🔄  VP Updated",
        "skipped": u"⏭️  Skipped",
        "lines":   u"📐  Lines",
        "error":   u"❌  Error",
    }

    def __init__(self, section, status, col1, col2, col3):
        # section: "VIEW" or "SHEET"
        self._section = section
        self._status  = status
        self._col1    = col1   # master (views) / sheet (sheets)
        self._col2    = col2   # view name
        self._col3    = col3   # detail

    @property
    def Section(self):  return self._section
    @property
    def Status(self):
        if self._section == "VIEW":
            return self._VIEW_LABELS.get(self._status, self._status)
        return self._SHEET_LABELS.get(self._status, self._status)
    @property
    def Col1(self):     return self._col1
    @property
    def Col2(self):     return self._col2
    @property
    def Col3(self):     return self._col3


n_v_errors = sum(1 for s, _, _, _ in view_results  if s == "error")
n_s_errors = sum(1 for s, _, _, _ in sheet_results if s == "error")
n_errors   = n_v_errors + n_s_errors
n_skipped  = sum(1 for s, _, _, _ in sheet_results if s == "skipped")

link_info = (chosen_link if chosen_link != NONE_OPTION else u"Local (no transform)")
subtitle  = (u"{} views created  \xb7  {} updated  \xb7  {} skipped"
             u"  \xb7  {} VPs on sheets").format(
    total_v_created, total_v_updated, total_v_skipped, vp_created + vp_updated)
if n_errors:
    subtitle += u"  \xb7  {} error{}".format(n_errors, u"s" if n_errors != 1 else u"")
if master_map_info:
    subtitle += u"  \xb7  " + master_map_info
if cancelled:
    subtitle += u"  \xb7  ⚠ partial"

_BODY_XAML = u"""
<Grid>
  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="*"/>
  </Grid.RowDefinitions>

  <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,16">
    <Border Background="#122E1C" BorderBrush="#50E898" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeVCreated" Foreground="#50E898" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border Background="#142244" BorderBrush="#7EB4F0" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeVUpdated" Foreground="#7EB4F0" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border Background="#1E2740" BorderBrush="#6B7394" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeVSkipped" Foreground="#6B7394" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border Background="#1A2535" BorderBrush="#5B8EC4" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeSVP" Foreground="#5B8EC4" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeLinesBorder" Background="#0F3038" BorderBrush="#5ED4E6"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeLines" Foreground="#5ED4E6" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeIdBorder" Background="#4A3810" BorderBrush="#FFCC66"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeIds" Foreground="#FFCC66" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeCropBorder" Background="#2A1E40" BorderBrush="#B8A0E8"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeCrop" Foreground="#B8A0E8" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeErrorBorder" Background="#3C1212" BorderBrush="#FF7070"
            BorderThickness="1" CornerRadius="4" Padding="10,4"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeErrors" Foreground="#FF7070" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
  </StackPanel>

  <DataGrid Grid.Row="1" x:Name="dgResults"
            AutoGenerateColumns="False" IsReadOnly="True"
            HeadersVisibility="Column" GridLinesVisibility="Horizontal"
            Background="#12131F" RowBackground="#12131F"
            AlternatingRowBackground="#1A1B2E"
            BorderBrush="#2A2D47" BorderThickness="1"
            HorizontalGridLinesBrush="#2A2D47"
            Foreground="#E8EBF5" FontFamily="Segoe UI" FontSize="12"
            ColumnHeaderHeight="32" RowHeight="28"
            SelectionMode="Extended" CanUserResizeRows="False"
            CanUserReorderColumns="False" CanUserSortColumns="True">
    <DataGrid.ColumnHeaderStyle>
      <Style TargetType="DataGridColumnHeader">
        <Setter Property="Background"      Value="#1E2235"/>
        <Setter Property="Foreground"      Value="#9099C8"/>
        <Setter Property="FontFamily"      Value="Segoe UI"/>
        <Setter Property="FontSize"        Value="11"/>
        <Setter Property="Padding"         Value="10,0"/>
        <Setter Property="BorderBrush"     Value="#2A2D47"/>
        <Setter Property="BorderThickness" Value="0,0,1,1"/>
      </Style>
    </DataGrid.ColumnHeaderStyle>
    <DataGrid.CellStyle>
      <Style TargetType="DataGridCell">
        <Setter Property="BorderThickness" Value="0"/>
        <Setter Property="Template">
          <Setter.Value>
            <ControlTemplate TargetType="DataGridCell">
              <Border Background="{TemplateBinding Background}" Padding="10,0">
                <ContentPresenter VerticalAlignment="Center"/>
              </Border>
            </ControlTemplate>
          </Setter.Value>
        </Setter>
        <Style.Triggers>
          <Trigger Property="IsSelected" Value="True">
            <Setter Property="Background" Value="#2A3050"/>
            <Setter Property="Foreground" Value="#E8EBF5"/>
          </Trigger>
        </Style.Triggers>
      </Style>
    </DataGrid.CellStyle>
    <DataGrid.Columns>
      <DataGridTextColumn Header=""           Binding="{Binding Section}" Width="60"/>
      <DataGridTextColumn Header="Status"     Binding="{Binding Status}"  Width="160"/>
      <DataGridTextColumn Header="Master / Sheet" Binding="{Binding Col1}" Width="180"/>
      <DataGridTextColumn Header="View / VP"  Binding="{Binding Col2}"    Width="*"/>
      <DataGridTextColumn Header="Detail"     Binding="{Binding Col3}"    Width="200"/>
    </DataGrid.Columns>
  </DataGrid>
</Grid>
"""

_FOOTER_XAML = u"""
<Grid>
  <StackPanel HorizontalAlignment="Left" Orientation="Horizontal">
    <Button x:Name="btnCopy" Content="Copy to clipboard" Style="{StaticResource BtnGhost}"/>
  </StackPanel>
  <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
    <Button x:Name="btnOK" Content="Close" Style="{StaticResource BtnPrimary}"/>
  </StackPanel>
</Grid>
"""

rows = ObservableCollection[_Row]()
for status, master, name, detail in view_results:
    if status == "master":
        continue
    rows.Add(_Row("VIEW", status, master, name, detail))
for status, sheet, name, detail in sheet_results:
    rows.Add(_Row("SHEET", status, sheet, name, detail))

win = ui.parse(u"Import Sheets with Views", subtitle, _BODY_XAML, _FOOTER_XAML,
               width=1020, height=620)

win.FindName("badgeVCreated").Text = u"✅  {} views created".format(total_v_created)
win.FindName("badgeVUpdated").Text = u"🔄  {} views updated".format(total_v_updated)
win.FindName("badgeVSkipped").Text = u"⏭️  {} views skipped".format(total_v_skipped)
win.FindName("badgeSVP").Text      = u"\U0001f4c4  {} VPs on sheets".format(
    vp_created + vp_updated)

if dl_created:
    win.FindName("badgeLinesBorder").Visibility = Visibility.Visible
    win.FindName("badgeLines").Text = u"📐  {} lines".format(dl_created)

if total_id_stamped:
    win.FindName("badgeIdBorder").Visibility = Visibility.Visible
    win.FindName("badgeIds").Text = u"🔖  {} IDs stamped".format(total_id_stamped)

if total_crop_adjusted:
    win.FindName("badgeCropBorder").Visibility = Visibility.Visible
    win.FindName("badgeCrop").Text = u"✂️  {} crops adjusted (1/8\" + hidden)".format(
        total_crop_adjusted)


if n_errors:
    win.FindName("badgeErrorBorder").Visibility = Visibility.Visible
    win.FindName("badgeErrors").Text = u"❌  {} error{}".format(
        n_errors, u"s" if n_errors != 1 else u"")

win.FindName("dgResults").ItemsSource = rows

def on_copy(s, e):
    lines_out = [u"Import Sheets with Views — " + subtitle, u""]
    for r in rows:
        line = u"{}  {}  |  {}  |  {}".format(
            r.Section, r.Status, r.Col1, r.Col2)
        if r.Col3:
            line += u"  |  " + r.Col3
        lines_out.append(line)
    WinFormsClipboard.SetText(u"\n".join(lines_out))
    s.Content = u"Copied ✓"

win.FindName("btnCopy").Click += on_copy
win.FindName("btnOK").Click   += lambda s, e: win.Close()
win.ShowDialog()
