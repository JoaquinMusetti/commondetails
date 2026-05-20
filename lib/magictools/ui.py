# -*- coding: utf-8 -*-
"""magictools.ui — Noir WPF theme for /slantis pyRevit tools.

Usage:
    from magictools import ui

    win = ui.parse("My Tool", "subtitle", BODY_XAML, footer=FOOTER_XAML)
    grid = win.FindName("grid")
    grid.ItemsSource = items
    win.ShowDialog()

Layout:
    ┌─────────────────────────────────────────┐
    │ ▰ gradient bar                          │
    │                                         │
    │  Title                                  │
    │  Subtitle                               │
    │                                         │
    │  ┌─ work surface (lighter card) ────┐   │
    │  │                                  │   │   ← BODY_XAML
    │  │  (search, grid, etc.)            │   │
    │  └──────────────────────────────────┘   │
    │                                         │
    │  count                  [Cancel] [OK]   │   ← FOOTER_XAML
    └─────────────────────────────────────────┘
"""

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
from System.Windows.Markup import XamlReader
from System.Windows import Visibility

# ── Brand & palette ───────────────────────────────────────────────────────────
VIOLET     = "#9013fe"
BLUE       = "#4a90e2"
GOLD       = "#f5a623"

WIN_BG     = "#0D111A"   # deepest — window background
CARD_BG    = "#1A2238"   # work surface — clearly lighter
CARD_BD    = "#2E3656"
ALT_ROW    = "#1F2942"   # alternating row on card
INPUT_BG   = "#242E4A"   # input/search — lightest, "active"
HOVER      = "#283358"
SELECTED   = "#3A1F70"

TEXT       = "#E8EBF5"
TEXT_DIM   = "#8088A8"
TEXT_MUTED = "#5A6286"

# ── Shared styles ─────────────────────────────────────────────────────────────
_STYLES = """
  <Style x:Key="BtnPrimary" TargetType="Button">
    <Setter Property="Foreground"      Value="White"/>
    <Setter Property="BorderThickness" Value="0"/>
    <Setter Property="Padding"         Value="18,7"/>
    <Setter Property="Cursor"          Value="Hand"/>
    <Setter Property="Template">
      <Setter.Value>
        <ControlTemplate TargetType="Button">
          <Border x:Name="bd" CornerRadius="3" Padding="{TemplateBinding Padding}">
            <Border.Background>
              <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                <GradientStop Color="#4a90e2" Offset="0"/>
                <GradientStop Color="#9013fe" Offset="1"/>
              </LinearGradientBrush>
            </Border.Background>
            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
          </Border>
          <ControlTemplate.Triggers>
            <Trigger Property="IsMouseOver" Value="True">
              <Setter TargetName="bd" Property="Opacity" Value="0.85"/>
            </Trigger>
            <Trigger Property="IsPressed" Value="True">
              <Setter TargetName="bd" Property="Opacity" Value="0.65"/>
            </Trigger>
          </ControlTemplate.Triggers>
        </ControlTemplate>
      </Setter.Value>
    </Setter>
  </Style>

  <Style x:Key="BtnGhost" TargetType="Button">
    <Setter Property="Foreground"      Value="#B0B8D0"/>
    <Setter Property="BorderThickness" Value="1"/>
    <Setter Property="BorderBrush"     Value="#3A4368"/>
    <Setter Property="Background"      Value="Transparent"/>
    <Setter Property="Padding"         Value="16,7"/>
    <Setter Property="Cursor"          Value="Hand"/>
    <Setter Property="Template">
      <Setter.Value>
        <ControlTemplate TargetType="Button">
          <Border x:Name="bd"
                  Background="{TemplateBinding Background}"
                  BorderBrush="{TemplateBinding BorderBrush}"
                  BorderThickness="{TemplateBinding BorderThickness}"
                  CornerRadius="3" Padding="{TemplateBinding Padding}">
            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
          </Border>
          <ControlTemplate.Triggers>
            <Trigger Property="IsMouseOver" Value="True">
              <Setter TargetName="bd" Property="Background" Value="#283358"/>
              <Setter TargetName="bd" Property="BorderBrush" Value="#4F588A"/>
            </Trigger>
          </ControlTemplate.Triggers>
        </ControlTemplate>
      </Setter.Value>
    </Setter>
  </Style>

  <Style TargetType="TextBox">
    <Setter Property="Background"       Value="#242E4A"/>
    <Setter Property="Foreground"       Value="#E8EBF5"/>
    <Setter Property="BorderBrush"      Value="#3A4368"/>
    <Setter Property="BorderThickness"  Value="1"/>
    <Setter Property="Padding"          Value="10,6"/>
    <Setter Property="CaretBrush"       Value="#9013fe"/>
    <Setter Property="SelectionBrush"   Value="#5A28B0"/>
    <Setter Property="FontSize"         Value="13"/>
    <Style.Triggers>
      <Trigger Property="IsFocused" Value="True">
        <Setter Property="BorderBrush" Value="#9013fe"/>
      </Trigger>
    </Style.Triggers>
  </Style>

  <Style TargetType="ComboBox">
    <Setter Property="Background"      Value="#242E4A"/>
    <Setter Property="Foreground"      Value="#E8EBF5"/>
    <Setter Property="BorderBrush"     Value="#3A4368"/>
    <Setter Property="BorderThickness" Value="1"/>
    <Setter Property="Padding"         Value="8,5"/>
  </Style>

  <Style TargetType="DataGrid">
    <Setter Property="Background"                Value="Transparent"/>
    <Setter Property="Foreground"                Value="#D8DCEC"/>
    <Setter Property="BorderThickness"           Value="0"/>
    <Setter Property="RowBackground"             Value="Transparent"/>
    <Setter Property="AlternatingRowBackground"  Value="#1F2942"/>
    <Setter Property="HorizontalGridLinesBrush"  Value="#2E3656"/>
    <Setter Property="VerticalGridLinesBrush"    Value="Transparent"/>
    <Setter Property="ColumnHeaderHeight"        Value="34"/>
    <Setter Property="RowHeight"                 Value="30"/>
    <Setter Property="SelectionMode"             Value="Extended"/>
    <Setter Property="CanUserResizeRows"         Value="False"/>
    <Setter Property="HeadersVisibility"         Value="Column"/>
    <Setter Property="AutoGenerateColumns"       Value="False"/>
    <Setter Property="IsReadOnly"                Value="True"/>
    <Setter Property="GridLinesVisibility"       Value="Horizontal"/>
  </Style>

  <Style TargetType="DataGridColumnHeader">
    <Setter Property="Background"      Value="Transparent"/>
    <Setter Property="Foreground"      Value="#9013fe"/>
    <Setter Property="FontWeight"      Value="SemiBold"/>
    <Setter Property="FontSize"        Value="11"/>
    <Setter Property="Padding"         Value="10,0"/>
    <Setter Property="BorderBrush"     Value="#9013fe"/>
    <Setter Property="BorderThickness" Value="0,0,0,1"/>
    <Setter Property="HorizontalContentAlignment" Value="Left"/>
  </Style>

  <Style TargetType="DataGridRow">
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

  <Style TargetType="DataGridCell">
    <Setter Property="BorderThickness" Value="0"/>
    <Setter Property="Padding"         Value="10,0"/>
    <Setter Property="Template">
      <Setter.Value>
        <ControlTemplate TargetType="DataGridCell">
          <Border Padding="{TemplateBinding Padding}"
                  Background="{TemplateBinding Background}">
            <ContentPresenter VerticalAlignment="Center"/>
          </Border>
        </ControlTemplate>
      </Setter.Value>
    </Setter>
  </Style>

  <Style TargetType="ScrollBar">
    <Setter Property="Background" Value="Transparent"/>
    <Setter Property="Width"      Value="8"/>
  </Style>
"""

# ── Window template ───────────────────────────────────────────────────────────
_TEMPLATE = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="__WIN_TITLE__" Width="__WIN_W__" __WIN_H_ATTR__
        WindowStartupLocation="CenterScreen"
        FontFamily="Segoe UI" FontSize="13"
        Background="#0D111A" Foreground="#E8EBF5"
        ResizeMode="CanResize">
  <Window.Resources>
__STYLES__
  </Window.Resources>

  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="3"/>
      <RowDefinition Height="__OUTER_ROW_H__"/>
    </Grid.RowDefinitions>

    <Border Grid.Row="0">
      <Border.Background>
        <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
          <GradientStop Color="#4a90e2" Offset="0"/>
          <GradientStop Color="#9013fe" Offset="1"/>
        </LinearGradientBrush>
      </Border.Background>
    </Border>

    <Grid Grid.Row="1" Margin="24,18,24,18">
      <Grid.RowDefinitions>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="__BODY_ROW_H__"/>
        <RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>

      <!-- Header -->
      <StackPanel Grid.Row="0" Margin="0,0,0,14">
        <TextBlock x:Name="__noir_title__"
                   FontSize="18" FontWeight="SemiBold" Foreground="#E8EBF5"/>
        <TextBlock x:Name="__noir_subtitle__"
                   FontSize="11" Foreground="#8088A8" Margin="0,3,0,0"/>
        <TextBlock x:Name="__noir_context__"
                   FontSize="12" Foreground="#8088A8"
                   Margin="0,10,0,0"
                   LineHeight="17"
                   TextWrapping="Wrap"/>
      </StackPanel>

      <!-- Work surface (elevated card) -->
      <Border Grid.Row="1"
              Background="#1A2238"
              BorderBrush="#2E3656"
              BorderThickness="1"
              CornerRadius="6"
              Padding="14">
__BODY__
      </Border>

      <!-- Footer slot -->
      <Grid Grid.Row="2" Margin="0,14,0,0">
__FOOTER__
      </Grid>
    </Grid>
  </Grid>
</Window>
"""


def _xml_esc(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;')
             .replace('>', '&gt;').replace('"', '&quot;')
             .replace("'", '&apos;'))


def build(title, subtitle, body, footer="", width=720, height=500):
    """Return the full Noir window XAML string.

    Pass height=None to let the window auto-size to its content
    (sets SizeToContent="Height" and collapses the body row to Auto).
    """
    safe_title = _xml_esc(title)
    if height is None:
        h_attr    = 'SizeToContent="Height" MinHeight="180"'
        outer_row = 'Auto'
        body_row  = 'Auto'
    else:
        h_attr    = 'Height="{}"'.format(height)
        outer_row = '*'
        body_row  = '*'
    return (_TEMPLATE
            .replace("__WIN_TITLE__",   safe_title)
            .replace("__WIN_W__",       str(width))
            .replace("__WIN_H_ATTR__",  h_attr)
            .replace("__OUTER_ROW_H__", outer_row)
            .replace("__BODY_ROW_H__",  body_row)
            .replace("__STYLES__",      _STYLES)
            .replace("__BODY__",        body)
            .replace("__FOOTER__",      footer))


def parse(title, subtitle, body, footer="", width=720, height=None, context=""):
    """Build, parse, and return a WPF Window ready for wiring.

    ``context`` is an optional longer paragraph that renders below the
    subtitle, explaining to the user what's being asked and why. Wraps
    to multiple lines automatically. Pass "" (default) to hide the block.
    """
    xaml = build(title, subtitle, body, footer, width, height)
    win = XamlReader.Parse(xaml)
    win.FindName("__noir_title__").Text = title
    win.FindName("__noir_subtitle__").Text = subtitle
    ctx_tb = win.FindName("__noir_context__")
    if context:
        ctx_tb.Text = context
    else:
        ctx_tb.Visibility = Visibility.Collapsed
    return win


def show(title, subtitle, body, footer="", width=720, height=None, context=""):
    """Build, parse, and ShowDialog."""
    return parse(title, subtitle, body, footer, width, height,
                 context=context).ShowDialog()


# ── Reusable Noir dialogs ────────────────────────────────────────────────────

_PICK_BODY = """
  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
    </Grid.RowDefinitions>
    <TextBox x:Name="txtFilter" Grid.Row="0" Height="32" Margin="0,0,0,12"
             ToolTip="Type to filter"/>
    <DataGrid x:Name="grid" Grid.Row="1" AutoGenerateColumns="False"
              IsReadOnly="True" HeadersVisibility="None"
              CanUserResizeRows="False" CanUserResizeColumns="False">
      <DataGrid.Columns>
        <DataGridTextColumn Width="*" Binding="{Binding Name}"/>
      </DataGrid.Columns>
    </DataGrid>
  </Grid>
"""

_PICK_FOOTER = """
  <Grid>
    <TextBlock x:Name="lblCount" VerticalAlignment="Center" Foreground="#5A6286"/>
    <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
      <Button x:Name="btnAll"    Content="Select all" Style="{StaticResource BtnGhost}" Margin="0,0,8,0"/>
      <Button x:Name="btnOK"    Content="__BTN__"     Style="{StaticResource BtnPrimary}" Margin="0,0,8,0"/>
      <Button x:Name="btnCancel" Content="Cancel"     Style="{StaticResource BtnGhost}"/>
    </StackPanel>
  </Grid>
"""

_ALERT_BODY = """
  <Grid>
    <TextBlock x:Name="lblMsg" TextWrapping="Wrap" FontSize="13"
               Foreground="#E8EBF5" VerticalAlignment="Center"/>
  </Grid>
"""

_ALERT_FOOTER = """
  <Grid>
    <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
      <Button x:Name="btnOK" Content="OK" Style="{StaticResource BtnPrimary}"/>
    </StackPanel>
  </Grid>
"""

_CONFIRM_FOOTER = """
  <Grid>
    <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
      <Button x:Name="btnYes" Content="__YES__" Style="{StaticResource BtnPrimary}" Margin="0,0,8,0"/>
      <Button x:Name="btnNo"  Content="Cancel"  Style="{StaticResource BtnGhost}"/>
    </StackPanel>
  </Grid>
"""


class _PickItem(object):
    def __init__(self, value, display=None):
        self._value = value
        self.Name = display if display is not None else str(value)


def pick_list(items, title, subtitle="", button_name="OK",
              multiselect=True, width=480, height=460, name_fn=None,
              context=""):
    """Show a Noir list picker.

    Args:
        items:       List of strings OR objects.
        name_fn:     Optional callable to extract display text from each item.
                     If omitted, str(item) is used as the display name.
                     Example: name_fn=lambda x: x.Name
        multiselect: If True, returns list of selected values; otherwise single value.

    Returns:
        List of selected values (multiselect=True), single value (multiselect=False),
        or None if cancelled.
    """
    from System.Collections.ObjectModel import ObservableCollection

    footer = _PICK_FOOTER.replace("__BTN__", _xml_esc(button_name))
    mode = "Extended" if multiselect else "Single"
    body = _PICK_BODY.replace('SelectionMode="Extended"', 'SelectionMode="{}"'.format(mode))

    win = parse(title, subtitle, body, footer, width, height, context=context)
    grid      = win.FindName("grid")
    txtFilter = win.FindName("txtFilter")
    lblCount  = win.FindName("lblCount")
    btnAll    = win.FindName("btnAll")
    btnOK     = win.FindName("btnOK")
    btnCancel = win.FindName("btnCancel")

    if name_fn is not None:
        all_items = [_PickItem(i, display=name_fn(i)) for i in items]
    else:
        all_items = [_PickItem(i) for i in items]
    state = {'result': None, 'filtered': all_items}

    if not multiselect:
        btnAll.Visibility = Visibility.Collapsed

    def _to_col(lst):
        c = ObservableCollection[object]()
        for it in lst:
            c.Add(it)
        return c

    def _refresh():
        q = txtFilter.Text.strip().lower()
        if not q:
            state['filtered'] = all_items
        else:
            state['filtered'] = [it for it in all_items if q in it.Name.lower()]
        grid.ItemsSource = _to_col(state['filtered'])
        lblCount.Text = "{} of {} items".format(len(state['filtered']), len(all_items))

    _refresh()

    def on_filter(s, e):
        _refresh()

    def on_all(s, e):
        grid.SelectAll()

    def on_ok(s, e):
        sel = list(grid.SelectedItems)
        if not sel:
            return
        if multiselect:
            state['result'] = [it._value for it in sel]
        else:
            state['result'] = sel[0]._value
        win.Close()

    def on_dblclick(s, e):
        sel = grid.SelectedItem
        if sel is None:
            return
        if multiselect:
            state['result'] = [sel._value]
        else:
            state['result'] = sel._value
        win.Close()

    txtFilter.TextChanged   += on_filter
    btnAll.Click            += on_all
    btnOK.Click             += on_ok
    btnCancel.Click         += lambda s, e: win.Close()
    grid.MouseDoubleClick   += on_dblclick

    txtFilter.Focus()
    win.ShowDialog()
    return state['result']


def alert(message, title="Alert", width=440, height=None, context=""):
    """Show a Noir alert dialog."""
    win = parse(title, "", _ALERT_BODY, _ALERT_FOOTER, width, height,
                context=context)
    win.FindName("lblMsg").Text = message
    win.FindName("btnOK").Click += lambda s, e: win.Close()
    win.ShowDialog()


_INPUT_BODY = """
  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>
    <TextBlock x:Name="lblPrompt" Grid.Row="0" TextWrapping="Wrap"
               FontSize="13" Foreground="#E8EBF5" Margin="0,0,0,12"/>
    <TextBox x:Name="txtInput" Grid.Row="1" Height="32"/>
  </Grid>
"""

_INPUT_FOOTER = """
  <Grid>
    <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
      <Button x:Name="btnOK"     Content="OK"     Style="{StaticResource BtnPrimary}" Margin="0,0,8,0"/>
      <Button x:Name="btnCancel" Content="Cancel" Style="{StaticResource BtnGhost}"/>
    </StackPanel>
  </Grid>
"""


def ask_for_string(prompt="", title="Input", default="",
                   width=440, height=None, context=""):
    """Show a Noir text-input dialog. Returns string or None on cancel."""
    win = parse(title, "", _INPUT_BODY, _INPUT_FOOTER, width, height,
                context=context)
    win.FindName("lblPrompt").Text = prompt
    txt = win.FindName("txtInput")
    txt.Text = default
    txt.SelectAll()
    state = {'result': None}

    def on_ok(s, e):
        state['result'] = txt.Text
        win.Close()

    win.FindName("btnOK").Click     += on_ok
    win.FindName("btnCancel").Click += lambda s, e: win.Close()
    txt.Focus()
    win.ShowDialog()
    return state['result']


def confirm(message, title="Confirm", yes_text="Continue",
            width=500, height=None, context=""):
    """Show a Noir confirmation dialog. Returns True if confirmed."""
    footer = _CONFIRM_FOOTER.replace("__YES__", _xml_esc(yes_text))
    win = parse(title, "", _ALERT_BODY, footer, width, height,
                context=context)
    win.FindName("lblMsg").Text = message
    state = {'ok': False}

    def on_yes(s, e):
        state['ok'] = True
        win.Close()

    win.FindName("btnYes").Click += on_yes
    win.FindName("btnNo").Click  += lambda s, e: win.Close()
    win.ShowDialog()
    return state['ok']


# ── Report window ─────────────────────────────────────────────────────────────

_REPORT_BODY = """
  <Grid>
    <TextBox x:Name="txtReport"
             IsReadOnly="True" TextWrapping="Wrap"
             VerticalScrollBarVisibility="Auto"
             HorizontalScrollBarVisibility="Disabled"
             FontFamily="Consolas" FontSize="12"
             Background="Transparent" Foreground="#E8EBF5"
             BorderThickness="0" Padding="2"/>
  </Grid>
"""

_REPORT_FOOTER = """
  <Grid>
    <TextBlock x:Name="lblSummary" VerticalAlignment="Center" Foreground="#5A6286"/>
    <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
      <Button x:Name="btnCopy"  Content="Copy to Clipboard" Style="{StaticResource BtnGhost}" Margin="0,0,8,0"/>
      <Button x:Name="btnClose" Content="Close"             Style="{StaticResource BtnPrimary}"/>
    </StackPanel>
  </Grid>
"""


def show_report(text, title="Report", subtitle="", summary="",
                width=680, height=560, context=""):
    """Show a scrollable read-only Noir window with a Copy to Clipboard button.

    Args:
        text:     Plain-text content to display (unicode string).
        title:    Window title (shown in header).
        subtitle: Smaller line below title.
        summary:  Short stats string shown on the left of the footer
                  (e.g. "✅ 12  ⚠ 3  ❌ 0").
        width:    Window width in px (default 680).
        height:   Window height in px (default 560). Fixed — the body scrolls.
    """
    from System.Windows import Clipboard
    win = parse(title, subtitle, _REPORT_BODY, _REPORT_FOOTER, width, height,
                context=context)
    win.FindName("txtReport").Text = text
    win.FindName("lblSummary").Text = summary

    def on_copy(s, e):
        try:
            Clipboard.SetText(text)
        except Exception:
            pass

    win.FindName("btnCopy").Click  += on_copy
    win.FindName("btnClose").Click += lambda s, e: win.Close()
    win.ShowDialog()


# ── Progress bar ──────────────────────────────────────────────────────────────

class ProgressBar(object):
    """Top-anchored pyRevit progress bar — Noir convention.

    Plain pass-through to ``pyrevit.forms.ProgressBar``. The window anchors
    to the TOP edge of the Revit window (pyRevit default). Kept as a class
    wrapper so callers continue to use ``ui.ProgressBar(...)`` as they did
    before — the only change vs. the previous bottom-anchored variant is
    the position.

    Usage (identical to forms.ProgressBar):

        from magictools import ui

        with ui.ProgressBar(title="My Tool", cancellable=True) as pb:
            for i, item in enumerate(items):
                if pb.cancelled:
                    break
                pb.title = u"My Tool — {}/{} — {}".format(i+1, n, item.Name)
                # ... process ...
                pb.update_progress(i + 1, n)
    """

    def __new__(cls, *args, **kwargs):
        from pyrevit import forms
        return forms.ProgressBar(*args, **kwargs)
