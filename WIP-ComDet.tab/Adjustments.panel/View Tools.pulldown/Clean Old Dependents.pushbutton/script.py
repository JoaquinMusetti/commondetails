# -*- coding: utf-8 -*-
__title__ = "Clean\nOld Dependents"
__doc__ = ("Lists dependent views (of the masters you select) that aren't "
           "placed on any sheet, and lets you pick which ones to delete. "
           "Hygiene tool — clears leftover unused dependents after iterations "
           "of view creation, exports, and renames.")

import sys
import os as _os
from pyrevit import revit, DB, script
from System.Collections.ObjectModel import ObservableCollection

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
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

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

def _get_detail_id(view):
    try:
        p = view.LookupParameter("Detail ID")
        return (p.AsString() or "") if p else ""
    except Exception:
        return ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. Index dependents grouped by master + figure out which are placed
# ─────────────────────────────────────────────────────────────────────────────

placed_view_ids = set()
for vp in DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements():
    placed_view_ids.add(vp.ViewId.IntegerValue)

# master_name → (master_view, [dep_views])
masters_by_name = {}
for v in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
    try:
        if v.IsTemplate:
            continue
        pid = v.GetPrimaryViewId()
        if pid == DB.ElementId.InvalidElementId:
            continue
        primary = doc.GetElement(pid)
        if primary is None:
            continue
        bucket = masters_by_name.setdefault(primary.Name, (primary, []))
        bucket[1].append(v)
    except Exception:
        pass

if not masters_by_name:
    ui.alert(u"This model has no dependent views to clean.",
             title=u"Clean Old Dependents")
    script.exit()

# Keep only masters that have at least one orphan dependent
master_labels   = []
label_to_master = {}
for mname in sorted(masters_by_name.keys()):
    mview, deps = masters_by_name[mname]
    n_orphans = sum(1 for d in deps if d.Id.IntegerValue not in placed_view_ids)
    if n_orphans == 0:
        continue
    label = u"{}    ·    {} dep{}    ·    {} orphan{}".format(
        mname, len(deps), u"s" if len(deps) != 1 else u"",
        n_orphans, u"s" if n_orphans != 1 else u"")
    master_labels.append(label)
    label_to_master[label] = mname

if not master_labels:
    ui.alert(u"No orphan dependents found — every dependent in this model "
             u"is placed on a sheet.",
             title=u"Clean Old Dependents")
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 2. Pick masters to scan
# ─────────────────────────────────────────────────────────────────────────────

picked_labels = ui.pick_list(
    master_labels,
    "Select masters to scan for orphan dependents",
    multiselect=True,
    button_name="Next",
    context=u"Pick the master views whose orphan dependents you want to review. "
            u"Only masters that have at least one orphan dependent are listed. "
            u"On the next screen you'll see the orphans (all ticked by default) "
            u"and can untick any you want to keep before deleting."
)
if not picked_labels:
    script.exit()

picked_master_names = {label_to_master[lbl] for lbl in picked_labels}

# ─────────────────────────────────────────────────────────────────────────────
# 3. Build orphan rows for the picked masters
# ─────────────────────────────────────────────────────────────────────────────

class _OrphanRow(object):
    def __init__(self, view, master_name):
        self._view   = view
        self._master = master_name

    @property
    def ViewName(self): return self._view.Name
    @property
    def Master(self):   return self._master
    @property
    def DetailId(self): return _get_detail_id(self._view) or u"—"
    @property
    def Scale(self):    return u"1 : {}".format(self._view.Scale)
    @property
    def ViewType(self): return _vt_label(self._view)
    @property
    def Element(self):  return self._view


orphan_rows = []
for mname in sorted(picked_master_names):
    mview, deps = masters_by_name[mname]
    for d in deps:
        if d.Id.IntegerValue not in placed_view_ids:
            orphan_rows.append(_OrphanRow(d, mname))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Noir DataGrid window — review and choose what to delete
# ─────────────────────────────────────────────────────────────────────────────

_BODY = u"""
<Grid>
  <DataGrid x:Name="grid" AutoGenerateColumns="False"
            CanUserAddRows="False" CanUserSortColumns="True"
            SelectionMode="Extended" IsReadOnly="True"
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
    <DataGrid.RowStyle>
      <Style TargetType="DataGridRow">
        <Setter Property="Foreground" Value="#E8EBF5"/>
        <Style.Triggers>
          <Trigger Property="IsMouseOver" Value="True">
            <Setter Property="Background" Value="#283358"/>
          </Trigger>
          <Trigger Property="IsSelected" Value="True">
            <Setter Property="Background" Value="#3A1F70"/>
            <Setter Property="Foreground" Value="#FFFFFF"/>
          </Trigger>
        </Style.Triggers>
      </Style>
    </DataGrid.RowStyle>
    <DataGrid.Columns>
      <DataGridTextColumn Header="View"      Width="*"   Binding="{Binding ViewName}"/>
      <DataGridTextColumn Header="Master"    Width="*"   Binding="{Binding Master}"/>
      <DataGridTextColumn Header="Detail ID" Width="100" Binding="{Binding DetailId}"/>
      <DataGridTextColumn Header="Scale"     Width="80"  Binding="{Binding Scale}"/>
      <DataGridTextColumn Header="Type"      Width="100" Binding="{Binding ViewType}"/>
    </DataGrid.Columns>
  </DataGrid>
</Grid>
"""

_FOOTER = u"""
<Grid>
  <TextBlock x:Name="lblCount" VerticalAlignment="Center" Foreground="#9099C8"
             FontFamily="Segoe UI" FontSize="12"/>
  <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
    <Button x:Name="btnCancel" Content="Cancel" Style="{StaticResource BtnGhost}" Margin="0,0,8,0"/>
    <Button x:Name="btnDelete" Content="Delete selected" Style="{StaticResource BtnPrimary}"/>
  </StackPanel>
</Grid>
"""

win = ui.parse(
    u"Clean Old Dependents",
    u"{} orphan dependent{} across {} master{}".format(
        len(orphan_rows), u"s" if len(orphan_rows) != 1 else u"",
        len(picked_master_names), u"s" if len(picked_master_names) != 1 else u""),
    _BODY, _FOOTER, width=900, height=560,
    context=u"These dependent views aren't placed on any sheet. All rows are ticked by "
            u"default — untick any you want to keep, then click Delete selected. "
            u"Use Ctrl/Shift to multi-select."
)

grid      = win.FindName("grid")
lbl_count = win.FindName("lblCount")
btn_del   = win.FindName("btnDelete")
btn_can   = win.FindName("btnCancel")

items = ObservableCollection[_OrphanRow]()
for r in orphan_rows:
    items.Add(r)
grid.ItemsSource = items
grid.SelectAll()   # pre-select everything

def _update_count(*a):
    n_sel = len(list(grid.SelectedItems))
    lbl_count.Text = u"{} of {} selected".format(n_sel, len(orphan_rows))

_update_count()
grid.SelectionChanged += _update_count

_to_delete = [None]

def _on_delete(s, e):
    selected = list(grid.SelectedItems)
    if not selected:
        ui.alert(u"Select at least one row, or click Cancel.",
                 title=u"Clean Old Dependents")
        return
    if not ui.confirm(
        u"Delete {} dependent view{}?\n\nUse Revit's Undo (Ctrl+Z) right after if "
        u"you change your mind.".format(
            len(selected), u"s" if len(selected) != 1 else u""),
        title=u"Confirm delete", yes_text=u"Delete"
    ):
        return
    _to_delete[0] = selected
    win.Close()

btn_del.Click += _on_delete
btn_can.Click += lambda s, e: win.Close()

win.ShowDialog()

if _to_delete[0] is None:
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 5. Delete in one transaction
# ─────────────────────────────────────────────────────────────────────────────

deleted = 0
errors  = 0
results = []   # (status, view_name, master, detail)

with revit.Transaction("Clean Old Dependents"):
    for row in _to_delete[0]:
        try:
            v_name = row.ViewName
            m_name = row.Master
            doc.Delete(row.Element.Id)
            results.append(("deleted", v_name, m_name, u""))
            deleted += 1
        except Exception as ex:
            results.append(("error", row.ViewName, row.Master, str(ex)))
            errors += 1

# ─────────────────────────────────────────────────────────────────────────────
# 6. Results window
# ─────────────────────────────────────────────────────────────────────────────

class _ResultRow(object):
    _ICONS = {"deleted": u"🗑", "error": u"❌"}
    def __init__(self, status, view_name, master, detail):
        self._status    = status
        self._view_name = view_name
        self._master    = master
        self._detail    = detail

    @property
    def Icon(self):     return self._ICONS.get(self._status, u"·")
    @property
    def ViewName(self): return self._view_name
    @property
    def Master(self):   return self._master
    @property
    def Detail(self):   return self._detail


_R_BODY = u"""
<Grid>
  <DataGrid x:Name="gridR" AutoGenerateColumns="False" IsReadOnly="True"
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
      <DataGridTextColumn Header=""        Width="32"  Binding="{Binding Icon}"/>
      <DataGridTextColumn Header="View"    Width="*"   Binding="{Binding ViewName}"/>
      <DataGridTextColumn Header="Master"  Width="*"   Binding="{Binding Master}"/>
      <DataGridTextColumn Header="Note"    Width="220" Binding="{Binding Detail}"/>
    </DataGrid.Columns>
  </DataGrid>
</Grid>
"""

_R_FOOTER = u"""
<Grid>
  <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
    <Button x:Name="btnOK" Content="Close" Style="{StaticResource BtnPrimary}"/>
  </StackPanel>
</Grid>
"""

subtitle = u"🗑 {} deleted".format(deleted)
if errors:
    subtitle += u"  ·  ❌ {} error{}".format(errors, u"s" if errors != 1 else u"")

rwin = ui.parse(u"Clean Old Dependents — done", subtitle,
                _R_BODY, _R_FOOTER, width=820, height=420)

result_items = ObservableCollection[_ResultRow]()
for st, vn, m, det in results:
    result_items.Add(_ResultRow(st, vn, m, det))
rwin.FindName("gridR").ItemsSource = result_items
rwin.FindName("btnOK").Click += lambda s, e: rwin.Close()
rwin.ShowDialog()
