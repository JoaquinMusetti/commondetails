# -*- coding: utf-8 -*-
__title__ = 'Export\nSheets with Views'
__doc__ = ('Exports dependent views and sheet layout together in a single JSON file. '
           'Select the sheets, choose the Common Details linked model, and save. '
           'The resulting file can be imported with "Import Sheets with Views" '
           'to recreate both the views and the sheet layout in one step.')

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

doc    = revit.doc
output = script.get_output()
output.close()

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def xyz_to_list(pt):
    return [pt.X, pt.Y, pt.Z]

def world_to_link(pt, link_transform):
    # link_transform=None means "we're exporting from Common Details itself"
    # — no link to reference, coordinates are already in CD local space.
    if link_transform is None:
        return pt
    return link_transform.Inverse.OfPoint(pt)

def get_detail_id(view):
    p = view.LookupParameter("Detail ID")
    return (p.AsString() or "") if p else ""

def get_viewport_type_name(vp):
    try:
        type_id = vp.GetTypeId()
        vp_type = doc.GetElement(type_id)
        if vp_type is None:
            return ""
        p = vp_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        return (p.AsString() or "") if p else vp_type.Name
    except Exception:
        return ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. Select Common Details linked model (or 'None' if exporting from CD itself)
# ─────────────────────────────────────────────────────────────────────────────

NONE_OPTION = "None (I'm exporting from the Common Details file itself)"

link_instances = DB.FilteredElementCollector(doc)\
    .OfClass(DB.RevitLinkInstance)\
    .ToElements()

link_by_name = {li.Name: li for li in link_instances}
link_options = [NONE_OPTION] + sorted(link_by_name.keys())

chosen_link = ui.pick_list(
    link_options,
    "1 of 3 — Reference Linked Model",
    button_name="Next",
    multiselect=False,
    context=u"Pick the Common Details link if you're exporting from a BUILDING — "
            u"the tool uses its transform to convert the building's custom-detail "
            u"coordinates into Common Details space. Pick 'None' if you're "
            u"exporting from the Common Details file itself (no transform needed; "
            u"coordinates are already in CD local space)."
)
if not chosen_link:
    script.exit()

if chosen_link == NONE_OPTION:
    link_instance  = None
    link_transform = None
else:
    link_instance  = link_by_name[chosen_link]
    link_transform = link_instance.GetTotalTransform()

# ─────────────────────────────────────────────────────────────────────────────
# 2. Select sheets
# ─────────────────────────────────────────────────────────────────────────────

all_sheets = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
all_sheets = sorted(all_sheets, key=lambda s: s.SheetNumber)

if not all_sheets:
    ui.alert("No sheets found in the model.", title="Export Sheets with Views")
    script.exit()

sheet_options   = []
sheet_by_option = {}
for s in all_sheets:
    label = "{} - {}".format(s.SheetNumber, s.Name)
    sheet_options.append(label)
    sheet_by_option[label] = s

chosen_options = ui.pick_list(
    sheet_options,
    "2 of 3 — Select Sheets",
    button_name="Next",
    context=u"Tick the sheets in this building that hold custom details you want to "
            u"migrate into Common Details. The export bundles BOTH the dependent views "
            u"and the sheet layout into a single JSON — then you import it into Common "
            u"Details with 'Import Sheets with Views'."
)
if not chosen_options:
    script.exit()

selected_sheets = [sheet_by_option[o] for o in chosen_options]

# ─────────────────────────────────────────────────────────────────────────────
# 3. Choose output file
# ─────────────────────────────────────────────────────────────────────────────

today = date.today().strftime("%Y%m%d")
doc_title = doc.Title.split(".")[0] if doc.Title else "Building"

save_path = forms.save_file(
    file_ext="json",
    default_name="{}_Sheets with views_{}.json".format(today, doc_title),
    title="3 of 3 — Save Sheets with Views JSON"
)
if not save_path:
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 4. Index views from selected sheets
# ─────────────────────────────────────────────────────────────────────────────

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

dep_view_by_id    = {}
master_view_by_id = {}
for v in all_views:
    try:
        pid = v.GetPrimaryViewId()
        if pid != DB.ElementId.InvalidElementId:
            dep_view_by_id[v.Id.IntegerValue] = v
        else:
            master_view_by_id[v.Id.IntegerValue] = v
    except Exception:
        pass

master_to_deps = {}  # master_name -> list of dep views

for sheet in selected_sheets:
    for vp_id in sheet.GetAllViewports():
        vp = doc.GetElement(vp_id)
        if vp is None:
            continue
        view_id = vp.ViewId.IntegerValue
        if view_id not in dep_view_by_id:
            continue
        dep = dep_view_by_id[view_id]
        try:
            primary_id  = dep.GetPrimaryViewId().IntegerValue
            primary     = master_view_by_id.get(primary_id)
            master_name = primary.Name if primary else "Unknown Master"
        except Exception:
            master_name = "Unknown Master"
        master_to_deps.setdefault(master_name, [])
        if dep not in master_to_deps[master_name]:
            master_to_deps[master_name].append(dep)

if not master_to_deps:
    ui.alert(
        "No dependent views found on the selected sheets.\n"
        "Make sure the sheets contain dependent views (not master views or schedules).",
        title="Export Sheets with Views"
    )
    script.exit()

total_found = sum(len(deps) for deps in master_to_deps.values())

# ─────────────────────────────────────────────────────────────────────────────
# 5. Build master_views section
# ─────────────────────────────────────────────────────────────────────────────

master_views_data = []
results   = []
exported  = 0
errors    = 0
cancelled = False

with ui.ProgressBar(title=u"Export Sheets with Views", cancellable=True, step=1) as pb:
    done = 0
    for master_name, dep_views in sorted(master_to_deps.items()):
        view_data = {
            "view_name":       master_name,
            "view_scale":      dep_views[0].Scale if dep_views else 1,
            "dependent_views": []
        }

        for dep in dep_views:
            if pb.cancelled:
                cancelled = True
                break
            pb.title = u"Exporting views — {}/{} — {}".format(
                done + 1, total_found, dep.Name)
            pb.update_progress(done + 1, total_found)
            done += 1

            try:
                crop_box = dep.CropBox
                if crop_box is None:
                    results.append(("warning", master_name, dep.Name, u"No CropBox"))
                    continue

                min_pt    = crop_box.Min
                max_pt    = crop_box.Max
                transform = crop_box.Transform

                local_corners = [
                    DB.XYZ(min_pt.X, min_pt.Y, min_pt.Z),
                    DB.XYZ(max_pt.X, min_pt.Y, min_pt.Z),
                    DB.XYZ(max_pt.X, max_pt.Y, min_pt.Z),
                    DB.XYZ(min_pt.X, max_pt.Y, min_pt.Z),
                ]
                world_corners = [transform.OfPoint(c) for c in local_corners]
                rel_corners   = [xyz_to_list(world_to_link(c, link_transform))
                                 for c in world_corners]

                title_on_sheet = ""
                try:
                    p = dep.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
                    if p:
                        title_on_sheet = p.AsString() or ""
                except Exception:
                    pass

                template_name = ""
                tid = dep.ViewTemplateId
                if tid != DB.ElementId.InvalidElementId:
                    tmpl = doc.GetElement(tid)
                    if tmpl:
                        template_name = tmpl.Name

                view_data["dependent_views"].append({
                    "view_name":      dep.Name,
                    "detail_id":      get_detail_id(dep),
                    "title_on_sheet": title_on_sheet,
                    "crop_corners":   rel_corners,
                    "view_template":  template_name,
                    "view_scale":     dep.Scale,
                    "view_type":      str(dep.ViewType),
                })

                results.append(("exported", master_name, dep.Name,
                                title_on_sheet or u"(no title)"))
                exported += 1

            except Exception as e:
                results.append(("error", master_name, dep.Name, str(e)))
                errors += 1

        master_views_data.append(view_data)

        if cancelled:
            break

# ─────────────────────────────────────────────────────────────────────────────
# 5a. Pass B — capture orphan dependents (created but not placed on
#     selected sheets). Same masters as Pass A; we only add dep views that
#     weren't already exported. This brings parity with the legacy
#     "Export Views" tool which captured ALL dependents unfiltered.
# ─────────────────────────────────────────────────────────────────────────────

orphan_count = 0

# Tag each Pass-A dep as placed (so consumers can tell apart)
for mv_data in master_views_data:
    for dv in mv_data["dependent_views"]:
        dv.setdefault("placed_on_sheet", True)

# Reverse index: master name → master view object (Pass A only kept names)
master_view_by_name = {v.Name: v for v in master_view_by_id.values()}

if not cancelled:
    for mv_data in master_views_data:
        mv_name = mv_data["view_name"]
        master_obj = master_view_by_name.get(mv_name)
        if master_obj is None:
            # "Unknown Master" or master we couldn't resolve — can't enumerate deps
            continue
        already_exported = {dv["view_name"] for dv in mv_data["dependent_views"]}
        for dep_id in master_obj.GetDependentViewIds():
            dep = doc.GetElement(dep_id)
            if dep is None or dep.Name in already_exported:
                continue
            try:
                crop_box = dep.CropBox
                if crop_box is None:
                    results.append(("warning", mv_name, dep.Name,
                                    u"Orphan skipped: no CropBox"))
                    continue

                min_pt    = crop_box.Min
                max_pt    = crop_box.Max
                transform = crop_box.Transform
                local_corners = [
                    DB.XYZ(min_pt.X, min_pt.Y, min_pt.Z),
                    DB.XYZ(max_pt.X, min_pt.Y, min_pt.Z),
                    DB.XYZ(max_pt.X, max_pt.Y, min_pt.Z),
                    DB.XYZ(min_pt.X, max_pt.Y, min_pt.Z),
                ]
                world_corners = [transform.OfPoint(c) for c in local_corners]
                rel_corners   = [xyz_to_list(world_to_link(c, link_transform))
                                 for c in world_corners]

                title_on_sheet = ""
                try:
                    p = dep.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
                    if p:
                        title_on_sheet = p.AsString() or ""
                except Exception:
                    pass

                template_name = ""
                tid = dep.ViewTemplateId
                if tid != DB.ElementId.InvalidElementId:
                    tmpl = doc.GetElement(tid)
                    if tmpl:
                        template_name = tmpl.Name

                mv_data["dependent_views"].append({
                    "view_name":       dep.Name,
                    "detail_id":       get_detail_id(dep),
                    "title_on_sheet":  title_on_sheet,
                    "crop_corners":    rel_corners,
                    "view_template":   template_name,
                    "view_scale":      dep.Scale,
                    "view_type":       str(dep.ViewType),
                    "placed_on_sheet": False,
                })
                results.append(("orphan", mv_name, dep.Name,
                                title_on_sheet or u"(no title)"))
                orphan_count += 1
            except Exception as e:
                results.append(("error", mv_name, dep.Name, u"Orphan: " + str(e)))
                errors += 1

# ─────────────────────────────────────────────────────────────────────────────
# 5b. Build sheets section (separate pass over selected_sheets)
# ─────────────────────────────────────────────────────────────────────────────

sheet_layout = []

for sheet in selected_sheets:
    sheet_entry = {
        "sheet_number": sheet.SheetNumber,
        "sheet_name":   sheet.Name,
        "viewports":    [],
        "detail_lines": []
    }
    for vp_id in sheet.GetAllViewports():
        vp = doc.GetElement(vp_id)
        if vp is None:
            continue
        view_id = vp.ViewId.IntegerValue
        if view_id not in dep_view_by_id:
            continue
        v      = dep_view_by_id[view_id]
        center = vp.GetBoxCenter()
        try:
            label_x = vp.LabelOffset.X
            label_y = vp.LabelOffset.Y
        except Exception:
            label_x = label_y = 0
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
        template_name = ""
        tid = v.ViewTemplateId
        if tid != DB.ElementId.InvalidElementId:
            tmpl = doc.GetElement(tid)
            if tmpl:
                template_name = tmpl.Name
        sheet_entry["viewports"].append({
            "view_name":         v.Name,
            "viewport_center_x": center.X,
            "viewport_center_y": center.Y,
            "label_offset_x":    label_x,
            "label_offset_y":    label_y,
            "viewport_type":     get_viewport_type_name(vp),
            "view_scale":        v.Scale,
            "view_template":     template_name,
            "title_on_sheet":    title_on_sheet,
            "detail_number":     detail_number,
            "view_type":         str(v.ViewType),
        })
    for dl in DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.CurveElement).ToElements():
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
        except Exception:
            pass
    sheet_layout.append(sheet_entry)

# ─────────────────────────────────────────────────────────────────────────────
# 6. Save combined JSON
# ─────────────────────────────────────────────────────────────────────────────

export_data = {
    "format":        "sheets_with_views",
    "link_doc_name": "" if link_transform is None else chosen_link,
    "link_origin":   [0.0, 0.0, 0.0] if link_transform is None
                                     else xyz_to_list(link_transform.Origin),
    "exported_from_cd": link_transform is None,
    "master_views":  master_views_data,
    "sheets":        sheet_layout,
}

with open(save_path, "w") as f:
    json.dump(export_data, f, indent=2)

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
        "warning":  u"⚠️  Warning",
        "error":    u"❌  Error",
    }
    def __init__(self, status, master, name, detail):
        self._status = status
        self._master = master
        self._name   = name
        self._detail = detail

    @property
    def Status(self):   return self._LABELS.get(self._status, self._status)
    @property
    def Master(self):   return self._master
    @property
    def ViewName(self): return self._name
    @property
    def Detail(self):   return self._detail


subtitle = u"{} views  \xb7  {} sheets  \xb7  {}".format(
    exported, len(sheet_layout), _os.path.basename(save_path))
if errors:
    subtitle += u"  \xb7  {} error{}".format(errors, u"s" if errors != 1 else u"")
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
      <TextBlock x:Name="badgeViews" Foreground="#50E898" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border Background="#1A2535" BorderBrush="#5B8EC4" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeSheets" Foreground="#5B8EC4" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border Background="#142244" BorderBrush="#7EB4F0" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeLink" Foreground="#7EB4F0" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeOrphanBorder" Background="#2A1E40" BorderBrush="#B8A0E8"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeOrphan" Foreground="#B8A0E8" FontFamily="Segoe UI" FontSize="13"/>
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
      <DataGridTextColumn Header="Status"         Binding="{Binding Status}"   Width="140"/>
      <DataGridTextColumn Header="Master View"    Binding="{Binding Master}"   Width="200"/>
      <DataGridTextColumn Header="View Name"      Binding="{Binding ViewName}" Width="*"/>
      <DataGridTextColumn Header="Title on Sheet" Binding="{Binding Detail}"   Width="200"/>
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
for status, master, name, detail in results:
    rows.Add(_Row(status, master, name, detail))

win = ui.parse(u"Export Sheets with Views", subtitle, _BODY_XAML, _FOOTER_XAML, width=960, height=560)

win.FindName("badgeViews").Text   = u"✅  {} views exported".format(exported)
if orphan_count:
    win.FindName("badgeOrphanBorder").Visibility = Visibility.Visible
    win.FindName("badgeOrphan").Text = u"🌒  {} orphan view{} (not placed)".format(
        orphan_count, u"s" if orphan_count != 1 else u"")
win.FindName("badgeSheets").Text  = u"\U0001f4c4  {} sheets".format(len(sheet_layout))
if link_transform is None:
    win.FindName("badgeLink").Text = u"\U0001f3e0  Exported from Common Details (no link)"
else:
    win.FindName("badgeLink").Text = u"\U0001f517  {}".format(
        chosen_link.split(" : ")[-1] if " : " in chosen_link else chosen_link)
if errors:
    win.FindName("badgeErrorBorder").Visibility = Visibility.Visible
    win.FindName("badgeErrors").Text = u"❌  {} error{}".format(
        errors, u"s" if errors != 1 else u"")

win.FindName("dgResults").ItemsSource = rows

def on_copy(s, e):
    lines = [u"Export Sheets with Views — " + subtitle, u""]
    for r in rows:
        lines.append(u"{}  |  {}  |  {}  |  {}".format(
            r.Status, r.Master, r.ViewName, r.Detail))
    WinFormsClipboard.SetText(u"\n".join(lines))
    s.Content = u"Copied ✓"

win.FindName("btnCopy").Click += on_copy
win.FindName("btnOK").Click   += lambda s, e: win.Close()
win.ShowDialog()
