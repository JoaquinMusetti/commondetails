# -*- coding: utf-8 -*-
__title__ = 'Annotation \nCrop Offset'
__doc__ = ('Sets the annotation crop offset (in inches) for selected views. '
           'Supports 3D views, floor plans, ceiling plans, area plans, elevations, and sections. '
           'Master and dependent views can be filtered and searched.')

import System
import clr
from pyrevit import revit, forms
from Autodesk.Revit.DB import *

from System.Windows import (
    Window, Thickness, HorizontalAlignment,
    Style, Setter
)
from System.Windows.Controls import (
    StackPanel, Grid, TextBox, Label, Button,
    ListView, ListViewItem,
    GridView, GridViewColumn,
    SelectionMode, ColumnDefinition, Orientation,
    GridViewColumnHeader, CheckBox
)
from System.Collections.ObjectModel import ObservableCollection
from System.ComponentModel import SortDescription, ListSortDirection
from System.Windows.Data import Binding, CollectionViewSource
from System.Windows.Media import SolidColorBrush, Colors

doc = revit.doc

# ------------------------------------------------------------
# Units
# ------------------------------------------------------------
def inches_to_feet(v):
    return v / 12.0

# ------------------------------------------------------------
# Setter with 3 fallbacks for maximum compatibility
# ------------------------------------------------------------
def set_annotation_crop_offset(rm, top, bottom, left, right):
    try:
        rm.TopAnnotationCropOffset    = top
        rm.BottomAnnotationCropOffset = bottom
        rm.LeftAnnotationCropOffset   = left
        rm.RightAnnotationCropOffset  = right
        return True
    except Exception:
        pass

    try:
        t     = rm.GetType()
        flags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance
        props = {p.Name: p for p in t.GetProperties(flags)}
        pairs = [
            ("TopAnnotationCropOffset",    top),
            ("BottomAnnotationCropOffset", bottom),
            ("LeftAnnotationCropOffset",   left),
            ("RightAnnotationCropOffset",  right),
        ]
        for name, val in pairs:
            if name in props:
                props[name].SetValue(rm, val, None)
        return True
    except Exception:
        pass

    try:
        rm.SetAnnotationCropOffset(top, bottom, left, right)
        return True
    except Exception:
        pass

    return False

# ------------------------------------------------------------
# Check if a view supports annotation crop
# ------------------------------------------------------------
def view_supports_annotation_crop(v):
    try:
        rm = v.GetCropRegionShapeManager()
        if rm is None:
            return False, "ShapeManager not available"
        _ = rm.TopAnnotationCropOffset
        return True, None
    except Exception as ex:
        return False, str(ex)

# ------------------------------------------------------------
# Excluded ViewTypes
# ------------------------------------------------------------
EXCLUDED_VIEW_TYPES = (
    ViewType.DrawingSheet,
    ViewType.ProjectBrowser,
    ViewType.SystemBrowser,
    ViewType.Internal,
    ViewType.Schedule,
    ViewType.DraftingView,
    ViewType.Legend
)

FILTERABLE_VIEW_TYPES = {
    "3D Views":      ViewType.ThreeD,
    "Floor Plans":   ViewType.FloorPlan,
    "Ceiling Plans": ViewType.CeilingPlan,
    "Area Plans":    ViewType.AreaPlan,
    "Elevations":    ViewType.Elevation,
    "Sections":      ViewType.Section
}

# ------------------------------------------------------------
# Collect views (once)
# ------------------------------------------------------------
all_views = [
    v for v in FilteredElementCollector(doc).OfClass(View)
    if not v.IsTemplate
    and v.ViewType not in EXCLUDED_VIEW_TYPES
]

# ------------------------------------------------------------
# Build Primary → Dependents map
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# Data object for ListView
# ------------------------------------------------------------
class ViewItem(object):
    def __init__(self, view):
        self.View     = view
        self.ViewType = view.ViewType.ToString()

        pid = view.GetPrimaryViewId()
        self.IsDependent = pid != ElementId.InvalidElementId

        if self.IsDependent:
            master = doc.GetElement(pid)
            self.ViewName   = u"{} \u2013 {}".format(master.Name, view.Name)
            self.Foreground = SolidColorBrush(Colors.DarkRed)
        else:
            self.ViewName   = view.Name
            self.Foreground = SolidColorBrush(Colors.Black)

# ------------------------------------------------------------
# Main window
# ------------------------------------------------------------
class AnnotationCropWindow(Window):
    def __init__(self):
        self.Title   = "Annotation Crop Offset (Inches)"
        self.Width   = 760
        self.Height  = 820
        self.Padding = Thickness(10)

        self.last_sort = None
        self.last_dir  = ListSortDirection.Ascending

        main = StackPanel()
        self.Content = main

        # ---- Presets ----
        preset_panel = StackPanel(
            Orientation=Orientation.Horizontal,
            Margin=Thickness(0, 0, 0, 10)
        )
        presets = [
            ("1/8\"", 0.125), ("1/4\"", 0.25), ("1/2\"", 0.5),
            ("3/4\"", 0.75),  ("1\"",   1.0),  ("2\"",   2.0)
        ]
        for label, value in presets:
            b = Button(Content=label, Width=60, Margin=Thickness(3))
            b.Click += lambda s, e, v=value: self.apply_preset(v)
            preset_panel.Children.Add(b)
        main.Children.Add(preset_panel)

        # ---- Inputs de offset ----
        grid = Grid(Margin=Thickness(0, 0, 0, 10))
        for _ in range(4):
            grid.ColumnDefinitions.Add(ColumnDefinition())

        self.inputs = {}
        for i, k in enumerate(["Top", "Bottom", "Left", "Right"]):
            st = StackPanel(Margin=Thickness(5))
            st.Children.Add(Label(Content="{} (in)".format(k)))
            tb = TextBox(Text="0", Width=70)
            st.Children.Add(tb)
            Grid.SetColumn(st, i)
            grid.Children.Add(st)
            self.inputs[k] = tb
        main.Children.Add(grid)

        # ---- CheckBoxes de ViewType ----
        main.Children.Add(Label(Content="View Type Filters:"))
        self.viewtype_checks = {}
        cb_panel = StackPanel(
            Orientation=Orientation.Horizontal,
            Margin=Thickness(0, 0, 0, 10)
        )
        for label, vt in FILTERABLE_VIEW_TYPES.items():
            cb = CheckBox(
                Content=label,
                IsChecked=True,
                Margin=Thickness(5, 0, 10, 0)
            )
            cb.Checked   += self.on_filter_changed
            cb.Unchecked += self.on_filter_changed
            self.viewtype_checks[vt] = cb
            cb_panel.Children.Add(cb)

        # ── Show/hide master views ──
        self.show_masters_cb = CheckBox(
            Content="Master Views",
            IsChecked=True,
            Margin=Thickness(15, 0, 10, 0)
        )
        self.show_masters_cb.Checked   += self.on_filter_changed
        self.show_masters_cb.Unchecked += self.on_filter_changed
        cb_panel.Children.Add(self.show_masters_cb)

        main.Children.Add(cb_panel)

        # ---- Search ----
        main.Children.Add(Label(Content="Search views:"))
        self.search_tb = TextBox(Margin=Thickness(0, 0, 0, 5))
        self.search_tb.TextChanged += self.on_search_changed
        main.Children.Add(self.search_tb)

        # ---- ListView ----
        self.items = ObservableCollection[ViewItem]()
        self.refresh_view_list()

        self.listview = ListView(
            Height=460,
            SelectionMode=SelectionMode.Extended,
            ItemsSource=self.items
        )
        gv = GridView()
        gv.Columns.Add(GridViewColumn(
            Header="View Type",
            DisplayMemberBinding=Binding("ViewType"),
            Width=220
        ))
        gv.Columns.Add(GridViewColumn(
            Header="View Name",
            DisplayMemberBinding=Binding("ViewName"),
            Width=480
        ))
        self.listview.View = gv
        self.listview.AddHandler(
            GridViewColumnHeader.ClickEvent,
            System.Windows.RoutedEventHandler(self.on_header_click)
        )
        style = Style(ListViewItem)
        style.Setters.Add(
            Setter(ListViewItem.ForegroundProperty, Binding("Foreground"))
        )
        self.listview.ItemContainerStyle = style
        main.Children.Add(self.listview)

        # ---- Buttons ----
        btn_panel = StackPanel(
            Orientation=Orientation.Horizontal,
            HorizontalAlignment=HorizontalAlignment.Right
        )
        apply_btn  = Button(Content="Apply",  Width=90, Margin=Thickness(5))
        cancel_btn = Button(Content="Cancel", Width=90, Margin=Thickness(5))
        apply_btn.Click  += self.on_apply
        cancel_btn.Click += lambda s, e: self.Close()
        btn_panel.Children.Add(apply_btn)
        btn_panel.Children.Add(cancel_btn)
        main.Children.Add(btn_panel)

    # ----------------------------------------------------------
    def populate_items(self, views):
        self.items.Clear()
        for v in views:
            self.items.Add(ViewItem(v))

    def apply_preset(self, value):
        for tb in self.inputs.values():
            tb.Text = str(value)

    def refresh_view_list(self):
        txt = self.search_tb.Text.lower()
        show_masters = self.show_masters_cb.IsChecked
        active_types = [
            vt for vt, cb in self.viewtype_checks.items()
            if cb.IsChecked
        ]
        filtered = [v for v in all_views if v.ViewType in active_types]
        if txt:
            primaries, dependents = build_view_map(filtered)
            result = []
            for pid, pv in primaries.items():
                name_match = txt in pv.Name.lower() or txt in pv.ViewType.ToString().lower()
                dep_list   = dependents.get(pid, [])
                dep_match  = any(txt in dv.Name.lower() for dv in dep_list)
                if name_match or dep_match:
                    if show_masters:
                        result.append(pv)
                    result.extend(dep_list)
            filtered = result
        else:
            primaries, dependents = build_view_map(filtered)
            result = []
            for pid, pv in primaries.items():
                if show_masters:
                    result.append(pv)
                result.extend(dependents.get(pid, []))
            filtered = result
        self.populate_items(filtered)

    def on_search_changed(self, s, e):
        self.refresh_view_list()

    def on_filter_changed(self, s, e):
        self.refresh_view_list()

    def on_header_click(self, sender, e):
        header = e.OriginalSource
        if not isinstance(header, GridViewColumnHeader):
            return
        field = "ViewType" if header.Content == "View Type" else "ViewName"
        view  = CollectionViewSource.GetDefaultView(self.items)
        view.SortDescriptions.Clear()
        if self.last_sort == field:
            self.last_dir = (
                ListSortDirection.Descending
                if self.last_dir == ListSortDirection.Ascending
                else ListSortDirection.Ascending
            )
        else:
            self.last_dir = ListSortDirection.Ascending
        view.SortDescriptions.Add(SortDescription(field, self.last_dir))
        self.last_sort = field

    # ----------------------------------------------------------
    def on_apply(self, sender, args):
        try:
            selected = [i.View for i in self.listview.SelectedItems]
            if not selected:
                forms.alert("No views selected.")
                return

            try:
                offsets = {
                    k: inches_to_feet(float(self.inputs[k].Text))
                    for k in self.inputs
                }
            except ValueError:
                forms.alert("Please enter valid numbers in all offset fields.")
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
                            offsets["Top"],
                            offsets["Bottom"],
                            offsets["Left"],
                            offsets["Right"]
                        )
                        if ok:
                            applied += 1
                        else:
                            failed.append(v.Name)
                    except Exception as ex:
                        failed.append(u"{}: {}".format(v.Name, str(ex)))

            msg_parts = [u"DONE \u2705 Applied to {} view(s).".format(applied)]
            if skipped:
                msg_parts.append(
                    u"\nSkipped {} view(s):\n  {}".format(
                        len(skipped), u"\n  ".join(skipped[:20])
                    )
                )
            if failed:
                msg_parts.append(
                    u"\nFailed on {} view(s):\n  {}".format(
                        len(failed), u"\n  ".join(failed[:20])
                    )
                )
            forms.alert(u"\n".join(msg_parts))
            self.Close()

        except Exception as ex:
            forms.alert(str(ex), title="ERROR")

# ------------------------------------------------------------
AnnotationCropWindow().ShowDialog()