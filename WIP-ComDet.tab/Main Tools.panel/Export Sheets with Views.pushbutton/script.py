# -*- coding: utf-8 -*-
__title__ = 'Export\nDetails'
__doc__ = ('Bundles dependent views + sheet layout from this file into a single '
           'JSON. The JSON can later be imported into any other Revit file (or '
           'back into this one). Common Details is the coordinate hub — any '
           'source/destination references CD via its linked instance, so '
           'coordinates can be translated cleanly across the 4 directions: '
           'CD->building, building->CD, building->building, CD->CD (round-trip).')

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

_vp_type_cache = {}

def get_viewport_type_name(vp):
    try:
        type_id = vp.GetTypeId()
        k = type_id.IntegerValue
        if k not in _vp_type_cache:
            vp_type = doc.GetElement(type_id)
            if vp_type is None:
                _vp_type_cache[k] = ""
            else:
                p = vp_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
                _vp_type_cache[k] = (p.AsString() or "") if p else vp_type.Name
        return _vp_type_cache[k]
    except Exception:
        return ""

# ── Template-name cache ───────────────────────────────────────────────────────
_template_cache = {}

def get_template_name(tid):
    if tid == DB.ElementId.InvalidElementId:
        return ""
    k = tid.IntegerValue
    if k not in _template_cache:
        tmpl = doc.GetElement(tid)
        _template_cache[k] = tmpl.Name if tmpl else ""
    return _template_cache[k]

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
    context=u"Pick the Common Details link if this file is a BUILDING — its "
            u"transform translates view geometry into Common Details space, so "
            u"the JSON is portable to any destination (other buildings or CD "
            u"itself). Pick 'None' if THIS file IS Common Details — coordinates "
            u"are written as-is (no transform needed)."
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
    context=u"Pick which sheets to bundle into the JSON. Every dependent view "
            u"placed on these sheets is captured with its crop geometry and "
            u"placement data. Views on sheets you didn't select are ignored — "
            u"only what's on the selected sheets ends up in the file."
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
orphan_count = 0  # kept for backwards compat with result window (always 0 now)

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

                template_name = get_template_name(dep.ViewTemplateId)

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
# 5b. Build sheets section (separate pass over selected_sheets)
# ─────────────────────────────────────────────────────────────────────────────

# ── Pre-bucket all sheet-level DetailLines (one global pass) ──────────────────
# FilteredElementCollector(doc, sheet.Id) forces Revit to compute element
# visibility for that view (loads view state, resolves VT, linked visibility).
# One global pass + dict bucket eliminates the O(N) per-sheet overhead.
_all_detail_lines = (DB.FilteredElementCollector(doc)
                     .OfClass(DB.CurveElement)
                     .ToElements())
_sheet_detail_lines = {}
for _dl in _all_detail_lines:
    if not isinstance(_dl, DB.DetailLine):
        continue
    _sid = _dl.OwnerViewId.IntegerValue
    if _sid not in _sheet_detail_lines:
        _sheet_detail_lines[_sid] = []
    _sheet_detail_lines[_sid].append(_dl)

sheet_layout = []
_n_sheets = len(selected_sheets)

with ui.ProgressBar(title=u"Building sheet layout", cancellable=False, step=1) as pb2:
  for _sheet_idx, sheet in enumerate(selected_sheets):
    pb2.title = u"Sheet layout — {}/{} — {} {}".format(
        _sheet_idx + 1, _n_sheets, sheet.SheetNumber, sheet.Name)
    pb2.update_progress(_sheet_idx + 1, _n_sheets)
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
        # Capture BoxOutline + LabelOutline for diagnostic comparison in import
        try:
            _out = vp.GetBoxOutline()
            _bo_min_x = _out.MinimumPoint.X
            _bo_min_y = _out.MinimumPoint.Y
            _bo_max_x = _out.MaximumPoint.X
            _bo_max_y = _out.MaximumPoint.Y
        except Exception:
            _bo_min_x = _bo_min_y = _bo_max_x = _bo_max_y = None
        try:
            _lout = vp.GetLabelOutline()
            _lo_min_x = _lout.MinimumPoint.X
            _lo_min_y = _lout.MinimumPoint.Y
            _lo_max_x = _lout.MaximumPoint.X
            _lo_max_y = _lout.MaximumPoint.Y
        except Exception:
            _lo_min_x = _lo_min_y = _lo_max_x = _lo_max_y = None
        try:
            label_x = vp.LabelOffset.X
            label_y = vp.LabelOffset.Y
        except Exception:
            label_x = label_y = 0
        # Title-line length — SIGNED: negative = line points LEFT, positive = RIGHT.
        # (Revit 2022+; one value carries both length and direction.)
        try:
            label_line_length = vp.LabelLineLength
        except Exception:
            label_line_length = None
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
        template_name = get_template_name(v.ViewTemplateId)
        sheet_entry["viewports"].append({
            "view_name":         v.Name,
            "viewport_center_x": center.X,
            "viewport_center_y": center.Y,
            "label_offset_x":    label_x,
            "label_offset_y":    label_y,
            "label_line_length": label_line_length,
            "viewport_type":     get_viewport_type_name(vp),
            "view_scale":        v.Scale,
            "view_template":     template_name,
            "title_on_sheet":    title_on_sheet,
            "detail_number":     detail_number,
            "view_type":         str(v.ViewType),
            # Diagnostic — BoxOutline + LabelOutline dimensions on the source sheet
            "box_min_x":         _bo_min_x,
            "box_min_y":         _bo_min_y,
            "box_max_x":         _bo_max_x,
            "box_max_y":         _bo_max_y,
            "label_min_x":       _lo_min_x,
            "label_min_y":       _lo_min_y,
            "label_max_x":       _lo_max_x,
            "label_max_y":       _lo_max_y,
        })
    for dl in _sheet_detail_lines.get(sheet.Id.IntegerValue, []):
        try:
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
# 7. Noir results window — grouped by sheet (collapsible)
# ─────────────────────────────────────────────────────────────────────────────

import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('PresentationCore')
from System.Windows import Visibility
from System.Windows.Forms import Clipboard as WinFormsClipboard
from System.Collections.ObjectModel import ObservableCollection


class _ViewRow(object):
    _ICONS = {"exported": u"✅", "warning": u"⚠️", "error": u"❌"}
    def __init__(self, status, master, name, detail):
        self._status = status
        self._master = master
        self._name   = name
        self._detail = detail
    @property
    def StatusIcon(self): return self._ICONS.get(self._status, u"\xb7")
    @property
    def Master(self):     return self._master
    @property
    def ViewName(self):   return self._name
    @property
    def Detail(self):     return self._detail


class _SheetGroup(object):
    def __init__(self, number, name, views):
        self._number = number
        self._name   = name
        self._views  = views
    @property
    def SheetNumber(self): return self._number
    @property
    def SheetName(self):   return self._name
    @property
    def Count(self):
        n = len(self._views)
        return u"{} view{}".format(n, u"s" if n != 1 else u"")
    @property
    def Views(self):       return self._views


# Build view → result lookup from Pass A
view_result_by_name = {}
for _st, _master, _name, _detail in results:
    view_result_by_name[_name] = (_st, _master, _detail)

# Build sheet groups (one per selected sheet, views in order they appear)
sheet_groups = ObservableCollection[_SheetGroup]()
for sh_entry in sheet_layout:
    views_oc = ObservableCollection[_ViewRow]()
    for vp_entry in sh_entry["viewports"]:
        vname = vp_entry["view_name"]
        if vname in view_result_by_name:
            _st, _master, _detail = view_result_by_name[vname]
        else:
            _st, _master, _detail = "warning", u"", u"(not captured)"
        views_oc.Add(_ViewRow(_st, _master, vname, _detail))
    sheet_groups.Add(_SheetGroup(
        sh_entry["sheet_number"], sh_entry["sheet_name"], views_oc))


subtitle = u"{} views  \xb7  {} sheets  \xb7  {}".format(
    exported, len(sheet_layout), _os.path.basename(save_path))
if errors:
    subtitle += u"  \xb7  {} error{}".format(errors, u"s" if errors != 1 else u"")
if cancelled:
    subtitle += u"  \xb7  ⚠ partial"


_BODY_XAML = u"""
<Grid>
  <Grid.Resources>

    <!-- Dark Expander — full ControlTemplate so the header row is
         completely re-skinned: arrow glyph + Noir background. -->
    <Style TargetType="Expander">
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Expander">
            <StackPanel>

              <!-- Header toggle button -->
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

              <!-- Collapsible body -->
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
    <RowDefinition Height="*"/>
  </Grid.RowDefinitions>

  <!-- Badge row -->
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
    <Border x:Name="badgeErrorBorder" Background="#3C1212" BorderBrush="#FF7070"
            BorderThickness="1" CornerRadius="4" Padding="10,4"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeErrors" Foreground="#FF7070" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
  </StackPanel>

  <!-- Sheet list -->
  <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto">
    <ItemsControl x:Name="icSheets">
      <ItemsControl.ItemTemplate>
        <DataTemplate>
          <Expander Margin="0,0,0,2">

            <!-- Sheet header: [A1.01]  Sheet name  · N views -->
            <Expander.Header>
              <StackPanel Orientation="Horizontal">
                <Border Background="#0F3038" BorderBrush="#5ED4E6" BorderThickness="1"
                        CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
                  <TextBlock Text="{Binding SheetNumber}" Foreground="#5ED4E6"
                             FontFamily="Consolas" FontSize="12"/>
                </Border>
                <TextBlock Text="{Binding SheetName}" Foreground="#E8EBF5"
                           FontFamily="Segoe UI" FontSize="13"
                           VerticalAlignment="Center" Margin="0,0,12,0"/>
                <Border Background="#1A1D30" CornerRadius="4" Padding="7,2">
                  <TextBlock Text="{Binding Count}" Foreground="#6B7394"
                             FontFamily="Segoe UI" FontSize="11"/>
                </Border>
              </StackPanel>
            </Expander.Header>

            <!-- View rows (indented, compact) -->
            <ItemsControl ItemsSource="{Binding Views}">
              <ItemsControl.ItemTemplate>
                <DataTemplate>
                  <Border Background="#12131F" BorderBrush="#2A2D47"
                          BorderThickness="0,0,0,1" Padding="44,0,16,0">
                    <Grid MinHeight="26">
                      <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="22"/>
                        <ColumnDefinition Width="180"/>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="170"/>
                      </Grid.ColumnDefinitions>
                      <TextBlock Grid.Column="0" Text="{Binding StatusIcon}"
                                 FontFamily="Segoe UI" FontSize="11"
                                 VerticalAlignment="Center"/>
                      <TextBlock Grid.Column="1" Text="{Binding Master}"
                                 Foreground="#9099C8" FontFamily="Segoe UI" FontSize="12"
                                 VerticalAlignment="Center" Margin="0,0,8,0"
                                 TextTrimming="CharacterEllipsis"/>
                      <TextBlock Grid.Column="2" Text="{Binding ViewName}"
                                 Foreground="#E8EBF5" FontFamily="Segoe UI" FontSize="12"
                                 VerticalAlignment="Center" Margin="0,0,8,0"
                                 TextTrimming="CharacterEllipsis"/>
                      <TextBlock Grid.Column="3" Text="{Binding Detail}"
                                 Foreground="#6B7394" FontFamily="Segoe UI" FontSize="12"
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

win = ui.parse(u"Export Sheets with Views", subtitle, _BODY_XAML, _FOOTER_XAML, width=900, height=560)

win.FindName("badgeViews").Text  = u"✅  {} views exported".format(exported)
win.FindName("badgeSheets").Text = u"\U0001f4c4  {} sheets".format(len(sheet_layout))
if link_transform is None:
    win.FindName("badgeLink").Text = u"\U0001f3e0  Exported from Common Details (no link)"
else:
    win.FindName("badgeLink").Text = u"\U0001f517  {}".format(
        chosen_link.split(" : ")[-1] if " : " in chosen_link else chosen_link)
if errors:
    win.FindName("badgeErrorBorder").Visibility = Visibility.Visible
    win.FindName("badgeErrors").Text = u"❌  {} error{}".format(
        errors, u"s" if errors != 1 else u"")

win.FindName("icSheets").ItemsSource = sheet_groups


def on_copy(s, e):
    lines = [u"Export Sheets with Views — " + subtitle, u""]
    for sg in sheet_groups:
        lines.append(u"{}  {}  ({})".format(sg.SheetNumber, sg.SheetName, sg.Count))
        for vr in sg.Views:
            lines.append(u"    {}  {}  |  {}  |  {}".format(
                vr.StatusIcon, vr.Master, vr.ViewName, vr.Detail))
        lines.append(u"")
    WinFormsClipboard.SetText(u"\n".join(lines))
    s.Content = u"Copied ✓"


win.FindName("btnCopy").Click += on_copy
win.FindName("btnOK").Click   += lambda s, e: win.Close()
win.ShowDialog()
