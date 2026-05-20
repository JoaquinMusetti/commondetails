# -*- coding: utf-8 -*-
__title__ = "Sheet Number\nReplace"
__doc__ = "Replaces a text string in sheet numbers across multiple sheets in batch. Enter a Find and Replace value, preview all affected sheets with their new numbers, deselect any exceptions, then apply."

import sys
import os as _os
_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

from pyrevit import revit, DB
from Autodesk.Revit.DB import FilteredElementCollector, ViewSheet
from System.Collections.ObjectModel import ObservableCollection

doc   = revit.doc
uidoc = revit.uidoc


# ── Row model ─────────────────────────────────────────────────────────────────

class SheetRow(object):
    def __init__(self, sheet, new_number):
        self._sheet     = sheet
        self._included  = True
        self._new_number = new_number

    @property
    def Included(self):         return self._included
    @Included.setter
    def Included(self, value):  self._included = bool(value)

    @property
    def CurrentNumber(self): return self._sheet.SheetNumber
    @property
    def NewNumber(self):     return self._new_number
    @property
    def SheetName(self):     return self._sheet.Name


# ── XAML ──────────────────────────────────────────────────────────────────────

_BODY = """
  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
    </Grid.RowDefinitions>

    <Grid Grid.Row="0" Margin="0,0,0,12">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="140"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="140"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
      </Grid.ColumnDefinitions>

      <TextBlock Grid.Column="0" Text="Find" VerticalAlignment="Center"
                 Foreground="#8890B5" Margin="0,0,8,0"/>
      <TextBox x:Name="txtFind" Grid.Column="1"/>
      <TextBlock Grid.Column="2" Text="&#x2192;" VerticalAlignment="Center"
                 Foreground="#8890B5" Margin="12,0"/>
      <TextBlock Grid.Column="3" Text="Replace" VerticalAlignment="Center"
                 Foreground="#8890B5" Margin="0,0,8,0"/>
      <TextBox x:Name="txtReplace" Grid.Column="4"/>
      <Button x:Name="btnPreview" Grid.Column="5" Content="Preview"
              Style="{StaticResource BtnPrimary}" Margin="12,0,0,0"/>
      <TextBlock x:Name="lblStatus" Grid.Column="7" VerticalAlignment="Center"
                 Foreground="#5A6286"/>
    </Grid>

    <DataGrid x:Name="grid" Grid.Row="1"
              AutoGenerateColumns="False"
              CanUserAddRows="False"
              CanUserSortColumns="True"
              SelectionMode="Single">
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
        <DataGridTemplateColumn Header="" Width="36" CanUserSort="False">
          <DataGridTemplateColumn.CellTemplate>
            <DataTemplate>
              <CheckBox IsChecked="{Binding Included, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"
                        HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </DataTemplate>
          </DataGridTemplateColumn.CellTemplate>
        </DataGridTemplateColumn>
        <DataGridTextColumn Header="CURRENT NUMBER" Width="160"
                            Binding="{Binding CurrentNumber}" IsReadOnly="True"/>
        <DataGridTextColumn Header="NEW NUMBER"     Width="160"
                            Binding="{Binding NewNumber}"     IsReadOnly="True"/>
        <DataGridTextColumn Header="SHEET NAME"     Width="*"
                            Binding="{Binding SheetName}"     IsReadOnly="True"/>
      </DataGrid.Columns>
    </DataGrid>
  </Grid>
"""

_FOOTER = """
  <Grid>
    <TextBlock x:Name="lblDuplicates" VerticalAlignment="Center" Foreground="#E05C5C"/>
    <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
      <Button x:Name="btnApply"  Content="Apply"  Style="{StaticResource BtnPrimary}"
              IsEnabled="False" Margin="0,0,8,0"/>
      <Button x:Name="btnCancel" Content="Cancel" Style="{StaticResource BtnGhost}"/>
    </StackPanel>
  </Grid>
"""


# ── Window setup ──────────────────────────────────────────────────────────────

win           = ui.parse("Sheet Number Replace", "Find & replace in sheet numbers", _BODY, _FOOTER, width=760, height=520)
txtFind       = win.FindName("txtFind")
txtReplace    = win.FindName("txtReplace")
btnPreview    = win.FindName("btnPreview")
lblStatus     = win.FindName("lblStatus")
grid          = win.FindName("grid")
btnApply      = win.FindName("btnApply")
btnCancel     = win.FindName("btnCancel")
lblDuplicates = win.FindName("lblDuplicates")

items = ObservableCollection[SheetRow]()
grid.ItemsSource = items

all_sheet_numbers = set(
    s.SheetNumber
    for s in FilteredElementCollector(doc).OfClass(ViewSheet)
    if not s.IsPlaceholder
)


# ── Handlers ──────────────────────────────────────────────────────────────────

def on_preview(s, e):
    find    = txtFind.Text.strip()
    replace = txtReplace.Text.strip()

    items.Clear()
    lblDuplicates.Text = ""
    btnApply.IsEnabled = False

    if not find:
        ui.alert("Enter a Find value.", title="Sheet Number Replace")
        return

    matched = sorted(
        (sh for sh in FilteredElementCollector(doc).OfClass(ViewSheet)
         if not sh.IsPlaceholder and find in sh.SheetNumber),
        key=lambda sh: sh.SheetNumber
    )

    if not matched:
        lblStatus.Text = u'No sheets match "{}".'.format(find)
        return

    for sh in matched:
        new_num = sh.SheetNumber.replace(find, replace)
        items.Add(SheetRow(sh, new_num))

    lblStatus.Text = u"{} sheet(s) found".format(len(items))
    btnApply.IsEnabled = True


def on_apply(s, e):
    selected = [r for r in items if bool(r.Included)]

    if not selected:
        ui.alert("No sheets selected.", title="Sheet Number Replace")
        return

    new_nums = [r.NewNumber for r in selected]
    duplicates_internal = set(n for n in new_nums if new_nums.count(n) > 1)

    renaming_current = set(r.CurrentNumber for r in selected)
    conflicts_existing = set(n for n in new_nums if n in (all_sheet_numbers - renaming_current))

    all_conflicts = duplicates_internal | conflicts_existing
    if all_conflicts:
        lblDuplicates.Text = u"Conflict: {}".format(u", ".join(sorted(all_conflicts)))
        return

    lblDuplicates.Text = ""

    with DB.Transaction(doc, "Sheet Number Replace") as t:
        t.Start()
        for row in selected:
            row._sheet.SheetNumber = row.NewNumber
        t.Commit()

    ui.alert(u"Done. {} sheet(s) renamed.".format(len(selected)),
             title="Sheet Number Replace")
    win.Close()


btnPreview.Click += on_preview
btnApply.Click   += on_apply
btnCancel.Click  += lambda s, e: win.Close()

win.ShowDialog()
