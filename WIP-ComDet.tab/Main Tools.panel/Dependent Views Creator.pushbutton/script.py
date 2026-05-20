# -*- coding: utf-8 -*-
__title__ = 'Create\nDetail Views'
__doc__ = ('Creates dependent views from Detail Line rectangles drawn in the active view. '
           'Before running: draw rectangles with a reserved LineStyle and (optionally) place '
           'a TextNote inside each rectangle to name the dependent view. '
           'SELECT both the rectangle lines and the name TextNotes, then run the tool — '
           'pickers will offer only the LineStyles / TextNoteTypes found in your selection. '
           'After creation, the source rectangles and labels are deleted automatically.')

import os as _os
import sys

sys.path.append(_os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))),
    'lib'
))
from magictools import ui

from pyrevit import revit, DB, script
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewDuplicateOption,
    CurveLoop,
    Line,
    XYZ,
    Transform,
    Transaction,
    DetailLine,
    TextNote,
)

doc   = revit.doc
uidoc = revit.uidoc

# Module-level notes list — used by helper functions to surface loose warnings
# (loops that don't close, multiple TextNotes inside a rectangle, …).
# Per-loop outcomes go into `results` inside main().
notes = []


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — coordinate transforms
# ─────────────────────────────────────────────────────────────────────────────

def get_view_transform(view):
    t = Transform.Identity
    t.BasisX = view.RightDirection
    t.BasisY = view.UpDirection
    t.BasisZ = view.ViewDirection
    t.Origin = view.Origin
    return t


def world_to_view(pt, view_transform):
    return view_transform.Inverse.OfPoint(pt)


def view_to_world(pt, view_transform):
    return view_transform.OfPoint(pt)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — endpoints and loops
# ─────────────────────────────────────────────────────────────────────────────

def get_endpoints(detail_line):
    crv = detail_line.GeometryCurve
    return crv.GetEndPoint(0), crv.GetEndPoint(1)


def pts_close(a, b, tol=1e-4):
    return a.DistanceTo(b) < tol


def build_loops(segments):
    segs = [[e, p0, p1] for e, p0, p1 in segments]
    remaining = list(range(len(segs)))
    result_loops = []

    while remaining:
        start_idx = remaining[0]
        loop_indices = [start_idx]
        remaining.remove(start_idx)

        loop_start_pt = segs[start_idx][1]
        current_end   = segs[start_idx][2]

        for _ in range(len(segs)):
            found = False
            for r in list(remaining):
                p0, p1 = segs[r][1], segs[r][2]
                if pts_close(current_end, p0):
                    loop_indices.append(r)
                    remaining.remove(r)
                    current_end = p1
                    found = True
                    break
                elif pts_close(current_end, p1):
                    segs[r][1], segs[r][2] = p1, p0
                    loop_indices.append(r)
                    remaining.remove(r)
                    current_end = p0
                    found = True
                    break
            if not found:
                break
            if pts_close(current_end, loop_start_pt):
                break

        if pts_close(current_end, loop_start_pt) and len(loop_indices) >= 3:
            result_loops.append([tuple(segs[i]) for i in loop_indices])
        else:
            notes.append(
                u"{} line(s) do not form a closed loop and will be ignored.".format(
                    len(loop_indices))
            )

    return result_loops


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — bounding box in view-space
# ─────────────────────────────────────────────────────────────────────────────

def loop_bbox_viewspace(loop_segs, view_transform):
    view_pts = []
    for _, p0, p1 in loop_segs:
        view_pts.append(world_to_view(p0, view_transform))
        view_pts.append(world_to_view(p1, view_transform))

    xs = [p.X for p in view_pts]
    ys = [p.Y for p in view_pts]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_bbox(pt_view, bbox, margin=1e-6):
    min_x, min_y, max_x, max_y = bbox
    return (min_x - margin <= pt_view.X <= max_x + margin and
            min_y - margin <= pt_view.Y <= max_y + margin)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — match TextNote names to loops
# ─────────────────────────────────────────────────────────────────────────────

def find_name_for_loop(bbox_viewspace, all_textnotes, view_transform):
    """Return (text, [tn_ids]) for TextNotes whose origin falls inside the bbox.
    Returns (None, []) when none found. When multiple are found, the first text
    wins but ALL matching TextNote ids are returned so they can all be removed.
    """
    found_text = []
    found_ids  = []
    for text, origin_world, tn_id in all_textnotes:
        origin_view = world_to_view(origin_world, view_transform)
        if point_in_bbox(origin_view, bbox_viewspace):
            found_text.append(text)
            found_ids.append(tn_id)

    if not found_text:
        return None, []
    if len(found_text) > 1:
        notes.append(
            u"{} name TextNotes inside one rectangle. Using first: {}".format(
                len(found_text), found_text[0])
        )
    return found_text[0], found_ids


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — crop region
# ─────────────────────────────────────────────────────────────────────────────

def make_crop_loop(bbox_viewspace, view_transform):
    min_x, min_y, max_x, max_y = bbox_viewspace

    w = max_x - min_x
    h = max_y - min_y

    if w < 0.01 or h < 0.01:
        raise ValueError(
            "Bounding box too small: {:.4f} x {:.4f} ft. "
            "Check that the lines are in the active view.".format(w, h)
        )

    corners_view = [
        XYZ(min_x, min_y, 0.0),
        XYZ(max_x, min_y, 0.0),
        XYZ(max_x, max_y, 0.0),
        XYZ(min_x, max_y, 0.0),
    ]
    corners_world = [view_to_world(c, view_transform) for c in corners_view]

    loop = CurveLoop()
    for k in range(4):
        loop.Append(Line.CreateBound(corners_world[k], corners_world[(k + 1) % 4]))

    return loop


def next_available_name(base, existing):
    if base not in existing:
        return base
    n = 2
    while True:
        candidate = "{} ({})".format(base, n)
        if candidate not in existing:
            return candidate
        n += 1


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    active_view = uidoc.ActiveView

    if not active_view.CanViewBeDuplicated(ViewDuplicateOption.AsDependent):
        ui.alert(
            "The active view does not support dependent views.\n"
            "Switch to a floor plan, elevation, or section view.",
            title="Dependent Views Creator",
        )
        script.exit()

    # ── Check selection FIRST (before any pickers) ──
    sel_ids = list(uidoc.Selection.GetElementIds())
    if not sel_ids:
        ui.alert(
            "Nothing selected.\n\n"
            "Select the Detail Lines that form the crop box rectangles "
            "(and optionally the TextNotes that label them).",
            title="Dependent Views Creator",
        )
        script.exit()

    # ── Partition selection: DetailLines + TextNotes ──
    selected_lines = []   # list of (DetailLine, linestyle_name)
    selected_notes = []   # list of (text, origin_xyz, tn_id, tn_type_name)
    linestyle_set  = set()
    tn_type_set    = set()

    for eid in sel_ids:
        el = doc.GetElement(eid)
        if isinstance(el, DetailLine):
            try:
                ls_name = el.LineStyle.Name
                selected_lines.append((el, ls_name))
                linestyle_set.add(ls_name)
            except Exception:
                pass
        elif isinstance(el, TextNote):
            try:
                tn_type = doc.GetElement(el.GetTypeId())
                tn_type_name = None
                if tn_type:
                    p = tn_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
                    tn_type_name = p.AsString() if p else tn_type.Name
                text = el.Text.strip()
                if text and tn_type_name:
                    selected_notes.append((text, el.Coord, el.Id, tn_type_name))
                    tn_type_set.add(tn_type_name)
            except Exception:
                pass

    if not selected_lines:
        ui.alert(
            "Selection has no Detail Lines.\n\n"
            "Select the Detail Lines that form the crop box rectangles.\n"
            "Make sure they are DetailLines (not ModelLines).",
            title="Dependent Views Creator",
        )
        script.exit()

    # ── Pick crop box LineStyle (auto-pick if only one in selection) ──
    linestyle_names = sorted(linestyle_set)
    if len(linestyle_names) == 1:
        chosen_linestyle = linestyle_names[0]
    else:
        chosen_linestyle = ui.pick_list(
            linestyle_names,
            "Crop Box LineStyle",
            subtitle="Select the LineStyle used for crop box rectangles:",
            multiselect=False,
            context=u"Reserved line style for crop box rectangles (typically "
                    u"'_CropBox'). The tool scans the active view for rectangles "
                    u"drawn in this style and creates one dependent view per "
                    u"rectangle, cropped to its bounds."
        )
        if not chosen_linestyle:
            script.exit()

    # ── Pick view name TextNoteType (skip if no TextNotes selected, auto-pick if only one) ──
    if not selected_notes:
        chosen_tn_type = None
    else:
        tn_type_names = sorted(tn_type_set)
        if len(tn_type_names) == 1:
            chosen_tn_type = tn_type_names[0]
        else:
            chosen_tn_type = ui.pick_list(
                tn_type_names,
                "View Name TextNoteType",
                subtitle="Select the TextNoteType used for view name labels:",
                multiselect=False,
                context=u"Reserved text type for view name labels (typically "
                        u"'_CropName'). Any TextNote of this type that falls INSIDE "
                        u"a rectangle gives its name to the matching dependent view. "
                        u"Rectangles without a text note get a generic auto-name."
            )
            if not chosen_tn_type:
                script.exit()

    # ── Filter by chosen LineStyle and (optionally) chosen TextNoteType ──
    detail_lines = [dl for dl, ls in selected_lines if ls == chosen_linestyle]
    if not detail_lines:
        ui.alert(
            "No Detail Lines with LineStyle '{}' in selection.".format(chosen_linestyle),
            title="Dependent Views Creator",
        )
        script.exit()

    segments = [(dl,) + get_endpoints(dl) for dl in detail_lines]
    loops = build_loops(segments)

    if not loops:
        ui.alert(
            "No closed loops detected.\n\n"
            "Make sure that:\n"
            "  - Each rectangle is fully closed\n"
            "  - Endpoints touch each other (use Snap to Endpoint)\n"
            "  - No loose lines are mixed in",
            title="Dependent Views Creator",
        )
        script.exit()

    view_transform = get_view_transform(active_view)
    if chosen_tn_type:
        all_textnotes = [
            (text, origin, tn_id)
            for (text, origin, tn_id, tn_type) in selected_notes
            if tn_type == chosen_tn_type
        ]
    else:
        all_textnotes = []

    # Names of all views in the project
    all_views_col = FilteredElementCollector(doc).OfClass(DB.View).ToElements()
    existing_names = set()
    for v in all_views_col:
        try:
            existing_names.add(v.Name)
        except Exception:
            pass

    # Names of existing dependent views from the active view
    existing_dep_names = set()
    for vid in active_view.GetDependentViewIds():
        dep = doc.GetElement(vid)
        if dep:
            try:
                existing_dep_names.add(dep.Name)
            except Exception:
                pass

    master_name = active_view.Name
    default_zone_counter = [0]

    # Per-loop results: list of (status, view_name, source, size_str)
    #   status ∈ {"created", "renamed", "skipped", "error"}
    results  = []
    created  = 0
    renamed  = 0
    skipped  = 0
    errors   = 0

    # Elements to clean up after creation. We only delete the DetailLines and
    # TextNotes belonging to loops that were actually created/renamed/skipped.
    # Errored loops keep their geometry so the user can investigate.
    lines_to_delete     = []   # ElementIds of DetailLines
    textnotes_to_delete = []   # ElementIds of TextNotes
    deleted_count       = 0

    with Transaction(doc, "Create Dependent Views from Detail Lines") as t:
        t.Start()

        for i, loop_segs in enumerate(loops):

            # DetailLine IDs that form this rectangle
            loop_line_ids = [seg[0].Id for seg in loop_segs]

            bbox = loop_bbox_viewspace(loop_segs, view_transform)
            min_x, min_y, max_x, max_y = bbox
            w_ft = max_x - min_x
            h_ft = max_y - min_y
            size_str = u"{:.0f} × {:.0f} mm".format(w_ft * 304.8, h_ft * 304.8)

            found_name, found_tn_ids = find_name_for_loop(
                bbox, all_textnotes, view_transform)

            if found_name:
                source = u"📝 {}".format(found_name)
                base_name = found_name
            else:
                default_zone_counter[0] += 1
                base_name = "{} - Zone {}".format(master_name, default_zone_counter[0])
                source = u"Zone {} (default)".format(default_zone_counter[0])

            if base_name in existing_dep_names:
                results.append((
                    "skipped", base_name,
                    source + u"  ·  already exists", size_str,
                ))
                skipped += 1
                # View already exists → rectangle + label are stale, remove them
                lines_to_delete.extend(loop_line_ids)
                textnotes_to_delete.extend(found_tn_ids)
                continue

            final_name = next_available_name(base_name, existing_names)
            was_renamed = (final_name != base_name)
            existing_names.add(final_name)

            try:
                crop_loop = make_crop_loop(bbox, view_transform)
            except ValueError as e:
                results.append((
                    "error", base_name,
                    u"{}  ·  {}".format(source, e), size_str,
                ))
                errors += 1
                # Keep geometry for errored loops (user needs to fix it)
                continue

            new_view_id = active_view.Duplicate(ViewDuplicateOption.AsDependent)
            new_view    = doc.GetElement(new_view_id)

            try:
                new_view.Name = final_name
            except Exception:
                try:
                    param = new_view.get_Parameter(DB.BuiltInParameter.VIEW_NAME)
                    if param and not param.IsReadOnly:
                        param.Set(final_name)
                except Exception:
                    pass

            new_view.CropBoxActive  = True
            new_view.CropBoxVisible = True
            crop_manager = new_view.GetCropRegionShapeManager()
            crop_manager.SetCropShape(crop_loop)

            if was_renamed:
                results.append((
                    "renamed", final_name,
                    source + u'  ·  renamed from "{}"'.format(base_name),
                    size_str,
                ))
                renamed += 1
            else:
                results.append(("created", final_name, source, size_str))
            created += 1
            # Successful create → rectangle + label are no longer needed
            lines_to_delete.extend(loop_line_ids)
            textnotes_to_delete.extend(found_tn_ids)

        # Clean up rectangles + labels for processed loops (inside the same Tx).
        # Dedupe ids in case the same TextNote got matched by multiple loops
        # (overlapping rectangles — rare but possible).
        for eid in set(lines_to_delete) | set(textnotes_to_delete):
            try:
                doc.Delete(eid)
                deleted_count += 1
            except Exception:
                pass

        t.Commit()

    return {
        "master_name":     master_name,
        "chosen_linestyle": chosen_linestyle,
        "chosen_tn_type":  chosen_tn_type,
        "n_loops":         len(loops),
        "results":         results,
        "created":         created,
        "renamed":         renamed,
        "skipped":         skipped,
        "errors":          errors,
        "deleted":         deleted_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# RESULTS WINDOW — Noir badges + DataGrid (matches Export & Import tools)
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
        "renamed": u"⚠️  Renamed",
        "skipped": u"⏭  Skipped",
        "error":   u"❌  Error",
    }
    def __init__(self, status, name, source, size):
        self._status = status
        self._name   = name
        self._source = source
        self._size   = size

    @property
    def Status(self):   return self._LABELS.get(self._status, self._status)
    @property
    def ViewName(self): return self._name
    @property
    def Source(self):   return self._source
    @property
    def Size(self):     return self._size


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
    <Border x:Name="badgeRenamedBorder" Background="#4A3810" BorderBrush="#FFCC66"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeRenamed" Foreground="#FFCC66" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeSkippedBorder" Background="#1E2235" BorderBrush="#64748B"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeSkipped" Foreground="#64748B" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeErrorBorder" Background="#3C1212" BorderBrush="#FF7070"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeErrors" Foreground="#FF7070" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeDeletedBorder" Background="#1E2235" BorderBrush="#64748B"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeDeleted" Foreground="#64748B" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeNotesBorder" Background="#4A3810" BorderBrush="#FFCC66"
            BorderThickness="1" CornerRadius="4" Padding="10,4"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeNotes" Foreground="#FFCC66" FontFamily="Segoe UI" FontSize="13"/>
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
      <DataGridTextColumn Header="View Name" Binding="{Binding ViewName}" Width="*"/>
      <DataGridTextColumn Header="Source"    Binding="{Binding Source}"   Width="280"/>
      <DataGridTextColumn Header="Size"      Binding="{Binding Size}"     Width="130"/>
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


def show_results(summary):
    master_name = summary["master_name"]
    results     = summary["results"]
    created     = summary["created"]
    renamed     = summary["renamed"]
    skipped     = summary["skipped"]
    errors      = summary["errors"]
    n_loops     = summary["n_loops"]

    subtitle = u"Active view: {}  ·  LineStyle: {}  ·  {} loop{}".format(
        master_name, summary["chosen_linestyle"],
        n_loops, u"s" if n_loops != 1 else u"")

    rows = ObservableCollection[_Row]()
    for status, name, source, size in results:
        rows.Add(_Row(status, name, source, size))

    win = ui.parse(u"Dependent Views Creator", subtitle,
                   _BODY_XAML, _FOOTER_XAML, width=960, height=560)

    win.FindName("badgeCreated").Text = u"✅  {} created".format(created)
    if renamed:
        win.FindName("badgeRenamedBorder").Visibility = Visibility.Visible
        win.FindName("badgeRenamed").Text = u"⚠️  {} renamed".format(renamed)
    if skipped:
        win.FindName("badgeSkippedBorder").Visibility = Visibility.Visible
        win.FindName("badgeSkipped").Text = u"⏭  {} skipped".format(skipped)
    if errors:
        win.FindName("badgeErrorBorder").Visibility = Visibility.Visible
        win.FindName("badgeErrors").Text = u"❌  {} error{}".format(
            errors, u"s" if errors != 1 else u"")
    if summary["deleted"]:
        win.FindName("badgeDeletedBorder").Visibility = Visibility.Visible
        win.FindName("badgeDeleted").Text = u"🗑  {} cleaned up".format(summary["deleted"])
    if notes:
        win.FindName("badgeNotesBorder").Visibility = Visibility.Visible
        win.FindName("badgeNotes").Text = u"⚠️  {} note{}".format(
            len(notes), u"s" if len(notes) != 1 else u"")

    win.FindName("dgResults").ItemsSource = rows

    def on_copy(s, e):
        out = [u"Dependent Views Creator — " + subtitle, u""]
        for r in rows:
            out.append(u"{}  |  {}  |  {}  |  {}".format(
                r.Status, r.ViewName, r.Source, r.Size))
        if notes:
            out.append(u"")
            out.append(u"Notes:")
            for n in notes:
                out.append(u"  ⚠ " + n)
        WinFormsClipboard.SetText(u"\n".join(out))
        s.Content = u"Copied ✓"

    win.FindName("btnCopy").Click += on_copy
    win.FindName("btnOK").Click   += lambda s, e: win.Close()
    win.ShowDialog()


summary = main()
if summary is not None:
    show_results(summary)
