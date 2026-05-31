# -*- coding: utf-8 -*-
__title__ = 'Assign\nDetail IDs'
__doc__ = ('Scans all dependent views in this document and assigns a sequential '
           '4-digit Detail ID to any view that does not have one yet. '
           'Run this in Common Details before exporting. '
           'Select sheets to act on their dependent views.')

import sys
import os as _os
from collections import defaultdict
from pyrevit import revit, DB, script
from Autodesk.Revit.DB import BuiltInParameterGroup, BuiltInCategory

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

from System.Collections.ObjectModel import ObservableCollection
from System.Windows import Clipboard

doc    = revit.doc
app    = revit.HOST_APP.app
output = script.get_output()
output.close()

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

_SPF_PATH = _os.path.join(_ext_dir, 'lib', 'common_details_shared_params.txt')


def _patch_spf_varies(spf_path, param_name):
    """Edit the SPF text file so VARIES_ACROSS_GROUPS=1 for param_name.
    The SPF format is tab-separated:
      PARAM  GUID  NAME  DATATYPE  DATACATEGORY  GROUP  VISIBLE  DESCRIPTION  USERMODIFIABLE  VARIES_ACROSS_GROUPS
    Returns True if the file was modified."""
    try:
        with open(spf_path, 'r') as f:
            lines = f.readlines()
        new_lines = []
        changed = False
        for line in lines:
            if line.startswith('PARAM\t'):
                parts = line.rstrip('\r\n').split('\t')
                # parts[2] is NAME
                if len(parts) >= 3 and parts[2] == param_name:
                    # Pad to at least 10 fields
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
    # Patch the SPF file first so VariesAcrossGroups=1 is baked in before Revit reads it
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
        # (VariesAcrossGroups already patched in the SPF file above)

        cat_set = app.Create.NewCategorySet()
        cat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Views)
        if cat:
            cat_set.Insert(cat)
        binding = app.Create.NewInstanceBinding(cat_set)

        # Check what's already bound in the project
        existing_key  = None
        is_instance   = False
        it = doc.ParameterBindings.ForwardIterator()
        while it.MoveNext():
            if it.Key.Name == "Detail ID":
                existing_key = it.Key
                is_instance  = isinstance(it.Current, DB.InstanceBinding)
                break

        if existing_key is None:
            # First time — insert fresh
            doc.ParameterBindings.Insert(defn, binding, BuiltInParameterGroup.PG_IDENTITY_DATA)
        elif is_instance:
            # Already an instance binding — ReInsert to pick up VariesAcrossGroups=True
            # without losing existing values
            doc.ParameterBindings.ReInsert(defn, binding, BuiltInParameterGroup.PG_IDENTITY_DATA)
        else:
            # Was a type binding — remove and re-add (values would have been unusable anyway)
            doc.ParameterBindings.Remove(existing_key)
            doc.ParameterBindings.Insert(defn, binding, BuiltInParameterGroup.PG_IDENTITY_DATA)
        return True
    finally:
        app.SharedParametersFilename = orig

def _checkout_views(doc, views):
    """Request worksharing checkout for views that need it. No-op if not workshared."""
    if not doc.IsWorkshared:
        return
    try:
        from Autodesk.Revit.DB import WorksharingUtils
        ids = [v.Id for v in views]
        WorksharingUtils.CheckoutElements(doc, ids)
    except Exception:
        pass  # best-effort — individual failures caught in _try_set


def show_result(title, subtitle, body_text):
    """Noir result modal with Copy to Clipboard + Close."""
    _RES_BODY = """
<Grid xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <TextBlock x:Name="res_text"
               Foreground="#E2E8F0" FontFamily="Segoe UI" FontSize="12"
               TextWrapping="Wrap" Margin="4,4,4,4"/>
  </ScrollViewer>
</Grid>"""
    _RES_FOOTER = """
<Grid xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">
  <StackPanel Orientation="Horizontal" HorizontalAlignment="Right"
              VerticalAlignment="Center" Margin="0,0,4,0">
    <Button x:Name="btn_copy" Content="Copy to Clipboard"
            Width="140" Height="30" Margin="0,0,8,0"
            Background="#252742" Foreground="#94A3B8"
            FontFamily="Segoe UI" FontSize="12"
            BorderThickness="1" BorderBrush="#3D4266" Cursor="Hand"/>
    <Button x:Name="btn_close" Content="Close"
            Width="80" Height="30"
            Background="#3B82F6" Foreground="White"
            FontFamily="Segoe UI" FontSize="12"
            BorderThickness="0" Cursor="Hand"/>
  </StackPanel>
</Grid>"""
    w = ui.parse(title, subtitle, _RES_BODY, _RES_FOOTER, width=520, height=300)
    w.FindName("res_text").Text = body_text
    w.FindName("btn_copy").Click  += lambda s, e: Clipboard.SetText(body_text)
    w.FindName("btn_close").Click += lambda s, e: w.Close()
    w.ShowDialog()


def get_detail_id(view):
    p = view.LookupParameter("Detail ID")
    if p:
        return p.AsString() or ""
    return ""

def next_available_id(all_dep_views):
    max_id = 0
    for v in all_dep_views:
        val = get_detail_id(v)
        if val:
            try:
                max_id = max(max_id, int(val))
            except ValueError:
                pass
    return max_id + 1

# ─────────────────────────────────────────────────────────────────────────────
# 1. Ensure parameter exists
# ─────────────────────────────────────────────────────────────────────────────

with revit.Transaction("Add Detail ID parameter"):
    ensure_detail_id_param(doc, app)

# ─────────────────────────────────────────────────────────────────────────────
# 2. Collect dependent views without Detail ID
# ─────────────────────────────────────────────────────────────────────────────

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

dep_views = []
for v in all_views:
    try:
        if v.IsTemplate:
            continue
        if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
            dep_views.append(v)
    except Exception:
        pass

unassigned = [v for v in dep_views if not get_detail_id(v)]

if not unassigned:
    ui.alert("All dependent views already have a Detail ID.\nNothing to assign.",
             title="Assign Detail IDs")
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 3. Map unassigned views → sheets via viewports
# ─────────────────────────────────────────────────────────────────────────────

viewports = DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements()
view_to_sheet = {}
for vp in viewports:
    view_to_sheet[vp.ViewId.IntegerValue] = doc.GetElement(vp.SheetId)

sheet_to_views = defaultdict(list)
sheet_objects  = {}
for v in unassigned:
    sheet = view_to_sheet.get(v.Id.IntegerValue)
    if sheet:
        sid = sheet.Id.IntegerValue
        sheet_to_views[sid].append(v)
        sheet_objects[sid] = sheet

if not sheet_to_views:
    view_list = u"\n".join(u"  • {}".format(v.Name) for v in sorted(unassigned, key=lambda x: x.Name))
    show_result(
        "Assign Detail IDs",
        u"{} unassigned view{} not placed on any sheet".format(
            len(unassigned), "s" if len(unassigned) != 1 else ""),
        u"These dependent views have no Detail ID and are not placed on a sheet:\n\n" + view_list
    )
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 4. WPF row class
# ─────────────────────────────────────────────────────────────────────────────

class SheetRow(object):
    def __init__(self, sheet, views):
        self._sheet = sheet
        self._views = views

    @property
    def SheetNumber(self): return self._sheet.SheetNumber
    @property
    def SheetName(self):   return self._sheet.Name
    @property
    def ViewCount(self):   return str(len(self._views))
    @property
    def Views(self):       return self._views

sheets_involved = sorted(
    [(sheet_objects[sid], views) for sid, views in sheet_to_views.items()],
    key=lambda x: x[0].SheetNumber
)
all_rows = [SheetRow(sheet, views) for sheet, views in sheets_involved]
rows = ObservableCollection[SheetRow](all_rows)

total_on_sheets = sum(len(v) for _, v in sheets_involved)

# ─────────────────────────────────────────────────────────────────────────────
# 5. UI
# ─────────────────────────────────────────────────────────────────────────────

_BODY = """
<Grid xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
      xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="*"/>
  </Grid.RowDefinitions>

  <TextBox x:Name="search_box" Grid.Row="0"
           Margin="0,0,0,8"
           Height="30" Padding="8,0"
           Background="#252742" Foreground="#E2E8F0"
           CaretBrush="#E2E8F0"
           BorderBrush="#2D3152" BorderThickness="1"
           FontFamily="Segoe UI" FontSize="12"
           VerticalContentAlignment="Center"
           Tag="Search sheets…"/>

  <DataGrid x:Name="grid" Grid.Row="1"
            AutoGenerateColumns="False"
            IsReadOnly="True"
            SelectionMode="Extended"
            SelectionUnit="FullRow"
            HeadersVisibility="Column"
            GridLinesVisibility="Horizontal"
            Background="#1A1B2E"
            Foreground="#E2E8F0"
            BorderThickness="0"
            RowBackground="#1A1B2E"
            AlternatingRowBackground="#1E2035"
            HorizontalGridLinesBrush="#2D3152"
            ColumnHeaderHeight="32"
            RowHeight="28"
            FontFamily="Segoe UI"
            FontSize="12">
    <DataGrid.ColumnHeaderStyle>
      <Style TargetType="DataGridColumnHeader">
        <Setter Property="Background" Value="#252742"/>
        <Setter Property="Foreground" Value="#94A3B8"/>
        <Setter Property="FontFamily" Value="Segoe UI"/>
        <Setter Property="FontSize" Value="11"/>
        <Setter Property="Padding" Value="8,0"/>
        <Setter Property="BorderThickness" Value="0"/>
      </Style>
    </DataGrid.ColumnHeaderStyle>
    <DataGrid.CellStyle>
      <Style TargetType="DataGridCell">
        <Setter Property="BorderThickness" Value="0"/>
        <Setter Property="Padding" Value="8,4"/>
        <Setter Property="Foreground" Value="#E2E8F0"/>
        <Setter Property="Background" Value="Transparent"/>
        <Style.Triggers>
          <Trigger Property="IsSelected" Value="True">
            <Setter Property="Background" Value="#2D3152"/>
            <Setter Property="Foreground" Value="#E2E8F0"/>
          </Trigger>
        </Style.Triggers>
      </Style>
    </DataGrid.CellStyle>
    <DataGrid.Columns>
      <DataGridTextColumn Header="Sheet #"    Binding="{Binding SheetNumber}" Width="90"/>
      <DataGridTextColumn Header="Sheet Name" Binding="{Binding SheetName}"   Width="*"/>
      <DataGridTextColumn Header="Views"      Binding="{Binding ViewCount}"   Width="60"/>
    </DataGrid.Columns>
  </DataGrid>
</Grid>
"""

_FOOTER = """
<Grid xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">
  <StackPanel Orientation="Horizontal" HorizontalAlignment="Left" VerticalAlignment="Center">
    <TextBlock x:Name="count_label"
               Foreground="#64748B" FontFamily="Segoe UI" FontSize="12"
               VerticalAlignment="Center"/>
  </StackPanel>
  <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Center" Margin="0,0,4,0">
    <Button x:Name="btn_assign" Content="Assign IDs"
            Width="110" Height="30" Margin="0,0,8,0"
            Background="#3B82F6" Foreground="White"
            FontFamily="Segoe UI" FontSize="12"
            BorderThickness="0" Cursor="Hand"
            IsEnabled="False" Opacity="0.4"/>
    <Button x:Name="btn_not_detail" Content="NOT A DETAIL"
            Width="120" Height="30" Margin="0,0,8,0"
            Background="#D97706" Foreground="White"
            FontFamily="Segoe UI" FontSize="12"
            BorderThickness="0" Cursor="Hand"
            IsEnabled="False" Opacity="0.4"/>
    <Button x:Name="btn_cancel" Content="Cancel"
            Width="80" Height="30"
            Background="#252742" Foreground="#94A3B8"
            FontFamily="Segoe UI" FontSize="12"
            BorderThickness="0" Cursor="Hand"/>
  </StackPanel>
</Grid>
"""

win = ui.parse(
    "Assign Detail IDs",
    "{} view{} on {} sheet{} need a Detail ID".format(
        total_on_sheets,     "s" if total_on_sheets != 1 else "",
        len(sheets_involved), "s" if len(sheets_involved) != 1 else ""
    ),
    _BODY, _FOOTER,
    width=640, height=460,
    context=u"Detail ID is the shared parameter (GUID 6543FE3B...) that survives "
            u"renames. Tick the views below and click 'Assign IDs' to number them "
            u"consecutively (0001, 0002...). Use 'NOT A DETAIL' for views that "
            u"shouldn't receive an ID (transition views, indices)."
)

grid        = win.FindName("grid")
search_box  = win.FindName("search_box")
count_label = win.FindName("count_label")
btn_assign  = win.FindName("btn_assign")
btn_not_det = win.FindName("btn_not_detail")
btn_cancel  = win.FindName("btn_cancel")

grid.ItemsSource = rows
count_label.Text = "Select sheets to continue"

def on_search_changed(s, e):
    query = search_box.Text.strip().lower()
    rows.Clear()
    for r in all_rows:
        if not query or query in r.SheetNumber.lower() or query in r.SheetName.lower():
            rows.Add(r)

action        = [None]
selected_rows = [None]

def on_selection_changed(s, e):
    n_views  = sum(len(r.Views) for r in grid.SelectedItems)
    n_sheets = grid.SelectedItems.Count
    has_sel  = n_views > 0
    btn_assign.IsEnabled  = has_sel
    btn_not_det.IsEnabled = has_sel
    btn_assign.Opacity    = 1.0 if has_sel else 0.4
    btn_not_det.Opacity   = 1.0 if has_sel else 0.4
    if has_sel:
        count_label.Text = "{} view{} on {} sheet{}".format(
            n_views,  "s" if n_views  != 1 else "",
            n_sheets, "s" if n_sheets != 1 else ""
        )
    else:
        count_label.Text = "Select sheets to continue"

def on_assign(s, e):
    selected_rows[0] = list(grid.SelectedItems)
    action[0] = "assign"
    win.Close()

def on_not_detail(s, e):
    selected_rows[0] = list(grid.SelectedItems)
    action[0] = "not_a_detail"
    win.Close()

def on_cancel(s, e):
    win.Close()

grid.SelectionChanged    += on_selection_changed
search_box.TextChanged   += on_search_changed
btn_assign.Click         += on_assign
btn_not_det.Click        += on_not_detail
btn_cancel.Click         += on_cancel

win.ShowDialog()

if not action[0] or not selected_rows[0]:
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 6. Apply
# ─────────────────────────────────────────────────────────────────────────────

selected_views = []
for row in selected_rows[0]:
    selected_views.extend(row.Views)

if not selected_views:
    ui.alert("No views found in the selected rows.\nTry closing and re-running the tool.",
             title="Assign Detail IDs")
    script.exit()

def _try_set(view, value):
    """Returns (True, None) on success, (False, reason_str) on failure."""
    p = view.LookupParameter("Detail ID")
    if p is None:
        return False, "param not found"
    if p.IsReadOnly:
        reason = "read-only"
        if doc.IsWorkshared:
            try:
                from Autodesk.Revit.DB import WorksharingUtils, CheckoutStatus
                status = WorksharingUtils.GetCheckoutStatus(doc, view.Id)
                if status == CheckoutStatus.OwnedByOtherUser:
                    info   = WorksharingUtils.GetWorksharingTooltipInfo(doc, view.Id)
                    owner  = info.Owner if (info and info.Owner) else "another user"
                    reason = u"owned by {}".format(owner)
                elif status == CheckoutStatus.OwnedByCurrentUser:
                    reason = "read-only despite being checked out (VariesAcrossGroups issue)"
            except Exception:
                pass
        return False, reason
    try:
        p.Set(value)
        return True, None
    except Exception as ex:
        return False, str(ex)

_checkout_views(doc, selected_views)

if action[0] == "assign":
    next_id   = next_available_id(dep_views)
    stamped   = 0
    no_param  = 0
    set_error = []
    with revit.Transaction("Assign Detail IDs"):
        for v in sorted(selected_views, key=lambda x: x.Name):
            ok, reason = _try_set(v, "{:04d}".format(next_id))
            if ok:
                next_id += 1
                stamped += 1
            elif reason == "param not found":
                no_param += 1
            else:
                set_error.append(u"  • {}: {}".format(v.Name, reason))
    lines = ["{} Detail ID{} assigned.".format(stamped, "s" if stamped != 1 else "")]
    if no_param:
        lines.append("\n{} view{} skipped — 'Detail ID' param not found.".format(
            no_param, "s" if no_param != 1 else ""))
    if set_error:
        lines.append("\n{} view{} could not be set:\n{}".format(
            len(set_error), "s" if len(set_error) != 1 else "",
            "\n".join(set_error)))
    msg = "\n".join(lines)
    subtitle = u"✓ {} assigned".format(stamped) if not (no_param + len(set_error)) \
               else u"{} assigned  ·  {} failed".format(stamped, no_param + len(set_error))
    show_result("Assign Detail IDs", subtitle, msg)

elif action[0] == "not_a_detail":
    stamped   = 0
    no_param  = 0
    set_error = []
    with revit.Transaction("Mark views as Not a Detail"):
        for v in selected_views:
            ok, reason = _try_set(v, "not a detail")
            if ok:
                stamped += 1
            elif reason == "param not found":
                no_param += 1
            else:
                set_error.append(u"  • {}: {}".format(v.Name, reason))
    lines = ["{} view{} marked as 'not a detail'.".format(stamped, "s" if stamped != 1 else "")]
    if no_param:
        lines.append("\n{} view{} skipped — 'Detail ID' param not found.".format(
            no_param, "s" if no_param != 1 else ""))
    if set_error:
        lines.append("\n{} view{} could not be set:\n{}".format(
            len(set_error), "s" if len(set_error) != 1 else "",
            "\n".join(set_error)))
    msg = "\n".join(lines)
    subtitle = u"✓ {} marked".format(stamped) if not (no_param + len(set_error)) \
               else u"{} marked  ·  {} failed".format(stamped, no_param + len(set_error))
    show_result("Assign Detail IDs", subtitle, msg)
