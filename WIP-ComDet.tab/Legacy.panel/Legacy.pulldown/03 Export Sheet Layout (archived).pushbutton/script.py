# -*- coding: utf-8 -*-
__title__ = 'Export Sheet Layout\n(archived)'
__doc__ = ('Exports viewport positions, detail numbers, and detail lines '
           'from selected sheets to a dated Sheet layout JSON file.')

import json
import sys
import os as _os
from datetime import date
from pyrevit import revit, DB, script, forms

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

# ─────────────────────────────────────────────────────────────────────────────
# Legacy guard — confirm before running. This tool was moved to the Legacy
# pulldown on 2026-05-19 because the combined "Sheets with Views" flow now
# covers its workflow. Kept here for backwards-compat with old JSONs.
# ─────────────────────────────────────────────────────────────────────────────
if not ui.confirm(
    u"This tool is part of the LEGACY workflow.\n\n"
    u"The current workflow uses 'Export Sheets with Views' + 'Import "
    u"Sheets with Views' (and their PRO variant) from the Export & Import "
    u"panel. The matching new Audit tools live under Audit > Audit Views / "
    u"Audit Sheets pulldowns.\n\n"
    u"Continue with the legacy tool anyway?",
    title=u"Legacy Tool",
    yes_text=u"Continue (legacy)",
    context=u"Common Details migrated to a combined JSON flow on 2026-05-19. "
            u"The legacy tools stay here in case you need to interop with old "
            u"JSONs or a feature not yet covered in the new flow."
):
    script.exit()

doc    = revit.doc
output = script.get_output()
output.close()

# ─────────────────────────────────────────────────────────────────────────────
# 1. Collect all sheets and show selection list
# ─────────────────────────────────────────────────────────────────────────────

all_sheets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements())
all_sheets.sort(key=lambda s: s.SheetNumber)

if not all_sheets:
    ui.alert("No sheets found in the model.", title="Export Sheet Layout")
    script.exit()

sheet_options   = []
sheet_by_option = {}
for s in all_sheets:
    label = "{} - {}".format(s.SheetNumber, s.Name)
    sheet_options.append(label)
    sheet_by_option[label] = s

chosen_options = ui.pick_list(
    sheet_options,
    "Select Sheets to Export",
    button_name="Export",
    context=u"Pick the sheets whose layout (viewports, detail numbers, detail lines) "
            u"you want to save. The dependent views themselves still live under their "
            u"masters — this only exports the sheet placement. Later it's imported with "
            u"'Import Sheet Layout' or via 'Full Sync'."
)
if not chosen_options:
    script.exit()

selected_sheets = [sheet_by_option[o] for o in chosen_options]

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
        p = vp_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p:
            return p.AsString() or ""
        return vp_type.Name
    except Exception:
        return ""

def xyz_to_list(pt):
    return [pt.X, pt.Y, pt.Z]

# ─────────────────────────────────────────────────────────────────────────────
# 5. Build the JSON + collect results
# ─────────────────────────────────────────────────────────────────────────────

layout         = []
results        = []
total_vp       = 0
total_lines    = 0
errors         = 0
cancelled      = False

with ui.ProgressBar(title=u"Export Sheet Layout", cancellable=True, step=1) as pb:
    for i, sheet in enumerate(selected_sheets):
        if pb.cancelled:
            cancelled = True
            break
        pb.title = u"Export Sheet Layout — {}/{} — {} {}".format(
            i + 1, len(selected_sheets), sheet.SheetNumber, sheet.Name)
        pb.update_progress(i + 1, len(selected_sheets))

        sheet_entry = {
            "sheet_number": sheet.SheetNumber,
            "sheet_name":   sheet.Name,
            "viewports":    [],
            "detail_lines": []
        }

        sheet_vp    = 0
        sheet_lines = 0
        sheet_label = u"{} - {}".format(sheet.SheetNumber, sheet.Name)

        # ── Viewports ──
        for vp_id in sheet.GetAllViewports():
            vp = doc.GetElement(vp_id)
            if vp is None:
                continue

            view_id = vp.ViewId.IntegerValue
            if view_id not in dep_views_by_id:
                continue

            v      = dep_views_by_id[view_id]
            center = vp.GetBoxCenter()

            try:
                label_x = vp.LabelOffset.X
                label_y = vp.LabelOffset.Y
            except Exception:
                label_x = label_y = 0

            template_name = ""
            tid = v.ViewTemplateId
            if tid != DB.ElementId.InvalidElementId:
                tmpl = doc.GetElement(tid)
                if tmpl:
                    template_name = tmpl.Name

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

            try:
                scale = v.Scale
            except Exception:
                scale = 1

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
            sheet_vp += 1

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
                sheet_lines += 1
            except Exception as e:
                results.append(("error", sheet_label, u"Detail line", str(e)))
                errors += 1

        layout.append(sheet_entry)
        total_vp    += sheet_vp
        total_lines += sheet_lines
        results.append(("exported", sheet_label,
                        u"{} viewport{}".format(sheet_vp, u"s" if sheet_vp != 1 else u""),
                        u"{} line{}".format(sheet_lines, u"s" if sheet_lines != 1 else u"")))

# ─────────────────────────────────────────────────────────────────────────────
# 6. Save JSON
# ─────────────────────────────────────────────────────────────────────────────

with open(save_path, "w") as f:
    json.dump(layout, f, indent=2)

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
    _LABELS = {
        "exported": u"✅  Exported",
        "error":    u"❌  Error",
    }
    def __init__(self, status, sheet, viewports, lines):
        self._status    = status
        self._sheet     = sheet
        self._viewports = viewports
        self._lines     = lines

    @property
    def Status(self):    return self._LABELS.get(self._status, self._status)
    @property
    def Sheet(self):     return self._sheet
    @property
    def Viewports(self): return self._viewports
    @property
    def Lines(self):     return self._lines


subtitle = u"{} sheets  ·  {} viewports  ·  {} lines  ·  {}".format(
    len(selected_sheets), total_vp, total_lines, _os.path.basename(save_path))
if errors:
    subtitle += u"  ·  {} error{}".format(errors, u"s" if errors != 1 else u"")
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
      <TextBlock x:Name="badgeSheets" Foreground="#50E898" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border Background="#142244" BorderBrush="#7EB4F0" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeVP" Foreground="#7EB4F0" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border Background="#1E2740" BorderBrush="#6B7394" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeLines" Foreground="#6B7394" FontFamily="Segoe UI" FontSize="13"/>
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
      <DataGridTextColumn Header="Status"    Binding="{Binding Status}"    Width="140"/>
      <DataGridTextColumn Header="Sheet"     Binding="{Binding Sheet}"     Width="*"/>
      <DataGridTextColumn Header="Viewports" Binding="{Binding Viewports}" Width="120"/>
      <DataGridTextColumn Header="Lines"     Binding="{Binding Lines}"     Width="120"/>
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
for status, sheet, viewports, lines in results:
    rows.Add(_Row(status, sheet, viewports, lines))

win = ui.parse(u"Export Sheet Layout", subtitle, _BODY_XAML, _FOOTER_XAML, width=900, height=500)

win.FindName("badgeSheets").Text = u"✅  {} sheet{}".format(len(selected_sheets), u"s" if len(selected_sheets) != 1 else u"")
win.FindName("badgeVP").Text     = u"🗂  {} viewport{}".format(total_vp, u"s" if total_vp != 1 else u"")
win.FindName("badgeLines").Text  = u"📐  {} line{}".format(total_lines, u"s" if total_lines != 1 else u"")
if errors:
    win.FindName("badgeErrorBorder").Visibility = Visibility.Visible
    win.FindName("badgeErrors").Text = u"❌  {} error{}".format(errors, u"s" if errors != 1 else u"")

win.FindName("dgResults").ItemsSource = rows

def on_copy(s, e):
    lines_out = [u"Export Sheet Layout — " + subtitle, u""]
    for r in rows:
        lines_out.append(u"{}  |  {}  |  {}  |  {}".format(
            r.Status, r.Sheet, r.Viewports, r.Lines))
    WinFormsClipboard.SetText(u"\n".join(lines_out))
    s.Content = u"Copied ✓"

win.FindName("btnCopy").Click += on_copy
win.FindName("btnOK").Click   += lambda s, e: win.Close()
win.ShowDialog()
