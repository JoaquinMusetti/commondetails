# -*- coding: utf-8 -*-
__title__ = 'Full Sync\nPRO'
__doc__ = ('Runs Full Sync across multiple open Revit documents in one go. '
           'JSONs, linked model, and master views are selected once; '
           'only the prefix changes per building. '
           'Phases: clean sheets → clean views → import views → adjust views → import sheet layout.')

import json
import sys
import os as _os
import time
import System
from pyrevit import revit, DB, script, forms
from Autodesk.Revit.DB import CurveLoop, Line

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

uiapp  = __revit__                       # noqa: F821
app    = uiapp.Application
output = script.get_output()

# ─────────────────────────────────────────────────────────────────────────────
# 1. COLLECT SHARED INPUTS
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("# ⛓ Full Sync PRO — Multi-Document")
output.print_md("Collecting shared inputs…")

details_json_path = forms.pick_file(
    file_ext="json",
    title="1 — Select Dependent Views JSON (YYYYMMDD_Dependent views.json)"
)
if not details_json_path:
    script.exit()

sheets_json_path = forms.pick_file(
    file_ext="json",
    title="2 — Select Sheet Layout JSON (YYYYMMDD_Sheet layout.json)"
)
if not sheets_json_path:
    script.exit()

with open(details_json_path, "r") as f:
    views_data = json.load(f)
with open(sheets_json_path, "r") as f:
    layout = json.load(f)

# ── Select master views (from JSON) ──
master_options = sorted([mv["view_name"] for mv in views_data["master_views"]])
chosen_masters = ui.pick_list(
    master_options,
    "3 - Select Master Views",
    button_name="Next",
    context=u"Full Sync PRO runs Full Sync on multiple buildings in one go. You pick "
            u"the JSONs ONCE, then pick the destination docs (must be open in this "
            u"Revit session) and configure their prefix + link. The masters you tick "
            u"here apply to all docs equally."
)
if not chosen_masters:
    script.exit()
chosen_masters_set = set(chosen_masters)

# ── Build JSON lookups (shared) ──
json_view_info = {}
for mv in views_data["master_views"]:
    if mv["view_name"] in chosen_masters_set:
        for dv in mv["dependent_views"]:
            json_view_info[dv["view_name"]] = dv
json_view_names = set(json_view_info.keys())

views_in_scope = sum(
    len(mv["dependent_views"])
    for mv in views_data["master_views"]
    if mv["view_name"] in chosen_masters_set
)

# ─────────────────────────────────────────────────────────────────────────────
# 2. SELECT DOCUMENTS & PREFIXES
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("Listing open documents…")

all_open_docs = []
for d in app.Documents:
    try:
        if not d.IsFamilyDocument and d.Title:
            all_open_docs.append(d)
    except Exception:
        pass

if not all_open_docs:
    ui.alert("No project documents open.", title="Full Sync PRO")
    script.exit()

doc_labels = sorted([d.Title for d in all_open_docs])
chosen_doc_labels = ui.pick_list(
    doc_labels,
    "4 - Select Documents to Sync",
    button_name="Next",
    context=u"Pick the open Revit docs you want to sync. The tool does NOT open "
            u"files — they must be loaded in this session already. For each picked "
            u"doc you'll be asked separately for its 2-letter prefix and its "
            u"Common Details link."
)
if not chosen_doc_labels:
    script.exit()

# Map label → doc
doc_by_title = {d.Title: d for d in all_open_docs}
chosen_docs = [doc_by_title[lbl] for lbl in chosen_doc_labels if lbl in doc_by_title]

# Collect prefix per document
doc_configs = []   # list of (doc, prefix, link_name)

for target_doc in chosen_docs:
    prefix = ui.ask_for_string(
        prompt="Enter the 2-letter prefix for:\n\n  {}\n\n(e.g. AE, AB, AC...)".format(
            target_doc.Title),
        title="Prefix - {}".format(target_doc.Title),
        context=u"2-letter prefix used to rewrite sheet names for this specific "
                u"building (AE/AB/AC/AD/AF/AG/AK/AS). Asked once per selected doc."
    )
    if not prefix:
        script.exit()
    prefix = prefix.strip().upper()

    # Find linked model in this document
    link_instances = DB.FilteredElementCollector(target_doc)\
        .OfClass(DB.RevitLinkInstance).ToElements()
    if not link_instances:
        ui.alert("No linked models in '{}'. Skipping.".format(target_doc.Title),
                 title="Full Sync PRO")
        continue

    link_by_name = {li.Name: li for li in link_instances}
    link_name = ui.pick_list(
        sorted(link_by_name.keys()),
        "Linked Model - {}".format(target_doc.Title),
        button_name="Select",
        multiselect=False,
        context=u"Pick the Common Details link inside this specific document. "
                u"The link name may not be identical across buildings — that's why "
                u"this picker is per-doc. Its transform converts JSON coordinates "
                u"into the building's space."
    )
    if not link_name:
        script.exit()

    doc_configs.append((target_doc, prefix, link_name))

if not doc_configs:
    ui.alert("No documents configured. Nothing to do.", title="Full Sync PRO")
    script.exit()

# ── Confirmation ──
summary_lines = []
for target_doc, prefix, link_name in doc_configs:
    summary_lines.append("  * {} -> prefix: {} -> link: {}".format(
        target_doc.Title, prefix, link_name))

confirm = ui.confirm(
    "Ready to run Full Sync PRO:\n\n"
    "Documents ({}):\n{}\n\n"
    "  * {} sheets from JSON\n"
    "  * {} dependent views in scope\n"
    "  * {} master views\n\n"
    "Per document this will:\n"
    "  0. Unload the linked model\n"
    "  1. Remove viewports not in JSON from sheets\n"
    "  2. Delete orphaned / mismatched views\n"
    "  3. Create / update dependent views\n"
    "  4. Adjust annotation crop offset + hide crop region\n"
    "  5. Place viewports with positions and detail numbers\n"
    "  6. Ask whether to reload links at the end\n\n"
    "Continue?".format(
        len(doc_configs),
        "\n".join(summary_lines),
        len(layout),
        views_in_scope,
        len(chosen_masters)),
    title="Full Sync PRO - Confirm",
    yes_text="Run Full Sync PRO",
    width=640, height=500
)
if not confirm:
    script.exit()


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS  (all take `doc` as first arg to work with any open document)
# ─────────────────────────────────────────────────────────────────────────────

def make_crop_loop(corners_world):
    loop = CurveLoop()
    for k in range(4):
        loop.Append(Line.CreateBound(
            corners_world[k],
            corners_world[(k + 1) % 4]
        ))
    return loop

def apply_crop_shape(view, corners_link, link_xform):
    try:
        corners_world = [link_xform.OfPoint(DB.XYZ(c[0], c[1], c[2]))
                         for c in corners_link]
        crop_loop = make_crop_loop(corners_world)
        view.CropBoxActive = True
        view.GetCropRegionShapeManager().SetCropShape(crop_loop)
        view.CropBoxVisible = False
        return True
    except Exception as e:
        return str(e)

def get_view_name_from_vp(d, vp):
    try:
        v = d.GetElement(vp.ViewId)
        return v.Name if v else ""
    except Exception:
        return ""

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
        return p.AsString() or "" if p else ""
    except Exception:
        return ""

def set_title_on_sheet(view, title):
    try:
        p = view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
        if p and not p.IsReadOnly:
            p.Set(title or "")
    except Exception:
        pass

def set_annotation_crop_offset(rm, offset):
    try:
        rm.TopAnnotationCropOffset    = offset
        rm.BottomAnnotationCropOffset = offset
        rm.LeftAnnotationCropOffset   = offset
        rm.RightAnnotationCropOffset  = offset
        return True
    except Exception:
        pass
    try:
        flags = (System.Reflection.BindingFlags.Public |
                 System.Reflection.BindingFlags.Instance)
        props = {p.Name: p for p in rm.GetType().GetProperties(flags)}
        for name in ("TopAnnotationCropOffset", "BottomAnnotationCropOffset",
                     "LeftAnnotationCropOffset", "RightAnnotationCropOffset"):
            if name in props:
                props[name].SetValue(rm, offset, None)
        return True
    except Exception:
        pass
    try:
        rm.SetAnnotationCropOffset(offset, offset, offset, offset)
        return True
    except Exception:
        pass
    return False

ANNOT_OFFSET = (1.0 / 8.0) / 12.0


# ═══════════════════════════════════════════════════════════════════════════════
# run_sync — execute the 5-phase pipeline on a single document
# ═══════════════════════════════════════════════════════════════════════════════

def run_sync(d, dest_prefix, link_name):
    """Run full sync on document `d`. Returns a results dict."""

    res = {
        "title": d.Title, "prefix": dest_prefix,
        "p1_removed": 0, "p1_details": [],
        "p2_deleted": 0, "p2_views_deleted": [],
        "p3_created": 0, "p3_updated": 0,
        "p4_adjusted": 0, "p4_adj_skipped": 0,
        "p5_created": 0, "p5_updated": 0, "p5_dl_created": 0,
        "p5_sheet_stats": {},
        "errors": [], "skipped": [],
        "link_unloaded": False, "link_was_already_off": False, "link_reloaded": False,
    }

    # ── Resolve link ──
    link_instances = DB.FilteredElementCollector(d)\
        .OfClass(DB.RevitLinkInstance).ToElements()
    link_by_name = {li.Name: li for li in link_instances}
    if link_name not in link_by_name:
        res["errors"].append("Linked model '{}' not found".format(link_name))
        return res

    link_instance  = link_by_name[link_name]
    link_xform     = link_instance.GetTotalTransform()
    link_type      = d.GetElement(link_instance.GetTypeId())

    # ── PRE-SYNC: Unload link ──
    output.print_md("### Pre-sync — Unload Link")
    if not DB.RevitLinkType.IsLoaded(d, link_type.Id):
        res["link_unloaded"]        = True
        res["link_was_already_off"] = True
        output.print_md("ℹ️ *{}* already unloaded".format(link_name))
    else:
        try:
            try:
                link_type.UnloadLocally(None)
            except AttributeError:
                link_type.Unload(None)
            res["link_unloaded"] = True
            output.print_md("✅ *{}* unloaded".format(link_name))
        except Exception as e:
            res["errors"].append("Could not unload link: {}".format(e))
            output.print_md("❌ Could not unload link: {}".format(e))
            return res

    # ── INDEX MODEL ──
    output.print_md("### Indexing model…")

    all_views_raw = DB.FilteredElementCollector(d).OfClass(DB.View).ToElements()

    dep_view_by_name    = {}
    master_view_by_name = {}
    template_by_name    = {}
    for v in all_views_raw:
        try:
            if v.IsTemplate:
                template_by_name[v.Name] = v
                continue
            pid = v.GetPrimaryViewId()
            if pid != DB.ElementId.InvalidElementId:
                dep_view_by_name[v.Name] = v
            else:
                master_view_by_name[v.Name] = v
        except Exception:
            pass

    all_sheets = DB.FilteredElementCollector(d).OfClass(DB.ViewSheet).ToElements()
    sheet_by_suffix = {}
    for s in all_sheets:
        suffix = s.SheetNumber[2:] if len(s.SheetNumber) > 2 else s.SheetNumber
        sheet_by_suffix[suffix] = s

    all_vps = list(DB.FilteredElementCollector(d).OfClass(DB.Viewport).ToElements())
    viewport_by_view_id = {vp.ViewId.IntegerValue: vp for vp in all_vps}
    viewports_by_sheet  = {}
    for vp in all_vps:
        viewports_by_sheet.setdefault(vp.SheetId.IntegerValue, []).append(vp)

    vp_type_by_name = {}
    for t in DB.FilteredElementCollector(d).OfClass(DB.ElementType).ToElements():
        try:
            p = t.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
            if p:
                name = p.AsString()
                if name:
                    fp = t.get_Parameter(DB.BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
                    if fp and fp.AsString() == "Viewport":
                        vp_type_by_name[name] = t.Id
        except Exception:
            pass

    line_style_by_name = {}
    try:
        detail_cat = d.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
        for sub in detail_cat.SubCategories:
            gs = sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
            if gs:
                line_style_by_name[sub.Name] = gs
    except Exception:
        pass

    def get_line_style(name):
        if name and name in line_style_by_name:
            return line_style_by_name[name]
        if line_style_by_name:
            return list(line_style_by_name.values())[0]
        return None

    output.print_md("Dep views: **{}** · Masters: **{}** · Sheets: **{}** · VP types: **{}**".format(
        len(dep_view_by_name), len(master_view_by_name),
        len(sheet_by_suffix), len(vp_type_by_name)))

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1 — CLEAN SHEETS
    # ══════════════════════════════════════════════════════════════════════
    output.print_md("### Phase 1 — Clean Sheets")

    orphaned_vp_ids = []
    for sheet_data in layout:
        sheet_number = sheet_data["sheet_number"]
        suffix       = sheet_number[2:] if len(sheet_number) > 2 else sheet_number
        if suffix not in sheet_by_suffix:
            continue
        target_sheet  = sheet_by_suffix[suffix]
        json_vp_names = set(e["view_name"] for e in sheet_data["viewports"])
        sheet_vps     = viewports_by_sheet.get(target_sheet.Id.IntegerValue, [])

        for vp in sheet_vps:
            vname = get_view_name_from_vp(d, vp)
            if not vname or vname in json_vp_names:
                continue
            try:
                v = d.GetElement(vp.ViewId)
                if v and v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
                    orphaned_vp_ids.append(vp.Id)
                    res["p1_details"].append(("{}{}".format(dest_prefix, suffix), vname))
            except Exception:
                pass

    if orphaned_vp_ids:
        t = DB.Transaction(d, "Full Sync PRO — Clean Sheets")
        t.Start()
        for vp_id in orphaned_vp_ids:
            try:
                d.Delete(vp_id)
                res["p1_removed"] += 1
            except Exception as e:
                res["errors"].append("VP remove: {}".format(e))
        t.Commit()
        output.print_md("✅ Removed **{}** orphaned viewport(s)".format(res["p1_removed"]))
    else:
        output.print_md("✅ No orphaned viewports")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2 — CLEAN VIEWS
    # ══════════════════════════════════════════════════════════════════════
    output.print_md("### Phase 2 — Clean Views")

    views_to_delete = []
    for v in DB.FilteredElementCollector(d).OfClass(DB.View).ToElements():
        try:
            pid = v.GetPrimaryViewId()
            if pid == DB.ElementId.InvalidElementId:
                continue
            primary = d.GetElement(pid)
            if not primary or primary.Name not in chosen_masters_set:
                continue
        except Exception:
            continue

        reason = None
        if v.Name not in json_view_names:
            # Safety guard: only delete if not placed on any sheet.
            # Views placed on sheets are treated as custom/intentional — do not remove.
            if v.Id.IntegerValue not in viewport_by_view_id:
                reason = "not in JSON and not placed on any sheet"
        else:
            dv_info    = json_view_info.get(v.Name, {})
            json_scale = dv_info.get("view_scale")
            json_type  = dv_info.get("view_type", "")
            if json_scale is not None:
                try:
                    if v.Scale != int(json_scale):
                        reason = "scale mismatch"
                except Exception:
                    pass
            if reason is None and json_type:
                if str(v.ViewType) != json_type:
                    reason = "type mismatch"

        if reason:
            views_to_delete.append((v.Name, v.Id, reason))

    if views_to_delete:
        t = DB.Transaction(d, "Full Sync PRO — Clean Views")
        t.Start()
        for vname, vid, reason in views_to_delete:
            try:
                d.Delete(vid)
                res["p2_deleted"] += 1
                res["p2_views_deleted"].append((vname, reason))
            except Exception as e:
                res["errors"].append("Delete '{}': {}".format(vname, e))
        t.Commit()
        output.print_md("✅ Deleted **{}** view(s)".format(res["p2_deleted"]))
    else:
        output.print_md("✅ No views to clean")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3 — IMPORT VIEWS
    # ══════════════════════════════════════════════════════════════════════
    output.print_md("### Phase 3 — Import Views")

    dep_view_by_name_p3 = {}
    for v in DB.FilteredElementCollector(d).OfClass(DB.View).ToElements():
        try:
            if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
                dep_view_by_name_p3[v.Name] = v
        except Exception:
            pass

    for mv_data in views_data["master_views"]:
        if mv_data["view_name"] not in chosen_masters_set:
            continue
        master_name = mv_data["view_name"]

        if master_name not in master_view_by_name:
            res["skipped"].append("Master '{}' not found".format(master_name))
            continue

        master_view = master_view_by_name[master_name]

        t = DB.Transaction(d, "Full Sync PRO — Import Views: {}".format(master_name))
        t.Start()
        for dv in mv_data["dependent_views"]:
            view_name     = dv["view_name"]
            corners       = dv.get("crop_corners", [])
            title         = dv.get("title_on_sheet", "")
            template_name = dv.get("view_template", "")

            try:
                if view_name in dep_view_by_name_p3:
                    existing = dep_view_by_name_p3[view_name]
                    if corners:
                        result = apply_crop_shape(existing, corners, link_xform)
                        if result is not True:
                            res["errors"].append("Crop '{}': {}".format(view_name, result))
                    set_title_on_sheet(existing, title)
                    res["p3_updated"] += 1
                else:
                    new_id   = master_view.Duplicate(DB.ViewDuplicateOption.AsDependent)
                    new_view = d.GetElement(new_id)
                    new_view.Name = view_name
                    if corners:
                        apply_crop_shape(new_view, corners, link_xform)
                    set_title_on_sheet(new_view, title)
                    if template_name and template_name in template_by_name:
                        new_view.ViewTemplateId = template_by_name[template_name].Id
                    dep_view_by_name_p3[view_name] = new_view
                    res["p3_created"] += 1
            except Exception as e:
                res["errors"].append("View '{}': {}".format(view_name, str(e)))
        t.Commit()

    output.print_md("✅ Created **{}** | Updated **{}**".format(
        res["p3_created"], res["p3_updated"]))

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 4 — ADJUST VIEWS
    # ══════════════════════════════════════════════════════════════════════
    output.print_md("### Phase 4 — Adjust Views")

    dep_views_p4 = []
    for v in DB.FilteredElementCollector(d).OfClass(DB.View).ToElements():
        try:
            pid = v.GetPrimaryViewId()
            if pid == DB.ElementId.InvalidElementId:
                continue
            primary = d.GetElement(pid)
            if primary and primary.Name in chosen_masters_set:
                dep_views_p4.append(v)
        except Exception:
            pass

    if dep_views_p4:
        t = DB.Transaction(d, "Full Sync PRO — Adjust Views")
        t.Start()
        for v in dep_views_p4:
            try:
                v.CropBoxVisible = False
                try:
                    if not v.CropBoxActive:
                        v.CropBoxActive = True
                except Exception:
                    pass
                try:
                    if not v.AnnotationCropActive:
                        v.AnnotationCropActive = True
                        d.Regenerate()
                except Exception:
                    pass
                try:
                    rm = v.GetCropRegionShapeManager()
                    if rm is None:
                        res["p4_adj_skipped"] += 1
                        continue
                    _ = rm.TopAnnotationCropOffset
                except Exception:
                    res["p4_adj_skipped"] += 1
                    continue
                ok = set_annotation_crop_offset(rm, ANNOT_OFFSET)
                if ok:
                    res["p4_adjusted"] += 1
                else:
                    res["errors"].append("Adjust '{}': offset failed".format(v.Name))
            except Exception as e:
                res["errors"].append("Adjust '{}': {}".format(v.Name, str(e)))
        t.Commit()
        output.print_md("✅ Adjusted **{}** view(s)".format(res["p4_adjusted"]))
    else:
        output.print_md("✅ No views to adjust")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 5 — IMPORT SHEETS
    # ══════════════════════════════════════════════════════════════════════
    output.print_md("### Phase 5 — Import Sheets")

    # Re-index after phases 2-4
    dep_view_by_name_p5 = {}
    for v in DB.FilteredElementCollector(d).OfClass(DB.View).ToElements():
        try:
            if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
                dep_view_by_name_p5[v.Name] = v
        except Exception:
            pass

    viewport_by_view_id_p5 = {}
    for vp in DB.FilteredElementCollector(d).OfClass(DB.Viewport).ToElements():
        viewport_by_view_id_p5[vp.ViewId.IntegerValue] = vp

    existing_dls_by_sheet = {}
    for dl in DB.FilteredElementCollector(d).OfClass(DB.CurveElement).ToElements():
        try:
            if not isinstance(dl, DB.DetailLine):
                continue
            owner_id = dl.OwnerViewId.IntegerValue
            existing_dls_by_sheet.setdefault(owner_id, []).append(dl)
        except Exception:
            pass

    def update_position_p5(vp, center, entry):
        vp.SetBoxCenter(center)
        try:
            vp.LabelOffset = DB.XYZ(
                entry.get("label_offset_x", 0),
                entry.get("label_offset_y", 0), 0)
        except Exception:
            pass
        vp_tn = entry.get("viewport_type", "")
        if vp_tn and vp_tn in vp_type_by_name:
            try:
                vp.ChangeTypeId(vp_type_by_name[vp_tn])
            except Exception:
                pass

    def update_title_p5(view, entry):
        title = entry.get("title_on_sheet", "")
        if not title:
            return
        try:
            p = view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
            if p and not p.IsReadOnly:
                cur = p.AsString() or ""
                if cur != title:
                    p.Set(title)
        except Exception:
            pass

    # Re-index sheets after phase 1 deletions may have changed things
    all_sheets_p5 = DB.FilteredElementCollector(d).OfClass(DB.ViewSheet).ToElements()
    sheet_by_suffix_p5 = {}
    for s in all_sheets_p5:
        suffix = s.SheetNumber[2:] if len(s.SheetNumber) > 2 else s.SheetNumber
        sheet_by_suffix_p5[suffix] = s

    for sheet_data in layout:
        sheet_number = sheet_data["sheet_number"]
        sheet_name   = sheet_data["sheet_name"]
        suffix       = sheet_number[2:] if len(sheet_number) > 2 else sheet_number

        if suffix not in sheet_by_suffix_p5:
            res["skipped"].append("Sheet not found: '{}{}' ({})".format(
                dest_prefix, suffix, sheet_name))
            continue

        target_sheet = sheet_by_suffix_p5[suffix]
        sheet_label  = "{}{}".format(dest_prefix, suffix)
        res["p5_sheet_stats"][sheet_label] = {"placed": 0, "updated": 0}

        # ── T1: viewports + detail lines ──
        t1 = DB.Transaction(d, "Full Sync PRO — Layout T1 - {}".format(sheet_label))
        vp_id_to_det = {}

        try:
            t1.Start()

            all_sheet_vps = list(DB.FilteredElementCollector(d, target_sheet.Id)
                .OfClass(DB.Viewport).ToElements())

            original_numbers = {}
            for sv in all_sheet_vps:
                original_numbers[sv.Id.IntegerValue] = get_detail_number(sv)

            ts = str(int(time.time()))
            for i, sv in enumerate(all_sheet_vps):
                set_detail_number(sv, "zzz{}_{}".format(ts, i))

            processed_ids = []
            for entry in sheet_data["viewports"]:
                view_name = entry["view_name"]
                center    = DB.XYZ(entry["viewport_center_x"],
                                   entry["viewport_center_y"], 0)

                if view_name not in dep_view_by_name_p5:
                    res["skipped"].append("View not found: '{}'".format(view_name))
                    continue

                target_view = dep_view_by_name_p5[view_name]
                det_num     = entry.get("detail_number", "")

                try:
                    if target_view.Id.IntegerValue in viewport_by_view_id_p5:
                        evp = viewport_by_view_id_p5[target_view.Id.IntegerValue]
                        if evp.SheetId.IntegerValue == target_sheet.Id.IntegerValue:
                            update_position_p5(evp, center, entry)
                            update_title_p5(target_view, entry)
                            if det_num:
                                vp_id_to_det[evp.Id.IntegerValue] = det_num
                            processed_ids.append(evp.Id.IntegerValue)
                            res["p5_updated"] += 1
                            res["p5_sheet_stats"][sheet_label]["updated"] += 1
                        else:
                            res["skipped"].append(
                                "'{}' on different sheet".format(view_name))
                    else:
                        nvp = DB.Viewport.Create(
                            d, target_sheet.Id, target_view.Id, center)
                        update_position_p5(nvp, center, entry)
                        update_title_p5(target_view, entry)
                        if det_num:
                            vp_id_to_det[nvp.Id.IntegerValue] = det_num
                        processed_ids.append(nvp.Id.IntegerValue)
                        res["p5_created"] += 1
                        res["p5_sheet_stats"][sheet_label]["placed"] += 1
                except Exception as e:
                    res["errors"].append("VP '{}': {}".format(view_name, str(e)))

            for sv in all_sheet_vps:
                if sv.Id.IntegerValue not in processed_ids:
                    orig = original_numbers.get(sv.Id.IntegerValue, "")
                    if orig:
                        set_detail_number(sv, orig)

            # Detail lines
            dl_data = sheet_data.get("detail_lines", [])
            if dl_data:
                sheet_id     = target_sheet.Id.IntegerValue
                existing_dls = existing_dls_by_sheet.get(sheet_id, [])
                for dl in existing_dls:
                    try:
                        d.Delete(dl.Id)
                    except Exception:
                        pass
                for dl_entry in dl_data:
                    try:
                        p0  = dl_entry["p0"]
                        p1_ = dl_entry["p1"]
                        pt0 = DB.XYZ(p0[0], p0[1], p0[2])
                        pt1 = DB.XYZ(p1_[0], p1_[1], p1_[2])
                        if pt0.DistanceTo(pt1) < 1e-4:
                            continue
                        line   = DB.Line.CreateBound(pt0, pt1)
                        new_dl = d.Create.NewDetailCurve(target_sheet, line)
                        style  = get_line_style(dl_entry.get("line_style", ""))
                        if style:
                            new_dl.LineStyle = style
                        res["p5_dl_created"] += 1
                    except Exception as e:
                        res["errors"].append("DL on {}: {}".format(sheet_label, str(e)))

            t1.Commit()
        except Exception as e:
            try:
                if t1.HasStarted() and not t1.HasEnded():
                    t1.RollBack()
            except Exception:
                pass
            res["errors"].append("Sheet {} T1 rolled back: {}".format(sheet_label, str(e)))
            continue

        # ── T2: detail numbers ──
        if not vp_id_to_det:
            continue

        t2 = DB.Transaction(d, "Full Sync PRO — Det# T2 - {}".format(sheet_label))
        try:
            t2.Start()
            all_sheet_vps_t2 = list(DB.FilteredElementCollector(d, target_sheet.Id)
                .OfClass(DB.Viewport).ToElements())
            ts2 = str(int(time.time())) + "b"
            for i, sv in enumerate(all_sheet_vps_t2):
                set_detail_number(sv, "zzz{}_{}".format(ts2, i))
            for sv in all_sheet_vps_t2:
                vid = sv.Id.IntegerValue
                if vid in vp_id_to_det:
                    set_detail_number(sv, vp_id_to_det[vid])
            t2.Commit()
        except Exception as e:
            try:
                if t2.HasStarted() and not t2.HasEnded():
                    t2.RollBack()
            except Exception:
                pass
            res["errors"].append("Sheet {} T2 rolled back: {}".format(sheet_label, str(e)))

    output.print_md("✅ Created **{}** | Updated **{}** | Lines **{}**".format(
        res["p5_created"], res["p5_updated"], res["p5_dl_created"]))

    # Store link_type for later reload
    res["_link_type"] = link_type
    return res


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RUN SYNC ON EACH DOCUMENT
# ═══════════════════════════════════════════════════════════════════════════════

all_results = []

for idx, (target_doc, prefix, link_name) in enumerate(doc_configs, 1):
    output.print_md("\n---")
    output.print_md("## 📄 Document {}/{} — {} (prefix: {})".format(
        idx, len(doc_configs), target_doc.Title, prefix))

    result = run_sync(target_doc, prefix, link_name)
    all_results.append(result)

    output.print_md("✅ *{}* sync complete".format(target_doc.Title))


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RELOAD LINKS
# ═══════════════════════════════════════════════════════════════════════════════

docs_with_unloaded = [(r, r["_link_type"]) for r in all_results
                      if r.get("link_unloaded") and "_link_type" in r]

if docs_with_unloaded:
    do_reload = ui.confirm(
        "All syncs complete.\n\n"
        "Reload linked models in {} document(s)?\n\n"
        "(Skip if the link is open in another session.)".format(
            len(docs_with_unloaded)),
        title="Reload Links?",
        yes_text="Reload"
    )
    if do_reload:
        output.print_md("\n---")
        output.print_md("## Post-sync — Reload Links")
        for r, lt in docs_with_unloaded:
            try:
                try:
                    lt.LoadLocally()
                except AttributeError:
                    lt.Load()
                r["link_reloaded"] = True
                output.print_md("✅ *{}* — link reloaded".format(r["title"]))
            except Exception as e:
                output.print_md("⚠️ *{}* — could not reload: {}".format(r["title"], e))
    else:
        output.print_md("\n---")
        output.print_md("## Post-sync — Reload Links")
        output.print_md("⏭️ Skipped — reload manually when ready")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. GRAND SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

output.print_md("\n---")
output.print_md("# ✅ Full Sync PRO Complete — {} document(s)".format(len(all_results)))
output.print_md("")

# Per-document summary table
output.print_md("| Document | Prefix | P1 Removed | P2 Deleted | P3 Created | P3 Updated | P4 Adjusted | P5 Placed | P5 Updated | P5 Lines | Link |")
output.print_md("|----------|--------|------------|------------|------------|------------|-------------|-----------|------------|----------|------|")
for r in all_results:
    link_status = "✅" if r["link_reloaded"] else ("⏭️" if r["link_unloaded"] else "—")
    output.print_md("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
        r["title"], r["prefix"],
        r["p1_removed"], r["p2_deleted"],
        r["p3_created"], r["p3_updated"],
        r["p4_adjusted"],
        r["p5_created"], r["p5_updated"], r["p5_dl_created"],
        link_status))

# Totals
total_errors  = sum(len(r["errors"])  for r in all_results)
total_skipped = sum(len(r["skipped"]) for r in all_results)

if total_skipped:
    output.print_md("\n**⏭️ Skipped across all documents ({})**".format(total_skipped))
    for r in all_results:
        for s in r["skipped"]:
            output.print_md("  - [{}] {}".format(r["prefix"], s))

if total_errors:
    output.print_md("\n**❌ Errors across all documents ({})**".format(total_errors))
    for r in all_results:
        for e in r["errors"]:
            output.print_md("  - [{}] {}".format(r["prefix"], e))
else:
    output.print_md("\n🎉 **No errors across all documents.**")
