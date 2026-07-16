# -*- coding: utf-8 -*-
__title__ = 'Import\nDetails'
__doc__ = ('Reads a Sheets with Views JSON (produced by "Export Sheets with '
           'Views") and recreates the dependent views + sheet layout in the '
           'active document. Direction-agnostic: works for CD->building, '
           'building->CD, and building->building (via Common Details as the '
           'common coordinate space).')

import json
import sys
import os as _os
import time
from pyrevit import revit, DB, script, forms, HOST_APP
from Autodesk.Revit.DB import (CurveLoop, Line, ViewDuplicateOption,
                                BuiltInParameterGroup, BuiltInCategory,
                                ElementTransformUtils)

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui
from sheet_naming import building_tag, dest_building_letter

doc   = revit.doc
output = script.get_output()
output.close()

NONE_OPTION = "None (I'm importing views into the Common Details file)"
_SPF_PATH   = _os.path.join(_ext_dir, 'lib', 'common_details_shared_params.txt')

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
        grp = spf.Groups.get_Item("Common Details") or spf.Groups.Create("Common Details")
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
    context=u"Pick the Common Details link if you are IN a building (the link's "
            u"transform converts JSON coordinates into building space). Pick "
            u"'None' if THIS file IS Common Details — no transform needed, the "
            u"JSON's coordinates are already CD-relative (master_map.json "
            u"auto-inverts in this direction)."
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

map_path        = _os.path.join(_os.path.dirname(json_path), "master_map.json")
master_map_loaded = _os.path.isfile(map_path)
master_map_info = u""
if master_map_loaded:
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
# 1c2. Destination model prefix (asked early so the sheet picker can be filtered)
# ─────────────────────────────────────────────────────────────────────────────

dest_prefix = ui.ask_for_string(
    prompt="Enter the 2-letter prefix of the destination model\n(e.g. AE, AB, AC...)",
    title="2 of 5 — Destination Model Prefix",
    context=u"2-letter prefix used to rewrite the JSON's sheet names for this "
            u"destination. Common building prefixes: AE, AB, AC, AD, AF, AG, AK; "
            u"AS for Site. Use 'CD' when importing into Common Details itself."
)
if not dest_prefix:
    script.exit()

dest_prefix = dest_prefix.strip().upper()

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

# Filter JSON sheets to this destination building. The CD export carries every
# building's CUSTOM sheets (all AX-prefixed, tagged '.A', '.E'…) plus SHARED
# sheets (no letter tag). Keep a sheet only if it is shared (no tag) or its tag
# matches this building's letter -- same rule the Pre-Import Audit uses
# (building_tag / dest_building_letter live in lib/sheet_naming.py). Filtering by
# the leading 2-char prefix would keep every building's AX-prefixed customs.
_dest_letter = dest_building_letter(dest_prefix)
_full = len(sheets_in_json)
if _dest_letter is not None:
    sheets_in_json = [sh for sh in sheets_in_json
                      if building_tag(sh.get("sheet_number", u"")) in (None, _dest_letter)]
_skipped = _full - len(sheets_in_json)

if not sheets_in_json:
    ui.alert(
        u"The JSON has no shared sheets and no custom sheets for building '{}'.\n\n"
        u"It contains {} sheet(s) total (shared + other buildings). Re-run and "
        u"enter the correct destination prefix.".format(dest_prefix, _full),
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
    "3 of 5 — Select Sheets to Import",
    multiselect=True,
    context=(u"Only shared sheets plus building '{}'s custom sheets are shown "
             u"({} from other buildings hidden). Tick the sheets you want to bring "
             u"into the active "
             u"model. The tool figures out which dependent views each sheet needs "
             u"and creates them automatically under their matching master views. "
             u"Sheets you don't tick (and any views unique to them) are skipped "
             u"entirely.".format(dest_prefix, _skipped))
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

# Reverse-lookup used for diagnostic messages in Phase B
view_name_to_master = {
    dv["view_name"]: mv["view_name"]
    for mv in data["master_views"]
    for dv in mv["dependent_views"]
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Strategy for existing dependent views
# ─────────────────────────────────────────────────────────────────────────────

strategy = ui.pick_list(
    [
        "Skip existing — do not touch dependent views that already exist",
        "Update existing — re-apply crop boundary to views that already exist",
    ],
    "4 of 5 — Strategy for Existing Dependent Views",
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
    "5 of 5 — Sheet Update Options",
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
# 5. Index destination model resources
# ─────────────────────────────────────────────────────────────────────────────

all_views_dest = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

view_by_name      = {}
template_by_name  = {}
dep_view_by_name  = {}

# NOTE: dependent views are matched by NAME only. Detail ID is still STAMPED from
# the JSON onto new/blank views (identity for Export + the Detect Renames tool),
# but it is deliberately NOT used to match — duplicate Detail IDs (from copied
# views) made ID matching ambiguous and broke the import. Rename detection now
# lives in the opt-in "Detect Renames" tool.
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
# 5c. Pre-flight: type / scale / name mismatch check with optional auto-fix
# ─────────────────────────────────────────────────────────────────────────────
# Precedence per JSON dep entry:
#   1. exact match (ID + name)   → canonical, only check type/scale
#   2. ID-only match (name differs)  → name mismatch + type/scale check
#   3. name-only match           → type/scale check
#   4. nothing                    → new view, Phase A will create it
# Fix actions (chosen via checkboxes):
#   type/scale: delete the dep, Phase A recreates from the correct master
#   name:       rename in destination; if name is taken, collider → "<name> (old)"

from System.Collections.ObjectModel import ObservableCollection as _OC_PF

_VT_LABEL = {
    int(DB.ViewType.FloorPlan):    u"Floor Plan",
    int(DB.ViewType.CeilingPlan):  u"Ceiling Plan",
    int(DB.ViewType.Section):      u"Section",
    int(DB.ViewType.Elevation):    u"Elevation",
    int(DB.ViewType.DraftingView): u"Drafting",
    int(DB.ViewType.Detail):       u"Detail",
    int(DB.ViewType.AreaPlan):     u"Area Plan",
    int(DB.ViewType.ThreeD):       u"3D",
}

def _vt_label(v):
    try:
        return _VT_LABEL.get(int(v.ViewType), str(v.ViewType))
    except Exception:
        return u"?"


class _PFRow(object):
    def __init__(self, icon, view_name, issue, in_model, in_json):
        self._icon      = icon
        self._view_name = view_name
        self._issue     = issue
        self._in_model  = in_model
        self._in_json   = in_json

    @property
    def Icon(self):     return self._icon
    @property
    def ViewName(self): return self._view_name
    @property
    def Issue(self):    return self._issue
    @property
    def InModel(self):  return self._in_model
    @property
    def InJSON(self):   return self._in_json


_pf_rows       = _OC_PF[_PFRow]()
_pf_type_ids   = []   # ElementIds with type mismatch — candidates for delete
_pf_scale_ids  = []   # ElementIds with scale mismatch — candidates for delete

# Counters populated when fixes actually execute (used by results window badges)
n_type_fixed   = 0
n_scale_fixed  = 0
fix_errors     = []   # [(view_id_str, error_msg)] for results window Issues section

for _mv in data["master_views"]:
    _mname = _mv["view_name"]
    if _mname not in view_by_name:
        continue   # master missing — Phase A will report as skip_master
    _mview = view_by_name[_mname]

    for _dv in _mv["dependent_views"]:
        _dv_name    = _dv["view_name"]
        _json_scale = _dv.get("view_scale", _mview.Scale)

        # Match by NAME only. Detail ID is deliberately not used to match here
        # (duplicate IDs from copied views made it ambiguous). Rename detection
        # lives in the opt-in "Detect Renames" tool.
        _ex = dep_view_by_name.get(_dv_name)

        if _ex is None:
            continue   # new view — Phase A will create it

        # ── Structural mismatch: type takes precedence over scale ─────────────
        try:
            if _ex.ViewType != _mview.ViewType:
                _pf_rows.Add(_PFRow(
                    u"⛔", _dv_name, u"Type changed",
                    _vt_label(_ex), _vt_label(_mview)
                ))
                _pf_type_ids.append(_ex.Id)
            elif _ex.Scale != _json_scale:
                _pf_rows.Add(_PFRow(
                    u"⚠", _dv_name, u"Scale changed",
                    u"1 : {}".format(_ex.Scale),
                    u"1 : {}".format(_json_scale)
                ))
                _pf_scale_ids.append(_ex.Id)
        except Exception:
            pass

# ── Modal + fix transactions ────────────────────────────────────────────────
if len(_pf_rows) > 0:
    _n_type  = sum(1 for _r in _pf_rows if _r.Issue == u"Type changed")
    _n_scale = sum(1 for _r in _pf_rows if _r.Issue == u"Scale changed")
    _pf_parts = []
    if _n_type:  _pf_parts.append(u"{} type".format(_n_type))
    if _n_scale: _pf_parts.append(u"{} scale".format(_n_scale))

    _PF_BODY = u"""
<Grid>
  <Grid.RowDefinitions>
    <RowDefinition Height="*"/>
    <RowDefinition Height="Auto"/>
  </Grid.RowDefinitions>
  <DataGrid Grid.Row="0" x:Name="dgPF" AutoGenerateColumns="False"
            IsReadOnly="True" CanUserResizeColumns="True" CanUserSortColumns="True"
            Background="#12131F" Foreground="#E8EBF5"
            BorderBrush="#2A2D47" BorderThickness="1"
            RowBackground="#12131F" AlternatingRowBackground="#0E0F1A"
            HorizontalGridLinesBrush="#1A1D30" VerticalGridLinesBrush="#1A1D30"
            ColumnHeaderHeight="28" FontFamily="Segoe UI" FontSize="12"
            HeadersVisibility="Column">
    <DataGrid.ColumnHeaderStyle>
      <Style TargetType="DataGridColumnHeader">
        <Setter Property="Background" Value="#1E2235"/>
        <Setter Property="Foreground" Value="#9099C8"/>
        <Setter Property="FontFamily" Value="Segoe UI"/>
        <Setter Property="FontSize"   Value="11"/>
        <Setter Property="Padding"    Value="8,0"/>
      </Style>
    </DataGrid.ColumnHeaderStyle>
    <DataGrid.Columns>
      <DataGridTextColumn Header=""         Binding="{Binding Icon}"    Width="28"/>
      <DataGridTextColumn Header="View"     Binding="{Binding ViewName}" Width="*"/>
      <DataGridTextColumn Header="Issue"    Binding="{Binding Issue}"   Width="130"/>
      <DataGridTextColumn Header="In model" Binding="{Binding InModel}" Width="150"/>
      <DataGridTextColumn Header="In JSON"  Binding="{Binding InJSON}"  Width="150"/>
    </DataGrid.Columns>
  </DataGrid>
  <StackPanel Grid.Row="1" Margin="0,14,0,0">
    <CheckBox x:Name="chkFixType"  Visibility="Collapsed" IsChecked="True"
              Foreground="#E8EBF5" FontFamily="Segoe UI" FontSize="12" Margin="0,2,0,2"
              Content="⛔  Fix type mismatches — delete the old dependent, Phase A recreates it from the correct master"/>
    <CheckBox x:Name="chkFixScale" Visibility="Collapsed" IsChecked="True"
              Foreground="#E8EBF5" FontFamily="Segoe UI" FontSize="12" Margin="0,2,0,2"
              Content="⚠  Fix scale mismatches — delete the old dependent, Phase A recreates it from the correct-scale master"/>
  </StackPanel>
</Grid>
"""

    _PF_FOOTER = u"""
<Grid>
  <StackPanel HorizontalAlignment="Left" Orientation="Horizontal">
    <Button x:Name="btnPFAbort"   Content="Abort" Style="{StaticResource BtnGhost}"/>
  </StackPanel>
  <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
    <Button x:Name="btnPFProceed" Content="Proceed" Style="{StaticResource BtnPrimary}"/>
  </StackPanel>
</Grid>
"""

    _pf_win = ui.parse(
        u"Pre-flight — Mismatched Views",
        u"⚠  " + u"  \xb7  ".join(_pf_parts) + u" mismatch" + (u"es" if (_n_type + _n_scale) > 1 else u""),
        _PF_BODY, _PF_FOOTER,
        width=920, height=560,
        context=u"Tick the fix boxes you want to apply before the import runs. "
                u"Type/scale fixes delete the old dependent so Phase A recreates it from "
                u"the correct master."
    )
    _pf_win.FindName("dgPF").ItemsSource = _pf_rows

    from System.Windows import Visibility as _PFVis
    if _n_type:  _pf_win.FindName("chkFixType").Visibility  = _PFVis.Visible
    if _n_scale: _pf_win.FindName("chkFixScale").Visibility = _PFVis.Visible

    _pf_proceed = [False]

    def _on_pf_proceed(s, e, _w=_pf_win, _c=_pf_proceed):
        _c[0] = True
        _w.Close()

    _pf_win.FindName("btnPFProceed").Click += _on_pf_proceed
    _pf_win.FindName("btnPFAbort").Click   += lambda s, e, _w=_pf_win: _w.Close()

    # Capture checkbox state BEFORE closing the window (FindName fails after Close)
    _fix_type_chk  = _pf_win.FindName("chkFixType")
    _fix_scale_chk = _pf_win.FindName("chkFixScale")

    _pf_win.ShowDialog()

    if not _pf_proceed[0]:
        script.exit()

    _fix_type  = _n_type  > 0 and bool(_fix_type_chk.IsChecked)
    _fix_scale = _n_scale > 0 and bool(_fix_scale_chk.IsChecked)

    # ── Fix tx: delete master-mismatched views (type + scale combined) ──────
    _delete_ids = []
    if _fix_type:  _delete_ids += _pf_type_ids
    if _fix_scale: _delete_ids += _pf_scale_ids

    if _delete_ids:
        with revit.Transaction("Import — Delete master-mismatched views"):
            for _eid in _delete_ids:
                try:
                    doc.Delete(_eid)
                    if _eid in _pf_type_ids:  n_type_fixed  += 1
                    if _eid in _pf_scale_ids: n_scale_fixed += 1
                except Exception as _e:
                    fix_errors.append((str(_eid.IntegerValue),
                                       u"Delete failed: {}".format(_e)))

    # ── Rebuild stale indices if anything was deleted ───────────────────────
    if _delete_ids:
        view_by_name      = {}
        template_by_name  = {}
        dep_view_by_name  = {}
        for _v in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
            try:
                view_by_name[_v.Name] = _v
                if _v.IsTemplate:
                    template_by_name[_v.Name] = _v.Id
                else:
                    if _v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
                        dep_view_by_name[_v.Name] = _v
            except Exception:
                pass
        existing_proj_names = set(view_by_name.keys())
        viewport_by_view_id = {}
        for _vp in DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements():
            viewport_by_view_id[_vp.ViewId.IntegerValue] = _vp

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

# Maps JSON dep view_name → actual Revit view object, populated in Phase A.
# Phase B uses this so it still finds the view even when:
#   • Phase A matched by detail_id but the destination view has a different name
#   • Phase A renamed the created view to avoid a name collision ("Name (2)", etc.)
phase_a_lookup = {}

# Maps JSON dep view_name → (icon, action_label, detail) for the grouped results UI.
# Populated alongside phase_a_lookup so section 7 can show per-view Phase A outcome.
phase_a_result_by_view = {}   # dv_name → (icon_str, action_str, detail_str)

# 1/8" expressed in Revit internal units (feet). 1/8 in ÷ 12 in/ft = 1/96 ft.
ANNOTATION_CROP_OFFSET = 1.0 / 96.0

total_views = sum(len(mv["dependent_views"]) for mv in data["master_views"])
processed   = 0

sheets_data = data.get("sheets", [])

# ── Unload CD link "for me" before the import loop ──────────────────────────
# Matches Full Sync PRO behaviour: temporarily unload the linked model for the
# current user so Revit doesn't recompute link geometry on every view operation.
# Only done when importing into a building (link_transform is not None).
_cd_link_type      = None
_link_was_unloaded = False
if link_transform is not None:
    try:
        _cd_link_inst = link_by_name.get(chosen_link)
        if _cd_link_inst is not None:
            _cd_link_type = doc.GetElement(_cd_link_inst.GetTypeId())
            if _cd_link_type is not None:
                _cd_link_type.UnloadLocally(None)
                _link_was_unloaded = True
    except Exception:
        pass   # Non-critical — if unload fails, continue without it

with ui.ProgressBar(title=u"Import Sheets with Views", cancellable=True, step=5) as pb:
    pb.update_progress(0, total_views + len(sheets_data))

    # ── Phase A: Views ──
    with revit.Transaction("Import Sheets with Views — Views"):
        for view_data in data["master_views"]:
            view_name = view_data["view_name"]

            if view_name not in view_by_name:
                if master_map_loaded:
                    _mmj_hint = (u"master_map.json is present but '{}' was not remapped — "
                                 u"add an entry for it").format(view_name)
                else:
                    _mmj_hint = (u"no master_map.json found next to the JSON — "
                                 u"create one to remap source names to destination names")
                view_results.append((
                    "skip_master", view_name, u"—",
                    u"Master '{}' not in this model  •  {}".format(view_name, _mmj_hint)
                ))
                continue

            master_view = view_by_name[view_name]
            view_results.append(("master", view_name, "", ""))

            existing_by_name = {}
            for vid in master_view.GetDependentViewIds():
                dep = doc.GetElement(vid)
                if dep:
                    existing_by_name[dep.Name] = dep

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

                # Match by name only (see note at top: Detail ID is stamped, not matched).
                existing_view = existing_by_name.get(dv_name)

                processed += 1
                pb.title = u"Importing views — {}/{} — {}".format(
                    processed, total_views + len(sheets_data), view_name)
                pb.update_progress(processed, total_views + len(sheets_data))

                # Already exists (matched by name)
                if existing_view is not None:
                    phase_a_lookup[dv_name] = existing_view

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
                            _det = u"crop updated  •  matched by name"
                            view_results.append(("updated", view_name, existing_view.Name, _det))
                            phase_a_result_by_view[dv_name] = (u"🔄", u"updated", _det)
                            total_v_updated += 1
                        except Exception as e:
                            view_results.append(("error", view_name, dv_name, str(e)))
                            phase_a_result_by_view[dv_name] = (u"❌", u"error", str(e)[:80])
                    else:
                        _det = u"already exists, strategy = keep  •  matched by name"
                        view_results.append(("skipped", view_name, existing_view.Name, _det))
                        phase_a_result_by_view[dv_name] = (u"⏭️", u"kept", _det)
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

                    _rename_note = u" (renamed to '{}')".format(final_name) if final_name != dv_name else u""
                    _det = u"ID: {}  |  Title: {}{}".format(
                        dv_id or u"—", title_on_sheet or u"(no title)", _rename_note)
                    view_results.append(("created", view_name, final_name, _det))
                    phase_a_result_by_view[dv_name] = (u"✅", u"created", _det)
                    total_v_created += 1
                    existing_proj_names.add(final_name)
                    dep_view_by_name[final_name] = new_view   # keyed by final (possibly renamed) name
                    phase_a_lookup[dv_name]      = new_view   # keyed by original JSON name for Phase B

                except Exception as e:
                    view_results.append(("error", view_name, dv_name, str(e)))
                    phase_a_result_by_view[dv_name] = (u"❌", u"error", str(e)[:80])

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
    vp_moved   = 0
    vp_cleaned = 0   # Phase B.0 — viewports removed from sheets (view stays alive)
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
                                  u"No sheet with number '{}' in this model — "
                                  u"check prefix or create the sheet first".format(sheet_label)))
            continue

        target_sheet     = sheet_by_suffix[suffix]
        vp_id_to_det_num = {}
        original_numbers = {}   # VP id → detail number at sheet-start (populated by T0)
        vp_id_to_target_center = {}   # VP id → DB.XYZ target box center (for T3 alignment)
        vp_id_to_source_box = {}      # VP id → (view_name, src_min_x, src_min_y, src_max_x, src_max_y) for diagnostic

        # ══ B.0: clean orphan viewports ══════════════════════════════════════
        # Per-sheet strict: any viewport on this sheet whose view is NOT in this
        # sheet's JSON viewports gets removed. The View element itself is kept —
        # only the placement on this sheet goes away, so it remains available
        # for future imports onto other sheets.
        _json_view_ids_this_sheet = set()
        for _vp_entry in sheet_data["viewports"]:
            _tv = phase_a_lookup.get(_vp_entry["view_name"]) or dep_view_by_name.get(_vp_entry["view_name"])
            if _tv is not None:
                _json_view_ids_this_sheet.add(_tv.Id.IntegerValue)

        _orphan_vps = []
        for _vp in DB.FilteredElementCollector(doc, target_sheet.Id).OfClass(DB.Viewport).ToElements():
            if _vp.ViewId.IntegerValue not in _json_view_ids_this_sheet:
                _orphan_vps.append(_vp)

        if _orphan_vps:
            _b0 = DB.Transaction(doc, "Import Sheets with Views B0 - {}".format(sheet_label))
            try:
                _b0.Start()
                for _vp in _orphan_vps:
                    _vid_int  = _vp.ViewId.IntegerValue
                    _vp_id_int = _vp.Id.IntegerValue  # snapshot BEFORE delete
                    try:
                        _v_obj   = doc.GetElement(DB.ElementId(_vid_int))
                        _v_name  = _v_obj.Name if _v_obj else u"?"
                    except Exception:
                        _v_name  = u"?"
                    # Delete first; only the delete itself can fail the cleanup.
                    try:
                        doc.Delete(_vp.Id)
                    except Exception as _ce:
                        sheet_results.append(("error", sheet_label, _v_name,
                                              u"B.0 cleanup failed: {}".format(_ce)))
                        continue
                    # Delete succeeded → count as cleaned. Cache invalidation
                    # below must NOT touch the deleted Viewport element (.Id on
                    # a deleted element throws InvalidObjectException in some
                    # Revit versions). Compare integer values instead.
                    try:
                        _cached = viewport_by_view_id.get(_vid_int)
                        if _cached is not None and _cached.Id.IntegerValue == _vp_id_int:
                            del viewport_by_view_id[_vid_int]
                    except Exception:
                        viewport_by_view_id.pop(_vid_int, None)
                    vp_cleaned += 1
                    sheet_results.append(("cleaned", sheet_label, _v_name,
                                          u"✓ removed from sheet — view kept, available for future placement"))
                _b0.Commit()
            except Exception as _be:
                try:
                    if _b0.HasStarted() and not _b0.HasEnded():
                        _b0.RollBack()
                except Exception:
                    pass
                sheet_results.append(("error", sheet_label, u"B.0 rolled back", str(_be)))

        # ══ T0: free up the detail-number namespace ══════════════════════════
        # Move every existing VP on this sheet to a unique temp number in its
        # own committed transaction.  This guarantees a clean slate before T1
        # creates/places new VPs (Revit auto-assigns numbers to new VPs; those
        # auto-numbers can collide with the targets we'll set in T2 — but only
        # if the originals are still occupying those slots).
        # Skipped when DO_DET_NUMBER is False (numbers are never touched).
        if DO_DET_NUMBER:
            t0 = DB.Transaction(doc,
                                "Import Sheets with Views T0 - {}".format(sheet_label))
            try:
                t0.Start()
                _vps_t0 = list(DB.FilteredElementCollector(doc, target_sheet.Id)
                               .OfClass(DB.Viewport).ToElements())
                original_numbers = {sv.Id.IntegerValue: get_detail_number(sv)
                                    for sv in _vps_t0}
                _ts0 = str(int(time.time()))
                for _idx, sv in enumerate(_vps_t0):
                    set_detail_number(sv, "zzz{}_{}".format(_ts0, _idx))
                t0.Commit()
            except Exception as e:
                try:
                    if t0.HasStarted() and not t0.HasEnded():
                        t0.RollBack()
                except Exception:
                    pass
                sheet_results.append(("error", sheet_label, u"T0 rolled back", str(e)))
                continue

        # ══ T1: viewports + lines ═══════════════════════════════════════════
        # Creates, updates, or moves viewports.  Detail numbers are NOT touched
        # here — T0 already cleared the namespace; T2 will assign finals.
        t1 = DB.Transaction(doc, "Import Sheets with Views T1 - {}".format(sheet_label))
        try:
            t1.Start()

            for entry in sheet_data["viewports"]:
                view_name   = entry["view_name"]
                center      = DB.XYZ(entry["viewport_center_x"],
                                     entry["viewport_center_y"], 0)
                target_det  = entry.get("detail_number", "")

                # Prefer phase_a_lookup (keyed by original JSON name) — handles
                # cases where Phase A renamed the view or matched by detail_id
                # to a differently-named existing dep. Fall back to dep_view_by_name
                # for views that already existed and weren't in the JSON's master_views.
                target_view = phase_a_lookup.get(view_name) or dep_view_by_name.get(view_name)
                if target_view is None:
                    master_hint = view_name_to_master.get(view_name, u"unknown master")
                    sheet_results.append(("skipped", sheet_label, view_name,
                                          u"View not created — master '{}' not found in destination "
                                          u"(Phase A skipped it)".format(master_hint)))
                    continue

                try:
                    if target_view.Id.IntegerValue in viewport_by_view_id:
                        existing_vp = viewport_by_view_id[target_view.Id.IntegerValue]
                        if existing_vp.SheetId.IntegerValue == target_sheet.Id.IntegerValue:
                            # IMPORTANT order: ChangeTypeId → LabelOffset → title → SetBoxCenter LAST.
                            # SetBoxCenter must be the FINAL operation because LabelOffset (and to a
                            # lesser extent the type/title) can shift the box outline. Calling
                            # SetBoxCenter last guarantees the requested center is the final state.
                            if DO_VP_TYPE:
                                vt = entry.get("viewport_type", "")
                                if vt and vt in vp_type_by_name:
                                    try:
                                        existing_vp.ChangeTypeId(vp_type_by_name[vt])
                                    except Exception:
                                        pass
                            if DO_POSITION:
                                try:
                                    existing_vp.LabelOffset = DB.XYZ(
                                        entry.get("label_offset_x", 0),
                                        entry.get("label_offset_y", 0), 0)
                                except Exception:
                                    pass
                                _lll = entry.get("label_line_length")
                                if _lll is not None:
                                    try:
                                        existing_vp.LabelLineLength = _lll
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
                            if DO_POSITION:
                                existing_vp.SetBoxCenter(center)
                                vp_id_to_target_center[existing_vp.Id.IntegerValue] = center
                                vp_id_to_source_box[existing_vp.Id.IntegerValue] = (
                                    view_name,
                                    entry.get("box_min_x"), entry.get("box_min_y"),
                                    entry.get("box_max_x"), entry.get("box_max_y"),
                                    entry.get("label_min_x"), entry.get("label_min_y"),
                                    entry.get("label_max_x"), entry.get("label_max_y"),
                                    entry.get("title_on_sheet", u""),
                                    entry.get("detail_number", u""))
                            if DO_DET_NUMBER and target_det:
                                vp_id_to_det_num[existing_vp.Id.IntegerValue] = target_det
                            sheet_results.append(("updated", sheet_label, view_name,
                                                  u"det. {}".format(target_det) if target_det else u""))
                            vp_updated += 1
                        else:
                            # View is on a DIFFERENT sheet — move it here automatically.
                            try:
                                other_sheet = doc.GetElement(existing_vp.SheetId)
                                other_num   = other_sheet.SheetNumber if other_sheet else u"unknown"
                            except Exception:
                                other_num = u"unknown"
                            try:
                                doc.Delete(existing_vp.Id)
                                # Remove stale cache entry so it won't be found again
                                viewport_by_view_id.pop(target_view.Id.IntegerValue, None)
                                vp = DB.Viewport.Create(doc, target_sheet.Id, target_view.Id, center)
                                # Order: ChangeTypeId → LabelOffset → title → SetBoxCenter LAST.
                                if DO_VP_TYPE:
                                    vt = entry.get("viewport_type", "")
                                    if vt and vt in vp_type_by_name:
                                        try:
                                            vp.ChangeTypeId(vp_type_by_name[vt])
                                        except Exception:
                                            pass
                                if DO_POSITION:
                                    try:
                                        vp.LabelOffset = DB.XYZ(
                                            entry.get("label_offset_x", 0),
                                            entry.get("label_offset_y", 0), 0)
                                    except Exception:
                                        pass
                                    _lll = entry.get("label_line_length")
                                    if _lll is not None:
                                        try:
                                            vp.LabelLineLength = _lll
                                        except Exception:
                                            pass
                                title = entry.get("title_on_sheet", "")
                                if DO_TITLE and title:
                                    try:
                                        p = target_view.get_Parameter(
                                            DB.BuiltInParameter.VIEW_DESCRIPTION)
                                        if p and not p.IsReadOnly:
                                            p.Set(title)
                                    except Exception:
                                        pass
                                if DO_POSITION:
                                    vp.SetBoxCenter(center)
                                    vp_id_to_target_center[vp.Id.IntegerValue] = center
                                    vp_id_to_source_box[vp.Id.IntegerValue] = (
                                        view_name,
                                        entry.get("box_min_x"), entry.get("box_min_y"),
                                        entry.get("box_max_x"), entry.get("box_max_y"),
                                        entry.get("label_min_x"), entry.get("label_min_y"),
                                        entry.get("label_max_x"), entry.get("label_max_y"),
                                        entry.get("title_on_sheet", u""),
                                        entry.get("detail_number", u""))
                                if DO_DET_NUMBER and target_det:
                                    vp_id_to_det_num[vp.Id.IntegerValue] = target_det
                                sheet_results.append(("moved", sheet_label, view_name,
                                                      u"🚚 relocated from sheet {}{}".format(
                                                          other_num,
                                                          u"  •  det. {}".format(target_det) if target_det else u"")))
                                vp_moved += 1
                            except Exception as e:
                                sheet_results.append(("error", sheet_label, view_name,
                                                      u"Failed to move from sheet {}: {}".format(other_num, str(e))))
                    else:
                        vp = DB.Viewport.Create(doc, target_sheet.Id, target_view.Id, center)
                        # Order: ChangeTypeId → LabelOffset → title → SetBoxCenter LAST.
                        if DO_VP_TYPE:
                            vt = entry.get("viewport_type", "")
                            if vt and vt in vp_type_by_name:
                                try:
                                    vp.ChangeTypeId(vp_type_by_name[vt])
                                except Exception:
                                    pass
                        if DO_POSITION:
                            try:
                                vp.LabelOffset = DB.XYZ(
                                    entry.get("label_offset_x", 0),
                                    entry.get("label_offset_y", 0), 0)
                            except Exception:
                                pass
                            _lll = entry.get("label_line_length")
                            if _lll is not None:
                                try:
                                    vp.LabelLineLength = _lll
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
                        if DO_POSITION:
                            vp.SetBoxCenter(center)
                            vp_id_to_target_center[vp.Id.IntegerValue] = center
                            vp_id_to_source_box[vp.Id.IntegerValue] = (
                                view_name,
                                entry.get("box_min_x"), entry.get("box_min_y"),
                                entry.get("box_max_x"), entry.get("box_max_y"),
                                entry.get("label_min_x"), entry.get("label_min_y"),
                                entry.get("label_max_x"), entry.get("label_max_y"),
                                entry.get("title_on_sheet", u""),
                                entry.get("detail_number", u""))
                        if DO_DET_NUMBER and target_det:
                            vp_id_to_det_num[vp.Id.IntegerValue] = target_det
                        sheet_results.append(("created", sheet_label, view_name,
                                              u"det. {}".format(target_det) if target_det else u""))
                        vp_created += 1

                except Exception as e:
                    sheet_results.append(("error", sheet_label, view_name, str(e)))

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

        # ══ T2: assign final detail numbers ══════════════════════════════════
        # T0 cleared the namespace; T1 placed VPs (Revit auto-assigned numbers).
        # Now assign the targets from vp_id_to_det_num and restore original
        # numbers to any pre-existing VP that wasn't given a new target.
        if DO_DET_NUMBER and vp_id_to_det_num:
            t2 = DB.Transaction(doc, "Import Sheets with Views T2 - {}".format(sheet_label))
            try:
                t2.Start()
                all_sheet_vps_t2 = list(DB.FilteredElementCollector(doc, target_sheet.Id)
                                        .OfClass(DB.Viewport).ToElements())
                ts2 = str(int(time.time())) + "b"
                for idx, sv in enumerate(all_sheet_vps_t2):
                    set_detail_number(sv, "zzz{}_{}".format(ts2, idx))
                for sv in all_sheet_vps_t2:
                    vid     = sv.Id.IntegerValue
                    det_num = vp_id_to_det_num.get(vid)
                    if det_num:
                        set_detail_number(sv, det_num)
                    elif vid in original_numbers:
                        # Pre-existing VP not getting a new target — restore its
                        # original number (same pattern as Apply Detail Numbers).
                        orig = original_numbers[vid]
                        if orig:
                            set_detail_number(sv, orig)
                t2.Commit()
            except Exception as e:
                try:
                    if t2.HasStarted() and not t2.HasEnded():
                        t2.RollBack()
                except Exception:
                    pass
                sheet_results.append(("error", sheet_label, u"T2 rolled back", str(e)))

        # ══ T3: self-correcting alignment pass ═══════════════════════════════
        # The destination viewport's BoxOutline often differs slightly in size
        # from the source's, due to subtle differences between the dependent
        # views' masters across buildings (rendering, template, etc). Aligning
        # by box CENTER (T1's SetBoxCenter target) leaves the visible content
        # offset within the box. The canonical workflow reference is the title's
        # bottom-right corner — aligning that snaps the crop region's right edge
        # and the title position to source's, so the content lines up with the
        # detail lines drawn at world coords on the sheet.
        #
        # Anchor: crop-box bottom-LEFT corner (box_min_x, box_min_y).
        # Older versions anchored on label_max_x via GetLabelOutline().MaximumPoint.X.
        # But when a viewport title has a LINE spanning the box width, the label
        # outline returns the LINE's extent (not the text); since the box differs
        # building-to-building, the X anchor drifts and scrambles the sheet
        # (e.g. Non-Structural Framing Details). box_min_x is immune to the title
        # line/text width, and for plain-text titles it yields the same delta as
        # before (LabelOffset is copied in T1 and cancels).
        # Fallback: box center (for old JSON without box outline data).
        if vp_id_to_target_center:
            t3 = DB.Transaction(doc, "Import Sheets with Views T3 - align {}".format(sheet_label))
            try:
                t3.Start()
                TOL = 1.0 / 4096.0   # ~0.003" — anything below is sub-pixel noise
                for _vp_id_int, _target in vp_id_to_target_center.items():
                    _vp = doc.GetElement(DB.ElementId(_vp_id_int))
                    if _vp is None:
                        continue
                    _src = vp_id_to_source_box.get(_vp_id_int)
                    # Determine alignment target
                    if _src and _src[1] is not None:
                        # New JSON with box outline data — anchor on box bottom-left
                        _tgt_x = _src[1]   # box_min_x
                        _tgt_y = _src[2]   # box_min_y
                        try:
                            _d_box = _vp.GetBoxOutline()
                            _cur_x = _d_box.MinimumPoint.X
                            _cur_y = _d_box.MinimumPoint.Y
                        except Exception:
                            continue
                    else:
                        # Old JSON — fall back to box-center anchoring
                        try:
                            _bc = _vp.GetBoxCenter()
                            _tgt_x, _tgt_y = _target.X, _target.Y
                            _cur_x, _cur_y = _bc.X, _bc.Y
                        except Exception:
                            continue
                    _delta_x = _tgt_x - _cur_x
                    _delta_y = _tgt_y - _cur_y
                    if abs(_delta_x) < TOL and abs(_delta_y) < TOL:
                        continue
                    try:
                        ElementTransformUtils.MoveElement(
                            doc, _vp.Id, DB.XYZ(_delta_x, _delta_y, 0))
                    except Exception:
                        pass
                t3.Commit()
            except Exception as e:
                try:
                    if t3.HasStarted() and not t3.HasEnded():
                        t3.RollBack()
                except Exception:
                    pass
                sheet_results.append(("error", sheet_label, u"T3 rolled back", str(e)))

# ── Reload CD link after import ──────────────────────────────────────────────
_reload_succeeded = False
if _link_was_unloaded and _cd_link_type is not None:
    try:
        _cd_link_type.Load(None)
        _reload_succeeded = True
    except Exception:
        pass   # Non-critical — user can reload manually from Manage Links

# ─────────────────────────────────────────────────────────────────────────────
# 7. Noir results window — grouped by sheet (collapsible), issues section on top
# ─────────────────────────────────────────────────────────────────────────────

import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('PresentationCore')
from System.Windows import Visibility, Thickness
from System.Windows.Media import SolidColorBrush, Color
from System.Windows.Forms import Clipboard as WinFormsClipboard
from System.Collections.ObjectModel import ObservableCollection


# ── Data classes ──────────────────────────────────────────────────────────────

class _VPRow(object):
    """One viewport row inside a sheet expander."""
    _PB_ICONS = {
        "created": u"📄+", "updated": u"📄↺",
        "moved":   u"🚚",  "cleaned": u"🗑",
        "skipped": u"⏭️", "error": u"❌",
    }
    def __init__(self, view_name, pa_icon, pa_action, pb_status, pb_detail,
                 is_moved=False, is_cleaned=False):
        self._view_name  = view_name
        self._pa_icon    = pa_icon          # Phase A icon (✅/🔄/⏭️/❌/—)
        self._pa_action  = pa_action        # "created" / "kept" / "updated" / "error" / "—"
        self._pb_status  = pb_status        # "created" / "updated" / "moved" / "cleaned" / "skipped" / "error"
        self._pb_detail  = pb_detail
        self._is_moved   = is_moved
        self._is_cleaned = is_cleaned

    @property
    def ViewName(self):  return self._view_name
    @property
    def PhaseA(self):    return u"{}  {}".format(self._pa_icon, self._pa_action)
    @property
    def PhaseB(self):    return u"{}  {}".format(self._PB_ICONS.get(self._pb_status, u"·"), self._pb_detail)
    @property
    def IsMoved(self):   return self._is_moved
    @property
    def IsCleaned(self): return self._is_cleaned


class _SheetGroup(object):
    """One sheet expander in the import results."""
    def __init__(self, sheet_label, sheet_name, rows, sheet_status="ok", sheet_detail="",
                 is_open=False):
        self._label       = sheet_label
        self._name        = sheet_name
        self._rows        = rows
        self._status      = sheet_status   # "ok" | "not_found" | "error" | "partial"
        self._detail      = sheet_detail
        self._is_open     = is_open

    @property
    def SheetLabel(self):   return self._label
    @property
    def SheetName(self):    return self._name
    @property
    def StatusIcon(self):
        return {
            "ok":        u"✅", "partial": u"⚠️",
            "not_found": u"❌", "error":   u"❌",
        }.get(self._status, u"·")
    @property
    def Count(self):
        n = len(self._rows)
        return u"{} view{}".format(n, u"s" if n != 1 else u"")
    @property
    def Detail(self):       return self._detail
    @property
    def IsOpen(self):       return self._is_open
    @property
    def Rows(self):         return self._rows


class _IssueRow(object):
    """Issues not tied to a specific sheet (skip_master, view errors)."""
    def __init__(self, icon, subject, detail):
        self._icon    = icon
        self._subject = subject
        self._detail  = detail
    @property
    def Icon(self):    return self._icon
    @property
    def Subject(self): return self._subject
    @property
    def Detail(self):  return self._detail


# ── Build grouped data ────────────────────────────────────────────────────────

# Build sheet_results lookup: (sheet_label, view_name) → (status, detail)
_pb_by_sheet_view = {}
_sheet_lines      = {}   # sheet_label → "lines redrawn" marker
_cleaned_by_sheet = {}   # sheet_label → [(view_name, detail), ...]
for _st, _sl, _vn, _det in sheet_results:
    if _st == "lines":
        _sheet_lines[_sl] = u"lines redrawn"
    elif _st == "cleaned":
        _cleaned_by_sheet.setdefault(_sl, []).append((_vn, _det))
    else:
        _pb_by_sheet_view[(_sl, _vn)] = (_st, _det)

# Sheets not found (sheet_results entries with view_name == "—")
_not_found_sheets = {_sl for _st, _sl, _vn, _det in sheet_results
                     if _st == "skipped" and _vn == u"—"}

# Build ObservableCollections for the UI
sheet_groups_oc = ObservableCollection[_SheetGroup]()

for sh in sheets_data:
    suffix      = sh["sheet_number"][2:] if len(sh["sheet_number"]) > 2 else sh["sheet_number"]
    sheet_label = u"{}{}".format(dest_prefix, suffix)
    sheet_name  = sh.get("sheet_name", "")

    if sheet_label in _not_found_sheets:
        _det_for_sheet = next(
            (_det for _st, _sl, _vn, _det in sheet_results
             if _sl == sheet_label and _vn == u"—"), u"Sheet not found")
        sheet_groups_oc.Add(_SheetGroup(
            sheet_label, sheet_name,
            ObservableCollection[_VPRow](),
            sheet_status="not_found",
            sheet_detail=_det_for_sheet))
        continue

    rows_oc      = ObservableCollection[_VPRow]()
    sheet_ok     = True

    for vp_entry in sh.get("viewports", []):
        vn = vp_entry["view_name"]
        pa_icon, pa_action, _pa_det = phase_a_result_by_view.get(vn, (u"—", u"—", u""))
        pb_status, pb_detail        = _pb_by_sheet_view.get((sheet_label, vn), ("skipped", u"not processed"))
        is_moved = (pb_status == "moved")

        if pb_status in ("error", "skipped") or pa_action == "error":
            sheet_ok = False

        # Friendly Phase B label
        _pb_labels = {
            "created": u"VP placed",
            "updated": u"VP updated",
            "moved":   pb_detail,      # already has "moved from Xnn" text
            "skipped": pb_detail,
            "error":   pb_detail,
        }
        pb_display = _pb_labels.get(pb_status, pb_detail)

        rows_oc.Add(_VPRow(vn, pa_icon, pa_action, pb_status, pb_display, is_moved))

    # Append cleaned viewports (removed by Phase B.0) as extra rows so they show up
    for _cleaned_name, _cleaned_det in _cleaned_by_sheet.get(sheet_label, []):
        rows_oc.Add(_VPRow(_cleaned_name, u"—", u"—",
                           "cleaned", _cleaned_det,
                           is_moved=False, is_cleaned=True))

    _lines_note = _sheet_lines.get(sheet_label, "")
    sheet_groups_oc.Add(_SheetGroup(
        sheet_label, sheet_name, rows_oc,
        sheet_status="ok" if sheet_ok else "partial",
        sheet_detail=_lines_note))

# Issues: skip_master + view errors not tied to sheet + name fix errors
issues_oc = ObservableCollection[_IssueRow]()
for _st, _master, _vn, _det in view_results:
    if _st == "skip_master":
        issues_oc.Add(_IssueRow(u"❌", u"Master not found: {}".format(_master), _det))
    elif _st == "error":
        issues_oc.Add(_IssueRow(u"❌", u"View error: {}  /  {}".format(_master, _vn), _det))

for _nfe_subject, _nfe_detail in fix_errors:
    issues_oc.Add(_IssueRow(u"⚠", u"Fix: {}".format(_nfe_subject), _nfe_detail))


# ── Subtitle & counts ─────────────────────────────────────────────────────────

n_v_errors = sum(1 for s, _, _, _ in view_results  if s == "error")
n_s_errors = sum(1 for s, _, _, _ in sheet_results if s == "error")
n_errors   = n_v_errors + n_s_errors

subtitle = (u"{} views created  \xb7  {} updated  \xb7  {} skipped"
            u"  \xb7  {} VPs  \xb7  {} moved").format(
    total_v_created, total_v_updated, total_v_skipped,
    vp_created + vp_updated, vp_moved)
if vp_cleaned:
    subtitle += u"  \xb7  {} cleaned".format(vp_cleaned)
if n_type_fixed or n_scale_fixed:
    subtitle += u"  \xb7  {} fixed".format(n_type_fixed + n_scale_fixed)
if n_errors:
    subtitle += u"  \xb7  {} error{}".format(n_errors, u"s" if n_errors != 1 else u"")
if master_map_info:
    subtitle += u"  \xb7  " + master_map_info
if cancelled:
    subtitle += u"  \xb7  ⚠ partial"

# ── Pre-flight view-name sets (used by badge filters) ────────────────────────
_type_fixed_names  = {r._view_name for r in _pf_rows if r._issue == u"Type changed"}
_scale_fixed_names = {r._view_name for r in _pf_rows if r._issue == u"Scale changed"}

_BODY_XAML = u"""
<Grid>
  <Grid.Resources>

    <!-- Dark Expander — same template as Export results window -->
    <Style TargetType="Expander">
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Expander">
            <StackPanel>
              <ToggleButton x:Name="hdr"
                            IsChecked="{Binding IsExpanded, Mode=TwoWay,
                                        RelativeSource={RelativeSource TemplatedParent}}"
                            Content="{TemplateBinding Header}">
                <ToggleButton.Template>
                  <ControlTemplate TargetType="ToggleButton">
                    <Border x:Name="hdrBorder" Background="#1A1B2E"
                            BorderBrush="#2A2D47" BorderThickness="0,0,0,1"
                            Padding="12,9" Cursor="Hand">
                      <Grid>
                        <Grid.ColumnDefinitions>
                          <ColumnDefinition Width="16"/>
                          <ColumnDefinition Width="*"/>
                        </Grid.ColumnDefinitions>
                        <TextBlock x:Name="arrow" Grid.Column="0"
                                   Text="&#x25B6;" Foreground="#4A4F70"
                                   FontSize="9" VerticalAlignment="Center"
                                   HorizontalAlignment="Center"/>
                        <ContentPresenter Grid.Column="1" VerticalAlignment="Center"/>
                      </Grid>
                    </Border>
                    <ControlTemplate.Triggers>
                      <Trigger Property="IsChecked" Value="True">
                        <Setter TargetName="arrow"     Property="Text"       Value="&#x25BC;"/>
                        <Setter TargetName="arrow"     Property="Foreground" Value="#9099C8"/>
                        <Setter TargetName="hdrBorder" Property="Background" Value="#1E2235"/>
                      </Trigger>
                      <Trigger Property="IsMouseOver" Value="True">
                        <Setter TargetName="hdrBorder" Property="Background" Value="#1E2235"/>
                      </Trigger>
                    </ControlTemplate.Triggers>
                  </ControlTemplate>
                </ToggleButton.Template>
              </ToggleButton>
              <ContentPresenter x:Name="body" Visibility="Collapsed"/>
            </StackPanel>
            <ControlTemplate.Triggers>
              <Trigger Property="IsExpanded" Value="True">
                <Setter TargetName="body" Property="Visibility" Value="Visible"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

  </Grid.Resources>

  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="*"/>
  </Grid.RowDefinitions>

  <!-- Badge row — filterable badges have Cursor="Hand"; click to filter the sheet list.
       WrapPanel auto-wraps to a second row when badges don't fit horizontally. -->
  <WrapPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,12">
    <Border x:Name="badgeVCreatedBorder" Background="#122E1C" BorderBrush="#50E898"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,6"
            Cursor="Hand">
      <TextBlock x:Name="badgeVCreated" Foreground="#50E898" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeVUpdatedBorder" Background="#142244" BorderBrush="#7EB4F0"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,6"
            Cursor="Hand">
      <TextBlock x:Name="badgeVUpdated" Foreground="#7EB4F0" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeVSkippedBorder" Background="#1E2740" BorderBrush="#6B7394"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,6"
            Cursor="Hand">
      <TextBlock x:Name="badgeVSkipped" Foreground="#6B7394" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeSVPBorder" Background="#1A2535" BorderBrush="#5B8EC4"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,6"
            Cursor="Hand">
      <TextBlock x:Name="badgeSVP" Foreground="#5B8EC4" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeMovedBorder" Background="#2A1800" BorderBrush="#E87E20"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,6"
            Cursor="Hand" Visibility="Collapsed">
      <TextBlock x:Name="badgeMoved" Foreground="#E87E20" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeLinesBorder" Background="#0F3038" BorderBrush="#5ED4E6"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,6"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeLines" Foreground="#5ED4E6" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeIdBorder" Background="#4A3810" BorderBrush="#FFCC66"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,6"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeIds" Foreground="#FFCC66" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeTypeFixedBorder" Background="#2E0F0F" BorderBrush="#FF6B6B"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,6"
            Cursor="Hand" Visibility="Collapsed">
      <TextBlock x:Name="badgeTypeFixed" Foreground="#FF6B6B" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeScaleFixedBorder" Background="#2E1F0A" BorderBrush="#F0A050"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,6"
            Cursor="Hand" Visibility="Collapsed">
      <TextBlock x:Name="badgeScaleFixed" Foreground="#F0A050" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeVPCleanedBorder" Background="#0F1F2E" BorderBrush="#7AB8E0"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,6"
            Cursor="Hand" Visibility="Collapsed">
      <TextBlock x:Name="badgeVPCleaned" Foreground="#7AB8E0" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeErrorBorder" Background="#3C1212" BorderBrush="#FF7070"
            BorderThickness="1" CornerRadius="4" Padding="10,4"
            Cursor="Hand" Visibility="Collapsed">
      <TextBlock x:Name="badgeErrors" Foreground="#FF7070" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeLinkBorder" BorderThickness="1" CornerRadius="4"
            Padding="10,4" Margin="0,0,8,6" Visibility="Collapsed">
      <TextBlock x:Name="badgeLink" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
  </WrapPanel>

  <!-- Issues section (skip_master + view errors) — only shown when there are issues -->
  <Border Grid.Row="1" x:Name="issuesBorder" Visibility="Collapsed" Margin="0,0,0,8">
    <Expander x:Name="issuesExpander" IsExpanded="True">
      <Expander.Header>
        <StackPanel Orientation="Horizontal">
          <Border Background="#3C1212" BorderBrush="#FF7070" BorderThickness="1"
                  CornerRadius="4" Padding="8,3" Margin="0,0,10,0">
            <TextBlock x:Name="issuesHeader" Foreground="#FF7070"
                       FontFamily="Segoe UI" FontSize="12"/>
          </Border>
          <TextBlock Text="Click to expand details" Foreground="#6B7394"
                     FontFamily="Segoe UI" FontSize="12" VerticalAlignment="Center"/>
        </StackPanel>
      </Expander.Header>
      <ItemsControl x:Name="icIssues">
        <ItemsControl.ItemTemplate>
          <DataTemplate>
            <Border Background="#1A0E0E" BorderBrush="#2A2D47" BorderThickness="0,0,0,1"
                    Padding="44,0,16,0">
              <Grid MinHeight="26">
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="22"/>
                  <ColumnDefinition Width="260"/>
                  <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <TextBlock Grid.Column="0" Text="{Binding Icon}"
                           FontFamily="Segoe UI" FontSize="11" VerticalAlignment="Center"/>
                <TextBlock Grid.Column="1" Text="{Binding Subject}"
                           Foreground="#E8EBF5" FontFamily="Segoe UI" FontSize="12"
                           VerticalAlignment="Center" Margin="0,0,8,0"
                           TextTrimming="CharacterEllipsis"/>
                <TextBlock Grid.Column="2" Text="{Binding Detail}"
                           Foreground="#9099C8" FontFamily="Segoe UI" FontSize="11"
                           VerticalAlignment="Center" TextWrapping="Wrap"/>
              </Grid>
            </Border>
          </DataTemplate>
        </ItemsControl.ItemTemplate>
      </ItemsControl>
    </Expander>
  </Border>

  <!-- Sheet list -->
  <ScrollViewer Grid.Row="2" VerticalScrollBarVisibility="Auto">
    <ItemsControl x:Name="icSheets">
      <ItemsControl.ItemTemplate>
        <DataTemplate>
          <Expander Margin="0,0,0,2" IsExpanded="{Binding IsOpen}">

            <!-- Sheet header -->
            <Expander.Header>
              <StackPanel Orientation="Horizontal">
                <TextBlock Text="{Binding StatusIcon}" FontFamily="Segoe UI" FontSize="12"
                           VerticalAlignment="Center" Margin="0,0,8,0"/>
                <Border Background="#0F3038" BorderBrush="#5ED4E6" BorderThickness="1"
                        CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
                  <TextBlock Text="{Binding SheetLabel}" Foreground="#5ED4E6"
                             FontFamily="Consolas" FontSize="12"/>
                </Border>
                <TextBlock Text="{Binding SheetName}" Foreground="#E8EBF5"
                           FontFamily="Segoe UI" FontSize="13"
                           VerticalAlignment="Center" Margin="0,0,12,0"/>
                <Border Background="#1A1D30" CornerRadius="4" Padding="7,2" Margin="0,0,8,6">
                  <TextBlock Text="{Binding Count}" Foreground="#6B7394"
                             FontFamily="Segoe UI" FontSize="11"/>
                </Border>
                <TextBlock Text="{Binding Detail}" Foreground="#6B7394"
                           FontFamily="Segoe UI" FontSize="11"
                           VerticalAlignment="Center"/>
              </StackPanel>
            </Expander.Header>

            <!-- Viewport rows (Phase A + Phase B columns) -->
            <ItemsControl ItemsSource="{Binding Rows}">
              <ItemsControl.ItemTemplate>
                <DataTemplate>
                  <Border BorderBrush="#2A2D47" BorderThickness="0,0,0,1"
                          Padding="44,0,16,0">
                    <Border.Style>
                      <Style TargetType="Border">
                        <Setter Property="Background" Value="#12131F"/>
                        <Style.Triggers>
                          <DataTrigger Binding="{Binding IsMoved}" Value="True">
                            <Setter Property="Background"      Value="#1C1200"/>
                            <Setter Property="BorderBrush"     Value="#E87E20"/>
                            <Setter Property="BorderThickness" Value="3,0,0,1"/>
                          </DataTrigger>
                          <DataTrigger Binding="{Binding IsCleaned}" Value="True">
                            <Setter Property="Background"      Value="#0E1A24"/>
                            <Setter Property="BorderBrush"     Value="#7AB8E0"/>
                            <Setter Property="BorderThickness" Value="3,0,0,1"/>
                          </DataTrigger>
                        </Style.Triggers>
                      </Style>
                    </Border.Style>
                    <Grid MinHeight="26">
                      <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="160"/>
                        <ColumnDefinition Width="200"/>
                      </Grid.ColumnDefinitions>
                      <TextBlock Grid.Column="0" Text="{Binding ViewName}"
                                 Foreground="#E8EBF5" FontFamily="Segoe UI" FontSize="12"
                                 VerticalAlignment="Center" Margin="0,0,8,0"
                                 TextTrimming="CharacterEllipsis"/>
                      <TextBlock Grid.Column="1" Text="{Binding PhaseA}"
                                 Foreground="#9099C8" FontFamily="Segoe UI" FontSize="12"
                                 VerticalAlignment="Center" Margin="0,0,8,0"/>
                      <TextBlock Grid.Column="2" Text="{Binding PhaseB}"
                                 Foreground="#E8EBF5" FontFamily="Segoe UI" FontSize="12"
                                 VerticalAlignment="Center"
                                 TextTrimming="CharacterEllipsis"/>
                    </Grid>
                  </Border>
                </DataTemplate>
              </ItemsControl.ItemTemplate>
            </ItemsControl>

          </Expander>
        </DataTemplate>
      </ItemsControl.ItemTemplate>
    </ItemsControl>
  </ScrollViewer>
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

win = ui.parse(u"Import Sheets with Views", subtitle, _BODY_XAML, _FOOTER_XAML,
               width=1020, height=640)

win.FindName("badgeVCreated").Text = u"✅  {} views created".format(total_v_created)
win.FindName("badgeVUpdated").Text = u"🔄  {} updated".format(total_v_updated)
win.FindName("badgeVSkipped").Text = u"⏭️  {} kept".format(total_v_skipped)
win.FindName("badgeSVP").Text      = u"\U0001f4c4  {} VPs".format(vp_created + vp_updated)

if vp_moved:
    win.FindName("badgeMovedBorder").Visibility = Visibility.Visible
    win.FindName("badgeMoved").Text = u"🚚  {} moved".format(vp_moved)
if dl_created:
    # Count distinct sheets that had their detail lines redrawn (more useful
    # than total line count — the per-sheet expander has the line counts).
    _sheets_with_lines = len({_sl for _st, _sl, _vn, _det in sheet_results
                              if _st == "lines"})
    win.FindName("badgeLinesBorder").Visibility = Visibility.Visible
    win.FindName("badgeLines").Text = u"📐  lines redrawn on {} sheet{}".format(
        _sheets_with_lines, u"s" if _sheets_with_lines != 1 else u"")
if total_id_stamped:
    win.FindName("badgeIdBorder").Visibility = Visibility.Visible
    win.FindName("badgeIds").Text = u"🔖  {} IDs stamped".format(total_id_stamped)
if n_type_fixed:
    win.FindName("badgeTypeFixedBorder").Visibility = Visibility.Visible
    win.FindName("badgeTypeFixed").Text = u"⛔  {} type-fixed".format(n_type_fixed)
if n_scale_fixed:
    win.FindName("badgeScaleFixedBorder").Visibility = Visibility.Visible
    win.FindName("badgeScaleFixed").Text = u"⚠  {} scale-fixed".format(n_scale_fixed)
if vp_cleaned:
    win.FindName("badgeVPCleanedBorder").Visibility = Visibility.Visible
    win.FindName("badgeVPCleaned").Text = u"🧹  {} VPs cleaned".format(vp_cleaned)
if n_errors:
    win.FindName("badgeErrorBorder").Visibility = Visibility.Visible
    win.FindName("badgeErrors").Text = u"❌  {} error{}".format(
        n_errors, u"s" if n_errors != 1 else u"")
if _link_was_unloaded:
    _lb = win.FindName("badgeLinkBorder")
    _lt = win.FindName("badgeLink")
    _lb.Visibility = Visibility.Visible
    if _reload_succeeded:
        _lb.Background  = SolidColorBrush(Color.FromRgb(0x0F, 0x28, 0x1C))
        _lb.BorderBrush = SolidColorBrush(Color.FromRgb(0x50, 0xC8, 0x78))
        _lt.Foreground  = SolidColorBrush(Color.FromRgb(0x50, 0xC8, 0x78))
        _lt.Text = u"🔗  CD link reloaded"
    else:
        _lb.Background  = SolidColorBrush(Color.FromRgb(0x2E, 0x1F, 0x0A))
        _lb.BorderBrush = SolidColorBrush(Color.FromRgb(0xF0, 0xA0, 0x50))
        _lt.Foreground  = SolidColorBrush(Color.FromRgb(0xF0, 0xA0, 0x50))
        _lt.Text = u"⚠  CD link unloaded — reload from Manage Links"

win.FindName("icSheets").ItemsSource = sheet_groups_oc

# ── Badge filter logic ────────────────────────────────────────────────────────

_all_groups    = sheet_groups_oc   # permanent reference for reset
_active_filter = [None]            # mutable list so closures can write to it

_FILTER_PREDICATES = {
    "created":     lambda r: r._pa_action == "created",
    "updated":     lambda r: r._pa_action == "updated",
    "kept":        lambda r: r._pa_action == "kept",
    "vp":          lambda r: r._pb_status in ("created", "updated"),
    "moved":       lambda r: r._is_moved,
    "cleaned":     lambda r: r._is_cleaned,
    "error":       lambda r: r._pa_action == "error" or r._pb_status == "error",
    "type_fixed":  lambda r: r._view_name in _type_fixed_names,
    "scale_fixed": lambda r: r._view_name in _scale_fixed_names,
}

_BADGE_BORDERS = [
    ("badgeVCreatedBorder",   "created"),
    ("badgeVUpdatedBorder",   "updated"),
    ("badgeVSkippedBorder",   "kept"),
    ("badgeSVPBorder",        "vp"),
    ("badgeMovedBorder",      "moved"),
    ("badgeVPCleanedBorder",  "cleaned"),
    ("badgeErrorBorder",      "error"),
    ("badgeTypeFixedBorder",  "type_fixed"),
    ("badgeScaleFixedBorder", "scale_fixed"),
]

def _apply_filter(filter_key):
    ic = win.FindName("icSheets")
    if _active_filter[0] == filter_key:
        # Toggle off — show all
        _active_filter[0] = None
        ic.ItemsSource = _all_groups
        for bname, _ in _BADGE_BORDERS:
            b = win.FindName(bname)
            if b is not None:
                b.Opacity = 1.0
                b.BorderThickness = Thickness(1)
        return
    # Apply filter
    _active_filter[0] = filter_key
    pred = _FILTER_PREDICATES[filter_key]
    filtered = ObservableCollection[_SheetGroup]()
    for sg in _all_groups:
        matching = ObservableCollection[_VPRow]()
        for row in sg.Rows:
            if pred(row):
                matching.Add(row)
        if len(matching) > 0:
            filtered.Add(_SheetGroup(sg.SheetLabel, sg.SheetName, matching,
                                     sg._status, sg._detail, is_open=True))
    ic.ItemsSource = filtered
    # Visual feedback
    for bname, key in _BADGE_BORDERS:
        b = win.FindName(bname)
        if b is None:
            continue
        if key == filter_key:
            b.Opacity = 1.0
            b.BorderThickness = Thickness(2)
        else:
            b.Opacity = 0.45
            b.BorderThickness = Thickness(1)

for _bname, _fkey in _BADGE_BORDERS:
    _b = win.FindName(_bname)
    if _b is not None:
        _b.MouseLeftButtonUp += (lambda s, e, k=_fkey: _apply_filter(k))

if len(issues_oc) > 0:
    win.FindName("issuesBorder").Visibility = Visibility.Visible
    win.FindName("issuesHeader").Text = u"⚠  {} issue{}".format(
        len(issues_oc), u"s" if len(issues_oc) != 1 else u"")
    win.FindName("icIssues").ItemsSource = issues_oc


def on_copy(s, e):
    lines_out = [u"Import Sheets with Views — " + subtitle, u""]
    if len(issues_oc) > 0:
        lines_out.append(u"── ISSUES ──")
        for iss in issues_oc:
            lines_out.append(u"  {}  {}  |  {}".format(iss.Icon, iss.Subject, iss.Detail))
        lines_out.append(u"")
    for sg in sheet_groups_oc:
        lines_out.append(u"{}  {}  {}  ({}){}".format(
            sg.StatusIcon, sg.SheetLabel, sg.SheetName, sg.Count,
            u"  " + sg.Detail if sg.Detail else u""))
        for vr in sg.Rows:
            lines_out.append(u"    {}  |  {}  |  {}".format(
                vr.ViewName, vr.PhaseA, vr.PhaseB))
        lines_out.append(u"")
    WinFormsClipboard.SetText(u"\n".join(lines_out))
    s.Content = u"Copied ✓"


win.FindName("btnCopy").Click += on_copy
win.FindName("btnOK").Click   += lambda s, e: win.Close()
win.ShowDialog()
