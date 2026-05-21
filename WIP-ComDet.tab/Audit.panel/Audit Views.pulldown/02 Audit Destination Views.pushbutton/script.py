# -*- coding: utf-8 -*-
__title__ = 'Audit\nDestination Views'
__doc__ = ('Compares a Sheets with Views JSON against the destination model. '
           'Reports dependent views not accounted for in the JSON, orphaned '
           'views, scale mismatches, and view type mismatches. Offers to '
           'delete orphans, scale-mismatch views, type-mismatch views, and '
           'placed-views-not-in-JSON via action buttons in the report footer. '
           'Matches by Detail ID first (robust against prefixes and renames), '
           'then falls back to view name. Reads the combined JSON produced '
           'by "Export Sheets with Views" (format: sheets_with_views).')

import sys
import json
import os as _os
from pyrevit import revit, DB, script, forms

sys.path.append(_os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))),
    'lib'
))
from magictools import ui

import clr
clr.AddReference('PresentationCore')
clr.AddReference('System.Windows.Forms')
from System.Windows import Visibility
from System.Windows.Forms import Clipboard as WinFormsClipboard
from System.Collections.ObjectModel import ObservableCollection

doc = revit.doc

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_detail_id(view):
    try:
        p = view.LookupParameter("Detail ID")
        if p:
            val = p.AsString()
            return val.strip() if val else ""
    except Exception:
        pass
    return ""


def robust_delete(doc, rows, txn_name):
    """Delete views referenced by row objects. Returns (deleted, failed, errors).

    Uses DB.Transaction with explicit Commit/RollBack. Filters out invalid
    objects. Captures per-view exception messages for the status line.
    """
    deleted = 0
    failed  = 0
    errors  = []
    t = DB.Transaction(doc, txn_name)
    t.Start()
    try:
        for row in rows:
            v = row._view
            try:
                if v is None or not v.IsValidObject:
                    failed += 1
                    errors.append(u"'{}': object no longer valid".format(row.ViewName))
                    continue
                doc.Delete(v.Id)
                deleted += 1
            except Exception as ex:
                failed += 1
                errors.append(u"'{}': {}".format(row.ViewName, str(ex)[:100]))
        t.Commit()
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        deleted = 0
        failed = len(rows)
        errors = [u"Transaction failed: {}".format(str(ex)[:120])]
    return deleted, failed, errors


# ─────────────────────────────────────────────────────────────────────────────
# Row class — bound by WPF (PascalCase properties)
# ─────────────────────────────────────────────────────────────────────────────

class _IssueRow(object):
    def __init__(self, view, master, icon, detail, match_basis=u"", on_sheet=False, sheet_no=u""):
        self._view     = view
        self._master   = master
        self._icon     = icon
        self._detail   = detail
        self._mb       = match_basis or u""
        self._on_sheet = bool(on_sheet)
        self._sheet_no = sheet_no
        try:
            self._name_cache = view.Name if view is not None else u""
        except Exception:
            self._name_cache = u"(unknown)"

    @property
    def Icon(self):     return self._icon
    @property
    def Master(self):   return self._master
    @property
    def ViewName(self):
        try:
            if self._view is not None and self._view.IsValidObject:
                return self._view.Name
        except Exception:
            pass
        return self._name_cache
    @property
    def MatchTag(self): return u" [by ID]" if self._mb == "id" else u""
    @property
    def Detail(self):   return self._detail
    @property
    def Sheet(self):    return self._sheet_no
    @property
    def IsOnSheet(self):return self._on_sheet


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick JSON file
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(file_ext="json", title="Select Sheets with Views JSON")
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    data = json.load(f)

if data.get("format") != "sheets_with_views":
    ui.alert(
        u"Wrong file format.\n\n"
        u"Audit Destination Views expects a JSON exported by 'Export Sheets "
        u"with Views' (format: sheets_with_views). The selected file has "
        u"format: \"{}\".\n\nFor the legacy details_views.json format, use the "
        u"matching tool in the Legacy pulldown.".format(
            data.get("format", "unknown")),
        title=u"Audit Destination Views"
    )
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 2. Select master views to audit
# ─────────────────────────────────────────────────────────────────────────────

master_options = sorted([mv["view_name"] for mv in data["master_views"]])

chosen_masters = ui.pick_list(
    master_options,
    "Audit Destination Views",
    subtitle="Select which master views to include in the audit:",
    multiselect=True,
    context=u"Audits the active model (typically a building destination) against "
            u"the Common Details master JSON. Reports orphans (views in the JSON "
            u"that are missing from the model), scale/type mismatches, and "
            u"incorrect detail numbers. Dependents under masters you don't tick "
            u"are excluded from the report."
)
if not chosen_masters:
    script.exit()

chosen_masters_set = set(chosen_masters)

# ─────────────────────────────────────────────────────────────────────────────
# 3. Build JSON lookups — keyed by detail_id AND by view_name
# ─────────────────────────────────────────────────────────────────────────────

json_by_id   = {}
json_by_name = {}

for mv in data["master_views"]:
    if mv["view_name"] not in chosen_masters_set:
        continue
    for dv in mv["dependent_views"]:
        entry = {
            "view_name":       dv["view_name"],
            "view_scale":      dv.get("view_scale"),
            "view_type":       dv.get("view_type", ""),
            "placed_on_sheet": dv.get("placed_on_sheet", True),
            "master_name":     mv["view_name"],
        }
        did = (dv.get("detail_id") or "").strip()
        if did:
            json_by_id[did] = entry
        json_by_name[dv["view_name"]] = entry

json_view_names = set(json_by_name.keys())

has_scale_data = any(e["view_scale"] is not None for e in json_by_name.values())
has_type_data  = any(e["view_type"]              for e in json_by_name.values())

# ─────────────────────────────────────────────────────────────────────────────
# 4. Index dependent views in destination model + viewports
# ─────────────────────────────────────────────────────────────────────────────

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

dep_views_in_model = []
for v in all_views:
    try:
        if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
            dep_views_in_model.append(v)
    except Exception:
        pass

all_viewports = DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements()
placed_view_ids   = set(vp.ViewId.IntegerValue for vp in all_viewports)
sheet_no_by_view  = {}
for vp in all_viewports:
    try:
        sheet = doc.GetElement(vp.SheetId)
        if sheet is not None:
            sheet_no_by_view[vp.ViewId.IntegerValue] = sheet.SheetNumber
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# 5. Classify each dependent view in scope
# ─────────────────────────────────────────────────────────────────────────────

scale_rows  = []
type_rows   = []
orphan_rows = []
placed_rows = []
matched_ok  = 0

for v in dep_views_in_model:
    try:
        did = get_detail_id(v)
        json_entry  = json_by_id.get(did) if did else None
        match_basis = "id" if json_entry else None

        if json_entry is None:
            json_entry = json_by_name.get(v.Name)
            if json_entry:
                match_basis = "name"

        # Not matched → check scope via primary
        if json_entry is None:
            try:
                primary = doc.GetElement(v.GetPrimaryViewId())
                if primary is None or primary.Name not in chosen_masters_set:
                    continue
                master_name = primary.Name
            except Exception:
                continue
            is_placed  = v.Id.IntegerValue in placed_view_ids
            sheet_no   = sheet_no_by_view.get(v.Id.IntegerValue, u"")
            if is_placed:
                placed_rows.append(_IssueRow(
                    v, master_name, u"📌",
                    u"on sheet {}".format(sheet_no) if sheet_no else u"on a sheet",
                    on_sheet=True, sheet_no=sheet_no))
            else:
                orphan_rows.append(_IssueRow(
                    v, master_name, u"👻",
                    u"not on any sheet",
                    on_sheet=False))
            continue

        # Matched — compare scale / type
        master_name = json_entry["master_name"]
        is_placed   = v.Id.IntegerValue in placed_view_ids
        sheet_no    = sheet_no_by_view.get(v.Id.IntegerValue, u"")
        placement   = u"on sheet {}".format(sheet_no) if (is_placed and sheet_no) else (
                       u"on a sheet" if is_placed else u"not on any sheet")
        had_issue   = False

        if has_scale_data:
            json_scale = json_entry["view_scale"]
            if json_scale is not None:
                try:
                    json_scale_int = int(json_scale)
                    model_scale    = v.Scale
                    if model_scale != json_scale_int:
                        scale_rows.append(_IssueRow(
                            v, master_name, u"📐",
                            u"1:{} → 1:{}  ({})".format(model_scale, json_scale_int, placement),
                            match_basis=match_basis, on_sheet=is_placed,
                            sheet_no=sheet_no))
                        had_issue = True
                except (TypeError, ValueError):
                    pass

        if has_type_data:
            json_type  = json_entry["view_type"]
            model_type = str(v.ViewType)
            if json_type and model_type != json_type:
                type_rows.append(_IssueRow(
                    v, master_name, u"🔄",
                    u"{} → {}  ({})".format(model_type, json_type, placement),
                    match_basis=match_basis, on_sheet=is_placed,
                    sheet_no=sheet_no))
                had_issue = True

        if not had_issue:
            matched_ok += 1

    except Exception:
        continue

# Sort all categories by master then by view name for stable display
def _sort_key(r):
    return (r.Master, r.ViewName)
scale_rows.sort(key=_sort_key)
type_rows.sort(key=_sort_key)
orphan_rows.sort(key=_sort_key)
placed_rows.sort(key=_sort_key)

# ─────────────────────────────────────────────────────────────────────────────
# 6. Build ObservableCollections
# ─────────────────────────────────────────────────────────────────────────────

scale_oc  = ObservableCollection[object]()
type_oc   = ObservableCollection[object]()
orphan_oc = ObservableCollection[object]()
placed_oc = ObservableCollection[object]()
for r in scale_rows:  scale_oc.Add(r)
for r in type_rows:   type_oc.Add(r)
for r in orphan_rows: orphan_oc.Add(r)
for r in placed_rows: placed_oc.Add(r)

n_scale  = len(scale_rows)
n_type   = len(type_rows)
n_orphan = len(orphan_rows)
n_placed = len(placed_rows)
n_total_issues = n_scale + n_type + n_orphan + n_placed

# ─────────────────────────────────────────────────────────────────────────────
# 7. Build Noir window XAML
# ─────────────────────────────────────────────────────────────────────────────

_BODY_XAML = u"""
<Grid>
  <Grid.Resources>

    <!-- Dark Expander template (Noir family) -->
    <Style TargetType="Expander">
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Expander">
            <StackPanel>
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

    <!-- Row template, reused across all Expanders -->
    <DataTemplate x:Key="rowTemplate">
      <Border BorderThickness="0,0,0,1" Padding="14,0,16,0">
        <Border.Style>
          <Style TargetType="Border">
            <Setter Property="Background"  Value="#12131F"/>
            <Setter Property="BorderBrush" Value="#2A2D47"/>
            <Style.Triggers>
              <DataTrigger Binding="{Binding IsOnSheet}" Value="True">
                <Setter Property="Background"      Value="#1C0E0E"/>
                <Setter Property="BorderBrush"     Value="#5A2020"/>
                <Setter Property="BorderThickness" Value="3,0,0,1"/>
              </DataTrigger>
            </Style.Triggers>
          </Style>
        </Border.Style>
        <Grid MinHeight="28">
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="22"/>
            <ColumnDefinition Width="170"/>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="240"/>
          </Grid.ColumnDefinitions>
          <TextBlock Grid.Column="0" Text="{Binding Icon}" FontSize="12"
                     VerticalAlignment="Center"/>
          <TextBlock Grid.Column="1" Text="{Binding Master}" Foreground="#9099C8"
                     FontFamily="Segoe UI" FontSize="12"
                     VerticalAlignment="Center" Margin="0,0,8,0"
                     TextTrimming="CharacterEllipsis"/>
          <StackPanel Grid.Column="2" Orientation="Horizontal"
                      VerticalAlignment="Center" Margin="0,0,8,0">
            <TextBlock Text="{Binding ViewName}" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="12"
                       TextTrimming="CharacterEllipsis"/>
            <TextBlock Text="{Binding MatchTag}" Foreground="#7EB4F0"
                       FontFamily="Segoe UI" FontSize="11" FontStyle="Italic"
                       Margin="6,0,0,0"/>
          </StackPanel>
          <TextBlock Grid.Column="3" Text="{Binding Detail}" Foreground="#6B7394"
                     FontFamily="Segoe UI" FontSize="12"
                     VerticalAlignment="Center"
                     TextTrimming="CharacterEllipsis"/>
        </Grid>
      </Border>
    </DataTemplate>

  </Grid.Resources>

  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="*"/>
  </Grid.RowDefinitions>

  <!-- Badge row -->
  <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,16">
    <Border x:Name="badgeMatchedBorder" Background="#1A2535" BorderBrush="#5B8EC4"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeMatched" Foreground="#5B8EC4"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeOkBorder" Background="#122E1C" BorderBrush="#50E898"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeOk" Foreground="#50E898"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeScaleBorder" Background="#2A1C0E" BorderBrush="#E87E20"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeScale" Foreground="#E87E20"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeTypeBorder" Background="#2A1C0E" BorderBrush="#E87E20"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeType" Foreground="#E87E20"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeOrphanBorder" Background="#1A1D30" BorderBrush="#9099C8"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeOrphan" Foreground="#9099C8"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgePlacedBorder" Background="#3C1212" BorderBrush="#FF7070"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgePlaced" Foreground="#FF7070"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
  </StackPanel>

  <!-- Scroll body with all Expanders -->
  <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto">
    <StackPanel>

      <TextBlock x:Name="lblAllClear" FontFamily="Segoe UI" FontSize="14"
                 Foreground="#50E898" TextWrapping="Wrap"
                 Margin="4,12,0,0" Visibility="Collapsed">
        ✅  All matched views are in sync with the JSON. No orphans, no scale or type mismatches.
      </TextBlock>

      <Expander x:Name="expScale" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#2A1C0E" BorderBrush="#E87E20" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrScaleCount" Foreground="#E87E20"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="📐  Scale Mismatches" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13"
                       VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icScale" ItemTemplate="{StaticResource rowTemplate}"/>
      </Expander>

      <Expander x:Name="expType" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#2A1C0E" BorderBrush="#E87E20" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrTypeCount" Foreground="#E87E20"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="🔄  View Type Mismatches" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13"
                       VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icType" ItemTemplate="{StaticResource rowTemplate}"/>
      </Expander>

      <Expander x:Name="expOrphan" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#1A1D30" BorderBrush="#9099C8" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrOrphanCount" Foreground="#9099C8"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="👻  Orphans (not in JSON, not on a sheet)" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13"
                       VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icOrphan" ItemTemplate="{StaticResource rowTemplate}"/>
      </Expander>

      <Expander x:Name="expPlaced" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#3C1212" BorderBrush="#FF7070" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrPlacedCount" Foreground="#FF7070"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="📌  Placed on Sheets but Not in JSON" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13"
                       VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icPlaced" ItemTemplate="{StaticResource rowTemplate}"/>
      </Expander>

    </StackPanel>
  </ScrollViewer>
</Grid>
"""

_FOOTER_XAML = u"""
<Grid>
  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="Auto"/>
  </Grid.RowDefinitions>

  <Grid Grid.Row="0">
    <StackPanel HorizontalAlignment="Left" Orientation="Horizontal">
      <Button x:Name="btnCopy" Content="Copy to clipboard" Style="{StaticResource BtnGhost}"/>
    </StackPanel>
    <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
      <!-- Danger buttons: defined inline since BtnDanger doesn't exist in framework -->
      <Button x:Name="btnDelScale" Content="Delete scale mismatches"
              Foreground="#FFD0D0" Cursor="Hand" Padding="14,7"
              Background="#5A2020" BorderBrush="#FF7070" BorderThickness="1"
              Margin="0,0,8,0" Visibility="Collapsed"/>
      <Button x:Name="btnDelType" Content="Delete type mismatches"
              Foreground="#FFD0D0" Cursor="Hand" Padding="14,7"
              Background="#5A2020" BorderBrush="#FF7070" BorderThickness="1"
              Margin="0,0,8,0" Visibility="Collapsed"/>
      <Button x:Name="btnDelOrphan" Content="Delete orphans"
              Foreground="#FFD0D0" Cursor="Hand" Padding="14,7"
              Background="#5A2020" BorderBrush="#FF7070" BorderThickness="1"
              Margin="0,0,8,0" Visibility="Collapsed"/>
      <Button x:Name="btnDelPlaced" Content="Delete placed views"
              Foreground="#FFD0D0" Cursor="Hand" Padding="14,7"
              Background="#5A2020" BorderBrush="#FF7070" BorderThickness="1"
              Margin="0,0,8,0" Visibility="Collapsed"
              ToolTip="Warning: also removes these views from their sheets"/>
      <Button x:Name="btnClose" Content="Close" Style="{StaticResource BtnPrimary}"/>
    </StackPanel>
  </Grid>

  <TextBlock x:Name="lblStatus" Grid.Row="1" Foreground="#FF7070"
             FontFamily="Segoe UI" FontSize="11"
             Margin="0,10,0,0" TextWrapping="Wrap" Visibility="Collapsed"/>
</Grid>
"""

# ─────────────────────────────────────────────────────────────────────────────
# 8. Build subtitle + context, instantiate window
# ─────────────────────────────────────────────────────────────────────────────

subtitle = u"{} master view(s) audited  ·  {} dependent view(s) in scope  ·  {}".format(
    len(chosen_masters_set), len(json_view_names), _os.path.basename(json_path))

warnings = []
if not has_scale_data:
    warnings.append(u"No view_scale in JSON — scale comparison skipped.")
if not has_type_data:
    warnings.append(u"No view_type in JSON — type comparison skipped.")
if warnings:
    subtitle += u"  ·  ⚠ " + u" ".join(warnings)

win = ui.parse(
    u"Audit Destination Views",
    subtitle,
    _BODY_XAML,
    _FOOTER_XAML,
    width=1020,
    height=640,
    context=u"All findings below come from comparing every dependent view in this "
            u"model against the selected master views in the JSON. Matching uses "
            u"Detail ID first (rows tagged [by ID]) and view name as fallback. "
            u"Use the buttons in the footer to delete each category; rows with a "
            u"red left border are placed on a sheet — deleting them also removes "
            u"them from that sheet."
)

# Wire badges
win.FindName("badgeMatched").Text = u"📋  {} matched ok".format(matched_ok)

if n_total_issues == 0:
    win.FindName("badgeOkBorder").Visibility = Visibility.Visible
    win.FindName("badgeOk").Text = u"✅  all in sync"
    win.FindName("lblAllClear").Visibility = Visibility.Visible

if n_scale > 0:
    win.FindName("badgeScaleBorder").Visibility = Visibility.Visible
    win.FindName("badgeScale").Text = u"📐  {} scale".format(n_scale)
    win.FindName("expScale").Visibility = Visibility.Visible
    win.FindName("hdrScaleCount").Text = u" {} ".format(n_scale)
    win.FindName("icScale").ItemsSource = scale_oc
    btn = win.FindName("btnDelScale")
    btn.Content = u"Delete {} scale mismatch{}".format(
        n_scale, u"es" if n_scale != 1 else u"")
    btn.Visibility = Visibility.Visible

if n_type > 0:
    win.FindName("badgeTypeBorder").Visibility = Visibility.Visible
    win.FindName("badgeType").Text = u"🔄  {} type".format(n_type)
    win.FindName("expType").Visibility = Visibility.Visible
    win.FindName("hdrTypeCount").Text = u" {} ".format(n_type)
    win.FindName("icType").ItemsSource = type_oc
    btn = win.FindName("btnDelType")
    btn.Content = u"Delete {} type mismatch{}".format(
        n_type, u"es" if n_type != 1 else u"")
    btn.Visibility = Visibility.Visible

if n_orphan > 0:
    win.FindName("badgeOrphanBorder").Visibility = Visibility.Visible
    win.FindName("badgeOrphan").Text = u"👻  {} orphan".format(n_orphan)
    win.FindName("expOrphan").Visibility = Visibility.Visible
    win.FindName("hdrOrphanCount").Text = u" {} ".format(n_orphan)
    win.FindName("icOrphan").ItemsSource = orphan_oc
    btn = win.FindName("btnDelOrphan")
    btn.Content = u"Delete {} orphan{}".format(
        n_orphan, u"s" if n_orphan != 1 else u"")
    btn.Visibility = Visibility.Visible

if n_placed > 0:
    win.FindName("badgePlacedBorder").Visibility = Visibility.Visible
    win.FindName("badgePlaced").Text = u"📌  {} placed".format(n_placed)
    win.FindName("expPlaced").Visibility = Visibility.Visible
    win.FindName("hdrPlacedCount").Text = u" {} ".format(n_placed)
    win.FindName("icPlaced").ItemsSource = placed_oc
    btn = win.FindName("btnDelPlaced")
    btn.Content = u"Delete {} placed view{}".format(
        n_placed, u"s" if n_placed != 1 else u"")
    btn.Visibility = Visibility.Visible

# ─────────────────────────────────────────────────────────────────────────────
# 9. Wire action handlers
# ─────────────────────────────────────────────────────────────────────────────

def show_status(msg):
    lbl = win.FindName("lblStatus")
    lbl.Text = msg
    lbl.Visibility = Visibility.Visible


def make_delete_handler(oc, badge_border_name, badge_text_name, badge_icon,
                        badge_label, exp_name, button, txn_name, kind_label):
    def handler(s, e):
        rows = list(oc)
        if not rows:
            return
        deleted, failed, errors = robust_delete(doc, rows, txn_name)
        survivors = []
        for r in rows:
            try:
                if r._view is not None and r._view.IsValidObject:
                    survivors.append(r)
            except Exception:
                pass
        oc.Clear()
        for r in survivors:
            oc.Add(r)
        new_count = len(survivors)
        badge_border = win.FindName(badge_border_name)
        badge_text   = win.FindName(badge_text_name)
        exp          = win.FindName(exp_name)
        if new_count == 0:
            badge_border.Visibility = Visibility.Collapsed
            exp.Visibility = Visibility.Collapsed
        else:
            badge_text.Text = u"{}  {} {}".format(badge_icon, new_count, badge_label)
        button.Content = u"✓ {} deleted".format(deleted)
        button.IsEnabled = False
        if failed:
            head  = u"❌ {} {} could not be deleted — ".format(failed, kind_label)
            shown = u"   ".join(errors[:3])
            if len(errors) > 3:
                shown += u"   …+{} more".format(len(errors) - 3)
            show_status(head + shown)
    return handler


btn_del_scale  = win.FindName("btnDelScale")
btn_del_type   = win.FindName("btnDelType")
btn_del_orphan = win.FindName("btnDelOrphan")
btn_del_placed = win.FindName("btnDelPlaced")

btn_del_scale.Click += make_delete_handler(
    scale_oc, "badgeScaleBorder", "badgeScale", u"📐", u"scale",
    "expScale", btn_del_scale,
    "Delete Scale-Mismatch Dependent Views", u"scale-mismatch view(s)")

btn_del_type.Click += make_delete_handler(
    type_oc, "badgeTypeBorder", "badgeType", u"🔄", u"type",
    "expType", btn_del_type,
    "Delete View-Type-Mismatch Dependent Views", u"type-mismatch view(s)")

btn_del_orphan.Click += make_delete_handler(
    orphan_oc, "badgeOrphanBorder", "badgeOrphan", u"👻", u"orphan",
    "expOrphan", btn_del_orphan,
    "Delete Orphaned Dependent Views", u"orphan view(s)")

btn_del_placed.Click += make_delete_handler(
    placed_oc, "badgePlacedBorder", "badgePlaced", u"📌", u"placed",
    "expPlaced", btn_del_placed,
    "Delete Placed Dependent Views Not in JSON", u"placed view(s)")


def on_copy(s, e):
    lines = [u"Audit Destination Views — " + subtitle, u""]
    lines.append(u"📋  {} matched ok".format(matched_ok))
    lines.append(u"")

    def dump(label, rows):
        if not rows:
            return
        lines.append(u"── {} ({}) ──".format(label, len(rows)))
        # group by master
        by_master = {}
        for r in rows:
            by_master.setdefault(r.Master, []).append(r)
        for master in sorted(by_master.keys()):
            lines.append(u"  {}".format(master))
            for r in by_master[master]:
                tag = r.MatchTag
                lines.append(u"    {}  {}{}  —  {}".format(
                    r.Icon, r.ViewName, tag, r.Detail))
        lines.append(u"")

    dump(u"Scale mismatches",      list(scale_oc))
    dump(u"View type mismatches",  list(type_oc))
    dump(u"Orphans",               list(orphan_oc))
    dump(u"Placed but not in JSON", list(placed_oc))

    text = u"\n".join(lines)
    try:
        WinFormsClipboard.SetText(text)
    except Exception:
        pass
    btn = win.FindName("btnCopy")
    btn.Content = u"Copied ✓"


win.FindName("btnCopy").Click  += on_copy
win.FindName("btnClose").Click += lambda s, e: win.Close()

win.ShowDialog()
