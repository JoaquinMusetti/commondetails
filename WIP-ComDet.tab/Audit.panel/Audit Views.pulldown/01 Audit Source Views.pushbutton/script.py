# -*- coding: utf-8 -*-
__title__ = 'Audit\nSource Views'
__doc__ = ('Compares a Sheets with Views JSON against the current model. '
           'Reports views in JSON no longer in the model, new views not yet '
           'exported, and detects possible renames by matching Title on Sheet. '
           'Generates renames.json if confirmed. Reads the combined JSON '
           'produced by "Export Sheets with Views" (format: sheets_with_views).')

import json
import sys as _sys
import os as _os
from pyrevit import revit, DB, script, forms

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
_sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

import clr
clr.AddReference('PresentationCore')
clr.AddReference('System.Windows.Forms')
from System.Windows import Visibility
from System.Windows.Forms import (Clipboard as WinFormsClipboard,
                                   SaveFileDialog, DialogResult)
from System.Collections.ObjectModel import ObservableCollection

doc = revit.doc

# ─────────────────────────────────────────────────────────────────────────────
# Row classes — PascalCase properties for WPF binding
# ─────────────────────────────────────────────────────────────────────────────

class _ViewRow(object):
    """Generic row: Icon | Col1 (master/context) | Col2 (view name) | Detail"""
    def __init__(self, icon, col1, col2, detail=u""):
        self._icon   = icon
        self._col1   = col1
        self._col2   = col2
        self._detail = detail
    @property
    def Icon(self):   return self._icon
    @property
    def Col1(self):   return self._col1
    @property
    def Col2(self):   return self._col2
    @property
    def Detail(self): return self._detail


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick JSON file
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(
    file_ext="json",
    title="Select Sheets with Views JSON"
)
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    data = json.load(f)

if data.get("format") != "sheets_with_views":
    ui.alert(
        u"Wrong file format.\n\n"
        u"Audit Source Views expects a JSON exported by 'Export Sheets with "
        u"Views' (format: sheets_with_views). The selected file has format: "
        u"\"{}\".\n\nFor the legacy details_geometry.json format, use the "
        u"matching tool in the Legacy pulldown.".format(
            data.get("format", "unknown")),
        title=u"Audit Source Views"
    )
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 2. Index current dependent views in model
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

master_by_name = {v.Name: v for v in master_views}

model_deps_by_master = {}
for master in master_views:
    dep_names = []
    for vid in master.GetDependentViewIds():
        dep = doc.GetElement(vid)
        if dep:
            try:
                dep_names.append(dep.Name)
            except Exception:
                pass
    model_deps_by_master[master.Name] = dep_names


def get_title_on_sheet(view):
    try:
        p = view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
        if p:
            return p.AsString() or u""
    except Exception:
        pass
    return u""


dep_view_by_name = {}
for v in all_views:
    try:
        if v.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
            dep_view_by_name[v.Name] = v
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# 3. Compare JSON vs model → populate ObservableCollections
# ─────────────────────────────────────────────────────────────────────────────

missing_oc = ObservableCollection[object]()
new_oc     = ObservableCollection[object]()

total_missing  = 0
total_new      = 0
master_missing = []
all_missing    = {}  # view_name → title (used for rename detection)
all_new        = {}  # view_name → title

for view_data in data["master_views"]:
    master_name = view_data["view_name"]
    json_views  = {dv["view_name"]: dv for dv in view_data["dependent_views"]}

    if master_name not in master_by_name:
        master_missing.append(master_name)
        missing_oc.Add(_ViewRow(u"❌", u"—", master_name, u"master view not found"))
        continue

    model_names = model_deps_by_master.get(master_name, [])
    model_set   = set(model_names)
    json_set    = set(json_views.keys())

    missing = [n for n in json_views if n not in model_set]
    new     = [n for n in model_names if n not in json_set]

    total_missing += len(missing)
    total_new     += len(new)

    for name in missing:
        title = json_views[name].get("title_on_sheet", u"") or u""
        all_missing[name] = title
        missing_oc.Add(_ViewRow(u"🔴", master_name, name, title))

    for name in new:
        if name in dep_view_by_name:
            title = get_title_on_sheet(dep_view_by_name[name])
        else:
            title = u""
        all_new[name] = title
        new_oc.Add(_ViewRow(u"➕", master_name, name, title))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Detect renames by matching Title on Sheet
# ─────────────────────────────────────────────────────────────────────────────

title_to_missing = {}
for name, title in all_missing.items():
    if title:
        title_to_missing.setdefault(title, []).append(name)

title_to_new = {}
for name, title in all_new.items():
    if title:
        title_to_new.setdefault(title, []).append(name)

rename_suggestions = []
rename_oc = ObservableCollection[object]()

for title, missing_names in title_to_missing.items():
    if title in title_to_new:
        new_names = title_to_new[title]
        if len(missing_names) == 1 and len(new_names) == 1:
            rename_suggestions.append({
                "old_name":       missing_names[0],
                "new_name":       new_names[0],
                "title_on_sheet": title
            })
            # Col1=old_name (orange), Col2=new_name (green) via renameRowTpl
            rename_oc.Add(_ViewRow(u"🔄", missing_names[0], new_names[0], title))

n_missing = missing_oc.Count
n_new     = new_oc.Count
n_rename  = rename_oc.Count
n_issues  = total_missing + total_new + len(master_missing)

subtitle = u"{} master view(s) checked  ·  {}".format(
    len(data["master_views"]), _os.path.basename(json_path))

# ─────────────────────────────────────────────────────────────────────────────
# 5. Build WPF window
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

    <!-- Standard row template: Icon | Master/context | View name | Title -->
    <DataTemplate x:Key="rowTpl">
      <Border BorderThickness="0,0,0,1" Padding="14,0,16,0">
        <Border.Style>
          <Style TargetType="Border">
            <Setter Property="Background"  Value="#12131F"/>
            <Setter Property="BorderBrush" Value="#2A2D47"/>
          </Style>
        </Border.Style>
        <Grid MinHeight="28">
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="22"/>
            <ColumnDefinition Width="170"/>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="220"/>
          </Grid.ColumnDefinitions>
          <TextBlock Grid.Column="0" Text="{Binding Icon}" FontSize="12"
                     VerticalAlignment="Center"/>
          <TextBlock Grid.Column="1" Text="{Binding Col1}" Foreground="#9099C8"
                     FontFamily="Segoe UI" FontSize="12"
                     VerticalAlignment="Center" Margin="0,0,8,0"
                     TextTrimming="CharacterEllipsis"/>
          <TextBlock Grid.Column="2" Text="{Binding Col2}" Foreground="#E8EBF5"
                     FontFamily="Segoe UI" FontSize="12"
                     VerticalAlignment="Center" Margin="0,0,8,0"
                     TextTrimming="CharacterEllipsis"/>
          <TextBlock Grid.Column="3" Text="{Binding Detail}" Foreground="#6B7394"
                     FontFamily="Segoe UI" FontSize="12"
                     VerticalAlignment="Center"
                     TextTrimming="CharacterEllipsis"/>
        </Grid>
      </Border>
    </DataTemplate>

    <!-- Rename row template: Icon | OldName (orange) | → | NewName (green) | Title -->
    <DataTemplate x:Key="renameRowTpl">
      <Border BorderThickness="0,0,0,1" Padding="14,0,16,0">
        <Border.Style>
          <Style TargetType="Border">
            <Setter Property="Background"  Value="#12131F"/>
            <Setter Property="BorderBrush" Value="#2A2D47"/>
          </Style>
        </Border.Style>
        <Grid MinHeight="28">
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="22"/>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="24"/>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="200"/>
          </Grid.ColumnDefinitions>
          <TextBlock Grid.Column="0" Text="&#x1F504;" FontSize="12"
                     VerticalAlignment="Center"/>
          <TextBlock Grid.Column="1" Text="{Binding Col1}" Foreground="#E87E20"
                     FontFamily="Segoe UI" FontSize="12"
                     VerticalAlignment="Center" Margin="0,0,4,0"
                     TextTrimming="CharacterEllipsis"/>
          <TextBlock Grid.Column="2" Text="&#x2192;" Foreground="#9099C8"
                     FontFamily="Segoe UI" FontSize="14"
                     VerticalAlignment="Center" HorizontalAlignment="Center"/>
          <TextBlock Grid.Column="3" Text="{Binding Col2}" Foreground="#50E898"
                     FontFamily="Segoe UI" FontSize="12"
                     VerticalAlignment="Center" Margin="4,0,8,0"
                     TextTrimming="CharacterEllipsis"/>
          <TextBlock Grid.Column="4" Text="{Binding Detail}" Foreground="#6B7394"
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
    <Border x:Name="badgeCheckedBorder" Background="#1A2535" BorderBrush="#5B8EC4"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeChecked" Foreground="#5B8EC4"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeOkBorder" Background="#122E1C" BorderBrush="#50E898"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeOk" Foreground="#50E898"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeMissingBorder" Background="#2A1C0E" BorderBrush="#E87E20"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeMissing" Foreground="#E87E20"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeNewBorder" Background="#122E1C" BorderBrush="#50E898"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeNew" Foreground="#50E898"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeRenameBorder" Background="#1A1D30" BorderBrush="#9099C8"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeRename" Foreground="#9099C8"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
  </StackPanel>

  <!-- Scrollable body -->
  <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto">
    <StackPanel>

      <TextBlock x:Name="lblAllClear" FontFamily="Segoe UI" FontSize="14"
                 Foreground="#50E898" TextWrapping="Wrap"
                 Margin="4,12,0,0" Visibility="Collapsed">
        &#x2705;  All views in sync. The JSON matches the model perfectly. Safe to proceed with Import Sheets with Views.
      </TextBlock>

      <!-- Missing from model -->
      <Expander x:Name="expMissing" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#2A1C0E" BorderBrush="#E87E20" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrMissingCount" Foreground="#E87E20"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x1F534;  In JSON, missing from model" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icMissing" ItemTemplate="{StaticResource rowTpl}"/>
      </Expander>

      <!-- New in model, not in JSON -->
      <Expander x:Name="expNew" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#122E1C" BorderBrush="#50E898" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrNewCount" Foreground="#50E898"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x2795;  In model, not yet exported" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icNew" ItemTemplate="{StaticResource rowTpl}"/>
      </Expander>

      <!-- Possible renames -->
      <Expander x:Name="expRename" IsExpanded="True" Visibility="Collapsed" Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#1A1D30" BorderBrush="#9099C8" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrRenameCount" Foreground="#9099C8"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x1F504;  Possible renames (matched by Title on Sheet)" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icRename" ItemTemplate="{StaticResource renameRowTpl}"/>
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
      <Button x:Name="btnGenRenames" Content="Generate renames.json"
              Foreground="#FFD0D0" Cursor="Hand" Padding="14,7"
              Background="#5A2020" BorderBrush="#FF7070" BorderThickness="1"
              Margin="0,0,8,0" Visibility="Collapsed"/>
      <Button x:Name="btnClose" Content="Close" Style="{StaticResource BtnPrimary}"/>
    </StackPanel>
  </Grid>

  <TextBlock x:Name="lblStatus" Grid.Row="1" Foreground="#FF7070"
             FontFamily="Segoe UI" FontSize="11"
             Margin="0,10,0,0" TextWrapping="Wrap" Visibility="Collapsed"/>
</Grid>
"""

win = ui.parse(
    u"Audit Source Views",
    subtitle,
    _BODY_XAML,
    _FOOTER_XAML,
    width=1020,
    height=620,
    context=u"Compares the master_views section of the JSON against this model's "
            u"dependent views. Missing = in JSON but deleted or renamed in model. "
            u"New = in model but not yet exported. Renames are detected when a "
            u"missing view and a new view share the same Title on Sheet — confirm "
            u"them to generate renames.json."
)

# ─── Wire badges and sections ─────────────────────────────────────────────────

win.FindName("badgeChecked").Text = u"📋  {} master view(s)".format(
    len(data["master_views"]))

if n_issues == 0 and n_rename == 0:
    win.FindName("badgeOkBorder").Visibility = Visibility.Visible
    win.FindName("badgeOk").Text = u"✅  all in sync"
    win.FindName("lblAllClear").Visibility = Visibility.Visible
else:
    if n_missing > 0:
        win.FindName("badgeMissingBorder").Visibility = Visibility.Visible
        win.FindName("badgeMissing").Text = u"🔴  {} missing".format(n_missing)
        win.FindName("expMissing").Visibility = Visibility.Visible
        win.FindName("hdrMissingCount").Text = u" {} ".format(n_missing)
        win.FindName("icMissing").ItemsSource = missing_oc

    if n_new > 0:
        win.FindName("badgeNewBorder").Visibility = Visibility.Visible
        win.FindName("badgeNew").Text = u"➕  {} new".format(n_new)
        win.FindName("expNew").Visibility = Visibility.Visible
        win.FindName("hdrNewCount").Text = u" {} ".format(n_new)
        win.FindName("icNew").ItemsSource = new_oc

    if n_rename > 0:
        win.FindName("badgeRenameBorder").Visibility = Visibility.Visible
        win.FindName("badgeRename").Text = u"🔄  {} rename(s)".format(n_rename)
        win.FindName("expRename").Visibility = Visibility.Visible
        win.FindName("hdrRenameCount").Text = u" {} ".format(n_rename)
        win.FindName("icRename").ItemsSource = rename_oc
        win.FindName("btnGenRenames").Visibility = Visibility.Visible

# ─── Action handlers ──────────────────────────────────────────────────────────

def on_gen_renames(s, e):
    dlg = SaveFileDialog()
    dlg.Title  = "Save renames.json"
    dlg.Filter = "JSON files (*.json)|*.json"
    dlg.FileName = "renames.json"
    if dlg.ShowDialog() == DialogResult.OK:
        with open(dlg.FileName, "w") as fh:
            json.dump(rename_suggestions, fh, indent=2)
        btn = win.FindName("btnGenRenames")
        btn.Content   = u"✓ Saved"
        btn.IsEnabled = False


def on_copy(s, e):
    lines = [u"Audit Source Views — " + subtitle, u""]
    if n_issues == 0 and n_rename == 0:
        lines.append(u"✅  All views in sync.")
    else:
        if missing_oc.Count > 0:
            lines.append(u"── In JSON, missing from model ({}) ──".format(
                missing_oc.Count))
            by_master = {}
            for r in missing_oc:
                by_master.setdefault(r.Col1, []).append(r)
            for master in sorted(by_master.keys()):
                lines.append(u"  {}".format(master))
                for r in by_master[master]:
                    suffix = u"  (title: {})".format(r.Detail) if r.Detail else u""
                    lines.append(u"    🔴  {}{}".format(r.Col2, suffix))
            lines.append(u"")
        if new_oc.Count > 0:
            lines.append(u"── In model, not yet exported ({}) ──".format(
                new_oc.Count))
            by_master = {}
            for r in new_oc:
                by_master.setdefault(r.Col1, []).append(r)
            for master in sorted(by_master.keys()):
                lines.append(u"  {}".format(master))
                for r in by_master[master]:
                    suffix = u"  (title: {})".format(r.Detail) if r.Detail else u""
                    lines.append(u"    ➕  {}{}".format(r.Col2, suffix))
            lines.append(u"")
        if rename_oc.Count > 0:
            lines.append(u"── Possible renames ({}) ──".format(rename_oc.Count))
            for r in rename_oc:
                lines.append(u"  🔄  {}  →  {}  (title: {})".format(
                    r.Col1, r.Col2, r.Detail))
    text = u"\n".join(lines)
    try:
        WinFormsClipboard.SetText(text)
    except Exception:
        pass
    btn = win.FindName("btnCopy")
    btn.Content = u"Copied ✓"


win.FindName("btnGenRenames").Click += on_gen_renames
win.FindName("btnCopy").Click       += on_copy
win.FindName("btnClose").Click      += lambda s, e: win.Close()

win.ShowDialog()
