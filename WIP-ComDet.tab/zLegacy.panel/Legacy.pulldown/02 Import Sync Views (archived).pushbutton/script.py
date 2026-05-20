# -*- coding: utf-8 -*-
__title__ = 'Import/Sync Views\n(archived)'
__doc__ = ('Reads details_geometry.json and creates or updates dependent views in the destination model. '
           'Applies crop boundaries from the exported JSON using the linked model offset. '
           'When importing into the Common Details file itself, choose "None (I\'m importing views into the Common Details file)" '
           'as the linked model. '
           'Choose to skip or update existing dependent views.')

import json
import sys
import os as _os
from pyrevit import revit, DB, script, forms, HOST_APP
from Autodesk.Revit.DB import CurveLoop, Line, ViewDuplicateOption, BuiltInParameterGroup, BuiltInCategory

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

# ─────────────────────────────────────────────────────────────────────────────
# Legacy guard — confirm before running. This tool was moved to the Legacy
# pulldown on 2026-05-19 because the combined "Sheets with Views" flow now
# covers its workflow. Kept here for backwards-compat with old JSONs.
# ─────────────────────────────────────────────────────────────────────────────
if not ui.confirm(
    u"This tool is part of the LEGACY workflow.\n\n"
    u"The current workflow uses 'Export Sheets with Views' + 'Import "
    u"Sheets with Views' (and their PRO variant) from the Export & Import "
    u"panel. The matching new Audit tools live under Audit > Audit Views / "
    u"Audit Sheets pulldowns.\n\n"
    u"Continue with the legacy tool anyway?",
    title=u"Legacy Tool",
    yes_text=u"Continue (legacy)",
    context=u"Common Details migrated to a combined JSON flow on 2026-05-19. "
            u"The legacy tools stay here in case you need to interop with old "
            u"JSONs or a feature not yet covered in the new flow."
):
    script.exit()

doc    = revit.doc
output = script.get_output()
output.close()

NONE_OPTION = "None (I'm importing views into the Common Details file)"
_SPF_PATH   = _os.path.join(_ext_dir, 'lib', 'cucosync_shared_params.txt')

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

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

def set_detail_id(view, value):
    try:
        p = view.LookupParameter("Detail ID")
        if p and not p.IsReadOnly:
            p.Set(value)
    except Exception:
        pass

def link_to_world(pt_list, link_transform):
    pt = DB.XYZ(pt_list[0], pt_list[1], pt_list[2])
    if link_transform is None:
        return pt
    return link_transform.OfPoint(pt)

def make_crop_loop(corners_world):
    loop = CurveLoop()
    for k in range(4):
        loop.Append(Line.CreateBound(
            corners_world[k],
            corners_world[(k + 1) % 4]
        ))
    return loop

# ─────────────────────────────────────────────────────────────────────────────
# 1. Select JSON
# ─────────────────────────────────────────────────────────────────────────────

json_path = forms.pick_file(
    file_ext="json",
    title="Select details_geometry.json"
)
if not json_path:
    script.exit()

with open(json_path, "r") as f:
    data = json.load(f)

# ─────────────────────────────────────────────────────────────────────────────
# 1b. Select linked model (needed before master_map to know import direction)
# ─────────────────────────────────────────────────────────────────────────────

link_instances = DB.FilteredElementCollector(doc)\
    .OfClass(DB.RevitLinkInstance)\
    .ToElements()

link_by_name = {li.Name: li for li in link_instances}
link_options = [NONE_OPTION] + sorted(link_by_name.keys())

chosen_link = ui.pick_list(
    link_options,
    "Reference Linked Model",
    button_name="Select",
    multiselect=False,
    context=u"Pick the Common Details link if you're importing into a BUILDING "
            u"(JSON coordinates get transformed into building space using the link's "
            u"transform). Pick 'None' ONLY if you're importing inside the Common "
            u"Details file itself (no link — coordinates are used as-is)."
)
if not chosen_link:
    script.exit()

if chosen_link == NONE_OPTION:
    link_transform = None
else:
    link_transform = link_by_name[chosen_link].GetTotalTransform()

# ─────────────────────────────────────────────────────────────────────────────
# 1c. Apply master_map.json if present alongside the selected JSON
#     Forward  (CD name → building name): when importing INTO a building (link chosen)
#     Reversed (building name → CD name): when importing INTO CD (None chosen)
# ─────────────────────────────────────────────────────────────────────────────

map_path = _os.path.join(_os.path.dirname(json_path), "master_map.json")
master_map_info = u""
if _os.path.isfile(map_path):
    with open(map_path, "r") as f:
        raw_map = json.load(f)
    # Decide direction based on link choice
    if link_transform is None:
        # Importing into CD → reverse the map (building name → CD name)
        master_map = {v: k for k, v in raw_map.items()}
        map_dir = u"reversed"
    else:
        # Importing into a building → forward map (CD name → building name)
        master_map = raw_map
        map_dir = u"forward"
    remapped = 0
    for mv in data["master_views"]:
        if mv["view_name"] in master_map:
            mv["view_name"] = master_map[mv["view_name"]]
            remapped += 1
    master_map_info = u"master_map.json: {} remapped ({})".format(remapped, map_dir)

# ─────────────────────────────────────────────────────────────────────────────
# 1d. Select which master views to import
# ─────────────────────────────────────────────────────────────────────────────

master_options = [
    "{} ({} views)".format(mv["view_name"], len(mv["dependent_views"]))
    for mv in data["master_views"]
]
chosen_masters = ui.pick_list(
    master_options,
    "Select Master Views to Import",
    subtitle="All selected masters will be imported:",
    multiselect=True,
    context=u"Tick which masters from the JSON to bring over. The dependent views "
            u"under each master will be created (or updated, depending on the next "
            u"strategy step) under the matching master in the destination model."
)
if not chosen_masters:
    script.exit()

chosen_set = {opt.split(" (")[0] for opt in chosen_masters}
data["master_views"] = [mv for mv in data["master_views"] if mv["view_name"] in chosen_set]

# ─────────────────────────────────────────────────────────────────────────────
# 2. Strategy for existing dependent views
# ─────────────────────────────────────────────────────────────────────────────

strategy = ui.pick_list(
    [
        "Skip existing — do not touch dependent views that already exist",
        "Update existing — re-apply crop boundary to views that already exist",
    ],
    "Strategy for Existing Dependent Views",
    button_name="Next",
    multiselect=False,
    context=u"If a dependent already exists in the destination: 'Skip' leaves it "
            u"untouched (crop and properties unchanged). 'Update' overwrites the crop "
            u"geometry and metadata with what's in the JSON. Detail ID and name are "
            u"preserved in both cases."
)
if not strategy:
    script.exit()

update_existing = "Update" in strategy

# ─────────────────────────────────────────────────────────────────────────────
# 4. Index destination model views
# ─────────────────────────────────────────────────────────────────────────────

all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()
view_by_name = {}
for v in all_views:
    try:
        view_by_name[v.Name] = v
    except Exception:
        pass

# View templates
template_by_name = {}
for v in all_views:
    try:
        if v.IsTemplate:
            template_by_name[v.Name] = v.Id
    except Exception:
        pass

# Existing names in the project
existing_proj_names = set(view_by_name.keys())

# ─────────────────────────────────────────────────────────────────────────────
# 4b. Ensure Detail ID parameter exists in destination model
# ─────────────────────────────────────────────────────────────────────────────

with revit.Transaction("Add Detail ID parameter"):
    ensure_detail_id_param(doc, HOST_APP.app)

# ─────────────────────────────────────────────────────────────────────────────
# 5. Process each master view
# ─────────────────────────────────────────────────────────────────────────────

total_created    = 0
total_updated    = 0
total_skipped    = 0
total_id_stamped = 0

# Results buffer — all output.print_md calls happen AFTER the transaction
# to avoid pumping the Windows message queue (which triggers Revit UI
# regeneration indicators on every SetCropShape call inside the transaction).
results = []  # list of (status, master_name, view_name, detail_str)

total_views = sum(len(mv["dependent_views"]) for mv in data["master_views"])
processed   = 0

cancelled = False
with ui.ProgressBar(title=u"Import/Sync Views", cancellable=True, step=5) as pb:
 pb.update_progress(0, total_views)

 with revit.Transaction("Import Geometry — Dependent Views"):
    for view_data in data["master_views"]:
        view_name = view_data["view_name"]

        if view_name not in view_by_name:
            results.append(("skip_master", view_name, "", ""))
            continue

        master_view = view_by_name[view_name]
        results.append(("master", view_name, "", ""))

        # Index existing dependent views by Detail ID and by name
        existing_by_id   = {}
        existing_by_name = {}
        for vid in master_view.GetDependentViewIds():
            dep = doc.GetElement(vid)
            if dep:
                existing_by_name[dep.Name] = dep
                did = get_detail_id(dep)
                if did:
                    existing_by_id[did] = dep

        # pending_crops accumulates (view, crop_loop) for all newly created views.
        # SetCropShape is deferred to a second pass so that all Duplicate() calls
        # complete before Revit processes any crop regenerations.
        pending_crops = []

        for dv_data in view_data["dependent_views"]:
            if pb.cancelled:
                cancelled = True
                break
            dv_name        = dv_data["view_name"]
            dv_id          = dv_data.get("detail_id", "")
            title_on_sheet = dv_data.get("title_on_sheet", "")
            template_name  = dv_data.get("view_template", "")
            scale          = dv_data.get("view_scale", master_view.Scale)

            corners_world = [
                link_to_world(c, link_transform)
                for c in dv_data["crop_corners"]
            ]

            # Match by Detail ID first, fall back to name
            existing_view = existing_by_id.get(dv_id) if dv_id else None
            if existing_view is None:
                existing_view = existing_by_name.get(dv_name)

            processed += 1
            pb.title = u"Import/Sync Views — {}/{} — {}".format(processed, total_views, view_name)
            pb.update_progress(processed, total_views)

            # ── Already exists ──
            if existing_view is not None:
                # Transition: stamp Detail ID if missing
                if dv_id and not get_detail_id(existing_view):
                    set_detail_id(existing_view, dv_id)
                    total_id_stamped += 1

                if update_existing:
                    try:
                        crop_loop = make_crop_loop(corners_world)
                        existing_view.CropBoxActive = True
                        existing_view.GetCropRegionShapeManager().SetCropShape(crop_loop)
                        if title_on_sheet:
                            try:
                                p = existing_view.get_Parameter(
                                    DB.BuiltInParameter.VIEW_DESCRIPTION)
                                if p and not p.IsReadOnly:
                                    p.Set(title_on_sheet)
                            except Exception:
                                pass
                        results.append(("updated", view_name, existing_view.Name, ""))
                        total_updated += 1
                    except Exception as e:
                        results.append(("error", view_name, dv_name, str(e)))
                else:
                    results.append(("skipped", view_name, existing_view.Name, ""))
                    total_skipped += 1
                continue

            # ── Create new — Pass 1: Duplicate + metadata only ──
            try:
                new_vid  = master_view.Duplicate(ViewDuplicateOption.AsDependent)
                new_view = doc.GetElement(new_vid)

                # Name — resolve collision
                final_name = dv_name
                if dv_name in existing_proj_names:
                    n = 2
                    while "{} ({})".format(dv_name, n) in existing_proj_names:
                        n += 1
                    final_name = "{} ({})".format(dv_name, n)

                try:
                    new_view.Name = final_name
                except Exception:
                    pass

                if title_on_sheet:
                    try:
                        p = new_view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
                        if p and not p.IsReadOnly:
                            p.Set(title_on_sheet)
                    except Exception:
                        pass

                try:
                    new_view.Scale = scale
                except Exception:
                    pass

                if template_name and template_name in template_by_name:
                    try:
                        new_view.ViewTemplateId = template_by_name[template_name]
                    except Exception:
                        pass

                if dv_id:
                    set_detail_id(new_view, dv_id)

                # Queue crop for pass 2
                pending_crops.append((new_view, make_crop_loop(corners_world)))

                results.append(("created", view_name, final_name,
                                "ID: {}  |  Title: *{}*".format(
                                    dv_id or "—", title_on_sheet or "(no title)")))
                total_created += 1
                existing_proj_names.add(final_name)

            except Exception as e:
                results.append(("error", view_name, dv_name, str(e)))

        # ── Pass 2: apply all crop shapes for this master ──
        for new_view, crop_loop in pending_crops:
            try:
                new_view.CropBoxActive  = True
                new_view.CropBoxVisible = True
                new_view.GetCropRegionShapeManager().SetCropShape(crop_loop)
            except Exception as e:
                results.append(("error", view_name, new_view.Name, "SetCropShape: " + str(e)))
        if cancelled:
            break

# ─────────────────────────────────────────────────────────────────────────────
# 6. Show results in Noir window
# ─────────────────────────────────────────────────────────────────────────────

import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('PresentationCore')
from System.Windows import Visibility
from System.Windows.Forms import Clipboard as WinFormsClipboard
from System.Collections.ObjectModel import ObservableCollection

class _Row(object):
    _LABELS = {
        "created":     u"✅  Created",
        "updated":     u"🔄  Updated",
        "skipped":     u"⏭️  Skipped",
        "error":       u"❌  Error",
        "skip_master": u"⏭️  Master not found",
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


n_errors  = sum(1 for s, _, _, _ in results if s == "error")
link_info = chosen_link if chosen_link != NONE_OPTION else u"Local (no transform)"
subtitle  = u"{} created  ·  {} updated  ·  {} skipped".format(
    total_created, total_updated, total_skipped)
if n_errors:
    subtitle += u"  ·  {} error{}".format(n_errors, u"s" if n_errors != 1 else u"")
subtitle += u"  ·  " + link_info
if master_map_info:
    subtitle += u"  ·  " + master_map_info
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
      <TextBlock x:Name="badgeCreated" Foreground="#50E898" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border Background="#142244" BorderBrush="#7EB4F0" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeUpdated" Foreground="#7EB4F0" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border Background="#1E2740" BorderBrush="#6B7394" BorderThickness="1"
            CornerRadius="4" Padding="10,4" Margin="0,0,8,0">
      <TextBlock x:Name="badgeSkipped" Foreground="#6B7394" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeErrorBorder" Background="#3C1212" BorderBrush="#FF7070"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeErrors" Foreground="#FF7070" FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeIdBorder" Background="#4A3810" BorderBrush="#FFCC66"
            BorderThickness="1" CornerRadius="4" Padding="10,4"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeIds" Foreground="#FFCC66" FontFamily="Segoe UI" FontSize="13"/>
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
      <DataGridTextColumn Header="Status"      Binding="{Binding Status}"   Width="140"/>
      <DataGridTextColumn Header="Master View" Binding="{Binding Master}"   Width="200"/>
      <DataGridTextColumn Header="View Name"   Binding="{Binding ViewName}" Width="*"/>
      <DataGridTextColumn Header="Detail"      Binding="{Binding Detail}"   Width="220"/>
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
    if status == "master":
        continue
    rows.Add(_Row(status, master, name, detail))

win = ui.parse(u"Import/Sync Views", subtitle, _BODY_XAML, _FOOTER_XAML,
               width=960, height=580)

win.FindName("badgeCreated").Text = u"✅  {} created".format(total_created)
win.FindName("badgeUpdated").Text = u"🔄  {} updated".format(total_updated)
win.FindName("badgeSkipped").Text = u"⏭️  {} skipped".format(total_skipped)

if n_errors:
    win.FindName("badgeErrorBorder").Visibility = Visibility.Visible
    win.FindName("badgeErrors").Text = u"❌  {} error{}".format(
        n_errors, u"s" if n_errors != 1 else u"")

if total_id_stamped:
    win.FindName("badgeIdBorder").Visibility = Visibility.Visible
    win.FindName("badgeIds").Text = u"🔖  {} IDs stamped".format(total_id_stamped)

win.FindName("dgResults").ItemsSource = rows

def on_copy(s, e):
    lines = [u"Import/Sync Views — " + subtitle, u""]
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
