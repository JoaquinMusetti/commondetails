# -*- coding: utf-8 -*-
__title__ = "Scan\nView Density"
__doc__ = (
    "Scans all views in the model and counts detail lines, detail items, "
    "dimensions, and text notes per view. Sorted by line count descending so "
    "views with heavy manual drafting appear first — candidates for optimization "
    "by replacing hand-drawn elements with Detail Item families. "
    "Select a row and click 'Open View' to navigate directly to that view."
)

import sys
import os as _os
_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui
from pyrevit import forms

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, View, Viewport,
    ViewType,
)
from System.Collections.ObjectModel import ObservableCollection

doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


# ── Constants ────────────────────────────────────────────────────────────────

HEAVY_LINES = 500   # amber highlight threshold

_VALID_VIEW_TYPES = {
    ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.Section,
    ViewType.Elevation, ViewType.Detail, ViewType.DraftingView, ViewType.ThreeD,
}

_VT_LABELS = {
    ViewType.FloorPlan:    u"Plan",
    ViewType.CeilingPlan:  u"RCP",
    ViewType.Section:      u"Section",
    ViewType.Elevation:    u"Elevation",
    ViewType.Detail:       u"Detail",
    ViewType.DraftingView: u"Drafting",
    ViewType.ThreeD:       u"3D",
}

# Categories to count per view, in tuple order (lines, items, dims, text)
_CATS = [
    BuiltInCategory.OST_Lines,
    BuiltInCategory.OST_DetailComponents,
    BuiltInCategory.OST_Dimensions,
    BuiltInCategory.OST_TextNotes,
]


# ── Collect views ────────────────────────────────────────────────────────────

all_doc_views = [
    v for v in FilteredElementCollector(doc).OfClass(View).ToElements()
    if not v.IsTemplate and v.ViewType in _VALID_VIEW_TYPES
]

if not all_doc_views:
    ui.alert("No scannable views found in the model.", title="View Density")
    sys.exit()


# ── Sheet association (pre-scan, one pass) ───────────────────────────────────

view_to_sheet = {}
try:
    for vp in FilteredElementCollector(doc).OfClass(Viewport).ToElements():
        sheet = doc.GetElement(vp.SheetId)
        if sheet:
            view_to_sheet[vp.ViewId.IntegerValue] = sheet.SheetNumber
except Exception:
    pass


# ── Scan all views ───────────────────────────────────────────────────────────
# GetElementCount() is much faster than ToElements() — no object instantiation.

_scan_results = []   # list of (view, sheet_num, lines, items, dims, text)
n_total = len(all_doc_views)

with forms.ProgressBar(title="View Density", cancellable=True) as pb:
    for i, v in enumerate(all_doc_views):
        if pb.cancelled:
            break
        pb.title = u"View Density — {}/{} — {}".format(i + 1, n_total, v.Name)
        counts = []
        for cat in _CATS:
            try:
                n = (FilteredElementCollector(doc, v.Id)
                     .OfCategory(cat)
                     .WhereElementIsNotElementType()
                     .GetElementCount())
            except Exception:
                n = 0
            counts.append(n)
        sheet_num = view_to_sheet.get(v.Id.IntegerValue, u"")
        _scan_results.append((v, sheet_num, counts[0], counts[1], counts[2], counts[3]))
        if i % 5 == 0 or i == n_total - 1:
            pb.update_progress(i + 1, n_total)

if not _scan_results:
    ui.alert("Scan cancelled — no results.", title="View Density")
    sys.exit()


# ── Row model ────────────────────────────────────────────────────────────────

class ViewRow(object):
    def __init__(self, rank, view, sheet, lines, items, dims, text):
        self._rank  = rank
        self._view  = view
        self._sheet = sheet
        self._lines = lines
        self._items = items
        self._dims  = dims
        self._text  = text

    @property
    def Rank(self):    return self._rank
    @property
    def Name(self):    return self._view.Name
    @property
    def VType(self):   return _VT_LABELS.get(self._view.ViewType, u"?")
    @property
    def Sheet(self):   return self._sheet
    @property
    def Lines(self):   return self._lines
    @property
    def Items(self):   return self._items
    @property
    def Dims(self):    return self._dims
    @property
    def Text(self):    return self._text
    @property
    def Total(self):   return self._lines + self._items + self._dims + self._text
    @property
    def IsHeavy(self): return self._lines >= HEAVY_LINES


def _build_rows(results):
    sorted_r = sorted(results, key=lambda r: (-r[2], -(r[2] + r[3] + r[4] + r[5])))
    return [ViewRow(i + 1, v, sh, li, it, di, tx)
            for i, (v, sh, li, it, di, tx) in enumerate(sorted_r)]


_all_rows = _build_rows(_scan_results)


# ── XAML ─────────────────────────────────────────────────────────────────────

_BODY = """
<Grid>
  <Grid.Resources>
    <Style TargetType="ComboBox">
      <Setter Property="Foreground"      Value="#E8EBF5"/>
      <Setter Property="Background"      Value="#1E2235"/>
      <Setter Property="BorderBrush"     Value="#3A4070"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="Padding"         Value="8,5"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="ComboBox">
            <Grid>
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="20"/>
              </Grid.ColumnDefinitions>
              <Border Grid.ColumnSpan="2"
                      Background="{TemplateBinding Background}"
                      BorderBrush="{TemplateBinding BorderBrush}"
                      BorderThickness="{TemplateBinding BorderThickness}"
                      CornerRadius="2"/>
              <ContentPresenter Grid.Column="0"
                                Margin="{TemplateBinding Padding}"
                                VerticalAlignment="Center"
                                Content="{TemplateBinding SelectionBoxItem}"
                                IsHitTestVisible="False"/>
              <TextBlock Grid.Column="1" Text="&#9662;"
                         Foreground="{TemplateBinding Foreground}"
                         VerticalAlignment="Center" HorizontalAlignment="Center"
                         IsHitTestVisible="False"/>
              <ToggleButton Grid.ColumnSpan="2"
                            IsChecked="{Binding IsDropDownOpen,
                                        RelativeSource={RelativeSource TemplatedParent},
                                        Mode=TwoWay}">
                <ToggleButton.Template>
                  <ControlTemplate TargetType="ToggleButton">
                    <Border Background="Transparent"/>
                  </ControlTemplate>
                </ToggleButton.Template>
              </ToggleButton>
              <Popup Grid.ColumnSpan="2"
                     IsOpen="{TemplateBinding IsDropDownOpen}"
                     AllowsTransparency="True" Focusable="False" Placement="Bottom">
                <Border Background="#1E2235" BorderBrush="#3A4070" BorderThickness="1"
                        MinWidth="{Binding ActualWidth,
                                  RelativeSource={RelativeSource TemplatedParent}}">
                  <ScrollViewer MaxHeight="200" VerticalScrollBarVisibility="Auto">
                    <ItemsPresenter/>
                  </ScrollViewer>
                </Border>
              </Popup>
            </Grid>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style TargetType="ComboBoxItem">
      <Setter Property="Background" Value="#1E2235"/>
      <Setter Property="Foreground" Value="#E8EBF5"/>
      <Setter Property="Padding"    Value="8,4"/>
      <Style.Triggers>
        <Trigger Property="IsHighlighted" Value="True">
          <Setter Property="Background" Value="#4a90e2"/>
          <Setter Property="Foreground" Value="White"/>
        </Trigger>
      </Style.Triggers>
    </Style>
  </Grid.Resources>

  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="*"/>
  </Grid.RowDefinitions>

  <Grid Grid.Row="0" Margin="0,0,0,8">
    <Grid.ColumnDefinitions>
      <ColumnDefinition Width="*"/>
      <ColumnDefinition Width="8"/>
      <ColumnDefinition Width="148"/>
    </Grid.ColumnDefinitions>
    <TextBox x:Name="txtSearch" Grid.Column="0"
             ToolTip="Filter by view name..."/>
    <ComboBox x:Name="cmbType" Grid.Column="2"
              ToolTip="Filter by view type"/>
  </Grid>

  <DataGrid x:Name="grid" Grid.Row="1"
            AutoGenerateColumns="False" CanUserAddRows="False"
            CanUserSortColumns="True" SelectionMode="Single">
    <DataGrid.RowStyle>
      <Style TargetType="DataGridRow">
        <Setter Property="Foreground" Value="#E8EBF5"/>
        <Style.Triggers>
          <DataTrigger Binding="{Binding IsHeavy}" Value="True">
            <Setter Property="Foreground" Value="#f5a623"/>
          </DataTrigger>
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
      <DataGridTextColumn Header="#"     Width="38" Binding="{Binding Rank}"  IsReadOnly="True"/>
      <DataGridTextColumn Header="VIEW"  Width="*"  Binding="{Binding Name}"  IsReadOnly="True"/>
      <DataGridTextColumn Header="TYPE"  Width="76" Binding="{Binding VType}" IsReadOnly="True"/>
      <DataGridTextColumn Header="SHEET" Width="62" Binding="{Binding Sheet}" IsReadOnly="True"/>
      <DataGridTextColumn Header="LINES" Width="66" Binding="{Binding Lines}" IsReadOnly="True"/>
      <DataGridTextColumn Header="ITEMS" Width="66" Binding="{Binding Items}" IsReadOnly="True"/>
      <DataGridTextColumn Header="DIMS"  Width="66" Binding="{Binding Dims}"  IsReadOnly="True"/>
      <DataGridTextColumn Header="TEXT"  Width="66" Binding="{Binding Text}"  IsReadOnly="True"/>
      <DataGridTextColumn Header="TOTAL" Width="66" Binding="{Binding Total}" IsReadOnly="True"/>
    </DataGrid.Columns>
  </DataGrid>
</Grid>
"""

_FOOTER = """
<Grid>
  <TextBlock x:Name="lblCount" VerticalAlignment="Center" Foreground="#5A6286"/>
  <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
    <Button x:Name="btnOpen"  Content="Open View" Style="{StaticResource BtnPrimary}" Margin="0,0,8,0"/>
    <Button x:Name="btnClose" Content="Close"     Style="{StaticResource BtnGhost}"/>
  </StackPanel>
</Grid>
"""


# ── Window setup ─────────────────────────────────────────────────────────────

n_scanned = len(_scan_results)
win = ui.parse(
    "View Density",
    u"{} views scanned".format(n_scanned),
    _BODY, _FOOTER,
    width=920, height=560,
)

txtSearch = win.FindName("txtSearch")
cmbType   = win.FindName("cmbType")
grid      = win.FindName("grid")
lblCount  = win.FindName("lblCount")
btnOpen   = win.FindName("btnOpen")
btnClose  = win.FindName("btnClose")

_TYPE_OPTIONS = [u"All types", u"Plan", u"RCP", u"Section",
                 u"Elevation", u"Detail", u"Drafting", u"3D"]
for opt in _TYPE_OPTIONS:
    cmbType.Items.Add(opt)
cmbType.SelectedIndex = 0


def _to_col(items):
    col = ObservableCollection[object]()
    for it in items:
        col.Add(it)
    return col


def refresh_grid(*args):
    q           = (txtSearch.Text or u"").strip().lower()
    t_sel       = cmbType.SelectedItem
    type_filter = None if (t_sel is None or t_sel == u"All types") else t_sel

    rs = []
    for row in _all_rows:
        if q and q not in row.Name.lower():
            continue
        if type_filter and row.VType != type_filter:
            continue
        rs.append(row)

    grid.ItemsSource = _to_col(rs)
    lblCount.Text = u"{} of {} views".format(len(rs), len(_all_rows))


_selected_view = [None]


def on_open(s, e):
    row = grid.SelectedItem
    if row is None:
        ui.alert("Select a view row first.", title="View Density")
        return
    _selected_view[0] = row._view
    win.Close()


def on_double_click(s, e):
    if grid.SelectedItem is not None:
        on_open(s, e)


btnOpen.Click            += on_open
btnClose.Click           += lambda s, e: win.Close()
txtSearch.TextChanged    += refresh_grid
cmbType.SelectionChanged += refresh_grid
grid.MouseDoubleClick    += on_double_click

refresh_grid()
win.ShowDialog()

# Navigate to selected view after dialog closes (modal constraint workaround)
if _selected_view[0] is not None:
    try:
        uidoc.RequestViewChange(_selected_view[0])
    except Exception:
        pass
