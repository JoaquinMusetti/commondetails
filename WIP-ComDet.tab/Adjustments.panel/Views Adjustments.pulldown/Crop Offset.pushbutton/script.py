# -*- coding: utf-8 -*-
__title__ = 'Annotation\nCrop Offset'
__doc__ = ('Sets the annotation crop offset (in inches) for selected views. '
           'Supports 3D views, floor plans, ceiling plans, area plans, elevations, and sections. '
           'Use the preset buttons to fill all four fields at once.')

import os as _os
import sys
_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

import System
from pyrevit import revit, DB

from Autodesk.Revit.DB import (
    FilteredElementCollector, View, ViewType, ElementId
)
from System.Windows.Controls import Button, CheckBox
from System.Windows import Thickness
from System.Windows.Media import SolidColorBrush, Color
from System.Collections.ObjectModel import ObservableCollection

doc = revit.doc

# ─── Unit helpers ────────────────────────────────────────────────────────────

def inches_to_feet(v):
    return v / 12.0

# ─── Constants ───────────────────────────────────────────────────────────────

EXCLUDED_VIEW_TYPES = (
    ViewType.DrawingSheet,
    ViewType.ProjectBrowser,
    ViewType.SystemBrowser,
    ViewType.Internal,
    ViewType.Schedule,
    ViewType.DraftingView,
    ViewType.Legend,
)

FILTERABLE_VIEW_TYPES = [
    ("3D Views",      ViewType.ThreeD),
    ("Floor Plans",   ViewType.FloorPlan),
    ("Ceiling Plans", ViewType.CeilingPlan),
    ("Area Plans",    ViewType.AreaPlan),
    ("Elevations",    ViewType.Elevation),
    ("Sections",      ViewType.Section),
]

# ─── Collect views ───────────────────────────────────────────────────────────

all_views = [
    v for v in FilteredElementCollector(doc).OfClass(View)
    if not v.IsTemplate
    and v.ViewType not in EXCLUDED_VIEW_TYPES
]

# ─── Annotation crop helpers ─────────────────────────────────────────────────

def set_annotation_crop_offset(rm, top, bottom, left, right):
    # Method 1: direct properties
    try:
        rm.TopAnnotationCropOffset    = top
        rm.BottomAnnotationCropOffset = bottom
        rm.LeftAnnotationCropOffset   = left
        rm.RightAnnotationCropOffset  = right
        return True
    except Exception:
        pass
    # Method 2: .NET reflection
    try:
        t     = rm.GetType()
        flags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance
        props = {p.Name: p for p in t.GetProperties(flags)}
        for name, val in [
            ("TopAnnotationCropOffset",    top),
            ("BottomAnnotationCropOffset", bottom),
            ("LeftAnnotationCropOffset",   left),
            ("RightAnnotationCropOffset",  right),
        ]:
            if name in props:
                props[name].SetValue(rm, val, None)
        return True
    except Exception:
        pass
    # Method 3: unified method (some Revit versions)
    try:
        rm.SetAnnotationCropOffset(top, bottom, left, right)
        return True
    except Exception:
        pass
    return False


def view_supports_annotation_crop(v):
    try:
        rm = v.GetCropRegionShapeManager()
        if rm is None:
            return False, "ShapeManager not available"
        _ = rm.TopAnnotationCropOffset
        return True, None
    except Exception as ex:
        return False, str(ex)


def build_view_map(views):
    primaries  = {}
    dependents = {}
    for v in views:
        pid = v.GetPrimaryViewId()
        if pid == ElementId.InvalidElementId:
            primaries[v.Id] = v
        else:
            dependents.setdefault(pid, []).append(v)
    return primaries, dependents

# ─── Row colours ─────────────────────────────────────────────────────────────

_BRUSH_NORMAL = SolidColorBrush(Color.FromRgb(0xE8, 0xEB, 0xF5))  # #E8EBF5 normal
_BRUSH_DEP    = SolidColorBrush(Color.FromRgb(0xF5, 0xA6, 0x23))  # #f5a623 amber – dependents

# ─── Data object for DataGrid ─────────────────────────────────────────────────

class ViewItem(object):
    def __init__(self, view):
        self.View     = view
        self.ViewType = view.ViewType.ToString()
        pid = view.GetPrimaryViewId()
        self.IsDependent = pid != ElementId.InvalidElementId
        if self.IsDependent:
            master = doc.GetElement(pid)
            self.ViewName      = u"  {} – {}".format(master.Name, view.Name)
            self.RowForeground = _BRUSH_DEP
        else:
            self.ViewName      = view.Name
            self.RowForeground = _BRUSH_NORMAL

# ─── XAML ────────────────────────────────────────────────────────────────────

_BODY = """
  <Grid>
    <Grid.Resources>
      <Style TargetType="CheckBox">
        <Setter Property="Foreground" Value="#E8EBF5"/>
        <Setter Property="VerticalContentAlignment" Value="Center"/>
      </Style>
    </Grid.Resources>
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
    </Grid.RowDefinitions>

    <!-- Preset quick-fill buttons -->
    <StackPanel x:Name="presetPanel" Grid.Row="0"
                Orientation="Horizontal" Margin="0,0,0,10"/>

    <!-- Four offset inputs -->
    <Grid Grid.Row="1" Margin="0,0,0,10">
      <Grid.ColumnDefinitions>
        <ColumnDefinition/>
        <ColumnDefinition/>
        <ColumnDefinition/>
        <ColumnDefinition/>
      </Grid.ColumnDefinitions>
      <StackPanel Grid.Column="0" Margin="0,0,8,0">
        <TextBlock Text="Top (in)" Foreground="#8088A8" FontSize="11" Margin="0,0,0,4"/>
        <TextBox x:Name="txtTop" Text="0"/>
      </StackPanel>
      <StackPanel Grid.Column="1" Margin="0,0,8,0">
        <TextBlock Text="Bottom (in)" Foreground="#8088A8" FontSize="11" Margin="0,0,0,4"/>
        <TextBox x:Name="txtBottom" Text="0"/>
      </StackPanel>
      <StackPanel Grid.Column="2" Margin="0,0,8,0">
        <TextBlock Text="Left (in)" Foreground="#8088A8" FontSize="11" Margin="0,0,0,4"/>
        <TextBox x:Name="txtLeft" Text="0"/>
      </StackPanel>
      <StackPanel Grid.Column="3">
        <TextBlock Text="Right (in)" Foreground="#8088A8" FontSize="11" Margin="0,0,0,4"/>
        <TextBox x:Name="txtRight" Text="0"/>
      </StackPanel>
    </Grid>

    <!-- View type filter checkboxes (row 1) -->
    <StackPanel x:Name="filterPanel" Grid.Row="2"
                Orientation="Horizontal" Margin="0,0,0,4"/>

    <!-- Master / Dependent toggles (row 2) — added dynamically below -->
    <StackPanel x:Name="togglePanel" Grid.Row="3"
                Orientation="Horizontal" Margin="0,0,0,8"/>

    <!-- Search textbox -->
    <TextBox x:Name="txtSearch" Grid.Row="4"
             Margin="0,0,0,8" ToolTip="Type to filter views..."/>

    <!-- Views list -->
    <DataGrid x:Name="grid" Grid.Row="5"
              SelectionMode="Extended"
              CanUserSortColumns="True"
              AutoGenerateColumns="False">
      <DataGrid.RowStyle>
        <Style TargetType="DataGridRow">
          <Setter Property="Foreground" Value="{Binding RowForeground}"/>
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
        <DataGridTextColumn Header="VIEW TYPE" Width="200" Binding="{Binding ViewType}"/>
        <DataGridTextColumn Header="VIEW NAME" Width="*"   Binding="{Binding ViewName}"/>
      </DataGrid.Columns>
    </DataGrid>
  </Grid>
"""

_FOOTER = """
  <Grid>
    <TextBlock x:Name="lblCount" VerticalAlignment="Center" Foreground="#5A6286"/>
    <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
      <Button x:Name="btnApply"  Content="Apply"  Style="{StaticResource BtnPrimary}" Margin="0,0,8,0"/>
      <Button x:Name="btnCancel" Content="Cancel" Style="{StaticResource BtnGhost}"/>
    </StackPanel>
  </Grid>
"""

# ─── Build window ─────────────────────────────────────────────────────────────

win = ui.parse(
    "Annotation Crop Offset",
    "Set annotation crop offset (inches) for selected views",
    _BODY, _FOOTER,
    width=800, height=760,
)

presetPanel = win.FindName("presetPanel")
filterPanel = win.FindName("filterPanel")
togglePanel = win.FindName("togglePanel")
txtTop      = win.FindName("txtTop")
txtBottom   = win.FindName("txtBottom")
txtLeft     = win.FindName("txtLeft")
txtRight    = win.FindName("txtRight")
txtSearch   = win.FindName("txtSearch")
grid        = win.FindName("grid")
lblCount    = win.FindName("lblCount")
btnApply    = win.FindName("btnApply")
btnCancel   = win.FindName("btnCancel")

inputs = {"Top": txtTop, "Bottom": txtBottom, "Left": txtLeft, "Right": txtRight}

# ─── Populate DataGrid ────────────────────────────────────────────────────────

items = ObservableCollection[ViewItem]()
grid.ItemsSource = items

# ─── State ────────────────────────────────────────────────────────────────────

vtype_checks    = {}    # ViewType enum → CheckBox
show_masters_cb = None  # set after filterPanel is built
show_deps_cb    = None

# ─── Preset buttons ───────────────────────────────────────────────────────────

presets = [
    ('1/8"', 0.125), ('1/4"', 0.25), ('1/2"', 0.5),
    ('3/4"', 0.75),  ('1"',   1.0),  ('2"',   2.0),
]

def _make_preset_handler(val):
    def handler(s, e):
        for tb in inputs.values():
            tb.Text = str(val)
    return handler

for _label, _val in presets:
    _b = Button()
    _b.Content = _label
    _b.Style   = win.FindResource("BtnGhost")
    _b.Margin  = Thickness(0, 0, 6, 0)
    _b.Click  += _make_preset_handler(_val)
    presetPanel.Children.Add(_b)

# ─── List refresh ─────────────────────────────────────────────────────────────

def refresh_list(s=None, e=None):
    active       = [vt for vt, cb in vtype_checks.items() if cb.IsChecked]
    txt          = txtSearch.Text.strip().lower()
    show_masters = show_masters_cb.IsChecked if show_masters_cb else True
    show_deps    = show_deps_cb.IsChecked    if show_deps_cb    else True
    filtered     = [v for v in all_views if v.ViewType in active]

    primaries, dependents = build_view_map(filtered)
    result = []

    if txt:
        for pid, pv in primaries.items():
            name_match = txt in pv.Name.lower() or txt in pv.ViewType.ToString().lower()
            dep_list   = dependents.get(pid, [])
            dep_match  = any(txt in dv.Name.lower() for dv in dep_list)
            if name_match or dep_match:
                if show_masters:
                    result.append(pv)
                if show_deps:
                    result.extend(dep_list)
    else:
        for pid, pv in primaries.items():
            if show_masters:
                result.append(pv)
            if show_deps:
                result.extend(dependents.get(pid, []))

    items.Clear()
    for v in result:
        items.Add(ViewItem(v))
    lblCount.Text = u"{} view(s)".format(len(items))

# ─── View type checkboxes ─────────────────────────────────────────────────────

for _label, _vt in FILTERABLE_VIEW_TYPES:
    _cb            = CheckBox()
    _cb.Content    = _label
    _cb.IsChecked  = True
    _cb.Margin     = Thickness(0, 0, 14, 0)
    _cb.Checked   += refresh_list
    _cb.Unchecked += refresh_list
    vtype_checks[_vt] = _cb
    filterPanel.Children.Add(_cb)

# Master / Dependent toggles — second row
show_masters_cb           = CheckBox()
show_masters_cb.Content   = "Master Views"
show_masters_cb.IsChecked = True
show_masters_cb.Margin    = Thickness(0, 0, 14, 0)
show_masters_cb.Checked   += refresh_list
show_masters_cb.Unchecked += refresh_list
togglePanel.Children.Add(show_masters_cb)

show_deps_cb           = CheckBox()
show_deps_cb.Content   = "Dependent Views"
show_deps_cb.IsChecked = True
show_deps_cb.Margin    = Thickness(0, 0, 0, 0)
show_deps_cb.Checked   += refresh_list
show_deps_cb.Unchecked += refresh_list
togglePanel.Children.Add(show_deps_cb)

txtSearch.TextChanged += refresh_list

# Initial populate
refresh_list()

# ─── Apply handler ────────────────────────────────────────────────────────────

def on_apply(s, e):
    selected = [it.View for it in grid.SelectedItems]
    if not selected:
        ui.alert("No views selected.", title="Annotation Crop Offset")
        return

    try:
        offsets = {k: inches_to_feet(float(inputs[k].Text)) for k in inputs}
    except ValueError:
        ui.alert("Please enter valid numbers in all offset fields.",
                 title="Annotation Crop Offset")
        return

    skipped = []
    failed  = []
    applied = 0

    with revit.Transaction("Set Annotation Crop Offset"):
        for v in selected:
            try:
                if not v.CropBoxActive:
                    v.CropBoxActive = True
            except Exception:
                pass
            try:
                if not v.AnnotationCropActive:
                    v.AnnotationCropActive = True
                    doc.Regenerate()
            except Exception:
                pass

            supported, reason = view_supports_annotation_crop(v)
            if not supported:
                skipped.append(u"{} ({})".format(v.Name, reason))
                continue

            try:
                rm = v.GetCropRegionShapeManager()
                ok = set_annotation_crop_offset(
                    rm,
                    offsets["Top"], offsets["Bottom"],
                    offsets["Left"], offsets["Right"],
                )
                if ok:
                    applied += 1
                else:
                    failed.append(v.Name)
            except Exception as ex:
                failed.append(u"{}: {}".format(v.Name, str(ex)))

    msg_parts = [u"Applied to {} view(s).".format(applied)]
    if skipped:
        msg_parts.append(u"\nSkipped {}:\n  {}".format(
            len(skipped), u"\n  ".join(skipped[:20])))
    if failed:
        msg_parts.append(u"\nFailed {}:\n  {}".format(
            len(failed), u"\n  ".join(failed[:20])))

    ui.alert(u"\n".join(msg_parts), title="Annotation Crop Offset")
    win.Close()


btnApply.Click  += on_apply
btnCancel.Click += lambda s, e: win.Close()

win.ShowDialog()
