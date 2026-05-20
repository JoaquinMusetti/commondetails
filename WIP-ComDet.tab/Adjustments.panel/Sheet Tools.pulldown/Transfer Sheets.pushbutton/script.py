# -*- coding: utf-8 -*-
__title__ = "Transfer\nSheets"
__doc__ = "Transfer sheets from the active model to another open Revit project. Select the destination model, pick sheets to copy, and transfer them with their titleblocks and parameters. Duplicate sheet numbers are auto-renamed with a _1, _2 suffix and flagged at the end."

import sys
import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

import os as _os
_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewSheet, BuiltInCategory, ElementId,
    ElementTransformUtils, CopyPasteOptions, Transform,
    Transaction, SubTransaction,
    BuiltInParameter, StorageType
)
from System.Collections.ObjectModel import ObservableCollection
from System.Collections.Generic import List
from System.Windows import Clipboard

doc = __revit__.ActiveUIDocument.Document
app = __revit__.Application


# ── Shared alert/results window (copy-to-clipboard standard) ──────────────

_MSG_BODY = """
  <ScrollViewer VerticalScrollBarVisibility="Auto">
    <TextBlock x:Name="txtMsg"
               TextWrapping="Wrap"
               Foreground="#E8EBF5"
               FontFamily="Segoe UI"
               FontSize="13"
               LineHeight="22"
               Margin="0,0,8,0"/>
  </ScrollViewer>
"""
_MSG_FOOTER = """
  <Grid>
    <Button x:Name="btnCopy" Content="Copy to clipboard"
            Style="{StaticResource BtnGhost}" HorizontalAlignment="Left"/>
    <Button x:Name="btnOK" Content="OK"
            Style="{StaticResource BtnPrimary}" HorizontalAlignment="Right"/>
  </Grid>
"""

def _show_msg(subtitle, lines, width=520, height=300):
    """Show a scrollable message window with Copy to clipboard + OK."""
    text = u"\n".join(lines) if isinstance(lines, list) else lines
    mwin = ui.parse("Transfer Sheets", subtitle, _MSG_BODY, _MSG_FOOTER,
                    width=width, height=height)
    mwin.FindName("txtMsg").Text = text
    mwin.FindName("btnOK").Click += lambda s, e: mwin.Close()

    def on_copy(s, e):
        Clipboard.SetText(text)
        s.Content = u"Copied ✓"

    mwin.FindName("btnCopy").Click += on_copy
    mwin.ShowDialog()


# ── Destination documents ──────────────────────────────────────────────────

_dest_docs = []
for _d in app.Documents:
    try:
        if _d.IsValidObject and not _d.IsFamilyDocument and not _d.Equals(doc):
            _dest_docs.append(_d)
    except Exception:
        pass

if not _dest_docs:
    _show_msg("No models open", [
        "No other Revit models are currently open.",
        "",
        "Open the destination project first, then run this tool again."
    ])
    sys.exit()


# ── Row model ──────────────────────────────────────────────────────────────

class SheetRow(object):
    def __init__(self, sheet):
        self._sheet = sheet

    @property
    def Number(self): return self._sheet.SheetNumber or u""
    @property
    def Name(self):   return self._sheet.Name or u""


# ── Helpers ───────────────────────────────────────────────────────────────


def _get_src_tb_type_id(sheet, src_doc):
    try:
        tbs = list(
            FilteredElementCollector(src_doc, sheet.Id)
            .OfCategory(BuiltInCategory.OST_TitleBlocks)
            .ToElements()
        )
        return tbs[0].GetTypeId() if tbs else ElementId.InvalidElementId
    except Exception:
        return ElementId.InvalidElementId


def _dest_tb_types_snapshot(dest_doc):
    return set(
        e.Id.IntegerValue
        for e in FilteredElementCollector(dest_doc)
            .OfCategory(BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsElementType()
            .ToElements()
    )


def _get_type_name(type_elem):
    try:
        p = type_elem.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p:
            return p.AsString()
    except Exception:
        pass
    try:
        return type_elem.Name
    except Exception:
        return None


def _search_dest_by_name(dest_doc, src_name):
    if not src_name:
        return None
    for dt in (FilteredElementCollector(dest_doc)
               .OfCategory(BuiltInCategory.OST_TitleBlocks)
               .WhereElementIsElementType()
               .ToElements()):
        try:
            if (_get_type_name(dt) or u"").lower() == src_name.lower():
                return dt.Id
        except Exception:
            continue
    return None


def _ensure_tb_type_in_dest(src_type_id, src_doc, dest_doc, cache):
    key = src_type_id.IntegerValue
    if key in cache:
        return cache[key]
    if src_type_id == ElementId.InvalidElementId:
        cache[key] = ElementId.InvalidElementId
        return ElementId.InvalidElementId
    src_type = src_doc.GetElement(src_type_id)
    if src_type is None:
        cache[key] = ElementId.InvalidElementId
        return ElementId.InvalidElementId

    src_name = _get_type_name(src_type)

    found = _search_dest_by_name(dest_doc, src_name)
    if found is not None:
        cache[key] = found
        return found

    before = _dest_tb_types_snapshot(dest_doc)
    ids = List[ElementId]()
    ids.Add(src_type_id)
    try:
        ElementTransformUtils.CopyElements(
            src_doc, ids, dest_doc, Transform.Identity, CopyPasteOptions()
        )
    except Exception:
        pass

    after   = _dest_tb_types_snapshot(dest_doc)
    new_ids = after - before
    if new_ids:
        result = ElementId(list(new_ids)[0])
        cache[key] = result
        return result

    found  = _search_dest_by_name(dest_doc, src_name)
    result = found if found is not None else ElementId.InvalidElementId
    cache[key] = result
    return result


_SKIP_BIPS = {
    BuiltInParameter.SHEET_NUMBER,
    BuiltInParameter.SHEET_NAME,
    BuiltInParameter.VIEW_NAME,
}

def _copy_sheet_params(src_sheet, new_sheet):
    for src_p in src_sheet.Parameters:
        if src_p.IsReadOnly:
            continue
        try:
            bip = src_p.Definition.BuiltInParameter
            if bip in _SKIP_BIPS:
                continue
            dest_p = new_sheet.get_Parameter(bip)
        except Exception:
            bip    = None
            dest_p = None
        if dest_p is None:
            try:
                dest_p = new_sheet.get_Parameter(src_p.Definition.Guid)
            except Exception:
                continue
        if dest_p is None or dest_p.IsReadOnly:
            continue
        try:
            st = src_p.StorageType
            if st == StorageType.String:
                val = src_p.AsString()
                dest_p.Set(val if val is not None else u"")
            elif st == StorageType.Integer:
                dest_p.Set(src_p.AsInteger())
            elif st == StorageType.Double:
                dest_p.Set(src_p.AsDouble())
        except Exception:
            pass


def _unique_number(num, existing):
    if num not in existing:
        return num, False
    i = 1
    while True:
        candidate = u"{}_{}".format(num, i)
        if candidate not in existing:
            return candidate, True
        i += 1


# ── Load source sheets (active doc — instant) ─────────────────────────────

_source_rows = []
for _sh in sorted(
    FilteredElementCollector(doc).OfClass(ViewSheet).ToElements(),
    key=lambda sh: sh.SheetNumber
):
    _source_rows.append(SheetRow(_sh))


# ── XAML ──────────────────────────────────────────────────────────────────

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
                           VerticalAlignment="Center"
                           HorizontalAlignment="Center"
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
                       AllowsTransparency="True"
                       Focusable="False"
                       Placement="Bottom">
                  <Border Background="#1E2235"
                          BorderBrush="#3A4070"
                          BorderThickness="1"
                          MinWidth="{Binding ActualWidth, RelativeSource={RelativeSource TemplatedParent}}">
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
      <Style TargetType="TextBox">
        <Setter Property="Foreground"               Value="#E8EBF5"/>
        <Setter Property="Background"               Value="#1E2235"/>
        <Setter Property="BorderBrush"              Value="#3A4070"/>
        <Setter Property="BorderThickness"          Value="1"/>
        <Setter Property="Padding"                  Value="6,4"/>
        <Setter Property="CaretBrush"               Value="#E8EBF5"/>
        <Setter Property="SelectionBrush"           Value="#4a90e2"/>
        <Setter Property="VerticalContentAlignment" Value="Center"/>
      </Style>
    </Grid.Resources>

    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
    </Grid.RowDefinitions>

    <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,10"
                VerticalAlignment="Center">
      <TextBlock Text="Destination:" VerticalAlignment="Center"
                 Foreground="#9BA3BF" Margin="0,0,10,0"/>
      <ComboBox x:Name="cmbDest" Width="420" VerticalAlignment="Center"/>
    </StackPanel>

    <Grid Grid.Row="1" Margin="0,0,0,8">
      <StackPanel Orientation="Horizontal">
        <Button x:Name="btnSelectAll"   Content="Select All"
                Style="{StaticResource BtnGhost}" Margin="0,0,8,0"/>
        <Button x:Name="btnDeselectAll" Content="Deselect All"
                Style="{StaticResource BtnGhost}"/>
      </StackPanel>
      <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
        <TextBlock Text="Search:" VerticalAlignment="Center"
                   Foreground="#9BA3BF" Margin="0,0,8,0"/>
        <TextBox x:Name="txtSearch" Width="220" Height="28"
                 VerticalContentAlignment="Center"/>
      </StackPanel>
    </Grid>

    <DataGrid x:Name="grid" Grid.Row="2"
              AutoGenerateColumns="False"
              CanUserAddRows="False"
              CanUserSortColumns="True"
              SelectionMode="Extended">
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
        <DataGridTextColumn Header="NUMBER" Width="110"
                            Binding="{Binding Number}" IsReadOnly="True"/>
        <DataGridTextColumn Header="NAME"   Width="*"
                            Binding="{Binding Name}"   IsReadOnly="True"/>
      </DataGrid.Columns>
    </DataGrid>
  </Grid>
"""

_FOOTER = """
  <Grid>
    <TextBlock x:Name="lblCount" VerticalAlignment="Center" Foreground="#5A6286"/>
    <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
      <Button x:Name="btnTransfer" Content="Transfer"
              Style="{StaticResource BtnPrimary}" Margin="0,0,8,0"/>
      <Button x:Name="btnCancel"   Content="Cancel"
              Style="{StaticResource BtnGhost}"/>
    </StackPanel>
  </Grid>
"""


# ── Window setup ──────────────────────────────────────────────────────────

_subtitle = u"{} sheets in {}".format(len(_source_rows), doc.Title)
win         = ui.parse("Transfer Sheets", _subtitle, _BODY, _FOOTER, width=860, height=560)
grid        = win.FindName("grid")
lblCount    = win.FindName("lblCount")
cmbDest     = win.FindName("cmbDest")
txtSearch   = win.FindName("txtSearch")
btnSelAll   = win.FindName("btnSelectAll")
btnDeselAll = win.FindName("btnDeselectAll")
btnTransfer = win.FindName("btnTransfer")
btnCancel   = win.FindName("btnCancel")

all_rows = ObservableCollection[SheetRow]()
for row in _source_rows:
    all_rows.Add(row)
grid.ItemsSource = all_rows
lblCount.Text = u"{} sheet(s)".format(len(all_rows))

for d in _dest_docs:
    cmbDest.Items.Add(d.Title)

if len(_dest_docs) == 1:
    cmbDest.SelectedIndex = 0


# ── Filter logic ──────────────────────────────────────────────────────────

def _refresh_grid(s=None, e=None):
    query = (txtSearch.Text or u"").strip().lower()
    all_rows.Clear()
    for row in _source_rows:
        if query and query not in row.Number.lower() and query not in row.Name.lower():
            continue
        all_rows.Add(row)
    lblCount.Text = u"{} sheet(s)".format(len(all_rows))

txtSearch.TextChanged += _refresh_grid


# ── Event handlers ────────────────────────────────────────────────────────

def on_select_all(s, e):
    grid.SelectAll()

def on_deselect_all(s, e):
    grid.UnselectAll()


def on_transfer(s, e):
    selected = list(grid.SelectedItems)
    if not selected:
        _show_msg("Nothing selected", ["Select at least one sheet to transfer."])
        return

    idx = cmbDest.SelectedIndex
    if idx < 0:
        _show_msg("No destination", ["Select a destination model first."])
        return
    dest = _dest_docs[idx]

    existing = set(
        sh.SheetNumber
        for sh in FilteredElementCollector(dest).OfClass(ViewSheet).ToElements()
    )

    renamed = []
    errors  = []

    t = Transaction(dest, "Transfer Sheets")
    t.Start()

    # ── Pass 1: copy all needed titleblock types into dest (once per unique type)
    tb_cache = {}
    sub_tb = SubTransaction(dest)
    sub_tb.Start()
    for row in selected:
        src_tb_id = _get_src_tb_type_id(row._sheet, doc)
        _ensure_tb_type_in_dest(src_tb_id, doc, dest, tb_cache)
    sub_tb.Commit()

    # ── Pass 2: create sheets using cached titleblock type IDs
    for row in selected:
        src_sheet   = row._sheet
        src_num     = src_sheet.SheetNumber
        desired_num, was_renamed = _unique_number(src_num, existing)

        src_tb_id  = _get_src_tb_type_id(src_sheet, doc)
        dest_tb_id = tb_cache.get(src_tb_id.IntegerValue, ElementId.InvalidElementId)

        sub = SubTransaction(dest)
        sub.Start()
        try:
            new_sheet = ViewSheet.Create(dest, dest_tb_id)
            new_sheet.get_Parameter(BuiltInParameter.SHEET_NUMBER).Set(desired_num)
            new_sheet.get_Parameter(BuiltInParameter.SHEET_NAME).Set(src_sheet.Name or u"")
            _copy_sheet_params(src_sheet, new_sheet)

            existing.add(desired_num)
            if was_renamed:
                renamed.append((src_num, desired_num))

            sub.Commit()

        except Exception as ex:
            sub.RollBack()
            errors.append((src_num, u"[{}] {}".format(type(ex).__name__, str(ex))))

    t.Commit()

    lines = [
        u"Transfer complete.",
        u"",
        u"{} sheet(s) transferred.".format(len(selected) - len(errors))
    ]
    if renamed:
        lines += [u"", u"Renamed due to number conflict:"]
        for orig, new in renamed:
            lines.append(u"  {} → {}".format(orig, new))
    if errors:
        lines += [u"", u"Failed:"]
        for num, reason in errors:
            lines.append(u"  {} — {}".format(num, reason))

    win.Close()
    _show_msg("Summary", lines, width=520, height=360)


# ── Wire events ───────────────────────────────────────────────────────────

btnSelAll.Click   += on_select_all
btnDeselAll.Click += on_deselect_all
btnTransfer.Click += on_transfer
btnCancel.Click   += lambda s, e: win.Close()

win.ShowDialog()
