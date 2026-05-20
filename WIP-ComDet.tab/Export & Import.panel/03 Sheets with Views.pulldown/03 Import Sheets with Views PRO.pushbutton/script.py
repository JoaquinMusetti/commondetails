# -*- coding: utf-8 -*-
__title__ = 'Import\nSheets with Views PRO'
__doc__ = ('Batch version of Import Sheets with Views. Pick the JSON, master '
           'views, strategy and sheet update options ONCE; then choose multiple '
           'open documents and configure each one\'s prefix + link. The importer '
           'runs back-to-back over every selected doc and produces a single '
           'aggregated report.')

import json
import sys
import os as _os
import time
from pyrevit import revit, DB, script, forms, HOST_APP
from Autodesk.Revit.DB import (CurveLoop, Line, ViewDuplicateOption,
                                BuiltInParameterGroup, BuiltInCategory)
from System.Windows import Visibility

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

uiapp  = __revit__                       # noqa: F821
app    = uiapp.Application
output = script.get_output()
output.close()

_SPF_PATH = _os.path.join(_ext_dir, 'lib', 'cucosync_shared_params.txt')

# 1/8" expressed in Revit internal units (feet). 1/8 in ÷ 12 in/ft = 1/96 ft.
ANNOTATION_CROP_OFFSET = 1.0 / 96.0

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — copied verbatim from "02 Import Sheets with Views" so the two tools
# stay aligned. If a helper needs to change, change it in BOTH files.
# ─────────────────────────────────────────────────────────────────────────────

def _patch_spf_varies(spf_path, param_name):
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


def ensure_detail_id_param(d, app_):
    _patch_spf_varies(_SPF_PATH, "Detail ID")
    orig = app_.SharedParametersFilename
    app_.SharedParametersFilename = _SPF_PATH
    try:
        spf = app_.OpenSharedParameterFile()
        if spf is None:
            return False
        grp = spf.Groups.get_Item("CucoSync") or spf.Groups.Create("CucoSync")
        defn = grp.Definitions.get_Item("Detail ID")
        if defn is None:
            opts = DB.ExternalDefinitionCreationOptions("Detail ID", DB.SpecTypeId.String.Text)
            opts.UserModifiable = True
            opts.VariesAcrossGroups = True
            defn = grp.Definitions.Create(opts)
        cat_set = app_.Create.NewCategorySet()
        cat = d.Settings.Categories.get_Item(BuiltInCategory.OST_Views)
        if cat:
            cat_set.Insert(cat)
        binding = app_.Create.NewInstanceBinding(cat_set)
        existing_key = None
        is_instance   = False
        it = d.ParameterBindings.ForwardIterator()
        while it.MoveNext():
            if it.Key.Name == "Detail ID":
                existing_key = it.Key
                is_instance  = isinstance(it.Current, DB.InstanceBinding)
                break
        if existing_key is None:
            d.ParameterBindings.Insert(defn, binding, BuiltInParameterGroup.PG_IDENTITY_DATA)
        elif is_instance:
            d.ParameterBindings.ReInsert(defn, binding, BuiltInParameterGroup.PG_IDENTITY_DATA)
        else:
            d.ParameterBindings.Remove(existing_key)
            d.ParameterBindings.Insert(defn, binding, BuiltInParameterGroup.PG_IDENTITY_DATA)
        return True
    finally:
        app_.SharedParametersFilename = orig


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


def apply_crop_offset_and_hide(view):
    """Pass 3 of the import flow — used by both single-doc and PRO."""
    try:
        if not view.CropBoxActive:
            return False
        rm = view.GetCropRegionShapeManager()
        try:
            rm.TopAnnotationCropOffset    = ANNOTATION_CROP_OFFSET
            rm.BottomAnnotationCropOffset = ANNOTATION_CROP_OFFSET
            rm.LeftAnnotationCropOffset   = ANNOTATION_CROP_OFFSET
            rm.RightAnnotationCropOffset  = ANNOTATION_CROP_OFFSET
        except Exception:
            rm.SetAnnotationCropOffset(
                ANNOTATION_CROP_OFFSET, ANNOTATION_CROP_OFFSET,
                ANNOTATION_CROP_OFFSET, ANNOTATION_CROP_OFFSET)
        if view.CropBoxVisible:
            view.CropBoxVisible = False
        return True
    except Exception:
        return False


# ═════════════════════════════════════════════════════════════════════════════
# SHARED INPUTS — collected ONCE for every doc in the batch
# ═════════════════════════════════════════════════════════════════════════════

# 1. JSON
json_path = forms.pick_file(
    file_ext="json",
    title="1 of 7 — Select Sheets with Views JSON"
)
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    data_master = json.load(f)

if data_master.get("format") != "sheets_with_views":
    ui.alert(
        u"Wrong file selected.\n\n"
        u"Import Sheets with Views PRO expects a file exported by "
        u"\"Export Sheets with Views\" (format: sheets_with_views).\n\n"
        u"The selected file has format: \"{}\".".format(
            data_master.get("format", "unknown")),
        title=u"Import Sheets with Views PRO"
    )
    script.exit()

# 2. Strategy (existing dependents)
strategy = ui.pick_list(
    [
        "Skip existing — do not touch dependent views that already exist",
        "Update existing — re-apply crop boundary to views that already exist",
    ],
    "2 of 7 — Strategy for Existing Dependent Views",
    button_name="Next",
    multiselect=False,
    context=u"PRO applies the same strategy to every selected document. "
            u"'Skip' leaves existing dependents untouched. 'Update' overwrites "
            u"the crop geometry with what's in the JSON. Detail ID and name are "
            u"preserved in both cases."
)
if not strategy:
    script.exit()
update_existing = "Update" in strategy

# 3. Sheet update options
sheet_options_list = [
    "VIEWPORTS | Position & Title location",
    "VIEWPORTS | Match viewport types",
    "VIEWPORTS | Detail number",
    "VIEWPORTS | Title on sheet",
    "SHEET ELEMENTS | Detail lines  (delete existing and redraw)",
]
chosen_sheet_opts = ui.pick_list(
    sheet_options_list,
    "3 of 7 — Sheet Update Options",
    button_name="Next",
    context=u"If sheets already exist in a destination doc: pick what to overwrite. "
            u"Same setting is applied to every doc in the batch."
)
if chosen_sheet_opts is None:
    script.exit()

DO_POSITION   = any("Position & Title location" in o for o in chosen_sheet_opts)
DO_VP_TYPE    = any("Match viewport types"      in o for o in chosen_sheet_opts)
DO_DET_NUMBER = any("Detail number"             in o for o in chosen_sheet_opts)
DO_TITLE      = any("Title on sheet"            in o for o in chosen_sheet_opts)
DO_LINES      = any("Detail lines"              in o for o in chosen_sheet_opts)

# 4. Pick destination docs (from open Revit docs)
all_open_docs = []
for d_ in app.Documents:
    try:
        if not d_.IsFamilyDocument and d_.Title:
            all_open_docs.append(d_)
    except Exception:
        pass

if not all_open_docs:
    ui.alert("No project documents are open.\n\n"
             "PRO works on docs already loaded in this Revit session — "
             "open the destination buildings first, then re-run.",
             title="Import Sheets with Views PRO")
    script.exit()

doc_labels = sorted([d_.Title for d_ in all_open_docs])
chosen_doc_labels = ui.pick_list(
    doc_labels,
    "4 of 7 — Select Destination Documents",
    button_name="Next",
    context=u"Pick the open Revit docs you want to import into. The tool does "
            u"NOT open files — they must already be loaded in this session. For "
            u"each picked doc you'll be asked separately for its 2-letter prefix "
            u"and the Common Details link inside that doc."
)
if not chosen_doc_labels:
    script.exit()

doc_by_title = {d_.Title: d_ for d_ in all_open_docs}
chosen_docs = [doc_by_title[lbl] for lbl in chosen_doc_labels if lbl in doc_by_title]

# 5. Per-doc: prefix + link
NONE_OPTION = "None (import into Common Details itself)"

doc_configs = []   # list of (target_doc, prefix, link_name_or_None)

step = 5
for target_doc in chosen_docs:
    prefix = ui.ask_for_string(
        prompt="Prefix for:\n\n  {}\n\n(e.g. AE, AB, AC, AS for Site, CD for Common Details)".format(
            target_doc.Title),
        title="{} of 7 — Prefix — {}".format(step, target_doc.Title),
        context=u"2-letter prefix used to rewrite this doc's sheet names. The JSON's "
                u"'CD_XXXX_...' sheet names will be renamed to '<prefix>_XXXX_...' "
                u"in this destination."
    )
    if not prefix:
        script.exit()
    prefix = prefix.strip().upper()

    link_instances = DB.FilteredElementCollector(target_doc)\
        .OfClass(DB.RevitLinkInstance).ToElements()
    link_by_name = {li.Name: li for li in link_instances}
    link_options = [NONE_OPTION] + sorted(link_by_name.keys())

    link_choice = ui.pick_list(
        link_options,
        "{} of 7 — Link — {}".format(step, target_doc.Title),
        button_name="Next",
        multiselect=False,
        context=u"Pick the Common Details link inside this doc — its transform converts "
                u"JSON coordinates into the building's space. Pick 'None' only if this "
                u"doc IS Common Details (you're seeding/updating CD from a building JSON)."
    )
    if not link_choice:
        script.exit()

    doc_configs.append((target_doc, prefix, link_choice if link_choice != NONE_OPTION else None))
    step = min(step + 1, 6)   # cap label at "6 of 7" for the last per-doc step

# 6. Confirmation
total_masters_in_json = len(data_master.get("master_views", []))
total_sheets_in_json  = len(data_master.get("sheets", []))
summary_lines = []
for td, pfx, ln in doc_configs:
    summary_lines.append(u"  • {}  →  prefix '{}'  via link '{}'".format(
        td.Title, pfx, ln if ln else "None (into CD)"))

confirm_ok = ui.confirm(
    u"Ready to run Import Sheets with Views PRO:\n\n"
    u"JSON masters: {}\n"
    u"JSON sheets:  {}\n\n"
    u"Destinations ({}):\n{}\n\n"
    u"Strategy for existing dependents: {}\n"
    u"This will run back-to-back on every doc without further prompts.".format(
        total_masters_in_json, total_sheets_in_json,
        len(doc_configs), u"\n".join(summary_lines),
        "Update" if update_existing else "Skip"),
    title="7 of 7 — Confirm PRO run",
    yes_text="Run on all {}".format(len(doc_configs)),
    context=u"Once you click Run, the import iterates each doc in sequence with no "
            u"pauses. Each doc is wrapped in its own transactions so a failure in "
            u"doc B does not roll back doc A."
)
if not confirm_ok:
    script.exit()


# ═════════════════════════════════════════════════════════════════════════════
# import_one_doc — runs Phase A (views) + Phase B (sheets) on `target_doc`
# Returns a dict with per-doc stats. Does NOT raise on partial errors.
# ═════════════════════════════════════════════════════════════════════════════

def import_one_doc(target_doc, dest_prefix, link_instance_name, data_in, pb, total_views, total_sheets, base_progress):
    """Run import on one doc. Returns stats dict + appends to pb progress."""

    res = {
        "title":         target_doc.Title,
        "prefix":        dest_prefix,
        "link":          link_instance_name or "None",
        "v_created":     0,
        "v_updated":     0,
        "v_skipped":     0,
        "v_orphan":      0,
        "ids_stamped":   0,
        "crop_adjusted": 0,
        "vp_created":    0,
        "vp_updated":    0,
        "dl_created":    0,
        "dl_deleted":    0,
        "errors":        0,
        "warnings":      0,
        "cancelled":     False,
        "skipped_reason": None,
    }

    # Resolve link transform
    if link_instance_name is None:
        link_transform = None
    else:
        link_instances = DB.FilteredElementCollector(target_doc)\
            .OfClass(DB.RevitLinkInstance).ToElements()
        link_by_name_local = {li.Name: li for li in link_instances}
        if link_instance_name not in link_by_name_local:
            res["skipped_reason"] = u"Link '{}' not found".format(link_instance_name)
            res["errors"] += 1
            return res
        link_transform = link_by_name_local[link_instance_name].GetTotalTransform()

    # Deep copy the data so per-doc master_map remap doesn't mutate the shared dict
    data = json.loads(json.dumps(data_in))

    # Apply master_map.json (located next to the JSON we picked once)
    map_path = _os.path.join(_os.path.dirname(json_path), "master_map.json")
    if _os.path.isfile(map_path):
        try:
            with open(map_path, "r") as f:
                raw_map = json.load(f)
            if link_transform is None:
                master_map = {v: k for k, v in raw_map.items()}
            else:
                master_map = raw_map
            for mv in data["master_views"]:
                if mv["view_name"] in master_map:
                    mv["view_name"] = master_map[mv["view_name"]]
        except Exception:
            pass

    # Index destination resources
    all_views_dest = DB.FilteredElementCollector(target_doc).OfClass(DB.View).ToElements()
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

    all_sheets_dest = DB.FilteredElementCollector(target_doc).OfClass(DB.ViewSheet).ToElements()
    sheet_by_suffix = {}
    for s in all_sheets_dest:
        suffix = s.SheetNumber[2:] if len(s.SheetNumber) > 2 else s.SheetNumber
        sheet_by_suffix[suffix] = s

    vp_type_by_name = {}
    for t in DB.FilteredElementCollector(target_doc).OfClass(DB.ElementType).ToElements():
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

    existing_viewports_coll = DB.FilteredElementCollector(target_doc).OfClass(DB.Viewport).ToElements()
    viewport_by_view_id = {vp.ViewId.IntegerValue: vp for vp in existing_viewports_coll}

    existing_dls_by_sheet = {}
    for dl in DB.FilteredElementCollector(target_doc).OfClass(DB.CurveElement).ToElements():
        try:
            if not isinstance(dl, DB.DetailLine):
                continue
            owner_id = dl.OwnerViewId.IntegerValue
            existing_dls_by_sheet.setdefault(owner_id, []).append(dl)
        except Exception:
            pass

    line_style_by_name = {}
    try:
        detail_cat = target_doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
        for sub in detail_cat.SubCategories:
            gs = sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
            if gs:
                line_style_by_name[sub.Name] = gs
    except Exception:
        pass

    # Ensure Detail ID parameter
    t0 = DB.Transaction(target_doc, "Add Detail ID parameter")
    try:
        t0.Start()
        ensure_detail_id_param(target_doc, HOST_APP.app)
        t0.Commit()
    except Exception:
        try:
            if t0.HasStarted() and not t0.HasEnded():
                t0.RollBack()
        except Exception:
            pass

    sheets_data = data.get("sheets", [])
    processed_local = 0

    # ── Phase A: Views ──
    tA = DB.Transaction(target_doc, "Import Sheets with Views PRO — Views ({})".format(target_doc.Title))
    try:
        tA.Start()
        for view_data in data["master_views"]:
            view_name = view_data["view_name"]
            if view_name not in view_by_name:
                continue
            master_view = view_by_name[view_name]

            existing_by_id   = {}
            existing_by_name = {}
            for vid in master_view.GetDependentViewIds():
                dep = target_doc.GetElement(vid)
                if dep:
                    existing_by_name[dep.Name] = dep
                    did = get_detail_id(dep)
                    if did:
                        existing_by_id[did] = dep

            pending_crops = []
            for dv_data in view_data["dependent_views"]:
                if pb.cancelled:
                    res["cancelled"] = True
                    break

                dv_name        = dv_data["view_name"]
                dv_id          = dv_data.get("detail_id", "")
                title_on_sheet = dv_data.get("title_on_sheet", "")
                template_name  = dv_data.get("view_template", "")
                scale          = dv_data.get("view_scale", master_view.Scale)
                corners_world  = [link_to_world(c, link_transform)
                                  for c in dv_data["crop_corners"]]

                existing_view = existing_by_id.get(dv_id) if dv_id else None
                if existing_view is None:
                    existing_view = existing_by_name.get(dv_name)

                processed_local += 1
                pb.title = u"{} — view {}/{} — {}".format(
                    target_doc.Title, processed_local, total_views, dv_name)
                pb.update_progress(base_progress + processed_local,
                                   base_progress + total_views + total_sheets)

                if existing_view is not None:
                    if dv_id and not get_detail_id(existing_view):
                        set_detail_id(existing_view, dv_id)
                        res["ids_stamped"] += 1
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
                            res["v_updated"] += 1
                        except Exception:
                            res["errors"] += 1
                    else:
                        res["v_skipped"] += 1
                    continue

                # Create new
                try:
                    new_vid  = master_view.Duplicate(ViewDuplicateOption.AsDependent)
                    new_view = target_doc.GetElement(new_vid)

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
                    res["v_created"] += 1
                    if dv_data.get("placed_on_sheet", True) is False:
                        res["v_orphan"] += 1
                    existing_proj_names.add(final_name)
                    dep_view_by_name[final_name] = new_view
                except Exception:
                    res["errors"] += 1

            # Pass 2: SetCropShape on all created views
            for new_view, crop_loop in pending_crops:
                try:
                    new_view.CropBoxActive  = True
                    new_view.CropBoxVisible = True
                    new_view.GetCropRegionShapeManager().SetCropShape(crop_loop)
                except Exception:
                    res["errors"] += 1

            # Pass 3: AnnotationCropOffset 1/8" + hide
            for new_view, _ in pending_crops:
                if apply_crop_offset_and_hide(new_view):
                    res["crop_adjusted"] += 1

            if res["cancelled"]:
                break
        tA.Commit()
    except Exception:
        try:
            if tA.HasStarted() and not tA.HasEnded():
                tA.RollBack()
        except Exception:
            pass
        res["errors"] += 1
        return res

    # ── Phase B: Sheets (per-sheet T1+T2) ──
    for i, sheet_data in enumerate(sheets_data):
        if pb.cancelled:
            res["cancelled"] = True
            break

        sheet_number = sheet_data["sheet_number"]
        suffix       = sheet_number[2:] if len(sheet_number) > 2 else sheet_number
        sheet_label  = u"{}{}".format(dest_prefix, suffix)

        pb.title = u"{} — sheet {}/{} — {}".format(
            target_doc.Title, i + 1, total_sheets, sheet_label)
        pb.update_progress(base_progress + total_views + i + 1,
                           base_progress + total_views + total_sheets)

        if suffix not in sheet_by_suffix:
            res["warnings"] += 1
            continue

        target_sheet     = sheet_by_suffix[suffix]
        vp_id_to_det_num = {}

        # T1: viewports + lines
        t1 = DB.Transaction(target_doc, "Import PRO T1 — {} / {}".format(
            target_doc.Title, sheet_label))
        try:
            t1.Start()
            all_sheet_vps_t1 = list(DB.FilteredElementCollector(target_doc, target_sheet.Id)
                                    .OfClass(DB.Viewport).ToElements())
            original_numbers = {sv.Id.IntegerValue: get_detail_number(sv)
                                 for sv in all_sheet_vps_t1}
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
                    res["warnings"] += 1
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
                            res["vp_updated"] += 1
                        else:
                            res["warnings"] += 1
                    else:
                        vp = DB.Viewport.Create(target_doc, target_sheet.Id, target_view.Id, center)
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
                        res["vp_created"] += 1
                except Exception:
                    res["errors"] += 1

            for sv in all_sheet_vps_t1:
                if sv.Id.IntegerValue not in processed_vp_ids:
                    orig = original_numbers.get(sv.Id.IntegerValue, "")
                    if orig:
                        set_detail_number(sv, orig)

            if DO_LINES:
                dl_data = sheet_data.get("detail_lines", [])
                if dl_data:
                    sheet_id     = target_sheet.Id.IntegerValue
                    existing_dls = existing_dls_by_sheet.get(sheet_id, [])
                    deleted = 0
                    for dl in existing_dls:
                        try:
                            target_doc.Delete(dl.Id)
                            deleted += 1
                        except Exception:
                            pass
                    res["dl_deleted"] += deleted
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
                            new_dl = target_doc.Create.NewDetailCurve(target_sheet, line)
                            style  = get_line_style(dl_entry.get("line_style", ""),
                                                    line_style_by_name)
                            if style:
                                new_dl.LineStyle = style
                            created += 1
                        except Exception:
                            res["errors"] += 1
                    res["dl_created"] += created
            t1.Commit()
        except Exception:
            try:
                if t1.HasStarted() and not t1.HasEnded():
                    t1.RollBack()
            except Exception:
                pass
            res["errors"] += 1
            continue

        # T2: assign final detail numbers
        if not DO_DET_NUMBER or not vp_id_to_det_num:
            continue
        t2 = DB.Transaction(target_doc, "Import PRO T2 — {} / {}".format(
            target_doc.Title, sheet_label))
        try:
            t2.Start()
            all_sheet_vps_t2 = list(DB.FilteredElementCollector(target_doc, target_sheet.Id)
                                    .OfClass(DB.Viewport).ToElements())
            ts2 = str(int(time.time())) + "b"
            for idx, sv in enumerate(all_sheet_vps_t2):
                set_detail_number(sv, "zzz{}_{}".format(ts2, idx))
            for sv in all_sheet_vps_t2:
                det_num = vp_id_to_det_num.get(sv.Id.IntegerValue)
                if det_num:
                    set_detail_number(sv, det_num)
            t2.Commit()
        except Exception:
            try:
                if t2.HasStarted() and not t2.HasEnded():
                    t2.RollBack()
            except Exception:
                pass
            res["errors"] += 1

    return res


# ═════════════════════════════════════════════════════════════════════════════
# BATCH RUN
# ═════════════════════════════════════════════════════════════════════════════

all_results = []
total_views_per_doc  = sum(len(mv["dependent_views"]) for mv in data_master.get("master_views", []))
total_sheets_per_doc = len(data_master.get("sheets", []))
batch_total = (total_views_per_doc + total_sheets_per_doc) * len(doc_configs)

with ui.ProgressBar(title=u"Import Sheets with Views PRO", cancellable=True, step=5) as pb:
    pb.update_progress(0, batch_total)
    base_progress = 0
    for target_doc, dest_prefix, link_name in doc_configs:
        if pb.cancelled:
            break
        pb.title = u"PRO — starting {}…".format(target_doc.Title)
        r = import_one_doc(target_doc, dest_prefix, link_name, data_master,
                           pb, total_views_per_doc, total_sheets_per_doc, base_progress)
        all_results.append(r)
        base_progress += total_views_per_doc + total_sheets_per_doc
        if r.get("cancelled"):
            break


# ═════════════════════════════════════════════════════════════════════════════
# FINAL AGGREGATED MODAL
# ═════════════════════════════════════════════════════════════════════════════

agg = {
    "v_created":     sum(r["v_created"]     for r in all_results),
    "v_updated":     sum(r["v_updated"]     for r in all_results),
    "v_skipped":     sum(r["v_skipped"]     for r in all_results),
    "v_orphan":      sum(r["v_orphan"]      for r in all_results),
    "crop_adjusted": sum(r["crop_adjusted"] for r in all_results),
    "ids_stamped":   sum(r["ids_stamped"]   for r in all_results),
    "vp_created":    sum(r["vp_created"]    for r in all_results),
    "vp_updated":    sum(r["vp_updated"]    for r in all_results),
    "dl_created":    sum(r["dl_created"]    for r in all_results),
    "errors":        sum(r["errors"]        for r in all_results),
    "warnings":      sum(r["warnings"]      for r in all_results),
}

# Build a plain-text summary the user can copy
lines_out = [
    u"Import Sheets with Views PRO — Aggregated Report",
    u"=" * 60,
    u"JSON: {}".format(json_path),
    u"Docs: {}   Strategy: {}".format(
        len(doc_configs), "Update" if update_existing else "Skip"),
    u"",
    u"Aggregated totals:",
    u"  ✅ {} views created    🔄 {} updated    ⏭ {} skipped".format(
        agg["v_created"], agg["v_updated"], agg["v_skipped"]),
    u"  🌒 {} orphans (unplaced)    ✂️ {} crops adjusted    🔖 {} IDs stamped".format(
        agg["v_orphan"], agg["crop_adjusted"], agg["ids_stamped"]),
    u"  📄 {} viewports placed    🔄 {} viewports updated    📐 {} detail lines drawn".format(
        agg["vp_created"], agg["vp_updated"], agg["dl_created"]),
    u"  ❌ {} errors    ⚠ {} warnings".format(agg["errors"], agg["warnings"]),
    u"",
    u"Per-document:",
]
for r in all_results:
    lines_out.append(u"")
    lines_out.append(u"  • {}   [prefix: {}   link: {}]".format(
        r["title"], r["prefix"], r["link"]))
    if r.get("skipped_reason"):
        lines_out.append(u"      ⚠ SKIPPED: {}".format(r["skipped_reason"]))
        continue
    lines_out.append(
        u"      views   created={}  updated={}  skipped={}  orphans={}  crops_adj={}  ids={}".format(
            r["v_created"], r["v_updated"], r["v_skipped"],
            r["v_orphan"], r["crop_adjusted"], r["ids_stamped"]))
    lines_out.append(
        u"      sheets  vp_created={}  vp_updated={}  dl_created={}  errors={}  warnings={}".format(
            r["vp_created"], r["vp_updated"], r["dl_created"],
            r["errors"], r["warnings"]))
    if r.get("cancelled"):
        lines_out.append(u"      ⚠ Cancelled mid-doc — partial work committed.")

summary = u"📦 {} docs   ✅ {}  🔄 {}  ❌ {}".format(
    len(doc_configs), agg["v_created"] + agg["vp_created"],
    agg["v_updated"] + agg["vp_updated"], agg["errors"])

ui.show_report(
    u"\n".join(lines_out),
    title=u"Import Sheets with Views PRO — Results",
    subtitle=u"{} docs · JSON: {}".format(
        len(doc_configs), _os.path.basename(json_path)),
    summary=summary,
    width=820, height=620,
    context=u"One row per destination doc. Aggregated totals at the top. Errors "
            u"in one doc do NOT roll back another — each doc has its own "
            u"transactions."
)
