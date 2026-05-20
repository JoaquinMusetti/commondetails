# -*- coding: utf-8 -*-
__title__ = 'Export\nViews'
__doc__ = ('Exports crop boundaries, view names, and title on sheet '
           'for dependent views of selected master views to a dated JSON file. '
           'Run this from the Common Details file. '
           'Coordinates are stored in the document\'s local space.')

import json
import sys
import os as _os
from datetime import date
from pyrevit import revit, DB, script, forms, HOST_APP
from Autodesk.Revit.DB import BuiltInParameterGroup, BuiltInCategory

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

_SPF_PATH = _os.path.join(_ext_dir, 'lib', 'cucosync_shared_params.txt')

def xyz_to_list(pt):
    return [pt.X, pt.Y, pt.Z]

def ensure_detail_id_param(doc, app):
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        if it.Key.Name == "Detail ID":
            return False
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
            defn = grp.Definitions.Create(opts)
        cat_set = app.Create.NewCategorySet()
        cat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Views)
        if cat:
            cat_set.Insert(cat)
        binding = app.Create.NewInstanceBinding(cat_set)
        doc.ParameterBindings.Insert(defn, binding, BuiltInParameterGroup.PG_IDENTITY_DATA)
        return True
    finally:
        app.SharedParametersFilename = orig

def get_detail_id(view):
    p = view.LookupParameter("Detail ID")
    return (p.AsString() or "") if p else ""

def next_available_id(dep_views):
    max_id = 0
    for v in dep_views:
        val = get_detail_id(v)
        if val:
            try:
                max_id = max(max_id, int(val))
            except ValueError:
                pass
    return max_id + 1

# ─────────────────────────────────────────────────────────────────────────────
# 1. Select master views
# ─────────────────────────────────────────────────────────────────────────────

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

master_views = []
for v in all_views:
    try:
        if v.IsTemplate:
            continue
        if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
            continue
        if not v.CanViewBeDuplicated(DB.ViewDuplicateOption.AsDependent):
            continue
        master_views.append(v)
    except Exception:
        pass

master_views = sorted(master_views, key=lambda v: v.Name)
view_by_name = {v.Name: v for v in master_views}

chosen_views = ui.pick_list(
    [v.Name for v in master_views],
    "Select Master Views to Export",
    button_name="Export",
    context=u"Pick the master views whose dependents you want to export to JSON. "
            u"The JSON stores the crop box geometry (in CD local space) and metadata — "
            u"it does NOT include sheet layout. If you also need sheets, use "
            u"'Export Sheets with Views' instead."
)
if not chosen_views:
    script.exit()

selected_views = [view_by_name[n] for n in chosen_views]

# ─────────────────────────────────────────────────────────────────────────────
# 2. Choose output file
# ─────────────────────────────────────────────────────────────────────────────

today = date.today().strftime("%Y%m%d")

save_path = forms.save_file(
    file_ext="json",
    default_name="{}_Dependent views.json".format(today),
    title="Save Dependent Views"
)
if not save_path:
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 3. Ensure Detail ID parameter + backfill missing IDs
# ─────────────────────────────────────────────────────────────────────────────

with revit.Transaction("Add Detail ID parameter"):
    ensure_detail_id_param(doc, HOST_APP.app)

all_dep_views = []
for v in all_views:
    try:
        if not v.IsTemplate and v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
            all_dep_views.append(v)
    except Exception:
        pass

backfill_count = 0
views_needing_id = [v for v in all_dep_views if not get_detail_id(v)]
if views_needing_id:
    next_id = next_available_id(all_dep_views)
    with revit.Transaction("Backfill Detail IDs"):
        for v in views_needing_id:
            try:
                p = v.LookupParameter("Detail ID")
                if p and not p.IsReadOnly:
                    p.Set("{:04d}".format(next_id))
                    next_id += 1
                    backfill_count += 1
            except Exception:
                pass

# ─────────────────────────────────────────────────────────────────────────────
# 4. Collect dependent views per master view
# ─────────────────────────────────────────────────────────────────────────────

export_data = {
    "link_doc_name": doc.Title,
    "link_origin":   [0, 0, 0],
    "master_views":  []
}

results          = []
exported         = 0
errors           = 0
skipped_not_det  = 0
cancelled        = False

with ui.ProgressBar(title=u"Export Views", cancellable=True, step=1) as pb:
    for i, master_view in enumerate(selected_views):
        if pb.cancelled:
            cancelled = True
            break
        pb.title = u"Export Views — {}/{} — {}".format(i + 1, len(selected_views), master_view.Name)
        pb.update_progress(i + 1, len(selected_views))

        view_data = {
            "view_name":       master_view.Name,
            "view_scale":      master_view.Scale,
            "dependent_views": []
        }

        for vid in master_view.GetDependentViewIds():
            dep = doc.GetElement(vid)
            if dep is None:
                continue
            if get_detail_id(dep).strip().lower() == "not a detail":
                skipped_not_det += 1
                continue
            try:
                crop_box = dep.CropBox
                if crop_box is None:
                    results.append(("warning", master_view.Name, dep.Name, u"No CropBox"))
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
                rel_corners   = [xyz_to_list(c) for c in world_corners]

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

                results.append(("exported", master_view.Name, dep.Name,
                                title_on_sheet or u"(no title)"))
                exported += 1

            except Exception as e:
                results.append(("error", master_view.Name, dep.Name, str(e)))
                errors += 1

        export_data["master_views"].append(view_data)
        if cancelled:
            break

# ─────────────────────────────────────────────────────────────────────────────
# 5. Save JSON
# ─────────────────────────────────────────────────────────────────────────────

with open(save_path, "w") as f:
    json.dump(export_data, f, indent=2)

# ─────────────────────────────────────────────────────────────────────────────
# 6. Noir results window
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


subtitle = u"{} exported  ·  {}".format(exported, _os.path.basename(save_path))
if errors:
    subtitle += u"  ·  {} error{}".format(errors, u"s" if errors != 1 else u"")
if skipped_not_det:
    subtitle += u"  ·  {} skipped (not a detail)".format(skipped_not_det)
if backfill_count:
    subtitle += u"  ·  {} IDs backfilled".format(backfill_count)
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
      <TextBlock x:Name="badgeExported" Foreground="#50E898" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeSkippedBorder" Background="#1E2235" BorderBrush="#64748B"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeSkipped" Foreground="#64748B" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeBackfillBorder" Background="#4A3810" BorderBrush="#FFCC66"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeBackfill" Foreground="#FFCC66" FontFamily="Segoe UI" FontSize="13"/>
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

win = ui.parse(u"Export Views", subtitle, _BODY_XAML, _FOOTER_XAML, width=960, height=560)

win.FindName("badgeExported").Text = u"✅  {} exported".format(exported)
if skipped_not_det:
    win.FindName("badgeSkippedBorder").Visibility = Visibility.Visible
    win.FindName("badgeSkipped").Text = u"⊘  {} not a detail".format(skipped_not_det)
if backfill_count:
    win.FindName("badgeBackfillBorder").Visibility = Visibility.Visible
    win.FindName("badgeBackfill").Text = u"🔖  {} IDs backfilled".format(backfill_count)
if errors:
    win.FindName("badgeErrorBorder").Visibility = Visibility.Visible
    win.FindName("badgeErrors").Text = u"❌  {} error{}".format(errors, u"s" if errors != 1 else u"")

win.FindName("dgResults").ItemsSource = rows

def on_copy(s, e):
    lines = [u"Export Views — " + subtitle, u""]
    for r in rows:
        line = u"{}  |  {}  |  {}".format(r.Status, r.Master, r.ViewName)
        if r.Detail:
            line += u"  |  " + r.Detail
        lines.append(line)
    WinFormsClipboard.SetText(u"\n".join(lines))
    s.Content = u"Copied ✓"

win.FindName("btnCopy").Click += on_copy
win.FindName("btnOK").Click   += lambda s, e: win.Close()
win.ShowDialog()
