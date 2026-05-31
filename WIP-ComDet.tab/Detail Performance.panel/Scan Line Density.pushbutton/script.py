# -*- coding: utf-8 -*-
__title__ = "Scan\nLine Density"
__doc__ = (
    "Scans the active view for zones with high concentrations of detail curves. "
    "Clusters adjacent hot cells and shows a ranked list - double-click any row "
    "to zoom directly to that zone and select its curves. Useful for finding "
    "bolts, fasteners, or other components drawn with individual lines instead "
    "of Detail Items."
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
    FilteredElementCollector, BuiltInCategory, ElementId,
    ViewType,
)
from System.Collections.Generic import List as DotNetList
from System.Collections.ObjectModel import ObservableCollection
from System.Windows.Media import Brushes
from collections import defaultdict

doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


# ── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_CELL_SIZE_IN  = 3     # inches (~75 mm)
DEFAULT_MIN_LINES     = 6
DEFAULT_MAX_SPAN_IN   = 6     # inches — discard curves whose endpoints are
                              # farther apart than this. Long lines (grids,
                              # walls, dims) can have a midpoint inside a dense
                              # cell while their endpoints sit at opposite ends
                              # of the view — selecting them ruins the zoom.
MAX_CLUSTERS          = 50
HOT_THRESHOLD         = 20    # amber row above this count
PROGRESS_EVERY        = 25    # throttle ProgressBar updates (anti-flicker)


# ── Validate view type ──────────────────────────────────────────────────────

view = doc.ActiveView
_INVALID_VIEW_TYPES = (
    ViewType.DrawingSheet, ViewType.Schedule,
    ViewType.ProjectBrowser, ViewType.SystemBrowser,
    ViewType.Internal, ViewType.Undefined,
)
if view.ViewType in _INVALID_VIEW_TYPES:
    ui.alert(
        "Run this tool from a plan, section, elevation, drafting or detail view.",
        title="Line Density",
    )
    sys.exit()


# ── Cache view basis ────────────────────────────────────────────────────────
# Project everything into the view's 2D plane (u, v) using RightDirection and
# UpDirection. For plan views this matches world (X, Y); for sections and
# elevations it correctly uses world (X, Z). Without this, a section view
# would bin all curves into the same cell (because world Y is constant in the
# section plane) and the span filter would miss tall vertical lines.

_right    = view.RightDirection
_up       = view.UpDirection
_view_org = view.Origin
rX, rY, rZ = _right.X, _right.Y, _right.Z
uX, uY, uZ = _up.X,    _up.Y,    _up.Z
oX, oY, oZ = _view_org.X, _view_org.Y, _view_org.Z


# ── Collect curves ──────────────────────────────────────────────────────────

curves = list(
    FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_Lines)
        .WhereElementIsNotElementType()
)
if not curves:
    ui.alert("No detail curves in active view.", title="Line Density")
    sys.exit()


# ── Geometry loop with ProgressBar ──────────────────────────────────────────
# Project the element's world bbox to the view's (u, v) plane. Position =
# (u, v) center; span = max extent in u or v. Closed arcs/circles/splines
# work correctly because the world bbox captures their full curvature, and
# tall vertical lines in section views correctly get a large span_v.

midpoints = []   # list of (mu, mv, eid, style_name, span_ft)
n_total = len(curves)
# Top-anchored pyrevit ProgressBar (no bottom-positioning logic → no flicker).
# Updates throttled to every PROGRESS_EVERY iterations.
with forms.ProgressBar(title="Line Density", cancellable=True) as pb:
    for i, c in enumerate(curves):
        if pb.cancelled:
            break
        try:
            bb = c.get_BoundingBox(view)
            if bb is None:
                continue
            mn, mx_pt = bb.Min, bb.Max

            # Project Min/Max corners onto view's (u, v) axes.
            d1x = mn.X - oX
            d1y = mn.Y - oY
            d1z = mn.Z - oZ
            d2x = mx_pt.X - oX
            d2y = mx_pt.Y - oY
            d2z = mx_pt.Z - oZ
            u1 = d1x*rX + d1y*rY + d1z*rZ
            v1 = d1x*uX + d1y*uY + d1z*uZ
            u2 = d2x*rX + d2y*rY + d2z*rZ
            v2 = d2x*uX + d2y*uY + d2z*uZ
            if u1 <= u2:
                u_lo, u_hi = u1, u2
            else:
                u_lo, u_hi = u2, u1
            if v1 <= v2:
                v_lo, v_hi = v1, v2
            else:
                v_lo, v_hi = v2, v1

            span_u = u_hi - u_lo
            span_v = v_hi - v_lo
            span   = span_u if span_u > span_v else span_v
            mu = (u_lo + u_hi) * 0.5
            mv = (v_lo + v_hi) * 0.5

            try:
                style = c.LineStyle.Name if c.LineStyle else u"<none>"
            except Exception:
                style = u"<none>"
            midpoints.append((mu, mv, c.Id, style, span))
        except Exception:
            continue
        if i % PROGRESS_EVERY == 0 or i == n_total - 1:
            pb.update_progress(i + 1, n_total)

n_curves = len(midpoints)
if n_curves == 0:
    ui.alert("No usable curves found in active view.", title="Line Density")
    sys.exit()


# ── Row model ───────────────────────────────────────────────────────────────

class HotspotRow(object):
    def __init__(self, rank, count, bb_uv, ids, dom_style, dom_pct):
        self._rank      = rank
        self._count     = count
        # Tight bbox in view (u, v) coords (feet). Used for the Width/Height
        # and LocX/LocY display columns; zoom uses ShowElements which doesn't
        # need this rect.
        (self._u_lo, self._v_lo,
         self._u_hi, self._v_hi) = bb_uv
        self._ids       = ids
        self._dom_style = dom_style
        self._dom_pct   = dom_pct

    @property
    def Rank(self):      return self._rank
    @property
    def Count(self):     return self._count
    @property
    def Width(self):     return u'{:.1f}"'.format((self._u_hi - self._u_lo) * 12)
    @property
    def Height(self):    return u'{:.1f}"'.format((self._v_hi - self._v_lo) * 12)
    @property
    def LocX(self):      return u'{:.1f}'.format(((self._u_lo + self._u_hi) * 0.5) * 12)
    @property
    def LocY(self):      return u'{:.1f}'.format(((self._v_lo + self._v_hi) * 0.5) * 12)
    @property
    def LineStyle(self): return u'{} ({}%)'.format(self._dom_style, self._dom_pct)
    @property
    def IsHot(self):     return self._count >= HOT_THRESHOLD


# ── Clustering ──────────────────────────────────────────────────────────────
# Each hot cell is reported as its own hotspot — no neighbor merging, no BFS.
# Two adjacent hot cells = two separate rows. Keeps zones tight and prevents
# the chain-effect where dense continuous regions get glued into one giant
# cluster.

def compute_clusters(midpoints, cell_size_in, min_lines, max_span_in):
    cell_size_ft = cell_size_in / 12.0
    max_span_ft  = max_span_in  / 12.0

    # Filter long curves out before binning — their bbox spans the view so
    # selecting them defeats the zoom.
    pts = [m for m in midpoints if m[4] <= max_span_ft]
    if not pts:
        return []

    us = [m[0] for m in pts]
    vs = [m[1] for m in pts]
    uMin = min(us) - cell_size_ft   # 1-cell padding
    vMin = min(vs) - cell_size_ft

    cell_map  = defaultdict(list)   # (ix, iy) -> [(eid, style), ...]
    cell_bbox = {}                  # (ix, iy) -> [u_lo, v_lo, u_hi, v_hi]
    for mu, mv, eid, style, _span in pts:
        ix = int((mu - uMin) / cell_size_ft)
        iy = int((mv - vMin) / cell_size_ft)
        cell_map[(ix, iy)].append((eid, style))
        bb = cell_bbox.get((ix, iy))
        if bb is None:
            cell_bbox[(ix, iy)] = [mu, mv, mu, mv]
        else:
            if mu < bb[0]: bb[0] = mu
            if mv < bb[1]: bb[1] = mv
            if mu > bb[2]: bb[2] = mu
            if mv > bb[3]: bb[3] = mv

    rows_data = []
    for cell, items in cell_map.items():
        count = len(items)
        if count < min_lines:
            continue
        ids = [it[0] for it in items]

        # Tight bbox of midpoints in this cell, in view (u, v) coords.
        u_lo, v_lo, u_hi, v_hi = cell_bbox[cell]
        bb_uv = (u_lo, v_lo, u_hi, v_hi)

        style_counts = defaultdict(int)
        for _eid, st in items:
            style_counts[st] += 1
        dom_style, dom_n = max(style_counts.items(), key=lambda kv: kv[1])
        dom_pct = int(round(100.0 * dom_n / count))

        rows_data.append((count, bb_uv, ids, dom_style, dom_pct))

    rows_data.sort(key=lambda r: -r[0])
    rows_data = rows_data[:MAX_CLUSTERS]
    return [HotspotRow(i + 1, *r) for i, r in enumerate(rows_data)]


# ── XAML ────────────────────────────────────────────────────────────────────

_BODY = """
<Grid>
  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="*"/>
  </Grid.RowDefinitions>

  <Grid Grid.Row="0" Margin="0,0,0,8">
    <Grid.ColumnDefinitions>
      <ColumnDefinition Width="Auto"/>
      <ColumnDefinition Width="60"/>
      <ColumnDefinition Width="16"/>
      <ColumnDefinition Width="Auto"/>
      <ColumnDefinition Width="60"/>
      <ColumnDefinition Width="16"/>
      <ColumnDefinition Width="Auto"/>
      <ColumnDefinition Width="60"/>
      <ColumnDefinition Width="16"/>
      <ColumnDefinition Width="Auto"/>
      <ColumnDefinition Width="*"/>
    </Grid.ColumnDefinitions>
    <TextBlock Grid.Column="0" Text="Cell size (in):" VerticalAlignment="Center"
               Margin="0,0,8,0" Foreground="#E8EBF5"/>
    <TextBox   Grid.Column="1" x:Name="txtCellSize" Text="3" VerticalAlignment="Center"/>
    <TextBlock Grid.Column="3" Text="Min curves:" VerticalAlignment="Center"
               Margin="0,0,8,0" Foreground="#E8EBF5"/>
    <TextBox   Grid.Column="4" x:Name="txtMinLines" Text="6" VerticalAlignment="Center"/>
    <TextBlock Grid.Column="6" Text="Max span (in):" VerticalAlignment="Center"
               Margin="0,0,8,0" Foreground="#E8EBF5"
               ToolTip="Discard curves whose endpoints are farther apart than this — filters out grids/walls/dims."/>
    <TextBox   Grid.Column="7" x:Name="txtMaxSpan" Text="6" VerticalAlignment="Center"/>
    <Button    Grid.Column="9" x:Name="btnRescan" Content="Rescan"
               Style="{StaticResource BtnGhost}" Margin="16,0,0,0"/>
  </Grid>

  <TextBox x:Name="txtSearch" Grid.Row="1" Margin="0,0,0,8"
           ToolTip="Filter by line style name..."/>

  <DataGrid x:Name="grid" Grid.Row="2"
            AutoGenerateColumns="False" CanUserAddRows="False"
            CanUserSortColumns="True" SelectionMode="Single">
    <DataGrid.RowStyle>
      <Style TargetType="DataGridRow">
        <Setter Property="Foreground" Value="#E8EBF5"/>
        <Style.Triggers>
          <DataTrigger Binding="{Binding IsHot}" Value="True">
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
      <DataGridTextColumn Header="#"          Width="40"  Binding="{Binding Rank}"      IsReadOnly="True"/>
      <DataGridTextColumn Header="CURVES"     Width="70"  Binding="{Binding Count}"     IsReadOnly="True"/>
      <DataGridTextColumn Header="WIDTH"      Width="80"  Binding="{Binding Width}"     IsReadOnly="True"/>
      <DataGridTextColumn Header="HEIGHT"     Width="80"  Binding="{Binding Height}"    IsReadOnly="True"/>
      <DataGridTextColumn Header="LOC X"      Width="80"  Binding="{Binding LocX}"      IsReadOnly="True"/>
      <DataGridTextColumn Header="LOC Y"      Width="80"  Binding="{Binding LocY}"      IsReadOnly="True"/>
      <DataGridTextColumn Header="LINE STYLE" Width="*"   Binding="{Binding LineStyle}" IsReadOnly="True"/>
    </DataGrid.Columns>
  </DataGrid>
</Grid>
"""

_FOOTER = """
<Grid>
  <TextBlock x:Name="lblCount" VerticalAlignment="Center" Foreground="#5A6286"/>
  <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
    <Button x:Name="btnZoom"  Content="Zoom &amp; Select"
            Style="{StaticResource BtnPrimary}" Margin="0,0,8,0"/>
    <Button x:Name="btnClose" Content="Close" Style="{StaticResource BtnGhost}"/>
  </StackPanel>
</Grid>
"""


# ── Window setup ────────────────────────────────────────────────────────────

view_name = view.Name if hasattr(view, 'Name') else u"<view>"
win = ui.parse(
    "Line Density",
    u"{} curves scanned  -  view: {}".format(n_curves, view_name),
    _BODY, _FOOTER,
    width=820, height=520,
)

txtCellSize = win.FindName("txtCellSize")
txtMinLines = win.FindName("txtMinLines")
txtMaxSpan  = win.FindName("txtMaxSpan")
btnRescan   = win.FindName("btnRescan")
txtSearch   = win.FindName("txtSearch")
grid        = win.FindName("grid")
lblCount    = win.FindName("lblCount")
btnZoom     = win.FindName("btnZoom")
btnClose    = win.FindName("btnClose")
subtitle_tb = win.FindName("__noir_subtitle__")

state = {
    "all_rows":  [],
    "cell_size": DEFAULT_CELL_SIZE_IN,
    "min_lines": DEFAULT_MIN_LINES,
    "max_span":  DEFAULT_MAX_SPAN_IN,
}


def _to_col(items):
    col = ObservableCollection[object]()
    for it in items:
        col.Add(it)
    return col


def update_subtitle():
    if subtitle_tb is None:
        return
    cs = state["cell_size"]
    if isinstance(cs, float) and cs == int(cs):
        cs_txt = u'{}"'.format(int(cs))
    else:
        cs_txt = u'{}"'.format(cs)
    subtitle_tb.Text = (
        u"{} clusters  -  Cell: {}  -  Min: {}  -  Max span: {}\"  -  {} curves scanned".format(
            len(state["all_rows"]), cs_txt, state["min_lines"],
            state["max_span"], n_curves
        )
    )


def refresh_grid(*args):
    q = (txtSearch.Text or "").strip().lower()
    if q:
        rs = [r for r in state["all_rows"] if q in r._dom_style.lower()]
    else:
        rs = list(state["all_rows"])
    grid.ItemsSource = _to_col(rs)
    lblCount.Text = u"{} shown".format(len(rs))


def do_rescan(*args):
    ok = True
    try:
        cs = float(txtCellSize.Text)
        if cs <= 0:
            raise ValueError
        txtCellSize.BorderBrush = Brushes.Transparent
    except Exception:
        txtCellSize.BorderBrush = Brushes.IndianRed
        ok = False
        cs = None
    try:
        ml = int(txtMinLines.Text)
        if ml <= 0:
            raise ValueError
        txtMinLines.BorderBrush = Brushes.Transparent
    except Exception:
        txtMinLines.BorderBrush = Brushes.IndianRed
        ok = False
        ml = None
    try:
        ms = float(txtMaxSpan.Text)
        if ms <= 0:
            raise ValueError
        txtMaxSpan.BorderBrush = Brushes.Transparent
    except Exception:
        txtMaxSpan.BorderBrush = Brushes.IndianRed
        ok = False
        ms = None
    if not ok:
        return
    state["cell_size"] = cs
    state["min_lines"] = ml
    state["max_span"]  = ms
    state["all_rows"]  = compute_clusters(midpoints, cs, ml, ms)
    update_subtitle()
    refresh_grid()


def do_zoom(*args):
    row = grid.SelectedItem
    if row is None:
        ui.alert("Select a cluster row first.", title="Line Density")
        return
    ids = DotNetList[ElementId]()
    for eid in row._ids:
        ids.Add(eid)

    # ShowElements uses Revit's native "Zoom to Selection" logic which
    # measures the real bounding box of the elements and fits the viewport
    # tightly. Now that long lines (grids/walls/dims) are filtered out by
    # the max-span control, the only elements left in ids are short curves,
    # so the zoom locks tight to the hot zone.
    uidoc.Selection.SetElementIds(ids)
    try:
        uidoc.ShowElements(ids)
    except Exception:
        pass
    try:
        uidoc.RefreshActiveView()
    except Exception:
        pass
    # Don't close — user wants to navigate multiple hotspots in one session.


def on_row_double_click(s, e):
    if grid.SelectedItem is not None:
        do_zoom()


btnRescan.Click       += do_rescan
btnZoom.Click         += do_zoom
btnClose.Click        += lambda s, e: win.Close()
txtSearch.TextChanged += refresh_grid
grid.MouseDoubleClick += on_row_double_click

# Initial scan with defaults
do_rescan()

# Modal — required by ui.parse() framework. ZoomAndCenterRectangle and
# Selection.SetElementIds work through the modal dialog so the user can still
# inspect zones via the "Zoom & Select" button without closing the window.
# (For true non-modal behavior where the user can pan/zoom Revit manually
# while the tool is open, the window needs to be rebuilt with XamlReader.Parse
# instead of ui.parse — bigger refactor, defer until needed.)
win.ShowDialog()
