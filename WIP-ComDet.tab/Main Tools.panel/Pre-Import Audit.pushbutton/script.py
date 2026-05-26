# -*- coding: utf-8 -*-
__title__ = u'Pre-Import\nAudit'
__doc__ = (u'Pre-import gate between Export and Import Sheets with Views. '
           u'Compares a Sheets with Views JSON against the destination model: '
           u'reports missing sheets (with a Create action that materialises them '
           u'from a reference sheet), views not in model, views not placed, '
           u'removed-or-moved viewports, detail number mismatches, and viewport '
           u'type mismatches.')

import sys
import os as _os
import json
from pyrevit import revit, DB, script, forms

sys.path.append(_os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))))),
    'lib'
))
from magictools import ui

import clr
clr.AddReference('PresentationCore')
clr.AddReference('System.Windows.Forms')
from System.Windows import Visibility
from System.Windows.Forms import Clipboard as WinFormsClipboard
from System.Collections.ObjectModel import ObservableCollection
from System.Collections.Generic import List as DotNetList
from Autodesk.Revit.DB import (
    ElementTransformUtils, CopyPasteOptions, Transform,
    StorageType, BuiltInParameter
)

doc = revit.doc

# ─────────────────────────────────────────────────────────────────────────────
# Row class — PascalCase properties for WPF binding
# ─────────────────────────────────────────────────────────────────────────────

class _IssueRow(object):
    """Row: Icon | Col1 (sheet number) | Col2 (view name) | Detail (issue)"""
    def __init__(self, icon, sheet_num, view_name, detail):
        self._icon   = icon
        self._sheet  = sheet_num
        self._view   = view_name
        self._detail = detail

    @property
    def Icon(self):   return self._icon
    @property
    def Col1(self):   return self._sheet
    @property
    def Col2(self):   return self._view
    @property
    def Detail(self): return self._detail


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
        u"Wrong file format.\n\n"
        u"Pre-Import Audit expects a JSON exported by 'Export Sheets with "
        u"Views' (format: sheets_with_views). The selected file has format: "
        u"\"{}\".\n\nFor the legacy format, use the matching tool in the "
        u"Legacy pulldown.".format(data.get("format", "unknown")),
        title=u"Pre-Import Audit"
    )
    script.exit()

layout = data.get("sheets", [])

# ─────────────────────────────────────────────────────────────────────────────
# 2. Destination model prefix
# ─────────────────────────────────────────────────────────────────────────────

dest_prefix = ui.ask_for_string(
    prompt=u"Sheets in JSON: {}\n\nEnter the 2-letter prefix of the destination model\n"
           u"(e.g. AE, AB, AC, AS for Site, CD for Common Details)".format(len(layout)),
    title=u"Pre-Import Audit",
    context=u"The prefix is used to match JSON sheet numbers against sheets in "
            u"this model. JSON sheets carry the source prefix (e.g. AX.10.01); "
            u"the audit strips and re-applies the destination prefix you enter. "
            u"Run this before Import Sheets with Views to catch issues early."
)
if not dest_prefix:
    script.exit()

dest_prefix = dest_prefix.strip().upper()

# ─────────────────────────────────────────────────────────────────────────────
# 3. Cache-building helper (called at startup and after sheet creation)
# ─────────────────────────────────────────────────────────────────────────────

def _build_caches(d):
    all_views = DB.FilteredElementCollector(d).OfClass(DB.View).ToElements()
    dv_by_name = {}
    for v in all_views:
        try:
            primary_id = v.GetPrimaryViewId()
            if primary_id != DB.ElementId.InvalidElementId:
                dv_by_name[v.Name] = v
        except Exception:
            pass

    all_sheets_ = DB.FilteredElementCollector(d).OfClass(DB.ViewSheet).ToElements()
    s_by_suffix = {}
    for s in all_sheets_:
        sfx = s.SheetNumber[2:] if len(s.SheetNumber) > 2 else s.SheetNumber
        s_by_suffix[sfx] = s

    all_vps = DB.FilteredElementCollector(d).OfClass(DB.Viewport).ToElements()
    vp_by_vid = {vp.ViewId.IntegerValue: vp for vp in all_vps}
    vps_by_sheet = {}
    for vp in all_vps:
        sid = vp.SheetId.IntegerValue
        vps_by_sheet.setdefault(sid, []).append(vp)

    vp_type_by_name_ = {}
    for t in DB.FilteredElementCollector(d).OfClass(DB.ElementType).ToElements():
        try:
            if t.FamilyName == "Viewport":
                vp_type_by_name_[t.Name] = t
        except Exception:
            pass
    for vp in all_vps:
        try:
            type_id = vp.GetTypeId()
            vp_type = d.GetElement(type_id)
            if vp_type is not None:
                p = vp_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
                name = (p.AsString() if p else None) or vp_type.Name
                if name and name not in vp_type_by_name_:
                    vp_type_by_name_[name] = vp_type
        except Exception:
            pass

    return {
        'dep_view_by_name':    dv_by_name,
        'sheet_by_suffix':     s_by_suffix,
        'viewport_by_view_id': vp_by_vid,
        'viewports_by_sheet':  vps_by_sheet,
        'vp_type_by_name':     vp_type_by_name_,
    }


caches = _build_caches(doc)
dep_view_by_name    = caches['dep_view_by_name']
sheet_by_suffix     = caches['sheet_by_suffix']
viewport_by_view_id = caches['viewport_by_view_id']
viewports_by_sheet  = caches['viewports_by_sheet']
vp_type_by_name     = caches['vp_type_by_name']

# ─────────────────────────────────────────────────────────────────────────────
# 4. Pre-audit: missing viewport types (alert + continue)
# ─────────────────────────────────────────────────────────────────────────────

json_vp_types = set()
for sheet_data in layout:
    for entry in sheet_data.get("viewports", []):
        vt = entry.get("viewport_type", "")
        if vt:
            json_vp_types.add(vt)

missing_types = sorted([t for t in json_vp_types if t not in vp_type_by_name])

if missing_types:
    ui.alert(
        u"{} viewport type(s) from the JSON are missing in this model:\n\n{}\n\n"
        u"Transfer them from the source model via:\n"
        u"Manage > Transfer Project Standards > Viewport Types\n\n"
        u"Then re-run this audit before importing.".format(
            len(missing_types),
            u"\n".join(u"  - " + t for t in missing_types)
        ),
        title=u"Pre-Import Audit",
        context=u"The destination model needs the same viewport type families "
                u"that the source used; otherwise the importer cannot match "
                u"them and would fall back to the default type."
    )

# ─────────────────────────────────────────────────────────────────────────────
# 5. Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_detail_number(vp):
    try:
        p = vp.get_Parameter(DB.BuiltInParameter.VIEWPORT_DETAIL_NUMBER)
        if p:
            return p.AsString() or u""
    except Exception:
        pass
    return u""


def get_view_name_from_vp(vp):
    try:
        v = doc.GetElement(vp.ViewId)
        if v:
            return v.Name
    except Exception:
        pass
    return u""


def get_viewport_type_name(vp):
    try:
        type_id = vp.GetTypeId()
        vp_type = doc.GetElement(type_id)
        if vp_type is None:
            return u""
        p = vp_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p:
            return p.AsString() or u""
        return vp_type.Name
    except Exception:
        return u""


def _pick_reference_sheet(d, prefix):
    all_vs = DB.FilteredElementCollector(d).OfClass(DB.ViewSheet).ToElements()
    sheets = [s for s in all_vs if not s.IsPlaceholder]
    if not sheets:
        ui.alert(u"No sheets found in this model to use as a reference.",
                 title=u"Pre-Import Audit")
        return None
    # Prefix-matching sheets first, then alphabetical
    def sort_key(s):
        return (0 if s.SheetNumber.startswith(prefix) else 1, s.SheetNumber)
    sheets.sort(key=sort_key)
    chosen = ui.pick_list(
        sheets,
        u"Reference sheet for new sheets",
        multiselect=False,
        name_fn=lambda s: u"{} — {}".format(s.SheetNumber, s.Name),
        context=(u"Pick an existing sheet whose title block (family, type, "
                 u"position) and all editable parameters will be cloned onto "
                 u"each newly created sheet. Sheet Number and Sheet Name come "
                 u"from the JSON — everything else is copied from the reference.")
    )
    return chosen


def _clone_sheet_from_reference(d, ref_sheet, sheet_number, sheet_name):
    """Create one new ViewSheet cloned from ref_sheet. Must be called inside a Transaction."""
    # Find titleblock instances on ref sheet
    ref_tbs = list(
        DB.FilteredElementCollector(d, ref_sheet.Id)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()
    )
    if ref_tbs:
        sym_id = ref_tbs[0].GetTypeId()
        new_sheet = DB.ViewSheet.Create(d, sym_id)
        # Delete auto-placed titleblocks on the new sheet
        new_tbs = list(
            DB.FilteredElementCollector(d, new_sheet.Id)
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
        )
        for tb in new_tbs:
            try:
                d.Delete(tb.Id)
            except Exception:
                pass
        # Copy ref titleblocks (position + all instance params) to new sheet
        tb_ids = DotNetList[DB.ElementId]([tb.Id for tb in ref_tbs])
        ElementTransformUtils.CopyElements(
            ref_sheet, tb_ids, new_sheet, Transform.Identity, CopyPasteOptions()
        )
    else:
        # Ref sheet has no titleblock — attempt blank sheet
        try:
            new_sheet = DB.ViewSheet.Create(d, DB.ElementId.InvalidElementId)
        except Exception as ex:
            raise Exception(
                u"Reference sheet has no title block and blank sheet creation "
                u"failed: {}".format(ex)
            )

    # Set Number then Name (order matters — avoids default-name collision)
    new_sheet.SheetNumber = sheet_number
    new_sheet.Name = sheet_name

    # Copy editable sheet-level parameters from reference
    SKIP_BIPS = {
        BuiltInParameter.SHEET_NUMBER,
        BuiltInParameter.SHEET_NAME,
        BuiltInParameter.SHEET_ISSUE_DATE,
    }
    for p in ref_sheet.Parameters:
        if p.IsReadOnly:
            continue
        defn = p.Definition
        bip = getattr(defn, 'BuiltInParameter', BuiltInParameter.INVALID)
        if bip in SKIP_BIPS:
            continue
        tgt = new_sheet.get_Parameter(defn)
        if tgt is None or tgt.IsReadOnly:
            continue
        try:
            st = p.StorageType
            if st == StorageType.String:
                val = p.AsString()
                tgt.Set(val if val is not None else u"")
            elif st == StorageType.Integer:
                tgt.Set(p.AsInteger())
            elif st == StorageType.Double:
                tgt.Set(p.AsDouble())
            elif st == StorageType.ElementId:
                v = p.AsElementId()
                if v is not None:
                    tgt.Set(v)
        except Exception:
            pass

    return new_sheet


# ─────────────────────────────────────────────────────────────────────────────
# 6. Audit — build ObservableCollections per issue category
# ─────────────────────────────────────────────────────────────────────────────

not_found_oc  = ObservableCollection[object]()   # ❌ sheets not in model
not_model_oc  = ObservableCollection[object]()   # 🔴 view in JSON, not a dep view
not_placed_oc = ObservableCollection[object]()   # 📌 dep view exists, not on sheet
orphan_oc     = ObservableCollection[object]()   # ↻  viewport on sheet, not in JSON
detnum_oc     = ObservableCollection[object]()   # 🔢 detail number mismatch
vptype_oc     = ObservableCollection[object]()   # 🖼  viewport type mismatch
viewtype_oc   = ObservableCollection[object]()   # 🔄 view type mismatch

# Parallel list for sheet creation — (dest_num, json_sheet_name)
not_found_list = []

sheets_ok = 0

for sheet_data in layout:
    sheet_number = sheet_data["sheet_number"]
    sheet_name   = sheet_data.get("sheet_name", "")
    suffix       = sheet_number[2:] if len(sheet_number) > 2 else sheet_number
    dest_num     = u"{}{}".format(dest_prefix, suffix)

    if suffix not in sheet_by_suffix:
        not_found_oc.Add(_IssueRow(u"❌", dest_num, u"(not in model)", sheet_name))
        not_found_list.append((dest_num, sheet_name))
        continue

    target_sheet = sheet_by_suffix[suffix]
    json_vp_dict = {entry["view_name"]: entry for entry in sheet_data.get("viewports", [])}

    sheet_vps     = viewports_by_sheet.get(target_sheet.Id.IntegerValue, [])
    model_vp_dict = {}
    for vp in sheet_vps:
        vname = get_view_name_from_vp(vp)
        if vname:
            v_elem    = doc.GetElement(vp.ViewId)
            view_type = str(v_elem.ViewType) if v_elem else u""
            model_vp_dict[vname] = (
                vp, get_detail_number(vp), get_viewport_type_name(vp), view_type)

    sheet_had_issue = False

    # Views in JSON but not found as dependent views in the model
    for vname in json_vp_dict:
        if vname not in dep_view_by_name:
            not_model_oc.Add(_IssueRow(
                u"\U0001f534", dest_num, vname, u"renamed or deleted"))
            sheet_had_issue = True

    # Views in JSON that exist but aren't placed on any sheet
    for vname in json_vp_dict:
        if vname in dep_view_by_name:
            target_view = dep_view_by_name[vname]
            vid = target_view.Id.IntegerValue
            if vid not in viewport_by_view_id:
                not_placed_oc.Add(_IssueRow(
                    u"\U0001f4cc", dest_num, vname, u"exists but not on sheet"))
                sheet_had_issue = True

    # Viewports on sheet but not in JSON (removed or moved)
    for vname, (vp, det_num, type_name, _view_type) in model_vp_dict.items():
        if vname not in json_vp_dict:
            v = doc.GetElement(vp.ViewId)
            is_dependent = False
            try:
                if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
                    is_dependent = True
            except Exception:
                pass
            if is_dependent:
                orphan_oc.Add(_IssueRow(
                    u"↻", dest_num, vname, u"on sheet, absent from JSON"))
                sheet_had_issue = True

    # Detail number mismatches
    for vname, entry in json_vp_dict.items():
        json_det = entry.get("detail_number", u"")
        if vname in model_vp_dict:
            model_det = model_vp_dict[vname][1]
            if json_det and model_det and json_det != model_det:
                detnum_oc.Add(_IssueRow(
                    u"\U0001f522", dest_num, vname,
                    u"JSON: {}  Model: {}".format(json_det, model_det)))
                sheet_had_issue = True

    # Viewport type mismatches
    for vname, entry in json_vp_dict.items():
        json_type = entry.get("viewport_type", u"")
        if vname in model_vp_dict:
            model_type = model_vp_dict[vname][2]
            if json_type and model_type and json_type != model_type:
                vptype_oc.Add(_IssueRow(
                    u"\U0001f5bc", dest_num, vname,
                    u"JSON: {}  Model: {}".format(json_type, model_type)))
                sheet_had_issue = True
            elif json_type and not model_type:
                vptype_oc.Add(_IssueRow(
                    u"\U0001f5bc", dest_num, vname,
                    u"expected: {}  Model: (unknown)".format(json_type)))
                sheet_had_issue = True

    # View type mismatches
    for vname, entry in json_vp_dict.items():
        json_vtype = entry.get("view_type", u"")
        if json_vtype and vname in model_vp_dict:
            model_vtype = model_vp_dict[vname][3]
            if model_vtype and model_vtype != json_vtype:
                viewtype_oc.Add(_IssueRow(
                    u"\U0001f504", dest_num, vname,
                    u"JSON: {}  Model: {}  → delete & re-import".format(
                        json_vtype, model_vtype)))
                sheet_had_issue = True

    if not sheet_had_issue:
        sheets_ok += 1

n_not_found  = not_found_oc.Count
n_not_model  = not_model_oc.Count
n_not_placed = not_placed_oc.Count
n_orphan     = orphan_oc.Count
n_detnum     = detnum_oc.Count
n_vptype     = vptype_oc.Count
n_viewtype   = viewtype_oc.Count
n_issues     = (n_not_found + n_not_model + n_not_placed +
                n_orphan + n_detnum + n_vptype + n_viewtype)

if n_issues == 0:
    subtitle = u"✅  All {} sheets ready to import  ·  prefix {}  ·  {}".format(
        len(layout), dest_prefix, _os.path.basename(json_path))
elif n_not_found > 0 and (n_issues - n_not_found) == 0:
    subtitle = u"❌  {} sheet{} missing — create them before importing  ·  prefix {}  ·  {}".format(
        n_not_found, u"s" if n_not_found != 1 else u"",
        dest_prefix, _os.path.basename(json_path))
else:
    subtitle = u"{} sheets  ·  prefix {}  ·  {}".format(
        len(layout), dest_prefix, _os.path.basename(json_path))

# ─────────────────────────────────────────────────────────────────────────────
# 7. Build WPF window
# ─────────────────────────────────────────────────────────────────────────────

_BODY_XAML = u"""
<Grid>
  <Grid.Resources>

    <!-- Dark Expander template (Noir family) -->
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

    <!-- Row template: Icon | Sheet # | View name | Detail -->
    <DataTemplate x:Key="rowTpl">
      <Border BorderThickness="0,0,0,1" Padding="14,0,16,0">
        <Border.Style>
          <Style TargetType="Border">
            <Setter Property="Background"  Value="#12131F"/>
            <Setter Property="BorderBrush" Value="#2A2D47"/>
          </Style>
        </Border.Style>
        <Grid MinHeight="28">
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="22"/>
            <ColumnDefinition Width="90"/>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="280"/>
          </Grid.ColumnDefinitions>
          <TextBlock Grid.Column="0" Text="{Binding Icon}" FontSize="12"
                     VerticalAlignment="Center"/>
          <TextBlock Grid.Column="1" Text="{Binding Col1}" Foreground="#9099C8"
                     FontFamily="Segoe UI" FontSize="12"
                     VerticalAlignment="Center" Margin="0,0,8,0"
                     TextTrimming="CharacterEllipsis"/>
          <TextBlock Grid.Column="2" Text="{Binding Col2}" Foreground="#E8EBF5"
                     FontFamily="Segoe UI" FontSize="12"
                     VerticalAlignment="Center" Margin="0,0,8,0"
                     TextTrimming="CharacterEllipsis"/>
          <TextBlock Grid.Column="3" Text="{Binding Detail}" Foreground="#6B7394"
                     FontFamily="Segoe UI" FontSize="12"
                     VerticalAlignment="Center"
                     TextTrimming="CharacterEllipsis"/>
        </Grid>
      </Border>
    </DataTemplate>

  </Grid.Resources>

  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="*"/>
  </Grid.RowDefinitions>

  <!-- Badge row -->
  <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,16">
    <Border x:Name="badgeOkBorder" Background="#122E1C" BorderBrush="#50E898"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeOk" Foreground="#50E898"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeNotFoundBorder" Background="#3C1212" BorderBrush="#FF7070"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeNotFound" Foreground="#FF7070"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeNotModelBorder" Background="#2A1C0E" BorderBrush="#E87E20"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeNotModel" Foreground="#E87E20"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeNotPlacedBorder" Background="#1A2535" BorderBrush="#5B8EC4"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeNotPlaced" Foreground="#5B8EC4"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeOrphanBorder" Background="#1A1D30" BorderBrush="#9099C8"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeOrphan" Foreground="#9099C8"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeDetNumBorder" Background="#1A2535" BorderBrush="#5B8EC4"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeDetNum" Foreground="#5B8EC4"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeVpTypeBorder" Background="#2A1C0E" BorderBrush="#E87E20"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeVpType" Foreground="#E87E20"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeViewTypeBorder" Background="#2A1C0E" BorderBrush="#E87E20"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeViewType" Foreground="#E87E20"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
  </StackPanel>

  <!-- Scrollable body -->
  <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto">
    <StackPanel>

      <TextBlock x:Name="lblAllClear" FontFamily="Segoe UI" FontSize="14"
                 Foreground="#50E898" TextWrapping="Wrap"
                 Margin="4,12,0,0" Visibility="Collapsed">
        &#x2705;  All sheets are in sync with the JSON. Ready to import.
      </TextBlock>

      <!-- ❌ Sheets not found in model — FIRST (actionable: Create sheets) -->
      <Expander x:Name="expNotFound" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#3C1212" BorderBrush="#FF7070" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrNotFoundCount" Foreground="#FF7070"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x274C;  Sheets not found in model" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icNotFound" ItemTemplate="{StaticResource rowTpl}"/>
      </Expander>

      <!-- 🔴 Views not in model — SECOND (actionable via Import) -->
      <Expander x:Name="expNotModel" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#2A1C0E" BorderBrush="#E87E20" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrNotModelCount" Foreground="#E87E20"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x1F534;  Views not in model (renamed or deleted)" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <StackPanel>
          <TextBlock Text="&#x279C; Run Import Sheets with Views to create these views &#x2014; Phase A duplicates them from their masters."
                     Foreground="#6B7394" FontFamily="Segoe UI" FontSize="11" FontStyle="Italic"
                     Margin="14,8,14,6" TextWrapping="Wrap"/>
          <ItemsControl x:Name="icNotModel" ItemTemplate="{StaticResource rowTpl}"/>
        </StackPanel>
      </Expander>

      <Expander x:Name="expNotPlaced" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#1A2535" BorderBrush="#5B8EC4" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrNotPlacedCount" Foreground="#5B8EC4"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x1F4CC;  Views not placed on sheet" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icNotPlaced" ItemTemplate="{StaticResource rowTpl}"/>
      </Expander>

      <!-- ↻ Removed or moved — informational, Import handles these automatically -->
      <Expander x:Name="expOrphan" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#1A1D30" BorderBrush="#9099C8" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrOrphanCount" Foreground="#9099C8"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x21BB;  Removed or moved (absent from JSON)" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <StackPanel>
          <TextBlock Text="&#x21BB; These viewports will be cleaned up or relocated automatically by Import Sheets with Views."
                     Foreground="#6B7394" FontFamily="Segoe UI" FontSize="11" FontStyle="Italic"
                     Margin="14,8,14,6" TextWrapping="Wrap"/>
          <ItemsControl x:Name="icOrphan" ItemTemplate="{StaticResource rowTpl}"/>
        </StackPanel>
      </Expander>

      <Expander x:Name="expDetNum" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#1A2535" BorderBrush="#5B8EC4" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrDetNumCount" Foreground="#5B8EC4"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x1F522;  Detail number mismatches" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icDetNum" ItemTemplate="{StaticResource rowTpl}"/>
      </Expander>

      <Expander x:Name="expVpType" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#2A1C0E" BorderBrush="#E87E20" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrVpTypeCount" Foreground="#E87E20"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x1F5BC;  Viewport type mismatches" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icVpType" ItemTemplate="{StaticResource rowTpl}"/>
      </Expander>

      <Expander x:Name="expViewType" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#2A1C0E" BorderBrush="#E87E20" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrViewTypeCount" Foreground="#E87E20"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x1F504;  View type mismatches (delete &amp; re-import)" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icViewType" ItemTemplate="{StaticResource rowTpl}"/>
      </Expander>

    </StackPanel>
  </ScrollViewer>
</Grid>
"""

_FOOTER_XAML = u"""
<Grid>
  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="Auto"/>
  </Grid.RowDefinitions>

  <Grid Grid.Row="0">
    <StackPanel HorizontalAlignment="Left" Orientation="Horizontal">
      <Button x:Name="btnCopy" Content="Copy to clipboard" Style="{StaticResource BtnGhost}"/>
    </StackPanel>
    <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
      <Button x:Name="btnCreateSheets" Content="Create 0 sheets"
              Style="{StaticResource BtnPrimary}"
              Margin="0,0,8,0" Visibility="Collapsed"
              ToolTip="Creates empty sheets in this model based on the JSON, cloning title block and parameters from a reference sheet you choose."/>
      <Button x:Name="btnClose" Content="Close" Style="{StaticResource BtnGhost}"/>
    </StackPanel>
  </Grid>

  <TextBlock x:Name="lblStatus" Grid.Row="1" Foreground="#9099C8"
             FontFamily="Segoe UI" FontSize="11"
             Margin="0,10,0,0" TextWrapping="Wrap" Visibility="Collapsed"/>
</Grid>
"""

win = ui.parse(
    u"Pre-Import Audit",
    subtitle,
    _BODY_XAML,
    _FOOTER_XAML,
    width=1060,
    height=660,
    context=u"Compares the sheets section of the JSON against this model's sheets "
            u"and viewports. Use this before Import Sheets with Views to catch "
            u"missing sheets (and create them), views absent from the model, "
            u"stale viewport placements, and detail number or type mismatches."
)

# ─── Wire badges and expander sections ────────────────────────────────────────

if n_issues == 0:
    win.FindName("badgeOkBorder").Visibility = Visibility.Visible
    win.FindName("badgeOk").Text = u"✅  {} sheets in sync".format(sheets_ok)
    win.FindName("lblAllClear").Visibility = Visibility.Visible
else:
    if sheets_ok > 0:
        win.FindName("badgeOkBorder").Visibility = Visibility.Visible
        win.FindName("badgeOk").Text = u"✅  {}".format(sheets_ok)


def _wire_section(oc, badge_border, badge_txt, badge_text_val,
                  exp_name, hdr_count, ic_name):
    if oc.Count == 0:
        return
    win.FindName(badge_border).Visibility = Visibility.Visible
    win.FindName(badge_txt).Text = badge_text_val
    win.FindName(exp_name).Visibility = Visibility.Visible
    win.FindName(hdr_count).Text = u" {} ".format(oc.Count)
    win.FindName(ic_name).ItemsSource = oc


_wire_section(not_found_oc,  "badgeNotFoundBorder",  "badgeNotFound",
              u"❌  {} not found".format(n_not_found),
              "expNotFound",  "hdrNotFoundCount",  "icNotFound")
_wire_section(not_model_oc,  "badgeNotModelBorder",  "badgeNotModel",
              u"\U0001f534  {} not in model".format(n_not_model),
              "expNotModel",  "hdrNotModelCount",  "icNotModel")
_wire_section(not_placed_oc, "badgeNotPlacedBorder", "badgeNotPlaced",
              u"\U0001f4cc  {} not placed".format(n_not_placed),
              "expNotPlaced", "hdrNotPlacedCount", "icNotPlaced")
_wire_section(orphan_oc,     "badgeOrphanBorder",    "badgeOrphan",
              u"↻  {} removed/moved".format(n_orphan),
              "expOrphan",    "hdrOrphanCount",    "icOrphan")
_wire_section(detnum_oc,     "badgeDetNumBorder",    "badgeDetNum",
              u"\U0001f522  {} det#".format(n_detnum),
              "expDetNum",    "hdrDetNumCount",    "icDetNum")
_wire_section(vptype_oc,     "badgeVpTypeBorder",    "badgeVpType",
              u"\U0001f5bc  {} vp-type".format(n_vptype),
              "expVpType",    "hdrVpTypeCount",    "icVpType")
_wire_section(viewtype_oc,   "badgeViewTypeBorder",  "badgeViewType",
              u"\U0001f504  {} view-type".format(n_viewtype),
              "expViewType",  "hdrViewTypeCount",  "icViewType")

# Wire Create sheets button
if not_found_oc.Count > 0:
    btn_create = win.FindName("btnCreateSheets")
    btn_create.Content = u"Create {} sheet{}".format(
        not_found_oc.Count, u"s" if not_found_oc.Count != 1 else u"")
    btn_create.Visibility = Visibility.Visible

# ─── Create sheets handler ─────────────────────────────────────────────────────

def on_create_sheets(s, e):
    to_create = list(not_found_list)
    if not to_create:
        return

    # Pick reference sheet (lazy — called here, not at startup)
    ref_sheet = _pick_reference_sheet(doc, dest_prefix)
    if ref_sheet is None:
        return

    # Activate title block symbol if needed (must be inside a transaction)
    ref_tbs_check = list(
        DB.FilteredElementCollector(doc, ref_sheet.Id)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()
    )
    if ref_tbs_check:
        sym = doc.GetElement(ref_tbs_check[0].GetTypeId())
        if sym is not None and not sym.IsActive:
            t_act = DB.Transaction(doc, u"Activate title block")
            t_act.Start()
            try:
                sym.Activate()
                doc.Regenerate()
                t_act.Commit()
            except Exception:
                try:
                    t_act.RollBack()
                except Exception:
                    pass

    # Create sheets
    created   = 0
    failed    = 0
    fail_msgs = []

    t = DB.Transaction(doc, u"Create missing sheets from reference")
    t.Start()
    try:
        for dest_num, json_name in to_create:
            try:
                _clone_sheet_from_reference(doc, ref_sheet, dest_num, json_name)
                created += 1
            except Exception as ex:
                failed += 1
                fail_msgs.append(u"  {} — {}".format(dest_num, ex))
        t.Commit()
    except Exception as ex_outer:
        try:
            t.RollBack()
        except Exception:
            pass
        lbl = win.FindName("lblStatus")
        lbl.Text = u"❌ Transaction failed: {}".format(ex_outer)
        lbl.Visibility = Visibility.Visible
        return

    # Rebuild caches and re-check which sheets are still missing
    new_caches = _build_caches(doc)
    new_s_by_suffix = new_caches['sheet_by_suffix']

    del not_found_list[:]
    not_found_oc.Clear()

    for sheet_data in layout:
        snum  = sheet_data["sheet_number"]
        sname = sheet_data.get("sheet_name", u"")
        sfx   = snum[2:] if len(snum) > 2 else snum
        d_num = u"{}{}".format(dest_prefix, sfx)
        if sfx not in new_s_by_suffix:
            not_found_list.append((d_num, sname))
            not_found_oc.Add(_IssueRow(u"❌", d_num, u"(not in model)", sname))

    # Update button + expander + badge
    btn  = win.FindName("btnCreateSheets")
    exp  = win.FindName("expNotFound")
    bbdr = win.FindName("badgeNotFoundBorder")
    btxt = win.FindName("badgeNotFound")
    hdr  = win.FindName("hdrNotFoundCount")

    if not_found_oc.Count == 0:
        btn.Visibility  = Visibility.Collapsed
        exp.Visibility  = Visibility.Collapsed
        bbdr.Visibility = Visibility.Collapsed
    else:
        n_rem = not_found_oc.Count
        btn.Content = u"Create {} sheet{}".format(
            n_rem, u"s" if n_rem != 1 else u"")
        btxt.Text = u"❌  {} not found".format(n_rem)
        hdr.Text  = u" {} ".format(n_rem)

    # Update subtitle
    new_sub = u"✓ Created {}  ·  {} sheets  ·  prefix {}  ·  {}".format(
        created, len(layout), dest_prefix, _os.path.basename(json_path))
    win.FindName("__noir_subtitle__").Text = new_sub

    # Status line
    lbl = win.FindName("lblStatus")
    if failed:
        lbl.Text = u"❌ {} sheet(s) could not be created:\n{}".format(
            failed, u"\n".join(fail_msgs))
    else:
        lbl.Text = u"✓ {} sheet{} created successfully. Run Import Sheets with Views next.".format(
            created, u"s" if created != 1 else u"")
    lbl.Visibility = Visibility.Visible


# ─── Copy handler ─────────────────────────────────────────────────────────────

def on_copy(s, e):
    sections = [
        (not_found_oc,  u"Sheets not found in model"),
        (not_model_oc,  u"Views not in model"),
        (not_placed_oc, u"Views not placed on sheet"),
        (orphan_oc,     u"Removed or moved viewports"),
        (detnum_oc,     u"Detail number mismatches"),
        (vptype_oc,     u"Viewport type mismatches"),
        (viewtype_oc,   u"View type mismatches"),
    ]
    lines = [u"Pre-Import Audit — " + subtitle, u""]
    if n_issues == 0:
        lines.append(u"✅  All sheets in sync.")
    else:
        for oc, label in sections:
            if oc.Count == 0:
                continue
            lines.append(u"── {} ({}) ──".format(label, oc.Count))
            for r in oc:
                lines.append(u"  {}  {}  {}  —  {}".format(
                    r.Icon, r.Col1, r.Col2, r.Detail))
            lines.append(u"")
    text = u"\n".join(lines)
    try:
        WinFormsClipboard.SetText(text)
    except Exception:
        pass
    btn = win.FindName("btnCopy")
    btn.Content = u"Copied ✓"


# ─── Wire events ──────────────────────────────────────────────────────────────

win.FindName("btnCreateSheets").Click += on_create_sheets
win.FindName("btnCopy").Click          += on_copy
win.FindName("btnClose").Click         += lambda s, e: win.Close()

win.ShowDialog()
