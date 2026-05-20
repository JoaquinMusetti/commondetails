# -*- coding: utf-8 -*-
__title__ = 'Full\nSync'
__doc__ = ('Runs all cleanup and import operations in sequence with no interruptions: '
           'clean sheets → clean views → import views → adjust views → import sheet layout. '
           'All inputs are collected upfront. Auto-applies all changes.')

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

doc    = revit.doc
output = script.get_output()

# ─────────────────────────────────────────────────────────────────────────────
# 1. COLLECT ALL INPUTS UPFRONT
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("# ⛓ Full Sync")
output.print_md("Collecting inputs — 5 steps...")

details_json_path = forms.pick_file(
    file_ext="json",
    title="1 of 5 — Select Dependent Views JSON (YYYYMMDD_Dependent views.json)"
)
if not details_json_path:
    script.exit()

sheets_json_path = forms.pick_file(
    file_ext="json",
    title="2 of 5 — Select Sheet Layout JSON (YYYYMMDD_Sheet layout.json)"
)
if not sheets_json_path:
    script.exit()

with open(details_json_path, "r") as f:
    views_data = json.load(f)
with open(sheets_json_path, "r") as f:
    layout = json.load(f)

link_instances = DB.FilteredElementCollector(doc)\
    .OfClass(DB.RevitLinkInstance).ToElements()
if not link_instances:
    ui.alert("No linked models found in document.", title="Full Sync")
    script.exit()

link_by_name  = {li.Name: li for li in link_instances}
chosen_link   = ui.pick_list(
    sorted(link_by_name.keys()),
    "3 of 5 - Reference Linked Model",
    button_name="Next",
    multiselect=False,
    context=u"Full Sync runs Import/Sync Views + Import Sheet Layout + Align "
            u"Titleblocks back-to-back, with no pauses once confirmed. Pick the "
            u"Common Details link here — its transform is used to convert JSON "
            u"coordinates into this building's space."
)
if not chosen_link:
    script.exit()

link_instance  = link_by_name[chosen_link]
link_transform = link_instance.GetTotalTransform()
link_type      = doc.GetElement(link_instance.GetTypeId())  # RevitLinkType for unload/reload

dest_prefix = ui.ask_for_string(
    prompt="Enter the 2-letter prefix of the destination model\n(e.g. AE, AB, AC...)",
    title="4 of 5 - Destination Model Prefix",
    context=u"2-letter prefix of the destination building "
            u"(AE/AB/AC/AD/AF/AG/AK/AS). Used to rewrite sheet names from "
            u"'CD_XXXX_...' to '<prefix>_XXXX_...'."
)
if not dest_prefix:
    script.exit()
dest_prefix = dest_prefix.strip().upper()

master_options = sorted([mv["view_name"] for mv in views_data["master_views"]])
chosen_masters = ui.pick_list(
    master_options,
    "5 of 5 - Select Master Views",
    button_name="Sync",
    context=u"Final step before the sync runs: tick which masters from the JSON "
            u"to bring over. Dependents of unticked masters are skipped entirely. "
            u"After this you'll see a confirmation summary — last chance to abort."
)
if not chosen_masters:
    script.exit()
chosen_masters_set = set(chosen_masters)

views_in_scope = sum(
    len(mv["dependent_views"])
    for mv in views_data["master_views"]
    if mv["view_name"] in chosen_masters_set
)

confirm = ui.confirm(
    "Ready to run Full Sync:\n\n"
    "  * {} sheets from JSON\n"
    "  * {} dependent views in scope\n"
    "  * Destination prefix: {}\n"
    "  * Linked model: {}\n\n"
    "This will:\n"
    "  0. Unload the linked model (speeds up the sync significantly)\n"
    "  1. Remove viewports not in JSON from sheets (views are kept)\n"
    "  2. Delete views that are orphaned, scale-mismatched or type-mismatched\n"
    "  3. Create / update all dependent views\n"
    "  4. Set annotation crop offset to 1/8\" and hide crop region on all in-scope views\n"
    "  5. Place viewports with positions and detail numbers\n"
    "  6. Ask whether to reload the linked model\n\n"
    "No further prompts except the final reload question. Continue?".format(
        len(layout), views_in_scope, dest_prefix, chosen_link),
    title="Full Sync - Confirm",
    yes_text="Run Full Sync"
)
if not confirm:
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# PRE-SYNC — UNLOAD LINKED MODEL
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## Pre-sync — Unload Link")

link_unloaded        = False
link_was_already_off = False
if not DB.RevitLinkType.IsLoaded(doc, link_type.Id):
    link_unloaded        = True
    link_was_already_off = True
    output.print_md("ℹ️ *{}* already unloaded — skipping".format(chosen_link))
else:
    try:
        try:
            link_type.UnloadLocally(None)   # "Unload for me" — only affects current user
        except AttributeError:
            link_type.Unload(None)          # fallback for Revit < 2022
        link_unloaded = True
        output.print_md("✅ *{}* unloaded (for me)".format(chosen_link))
    except Exception as _ule:
        output.print_md("❌ Could not unload link: {}".format(_ule))
        output.print_md("Unload the link manually before running Full Sync, then try again.")
        script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 2. INDEX MODEL
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## Indexing model...")

all_views_raw = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

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

all_sheets_model = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
sheet_by_suffix  = {}
for s in all_sheets_model:
    suffix = s.SheetNumber[2:] if len(s.SheetNumber) > 2 else s.SheetNumber
    sheet_by_suffix[suffix] = s

all_viewports_raw    = list(DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements())
viewport_by_view_id  = {vp.ViewId.IntegerValue: vp for vp in all_viewports_raw}
viewports_by_sheet   = {}
for vp in all_viewports_raw:
    viewports_by_sheet.setdefault(vp.SheetId.IntegerValue, []).append(vp)

# Viewport type index (stores ElementId for ChangeTypeId)
vp_type_by_name = {}
for t in DB.FilteredElementCollector(doc).OfClass(DB.ElementType).ToElements():
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

# Line styles
line_style_by_name = {}
try:
    detail_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    for sub in detail_cat.SubCategories:
        gs = sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
        if gs:
            line_style_by_name[sub.Name] = gs
except Exception:
    pass

# JSON view info lookup
json_view_info = {}
for mv in views_data["master_views"]:
    if mv["view_name"] in chosen_masters_set:
        for dv in mv["dependent_views"]:
            json_view_info[dv["view_name"]] = dv

json_view_names = set(json_view_info.keys())

output.print_md("Dependent views: **{}** · Master views: **{}** · Sheets: **{}** · VP types: **{}**".format(
    len(dep_view_by_name), len(master_view_by_name),
    len(sheet_by_suffix), len(vp_type_by_name)))

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_view_name_from_vp(vp):
    try:
        v = doc.GetElement(vp.ViewId)
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

def get_line_style(name):
    if name and name in line_style_by_name:
        return line_style_by_name[name]
    if line_style_by_name:
        return list(line_style_by_name.values())[0]
    return None

def set_title_on_sheet(view, title):
    try:
        p = view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
        if p and not p.IsReadOnly:
            p.Set(title or "")
    except Exception:
        pass

def make_crop_loop(corners_world):
    """Build a closed CurveLoop from 4 world-space corner points."""
    loop = CurveLoop()
    for k in range(4):
        loop.Append(Line.CreateBound(
            corners_world[k],
            corners_world[(k + 1) % 4]
        ))
    return loop

def apply_crop_shape(view, corners_link):
    """Apply crop boundary via SetCropShape — matches standalone Import/Sync Views tool."""
    try:
        corners_world = [link_transform.OfPoint(DB.XYZ(c[0], c[1], c[2]))
                         for c in corners_link]
        crop_loop = make_crop_loop(corners_world)
        view.CropBoxActive = True
        view.GetCropRegionShapeManager().SetCropShape(crop_loop)
        view.CropBoxVisible = False
        return True
    except Exception as e:
        return str(e)

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — CLEAN SHEETS: remove orphaned viewports
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## Phase 1 — Clean Sheets")

p1_removed = 0
p1_errors  = []
p1_details = []   # list of (sheet_label, view_name)
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
        vname = get_view_name_from_vp(vp)
        if not vname or vname in json_vp_names:
            continue
        try:
            v = doc.GetElement(vp.ViewId)
            if v and v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
                orphaned_vp_ids.append(vp.Id)
                p1_details.append(("{}{}".format(dest_prefix, suffix), vname))
                output.print_md("  🗑️ *{}* on sheet {}{}".format(
                    vname, dest_prefix, suffix))
        except Exception:
            pass

if orphaned_vp_ids:
    with revit.Transaction("Full Sync — Remove Orphaned Viewports"):
        for vp_id in orphaned_vp_ids:
            try:
                doc.Delete(vp_id)
                p1_removed += 1
            except Exception as e:
                p1_errors.append("VP remove: {}".format(e))
    output.print_md("✅ Removed **{}** orphaned viewport(s)".format(p1_removed))
else:
    output.print_md("✅ No orphaned viewports found")

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — CLEAN VIEWS: delete orphaned, scale-mismatch, type-mismatch
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## Phase 2 — Clean Views")

p2_deleted = 0
p2_errors  = []
views_to_delete = []

# Re-collect after Phase 1 deletions
for v in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
    try:
        pid = v.GetPrimaryViewId()
        if pid == DB.ElementId.InvalidElementId:
            continue
        primary = doc.GetElement(pid)
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
                    reason = "scale mismatch (model: {}  JSON: {})".format(
                        v.Scale, json_scale)
            except Exception:
                pass
        if reason is None and json_type:
            if str(v.ViewType) != json_type:
                reason = "view type mismatch (model: {}  JSON: {})".format(
                    str(v.ViewType), json_type)

    if reason:
        views_to_delete.append((v.Name, v.Id, reason))
        output.print_md("  🗑️ *{}* — {}".format(v.Name, reason))

if views_to_delete:
    with revit.Transaction("Full Sync — Delete Outdated Views"):
        for vname, vid, reason in views_to_delete:
            try:
                doc.Delete(vid)
                p2_deleted += 1
            except Exception as e:
                p2_errors.append("Delete '{}': {}".format(vname, e))
    output.print_md("✅ Deleted **{}** view(s)".format(p2_deleted))
else:
    output.print_md("✅ No views to clean up")

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3 — IMPORT VIEWS: create / update dependent views
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## Phase 3 — Import Views")

p3_created = 0
p3_updated = 0
p3_skipped = []
p3_errors  = []

# Re-index dep views after Phase 2 deletions
dep_view_by_name_p3 = {}
for v in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
    try:
        if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
            dep_view_by_name_p3[v.Name] = v
    except Exception:
        pass

for mv_data in views_data["master_views"]:
    if mv_data["view_name"] not in chosen_masters_set:
        continue
    master_name = mv_data["view_name"]
    output.print_md("### {}".format(master_name))

    if master_name not in master_view_by_name:
        p3_skipped.append("Master view not found: '{}'".format(master_name))
        output.print_md("  ❌ Master view not found in model")
        continue

    master_view = master_view_by_name[master_name]

    with revit.Transaction("Full Sync — Import Views: {}".format(master_name)):
        for dv in mv_data["dependent_views"]:
            view_name     = dv["view_name"]
            corners       = dv.get("crop_corners", [])
            title         = dv.get("title_on_sheet", "")
            template_name = dv.get("view_template", "")

            try:
                if view_name in dep_view_by_name_p3:
                    existing = dep_view_by_name_p3[view_name]
                    if corners:
                        result = apply_crop_shape(existing, corners)
                        if result is not True:
                            p3_errors.append("Crop '{}': {}".format(view_name, result))
                    set_title_on_sheet(existing, title)
                    output.print_md("  🔄 Updated: *{}*".format(view_name))
                    p3_updated += 1
                else:
                    new_id   = master_view.Duplicate(DB.ViewDuplicateOption.AsDependent)
                    new_view = doc.GetElement(new_id)
                    new_view.Name = view_name
                    if corners:
                        apply_crop_shape(new_view, corners)
                    set_title_on_sheet(new_view, title)
                    if template_name and template_name in template_by_name:
                        new_view.ViewTemplateId = template_by_name[template_name].Id
                    dep_view_by_name_p3[view_name] = new_view
                    output.print_md("  ✅ Created: *{}*".format(view_name))
                    p3_created += 1
            except Exception as e:
                p3_errors.append("View '{}': {}".format(view_name, str(e)))

output.print_md("✅ Created **{}** | Updated **{}**".format(p3_created, p3_updated))

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4 — ADJUST VIEWS: annotation crop offset 1/8" + hide crop region
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## Phase 4 — Adjust Views")

ANNOT_OFFSET = (1.0 / 8.0) / 12.0   # 1/8 inch in feet (Revit internal units)

p4_adjusted = 0
p4_adj_skipped  = 0
p4_errors   = []

def set_annotation_crop_offset(rm, offset):
    """Apply offset to all four sides using same 3-fallback strategy as standalone tool."""
    # Fallback 1: direct property assignment (most common)
    try:
        rm.TopAnnotationCropOffset    = offset
        rm.BottomAnnotationCropOffset = offset
        rm.LeftAnnotationCropOffset   = offset
        rm.RightAnnotationCropOffset  = offset
        return True
    except Exception:
        pass
    # Fallback 2: reflection (handles some API variations)
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
    # Fallback 3: method call
    try:
        rm.SetAnnotationCropOffset(offset, offset, offset, offset)
        return True
    except Exception:
        pass
    return False

# Collect all dependent views whose primary view is in the chosen masters set
dep_views_p4 = []
for v in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
    try:
        pid = v.GetPrimaryViewId()
        if pid == DB.ElementId.InvalidElementId:
            continue
        primary = doc.GetElement(pid)
        if primary and primary.Name in chosen_masters_set:
            dep_views_p4.append(v)
    except Exception:
        pass

if dep_views_p4:
    with revit.Transaction("Full Sync — Adjust Views"):
        for v in dep_views_p4:
            try:
                # 1. Hide crop region outline
                v.CropBoxVisible = False

                # 2. Ensure crop box is active (required before enabling annotation crop)
                try:
                    if not v.CropBoxActive:
                        v.CropBoxActive = True
                except Exception:
                    pass

                # 3. Enable annotation crop and regenerate (required before setting offsets)
                try:
                    if not v.AnnotationCropActive:
                        v.AnnotationCropActive = True
                        doc.Regenerate()
                except Exception:
                    pass

                # 4. Check that this view supports annotation crop via ShapeManager
                try:
                    rm = v.GetCropRegionShapeManager()
                    if rm is None:
                        p4_adj_skipped += 1
                        continue
                    _ = rm.TopAnnotationCropOffset   # probe — raises if unsupported
                except Exception:
                    p4_adj_skipped += 1
                    continue

                # 5. Set 1/8" offset on all four sides
                ok = set_annotation_crop_offset(rm, ANNOT_OFFSET)
                if ok:
                    p4_adjusted += 1
                else:
                    p4_errors.append("Adjust '{}': all offset methods failed".format(v.Name))

            except Exception as e:
                p4_errors.append("Adjust '{}': {}".format(v.Name, str(e)))

    output.print_md("✅ Adjusted **{}** view(s){}".format(
        p4_adjusted,
        "  ·  **{}** skipped (annotation crop not supported)".format(p4_adj_skipped) if p4_adj_skipped else ""))
else:
    output.print_md("✅ No views in scope to adjust")

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 5 — IMPORT SHEETS: place viewports with positions, types, detail numbers, lines
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("## Phase 5 — Import Sheets")

p5_created    = 0
p5_updated    = 0
p5_dl_created = 0
p5_dl_deleted = 0
p5_skipped    = []
p5_errors     = []
p5_sheet_stats = {}

# Re-index dep views and viewports after Phases 2-4
dep_view_by_name_p5 = {}
for v in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
    try:
        if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
            dep_view_by_name_p5[v.Name] = v
    except Exception:
        pass

viewport_by_view_id_p5 = {}
for vp in DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements():
    viewport_by_view_id_p5[vp.ViewId.IntegerValue] = vp

# Index existing detail lines by sheet
existing_dls_by_sheet = {}
for dl in DB.FilteredElementCollector(doc).OfClass(DB.CurveElement).ToElements():
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
        label_x = entry.get("label_offset_x", 0)
        label_y = entry.get("label_offset_y", 0)
        vp.LabelOffset = DB.XYZ(label_x, label_y, 0)
    except Exception:
        pass
    vp_type_name = entry.get("viewport_type", "")
    if vp_type_name and vp_type_name in vp_type_by_name:
        try:
            vp.ChangeTypeId(vp_type_by_name[vp_type_name])
        except Exception:
            pass

def update_title_on_sheet_p5(view, entry):
    title = entry.get("title_on_sheet", "")
    if not title:
        return
    try:
        p = view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
        if p and not p.IsReadOnly:
            current = p.AsString() or ""
            if current != title:
                p.Set(title)
    except Exception:
        pass

for sheet_data in layout:
    sheet_number = sheet_data["sheet_number"]
    sheet_name   = sheet_data["sheet_name"]
    suffix       = sheet_number[2:] if len(sheet_number) > 2 else sheet_number

    if suffix not in sheet_by_suffix:
        p5_skipped.append("Sheet not found: '{}{}' ({})".format(
            dest_prefix, suffix, sheet_name))
        continue

    target_sheet = sheet_by_suffix[suffix]
    sheet_label  = "{}{}".format(dest_prefix, suffix)
    output.print_md("### Sheet {}".format(sheet_label))

    p5_sheet_stats[sheet_label] = {"placed": 0, "updated": 0}

    # ═══════════════════════════════════════════════════════════════════════
    # TRANSACTION 1 — move all to temp + create/update viewports + detail lines
    # ═══════════════════════════════════════════════════════════════════════

    t1 = DB.Transaction(doc, "Full Sync — Import Layout T1 - {}".format(sheet_label))
    vp_id_to_detail_number = {}

    try:
        t1.Start()

        # ── Step 0: snapshot existing detail numbers ──
        all_sheet_vps = list(DB.FilteredElementCollector(doc, target_sheet.Id)
            .OfClass(DB.Viewport)
            .ToElements())

        original_numbers = {}
        for sv in all_sheet_vps:
            original_numbers[sv.Id.IntegerValue] = get_detail_number(sv)

        # ── Step 1: move ALL existing to temp ──
        timestamp = str(int(time.time()))
        for i, sv in enumerate(all_sheet_vps):
            set_detail_number(sv, "zzz{}_{}".format(timestamp, i))

        # ── Step 2: create/update viewports ──
        processed_ids = []

        for entry in sheet_data["viewports"]:
            view_name = entry["view_name"]
            center    = DB.XYZ(entry["viewport_center_x"], entry["viewport_center_y"], 0)

            if view_name not in dep_view_by_name_p5:
                p5_skipped.append("View not found: '{}'".format(view_name))
                continue

            target_view    = dep_view_by_name_p5[view_name]
            target_det_num = entry.get("detail_number", "")

            try:
                if target_view.Id.IntegerValue in viewport_by_view_id_p5:
                    existing_vp = viewport_by_view_id_p5[target_view.Id.IntegerValue]
                    if existing_vp.SheetId.IntegerValue == target_sheet.Id.IntegerValue:
                        update_position_p5(existing_vp, center, entry)
                        update_title_on_sheet_p5(target_view, entry)
                        if target_det_num:
                            vp_id_to_detail_number[existing_vp.Id.IntegerValue] = target_det_num
                        processed_ids.append(existing_vp.Id.IntegerValue)
                        output.print_md("  🔄 Updated: *{}*".format(view_name))
                        p5_updated += 1
                        p5_sheet_stats[sheet_label]["updated"] += 1
                    else:
                        p5_skipped.append(
                            "'{}' is placed on a different sheet, not moved.".format(view_name))
                else:
                    vp = DB.Viewport.Create(doc, target_sheet.Id, target_view.Id, center)
                    update_position_p5(vp, center, entry)
                    update_title_on_sheet_p5(target_view, entry)
                    if target_det_num:
                        vp_id_to_detail_number[vp.Id.IntegerValue] = target_det_num
                    processed_ids.append(vp.Id.IntegerValue)
                    output.print_md("  ✅ Created: *{}*".format(view_name))
                    p5_created += 1
                    p5_sheet_stats[sheet_label]["placed"] += 1

            except Exception as e:
                p5_errors.append("Viewport *{}*: {}".format(view_name, str(e)))

        # Restore non-processed viewports to original numbers
        for sv in all_sheet_vps:
            if sv.Id.IntegerValue not in processed_ids:
                original = original_numbers.get(sv.Id.IntegerValue, "")
                if original:
                    set_detail_number(sv, original)

        # ── Detail Lines ──
        dl_data = sheet_data.get("detail_lines", [])
        if dl_data:
            sheet_id     = target_sheet.Id.IntegerValue
            existing_dls = existing_dls_by_sheet.get(sheet_id, [])

            deleted_count = 0
            for dl in existing_dls:
                try:
                    doc.Delete(dl.Id)
                    deleted_count += 1
                except Exception:
                    pass
            p5_dl_deleted += deleted_count
            if deleted_count:
                output.print_md("  🗑️ {} detail lines deleted".format(deleted_count))

            sheet_dl_created = 0
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
                    style  = get_line_style(dl_entry.get("line_style", ""))
                    if style:
                        new_dl.LineStyle = style
                    sheet_dl_created += 1
                except Exception as e:
                    p5_errors.append("Detail line on sheet {}: {}".format(
                        sheet_label, str(e)))

            p5_dl_created += sheet_dl_created
            output.print_md("  📐 {} detail lines created".format(sheet_dl_created))

        t1.Commit()
        output.print_md("  ✔ T1 committed")

    except Exception as e:
        try:
            if t1.HasStarted() and not t1.HasEnded():
                t1.RollBack()
        except Exception:
            pass
        p5_errors.append("Sheet {} T1 rolled back: {}".format(sheet_label, str(e)))
        output.print_md("  ❌ T1 rolled back: {}".format(str(e)))
        continue

    # ═══════════════════════════════════════════════════════════════════════
    # TRANSACTION 2 — assign final detail numbers
    # ═══════════════════════════════════════════════════════════════════════

    if not vp_id_to_detail_number:
        continue

    t2 = DB.Transaction(doc, "Full Sync — Detail Numbers T2 - {}".format(sheet_label))

    try:
        t2.Start()

        all_sheet_vps_t2 = list(DB.FilteredElementCollector(doc, target_sheet.Id)
            .OfClass(DB.Viewport)
            .ToElements())

        timestamp2 = str(int(time.time())) + "b"
        for i, sv in enumerate(all_sheet_vps_t2):
            set_detail_number(sv, "zzz{}_{}".format(timestamp2, i))

        for sv in all_sheet_vps_t2:
            vid = sv.Id.IntegerValue
            if vid in vp_id_to_detail_number:
                det_num = vp_id_to_detail_number[vid]
                set_detail_number(sv, det_num)
                output.print_md("  🔢 Detail number set: *{}*".format(det_num))

        t2.Commit()
        output.print_md("  ✔ T2 committed")

    except Exception as e:
        try:
            if t2.HasStarted() and not t2.HasEnded():
                t2.RollBack()
        except Exception:
            pass
        p5_errors.append("Sheet {} T2 rolled back: {}".format(sheet_label, str(e)))
        output.print_md("  ❌ T2 rolled back: {}".format(str(e)))

output.print_md("✅ Created **{}** | Updated **{}** | Lines **{}**".format(
    p5_created, p5_updated, p5_dl_created))

# ─────────────────────────────────────────────────────────────────────────────
# POST-SYNC — RELOAD LINKED MODEL
# ─────────────────────────────────────────────────────────────────────────────

link_reloaded = False
if link_unloaded:
    do_reload = ui.confirm(
        "Sync complete.\n\nReload linked model now?\n\n"
        "  {}\n\n"
        "(Skip if the model is open in another session or you prefer to keep it unloaded.)".format(
            chosen_link),
        title="Reload Link?",
        yes_text="Reload"
    )
    if do_reload:
        output.print_md("\n---")
        output.print_md("## Post-sync — Reload Link")
        try:
            try:
                link_type.LoadLocally()   # pair with UnloadLocally
            except AttributeError:
                link_type.Load()          # fallback for Revit < 2022
            link_reloaded = True
            output.print_md("✅ *{}* reloaded".format(chosen_link))
        except Exception as _rle:
            output.print_md("⚠️ Could not reload: {}".format(_rle))
            output.print_md("  → Reload manually via **Manage → Manage Links**")
    else:
        output.print_md("\n---")
        output.print_md("## Post-sync — Reload Link")
        output.print_md("⏭️ Skipped — reload manually when ready")

# ─────────────────────────────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

all_errors  = p1_errors + p2_errors + p3_errors + p4_errors + p5_errors
all_skipped = p3_skipped + p5_skipped

output.print_md("\n---")
output.print_md("# ✅ Full Sync Complete")
output.print_md("")
output.print_md("| Phase | Result |")
output.print_md("|-------|--------|")
output.print_md("| 0 — Unload Link       | {} |".format(
    "ℹ️ Already unloaded" if link_was_already_off else "✅ Unloaded"))
output.print_md("| 1 — Clean Sheets      | **{}** viewport(s) removed from sheets |".format(p1_removed))
output.print_md("| 2 — Clean Views       | **{}** view(s) deleted |".format(p2_deleted))
output.print_md("| 3 — Import Views      | **{}** created  ·  **{}** updated |".format(
    p3_created, p3_updated))
output.print_md("| 4 — Adjust Views      | **{}** adjusted (annotation crop 1/8\" + hide region) |".format(
    p4_adjusted))
output.print_md("| 5 — Import Sheets     | **{}** placed  ·  **{}** updated  ·  **{}** lines |".format(
    p5_created, p5_updated, p5_dl_created))
output.print_md("| 6 — Reload Link       | {} |".format(
    "✅ Reloaded" if link_reloaded else ("⏭️ Skipped" if link_unloaded else "—")))

# ── Phase 1 detail: which views were removed from which sheets ──
if p1_details:
    output.print_md("\n**Phase 1 — Viewports removed from sheets** *(views remain in model)*")
    output.print_md("| Sheet | View |")
    output.print_md("|-------|------|")
    for sheet_lbl, vname in p1_details:
        output.print_md("| {} | {} |".format(sheet_lbl, vname))

# ── Phase 2 detail: which views were deleted and why ──
if views_to_delete:
    output.print_md("\n**Phase 2 — Views deleted**")
    output.print_md("| View | Reason |")
    output.print_md("|------|--------|")
    for vname, _vid, reason in views_to_delete:
        output.print_md("| {} | {} |".format(vname, reason))

# ── Phase 5 detail: per-sheet change summary ──
sheets_with_changes = {lbl: s for lbl, s in p5_sheet_stats.items()
                       if s["placed"] > 0 or s["updated"] > 0}
if sheets_with_changes:
    output.print_md("\n**Phase 5 — Sheets with changes**")
    output.print_md("| Sheet | Placed | Updated |")
    output.print_md("|-------|--------|---------|")
    for lbl, s in sorted(sheets_with_changes.items()):
        output.print_md("| {} | {} | {} |".format(lbl, s["placed"], s["updated"]))

if all_skipped:
    output.print_md("\n**⏭️ Skipped ({})**".format(len(all_skipped)))
    for s in all_skipped:
        output.print_md("  - {}".format(s))

if all_errors:
    output.print_md("\n**❌ Errors ({})**".format(len(all_errors)))
    for e in all_errors:
        output.print_md("  - {}".format(e))
else:
    output.print_md("\n🎉 **No errors.**")
