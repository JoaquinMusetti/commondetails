# -*- coding: utf-8 -*-
__title__ = 'Import\nSheet Layout'
__doc__ = ('Reads a sheets_layout.json file and places dependent views and detail lines '
           'on destination model sheets. Supports position, label offset, viewport type, '
           'detail number, title on sheet, and detail line updates in two transactions per sheet.')

import json
import sys
import os as _os
import time
from pyrevit import revit, DB, script, forms

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

doc    = revit.doc
uidoc  = revit.uidoc
output = script.get_output()
output.close()

# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick JSON file
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(
    file_ext="json",
    title="Select sheets_layout.json"
)
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    layout = json.load(f)

if not isinstance(layout, list):
    ui.alert(
        u"Wrong file selected.\n\n"
        u"Import Sheet Layout expects a sheets_layout.json file (a list of sheet entries).\n"
        u"The selected file contains a {} instead.\n\n"
        u"Please run Export Sheet Layout first, then select that output file.".format(
            type(layout).__name__),
        title=u"Import Sheet Layout"
    )
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 2. Filter sheets to import
# ─────────────────────────────────────────────────────────────────────────────

sheets_in_json = sorted(
    "{} --- {}".format(e["sheet_number"], e["sheet_name"]) for e in layout
)

chosen_sheets = ui.pick_list(
    sheets_in_json,
    "Select Sheets to Import",
    button_name="Import",
    context=u"Tick which sheets from the JSON to apply to the active model. Sheets "
            u"are created if missing; their names are rewritten with the destination "
            u"model prefix you'll provide in a later step."
)
if not chosen_sheets:
    script.exit()

chosen_sheet_numbers = list({s.split(" --- ")[0].strip() for s in chosen_sheets})
layout = [e for e in layout if e["sheet_number"] in chosen_sheet_numbers]

# ─────────────────────────────────────────────────────────────────────────────
# 3. Checklist — what to update
# ─────────────────────────────────────────────────────────────────────────────

options = [
    "VIEWPORTS | Position & Title location",
    "VIEWPORTS | Match viewport types",
    "VIEWPORTS | Detail number",
    "VIEWPORTS | Title on sheet",
    "SHEET ELEMENTS | Detail lines  (delete existing and redraw)",
]

chosen_options = ui.pick_list(
    options,
    "Select What to Update",
    button_name="Apply",
    context=u"If the sheets already exist in the destination, pick which fields to "
            u"overwrite and which to leave alone. Options are independent — you can "
            u"update detail numbers without touching positions, for example."
)
if chosen_options is None:
    script.exit()

DO_POSITION   = any("Position & Title location" in o for o in chosen_options)
DO_VP_TYPE    = any("Match viewport types"      in o for o in chosen_options)
DO_DET_NUMBER = any("Detail number"             in o for o in chosen_options)
DO_TITLE      = any("Title on sheet"            in o for o in chosen_options)
DO_LINES      = any("Detail lines"              in o for o in chosen_options)

# ─────────────────────────────────────────────────────────────────────────────
# 4. Destination model prefix
# ─────────────────────────────────────────────────────────────────────────────

dest_prefix = ui.ask_for_string(
    prompt="Enter the 2-letter prefix of the destination model\n(e.g. AE, AB, AC...)",
    title="Destination Model Prefix",
    context=u"Sheets in Common Details are named 'CD_XXXX_...'. In each building we "
            u"swap 'CD' for the building's 2-letter prefix (AE, AB, AC, AD, AF, AG, "
            u"AK, AS for Site)."
)
if not dest_prefix:
    script.exit()

dest_prefix = dest_prefix.strip().upper()

# ─────────────────────────────────────────────────────────────────────────────
# 5. Index destination model resources
# ─────────────────────────────────────────────────────────────────────────────

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

dep_view_by_name = {}
for v in all_views:
    try:
        primary_id = v.GetPrimaryViewId()
        if primary_id != DB.ElementId.InvalidElementId:
            dep_view_by_name[v.Name] = v
    except Exception:
        pass

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

existing_viewports = DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements()
viewport_by_view_id = {vp.ViewId.IntegerValue: vp for vp in existing_viewports}

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

def get_line_style(name):
    if name and name in line_style_by_name:
        return line_style_by_name[name]
    if line_style_by_name:
        return list(line_style_by_name.values())[0]
    return None

# ─────────────────────────────────────────────────────────────────────────────
# 6. Helpers
# ─────────────────────────────────────────────────────────────────────────────

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

def update_position(vp, center, entry):
    if DO_POSITION:
        vp.SetBoxCenter(center)
        try:
            vp.LabelOffset = DB.XYZ(entry.get("label_offset_x", 0), entry.get("label_offset_y", 0), 0)
        except Exception:
            pass
    if DO_VP_TYPE:
        vp_type_name = entry.get("viewport_type", "")
        if vp_type_name and vp_type_name in vp_type_by_name:
            try:
                vp.ChangeTypeId(vp_type_by_name[vp_type_name])
            except Exception:
                pass

def update_title_on_sheet(view, entry):
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

# ─────────────────────────────────────────────────────────────────────────────
# 7. Process sheets — TWO transactions per sheet
# ─────────────────────────────────────────────────────────────────────────────

vp_created = 0
vp_updated = 0
dl_created = 0
dl_deleted = 0
results    = []
cancelled  = False

with ui.ProgressBar(title=u"Import Sheet Layout", cancellable=True, step=1) as pb:
    for i, sheet_data in enumerate(layout):
        if pb.cancelled:
            cancelled = True
            break
        sheet_number = sheet_data["sheet_number"]
        sheet_name   = sheet_data["sheet_name"]
        suffix       = sheet_number[2:] if len(sheet_number) > 2 else sheet_number
        sheet_label  = u"{}{}".format(dest_prefix, suffix)

        pb.title = u"Import Sheet Layout — {}/{} — {}".format(i + 1, len(layout), sheet_label)
        pb.update_progress(i + 1, len(layout))

        if suffix not in sheet_by_suffix:
            results.append(("skipped", sheet_label, u"—", u"Sheet not found in model"))
            continue

        target_sheet       = sheet_by_suffix[suffix]
        vp_id_to_det_num   = {}

        # ═══════════════════════════════════════════════════════════════════
        # TRANSACTION 1 — viewports + detail lines
        # ═══════════════════════════════════════════════════════════════════

        t1 = DB.Transaction(doc, "Import Layout T1 - {}".format(sheet_label))
        try:
            t1.Start()

            all_sheet_vps  = list(DB.FilteredElementCollector(doc, target_sheet.Id)
                                  .OfClass(DB.Viewport).ToElements())
            original_numbers = {sv.Id.IntegerValue: get_detail_number(sv) for sv in all_sheet_vps}

            # Move all to temp numbers to avoid conflicts
            timestamp = str(int(time.time()))
            for idx, sv in enumerate(all_sheet_vps):
                set_detail_number(sv, "zzz{}_{}".format(timestamp, idx))

            processed_ids = []

            for entry in sheet_data["viewports"]:
                view_name = entry["view_name"]
                center    = DB.XYZ(entry["viewport_center_x"], entry["viewport_center_y"], 0)

                if view_name not in dep_view_by_name:
                    results.append(("skipped", sheet_label, view_name, u"View not found in model"))
                    continue

                target_view    = dep_view_by_name[view_name]
                target_det_num = entry.get("detail_number", "")

                try:
                    if target_view.Id.IntegerValue in viewport_by_view_id:
                        existing_vp = viewport_by_view_id[target_view.Id.IntegerValue]
                        if existing_vp.SheetId.IntegerValue == target_sheet.Id.IntegerValue:
                            update_position(existing_vp, center, entry)
                            if DO_TITLE:
                                update_title_on_sheet(target_view, entry)
                            if DO_DET_NUMBER and target_det_num:
                                vp_id_to_det_num[existing_vp.Id.IntegerValue] = target_det_num
                            processed_ids.append(existing_vp.Id.IntegerValue)
                            results.append(("updated", sheet_label, view_name,
                                            u"det. {}".format(target_det_num) if target_det_num else u""))
                            vp_updated += 1
                        else:
                            results.append(("skipped", sheet_label, view_name,
                                            u"On a different sheet"))
                    else:
                        vp = DB.Viewport.Create(doc, target_sheet.Id, target_view.Id, center)
                        update_position(vp, center, entry)
                        update_title_on_sheet(target_view, entry)
                        if DO_DET_NUMBER and target_det_num:
                            vp_id_to_det_num[vp.Id.IntegerValue] = target_det_num
                        processed_ids.append(vp.Id.IntegerValue)
                        results.append(("created", sheet_label, view_name,
                                        u"det. {}".format(target_det_num) if target_det_num else u""))
                        vp_created += 1

                except Exception as e:
                    results.append(("error", sheet_label, view_name, str(e)))

            # Restore non-processed viewports
            for sv in all_sheet_vps:
                if sv.Id.IntegerValue not in processed_ids:
                    orig = original_numbers.get(sv.Id.IntegerValue, "")
                    if orig:
                        set_detail_number(sv, orig)

            # ── Detail Lines ──
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
                            style  = get_line_style(dl_entry.get("line_style", ""))
                            if style:
                                new_dl.LineStyle = style
                            created += 1
                        except Exception as e:
                            results.append(("error", sheet_label, u"Detail line", str(e)))

                    dl_created += created
                    results.append(("lines", sheet_label,
                                    u"{} created".format(created),
                                    u"{} deleted".format(deleted)))

            t1.Commit()

        except Exception as e:
            try:
                if t1.HasStarted() and not t1.HasEnded():
                    t1.RollBack()
            except Exception:
                pass
            results.append(("error", sheet_label, u"T1 rolled back", str(e)))
            continue

        # ═══════════════════════════════════════════════════════════════════
        # TRANSACTION 2 — assign final detail numbers
        # T2 is separate so Revit settles viewport auto-numbering after T1
        # commit before we do the temp → final reassignment pass.
        # ═══════════════════════════════════════════════════════════════════

        if not DO_DET_NUMBER or not vp_id_to_det_num:
            continue

        t2 = DB.Transaction(doc, "Import Detail Numbers T2 - {}".format(sheet_label))
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
            results.append(("error", sheet_label, u"T2 rolled back", str(e)))

# ─────────────────────────────────────────────────────────────────────────────
# 8. Noir results window
# ─────────────────────────────────────────────────────────────────────────────

import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('PresentationCore')
from System.Windows import Visibility
from System.Windows.Forms import Clipboard as WinFormsClipboard
from System.Collections.ObjectModel import ObservableCollection

class _Row(object):
    _LABELS = {
        "created": u"✅  Created",
        "updated": u"🔄  Updated",
        "lines":   u"📐  Lines",
        "skipped": u"⏭️  Skipped",
        "error":   u"❌  Error",
    }
    def __init__(self, status, sheet, name, detail):
        self._status = status
        self._sheet  = sheet
        self._name   = name
        self._detail = detail

    @property
    def Status(self):   return self._LABELS.get(self._status, self._status)
    @property
    def Sheet(self):    return self._sheet
    @property
    def ViewName(self): return self._name
    @property
    def Detail(self):   return self._detail


n_errors  = sum(1 for s, _, _, _ in results if s == "error")
n_skipped = sum(1 for s, _, _, _ in results if s == "skipped")
subtitle  = u"{} created  ·  {} updated  ·  {} lines  ·  {} skipped".format(
    vp_created, vp_updated, dl_created, n_skipped)
if n_errors:
    subtitle += u"  ·  {} error{}".format(n_errors, u"s" if n_errors != 1 else u"")
if cancelled:
    subtitle += u"  ·  ⚠ partial"

_BODY_XAML = u"""
<Grid>
  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="*"/>
  </Grid.RowDefinitions>
  <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,16">
    <Border Background="#122E1C" BorderBrush="#50E898" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeCreated" Foreground="#50E898" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border Background="#142244" BorderBrush="#7EB4F0" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeUpdated" Foreground="#7EB4F0" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border Background="#1E2740" BorderBrush="#6B7394" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeSkipped" Foreground="#6B7394" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeLinesBorder" Background="#0F3038" BorderBrush="#5ED4E6"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeLines" Foreground="#5ED4E6" FontFamily="Segoe UI" FontSize="13"/>
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
      <DataGridTextColumn Header="Status"    Binding="{Binding Status}"   Width="140"/>
      <DataGridTextColumn Header="Sheet"     Binding="{Binding Sheet}"    Width="120"/>
      <DataGridTextColumn Header="View Name" Binding="{Binding ViewName}" Width="*"/>
      <DataGridTextColumn Header="Detail"    Binding="{Binding Detail}"   Width="180"/>
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
for status, sheet, name, detail in results:
    rows.Add(_Row(status, sheet, name, detail))

win = ui.parse(u"Import Sheet Layout", subtitle, _BODY_XAML, _FOOTER_XAML, width=960, height=580)

win.FindName("badgeCreated").Text = u"✅  {} created".format(vp_created)
win.FindName("badgeUpdated").Text = u"🔄  {} updated".format(vp_updated)
win.FindName("badgeSkipped").Text = u"⏭️  {} skipped".format(n_skipped)
if dl_created:
    win.FindName("badgeLinesBorder").Visibility = Visibility.Visible
    win.FindName("badgeLines").Text = u"📐  {} lines".format(dl_created)
if n_errors:
    win.FindName("badgeErrorBorder").Visibility = Visibility.Visible
    win.FindName("badgeErrors").Text = u"❌  {} error{}".format(n_errors, u"s" if n_errors != 1 else u"")

win.FindName("dgResults").ItemsSource = rows

def on_copy(s, e):
    lines_out = [u"Import Sheet Layout — " + subtitle, u""]
    for r in rows:
        line = u"{}  |  {}  |  {}".format(r.Status, r.Sheet, r.ViewName)
        if r.Detail:
            line += u"  |  " + r.Detail
        lines_out.append(line)
    WinFormsClipboard.SetText(u"\n".join(lines_out))
    s.Content = u"Copied ✓"

win.FindName("btnCopy").Click += on_copy
win.FindName("btnOK").Click   += lambda s, e: win.Close()
win.ShowDialog()
