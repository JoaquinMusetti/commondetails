# -*- coding: utf-8 -*-
__title__ = 'Updates\nReport'
__doc__ = ('Compares two Sheets with Views JSON snapshots (previous vs. '
           'current) exported from the source model and builds a change '
           'report for the Production Team (Teams-ready, copy & paste).\n\n'
           'Detects:\n'
           '  - Views added / removed per sheet\n'
           '  - Views moved between sheets\n'
           '  - Views no longer used anywhere\n'
           '  - View type changes (e.g. Section -> FloorPlan)\n'
           '  - Detail number changes on views that stayed on the same sheet')

import sys
import json
import os
import re
from datetime import datetime
from pyrevit import script, forms

sys.path.append(os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
    'lib'
))
from magictools import ui

import clr
clr.AddReference('PresentationCore')
clr.AddReference('System.Windows.Forms')
from System.Windows import Visibility
from System.Windows.Forms import (Clipboard as WinFormsClipboard,
                                   SaveFileDialog, DialogResult)
from System.Collections.ObjectModel import ObservableCollection

output = script.get_output()
output.close()

# ─────────────────────────────────────────────────────────────────────────────
# Row class — PascalCase properties for WPF binding
# ─────────────────────────────────────────────────────────────────────────────

class _Row(object):
    """Generic row: Icon | Col1 | Col2 | Detail"""
    def __init__(self, icon, col1, col2=u"", detail=u""):
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
# 1. Pick the two JSON snapshots
# ─────────────────────────────────────────────────────────────────────────────

prev_path = forms.pick_file(
    file_ext="json",
    title="Select PREVIOUS Sheets with Views JSON (old snapshot)"
)
if not prev_path:
    script.exit()

curr_path = forms.pick_file(
    file_ext="json",
    title="Select CURRENT Sheets with Views JSON (new snapshot)"
)
if not curr_path:
    script.exit()

with open(prev_path, "r") as f:
    prev_data = json.load(f)
with open(curr_path, "r") as f:
    curr_data = json.load(f)


def _validate_format(d, label):
    if d.get("format") != "sheets_with_views":
        ui.alert(
            u"Wrong format for the {} snapshot.\n\n"
            u"Updates Report expects JSONs exported by 'Export Sheets with "
            u"Views' (format: sheets_with_views). The file you picked has "
            u"format: \"{}\".\n\nFor the legacy sheets_layout.json format, use "
            u"the matching tool in the Legacy pulldown.".format(
                label, d.get("format", "unknown")),
            title=u"Updates Report"
        )
        script.exit()


_validate_format(prev_data, "PREVIOUS")
_validate_format(curr_data, "CURRENT")

prev_layout = prev_data.get("sheets", [])
curr_layout = curr_data.get("sheets", [])


def _format_date(dt):
    months = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    return u"{} {} {}".format(dt.day, months[dt.month - 1], dt.year)


def _extract_date(path):
    name = os.path.basename(path)
    m = re.match(r"(\d{8})", name)
    if m:
        try:
            return _format_date(datetime.strptime(m.group(1), "%Y%m%d"))
        except Exception:
            pass
    try:
        ts = os.path.getmtime(path)
        return _format_date(datetime.fromtimestamp(ts))
    except Exception:
        return u"unknown"


TODAY_LABEL = _format_date(datetime.today())
prev_date   = _extract_date(prev_path)
curr_date   = _extract_date(curr_path)

# ─────────────────────────────────────────────────────────────────────────────
# 2. Index both snapshots
# ─────────────────────────────────────────────────────────────────────────────

def build_index(layout):
    sheets   = {}
    view_loc = {}
    for s in layout:
        sn   = s["sheet_number"]
        name = s.get("sheet_name", u"")
        views = {}
        for vp in s.get("viewports", []):
            vname = vp.get("view_name", u"")
            if not vname:
                continue
            views[vname] = {
                "detail_number": vp.get("detail_number", u""),
                "view_type":     vp.get("view_type", u""),
                "viewport_type": vp.get("viewport_type", u""),
            }
            view_loc.setdefault(vname, []).append(sn)
        sheets[sn] = {"sheet_name": name, "views": views}
    return sheets, view_loc


prev_sheets, prev_view_loc = build_index(prev_layout)
curr_sheets, curr_view_loc = build_index(curr_layout)

# ─────────────────────────────────────────────────────────────────────────────
# 3. Compute per-sheet diffs
# ─────────────────────────────────────────────────────────────────────────────

all_sheet_numbers = sorted(set(list(prev_sheets.keys()) + list(curr_sheets.keys())))

sheet_reports         = []
sheets_new            = []
sheets_removed        = []
removed_view_to_sheet = {}
added_view_to_sheet   = {}

for sn in all_sheet_numbers:
    prev_sheet = prev_sheets.get(sn)
    curr_sheet = curr_sheets.get(sn)

    if prev_sheet is None and curr_sheet is not None:
        sheets_new.append(sn)
        for vname in curr_sheet["views"].keys():
            added_view_to_sheet.setdefault(vname, []).append(sn)
        continue
    if curr_sheet is None and prev_sheet is not None:
        sheets_removed.append(sn)
        for vname in prev_sheet["views"].keys():
            removed_view_to_sheet.setdefault(vname, []).append(sn)
        continue

    prev_views = set(prev_sheet["views"].keys())
    curr_views = set(curr_sheet["views"].keys())

    added   = sorted(curr_views - prev_views)
    removed = sorted(prev_views - curr_views)
    stayed  = curr_views & prev_views

    for vname in added:
        added_view_to_sheet.setdefault(vname, []).append(sn)
    for vname in removed:
        removed_view_to_sheet.setdefault(vname, []).append(sn)

    view_type_changes = []
    detail_changes    = []
    for vname in sorted(stayed):
        p = prev_sheet["views"][vname]
        c = curr_sheet["views"][vname]
        if (p.get("view_type", u"") != c.get("view_type", u"")
                and p.get("view_type") and c.get("view_type")):
            view_type_changes.append((vname, p["view_type"], c["view_type"]))
        if p.get("detail_number", u"") != c.get("detail_number", u""):
            detail_changes.append(
                (vname, p.get("detail_number", u""), c.get("detail_number", u"")))

    has_changes = bool(added or removed or view_type_changes or detail_changes) or \
                  prev_sheet["sheet_name"] != curr_sheet["sheet_name"]

    sheet_reports.append({
        "sheet_number":      sn,
        "prev_name":         prev_sheet["sheet_name"],
        "curr_name":         curr_sheet["sheet_name"],
        "added":             added,
        "removed":           removed,
        "view_type_changes": view_type_changes,
        "detail_changes":    detail_changes,
        "changed":           has_changes,
    })

# Resolve moves
moves = []
moved_view_names = set()
for vname, removed_from in removed_view_to_sheet.items():
    if vname in added_view_to_sheet:
        added_to = added_view_to_sheet[vname]
        for i, rs in enumerate(removed_from):
            if i < len(added_to):
                moves.append((vname, rs, added_to[i]))
                moved_view_names.add(vname)

views_removed_entirely = sorted(
    [v for v in prev_view_loc.keys() if v not in curr_view_loc])
views_added_entirely   = sorted(
    [v for v in curr_view_loc.keys() if v not in prev_view_loc])

move_pairs = {}
for vname, frm, to in moves:
    move_pairs.setdefault((frm, to), []).append(vname)


def is_moved_in(vname, sheet_number):
    for mv, frm, to in moves:
        if mv == vname and to == sheet_number:
            return frm
    return None


def is_moved_out(vname, sheet_number):
    for mv, frm, to in moves:
        if mv == vname and frm == sheet_number:
            return to
    return None


for r in sheet_reports:
    sn = r["sheet_number"]
    real_added   = []
    real_removed = []
    moves_in     = {}
    moves_out    = {}
    for v in r["added"]:
        src = is_moved_in(v, sn)
        if src:
            moves_in.setdefault(src, []).append(v)
        else:
            real_added.append(v)
    for v in r["removed"]:
        dst = is_moved_out(v, sn)
        if dst:
            moves_out.setdefault(dst, []).append(v)
        else:
            real_removed.append(v)
    r["real_added"]   = real_added
    r["real_removed"] = real_removed
    r["moves_in"]     = moves_in
    r["moves_out"]    = moves_out
    r["renumbering_only"] = (
        not real_added and not real_removed
        and not moves_in and not moves_out
        and not r["view_type_changes"]
        and r["prev_name"] == r["curr_name"]
        and bool(r["detail_changes"])
    )
    r["detail_noise"] = bool(
        moves_in or moves_out or real_added or real_removed) and bool(r["detail_changes"])

changed_sheets    = [r for r in sheet_reports if r["changed"]]
unchanged_sheets  = [r for r in sheet_reports if not r["changed"]]
substantive_sheets = [r for r in changed_sheets if not r["renumbering_only"]]
renumbering_sheets = [r for r in changed_sheets if r["renumbering_only"]]

# ─────────────────────────────────────────────────────────────────────────────
# 4. Build per-sheet change data for inline XAML generation
# ─────────────────────────────────────────────────────────────────────────────

def _xesc(text):
    """XML/XAML escape for baking data into XAML strings."""
    if text is None:
        return u""
    return (unicode(text)
            .replace(u"&", u"&amp;")
            .replace(u"<", u"&lt;")
            .replace(u">", u"&gt;")
            .replace(u'"', u"&quot;"))


# sheet_change_data  — structural changes (adds, removes, moves, type changes, renames)
# renum_change_data  — renumbering-only sheets (detail number changes, cosmetic)
# Each entry: (sheet_number, curr_name, changes_list, badges_list)
#   changes_list: [(icon, view_name, detail_text, fg_color), ...]
#   badges_list:  [(label, fg, bg, border), ...]

sheet_change_data = []
renum_change_data = []

for r in [r for r in sheet_reports if r["changed"]]:
    sn        = r["sheet_number"]
    curr_name = r["curr_name"]
    changes   = []

    # Sheet rename
    if r["prev_name"] != r["curr_name"]:
        changes.append((u"✏", u"Sheet renamed",
                        u"{} → {}".format(r["prev_name"], r["curr_name"]),
                        u"#9099C8"))

    # Views moved in from another sheet
    for src in sorted(r["moves_in"].keys()):
        for v in sorted(r["moves_in"][src]):
            changes.append((u"↔", v,
                            u"moved in from {}".format(src),
                            u"#5B8EC4"))

    # Views moved out to another sheet
    for dst in sorted(r["moves_out"].keys()):
        for v in sorted(r["moves_out"][dst]):
            changes.append((u"↔", v,
                            u"moved out to {}".format(dst),
                            u"#7EB4F0"))

    # Real adds and removes
    for v in r["real_added"]:
        changes.append((u"➕", v, u"added", u"#50E898"))
    for v in r["real_removed"]:
        changes.append((u"➖", v, u"removed", u"#FF7070"))

    # View type changes
    for vname, old_t, new_t in r["view_type_changes"]:
        changes.append((u"🔄", vname,
                        u"{} → {}".format(old_t, new_t),
                        u"#E87E20"))

    # Detail number changes (shown only for renumbering-only sheets)
    if r["renumbering_only"]:
        for vname, old_det, new_det in r["detail_changes"]:
            changes.append((u"🔢", vname,
                            u"det# {} → {}".format(old_det, new_det),
                            u"#5B8EC4"))

    # Build header badges
    n_added   = len(r["real_added"])
    n_removed = len(r["real_removed"])
    n_moves   = (sum(len(v) for v in r["moves_in"].values()) +
                 sum(len(v) for v in r["moves_out"].values()))
    n_type    = len(r["view_type_changes"])
    n_renum   = len(r["detail_changes"]) if r["renumbering_only"] else 0
    renamed   = r["prev_name"] != r["curr_name"]

    badges = []
    if renamed:
        badges.append((u"✏ renamed", u"#9099C8", u"#1A1D30", u"#9099C8"))
    if n_added:
        badges.append((u"+{}".format(n_added),  u"#50E898", u"#122E1C", u"#50E898"))
    if n_removed:
        badges.append((u"−{}".format(n_removed), u"#FF7070", u"#3C1212", u"#FF7070"))
    if n_moves:
        badges.append((u"↔ {}".format(n_moves),  u"#5B8EC4", u"#1A2535", u"#5B8EC4"))
    if n_type:
        badges.append((u"🔄 {}".format(n_type),  u"#E87E20", u"#2A1C0E", u"#E87E20"))
    if n_renum:
        badges.append((u"🔢 {}".format(n_renum), u"#5B8EC4", u"#1A2535", u"#5B8EC4"))

    target = renum_change_data if r["renumbering_only"] else sheet_change_data
    target.append((sn, curr_name, changes, badges))

# Static section OCs — global data that isn't per-sheet
new_sheets_oc = ObservableCollection[object]()
rem_sheets_oc = ObservableCollection[object]()
new_views_oc  = ObservableCollection[object]()
gone_views_oc = ObservableCollection[object]()

for sn in sheets_new:
    new_sheets_oc.Add(_Row(u"🆕", sn, curr_sheets[sn]["sheet_name"]))
for sn in sheets_removed:
    rem_sheets_oc.Add(_Row(u"🗑", sn, prev_sheets[sn]["sheet_name"]))
for v in views_added_entirely:
    new_views_oc.Add(_Row(u"✨", v))
for v in views_removed_entirely:
    gone_views_oc.Add(_Row(u"🧹", v))

# ─────────────────────────────────────────────────────────────────────────────
# 5. Build Teams-ready text (for clipboard)
# ─────────────────────────────────────────────────────────────────────────────

headline_bits = []
if sheets_new:             headline_bits.append(u"{} new sheet(s)".format(len(sheets_new)))
if sheets_removed:         headline_bits.append(u"{} removed".format(len(sheets_removed)))
if substantive_sheets:     headline_bits.append(u"{} sheet(s) with content changes".format(len(substantive_sheets)))
if views_added_entirely:   headline_bits.append(u"{} new view(s) placed".format(len(views_added_entirely)))
if moves:                  headline_bits.append(u"{} view(s) moved".format(len(moves)))
if views_removed_entirely: headline_bits.append(u"{} view(s) no longer used".format(len(views_removed_entirely)))
if renumbering_sheets:     headline_bits.append(u"{} sheet(s) with detail renumbering only".format(len(renumbering_sheets)))
if not headline_bits:      headline_bits.append(u"no changes")

teams_lines = []
teams_lines.append(u"**Detail Sheets Update Report — {}**".format(TODAY_LABEL))
teams_lines.append(u"_Previous: {}  •  Current: {}_".format(prev_date, curr_date))
teams_lines.append(u"")
teams_lines.append(u"**Summary:** {}".format(u"  •  ".join(headline_bits)))
teams_lines.append(u"")

if sheets_new:
    teams_lines.append(u"**🆕 New sheets**")
    for sn in sheets_new:
        teams_lines.append(u"- {} — {}".format(sn, curr_sheets[sn]["sheet_name"]))
    teams_lines.append(u"")

if sheets_removed:
    teams_lines.append(u"**🗑️ Removed sheets**")
    for sn in sheets_removed:
        teams_lines.append(u"- {} — {}".format(sn, prev_sheets[sn]["sheet_name"]))
    teams_lines.append(u"")

if move_pairs:
    teams_lines.append(u"**↔️ Moves ({} views across {} sheet pair{})**".format(
        len(moves), len(move_pairs), u"" if len(move_pairs) == 1 else u"s"))
    for (frm, to), vs in sorted(move_pairs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        teams_lines.append(u"- **{} → {}** ({} view{})".format(
            frm, to, len(vs), u"" if len(vs) == 1 else u"s"))
        for v in sorted(vs):
            teams_lines.append(u"    - {}".format(v))
    teams_lines.append(u"")

renames   = [(r["sheet_number"], r["prev_name"], r["curr_name"])
             for r in substantive_sheets if r["prev_name"] != r["curr_name"]]
real_adds = [(r["sheet_number"], r["curr_name"], r["real_added"])
             for r in substantive_sheets if r["real_added"]]
real_rems = [(r["sheet_number"], r["curr_name"], r["real_removed"])
             for r in substantive_sheets if r["real_removed"]]
vtc_all   = [(r["sheet_number"], vtc) for r in substantive_sheets
             for vtc in r["view_type_changes"]]

if renames or real_adds or real_rems or vtc_all:
    teams_lines.append(u"**➕➖ Content changes**")
    for sn, old, new in renames:
        teams_lines.append(u"- {}: sheet renamed  {} → {}".format(sn, old, new))
    for sn, name, vs in real_adds:
        teams_lines.append(u"- {} ({}) — added:".format(sn, name))
        for v in vs:
            teams_lines.append(u"    + {}".format(v))
    for sn, name, vs in real_rems:
        teams_lines.append(u"- {} ({}) — removed:".format(sn, name))
        for v in vs:
            teams_lines.append(u"    − {}".format(v))
    for sn, (vname, old, new) in vtc_all:
        teams_lines.append(u"- {}: view type changed — {} ({} → {})".format(
            sn, vname, old, new))
    teams_lines.append(u"")

if views_added_entirely:
    teams_lines.append(u"**✨ New views placed**")
    for v in views_added_entirely:
        teams_lines.append(u"- {}".format(v))
    teams_lines.append(u"")

if views_removed_entirely:
    teams_lines.append(u"**🧹 Views no longer used on any sheet**")
    for v in views_removed_entirely:
        teams_lines.append(u"- {}".format(v))
    teams_lines.append(u"")

if renumbering_sheets:
    teams_lines.append(u"**🔢 Detail-number renumbering only** _(cosmetic)_")
    for r in renumbering_sheets:
        n = len(r["detail_changes"])
        teams_lines.append(u"- {} — {} ({} view{})".format(
            r["sheet_number"], r["curr_name"], n, u"" if n == 1 else u"s"))
    teams_lines.append(u"")

if not (sheets_new or sheets_removed or move_pairs or real_adds or real_rems
        or vtc_all or renames or views_added_entirely
        or views_removed_entirely or renumbering_sheets):
    teams_lines.append(u"_No changes detected between the two snapshots._")

def _strip_md(txt):
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", txt)
    out = re.sub(r"_(.+?)_", r"\1", out)
    return out

teams_plain = _strip_md(u"\r\n".join(teams_lines).rstrip())

subtitle = u"Previous: {}  ·  Current: {}  ·  {} unchanged".format(
    prev_date, curr_date, len(unchanged_sheets))

# ─────────────────────────────────────────────────────────────────────────────
# 6. Inline XAML builders (data baked in — no bindings needed per sheet)
# ─────────────────────────────────────────────────────────────────────────────

def _build_sheet_expanders(data_list, expanded=True):
    """Generates XAML Expander elements for a list of (sn, name, changes, badges)."""
    out = u""
    for sn, name, changes, badges in data_list:
        # Mini badge chips in the header
        badges_xaml = u""
        for label, fg, bg, border in badges:
            badges_xaml += (
                u'<Border Background="{bg}" BorderBrush="{bd}" BorderThickness="1" '
                u'CornerRadius="3" Padding="5,1" Margin="0,0,4,0">'
                u'<TextBlock Text="{lbl}" Foreground="{fg}" '
                u'FontFamily="Consolas" FontSize="11"/>'
                u'</Border>').format(
                    bg=bg, bd=border, fg=fg, lbl=_xesc(label))

        header_xaml = (
            u'<StackPanel Orientation="Horizontal">'
            u'<TextBlock Text="{sn}" Foreground="#9099C8" FontFamily="Consolas" '
            u'FontSize="12" VerticalAlignment="Center" Margin="0,0,12,0" Width="70"/>'
            u'<TextBlock Text="{name}" Foreground="#E8EBF5" FontFamily="Segoe UI" '
            u'FontSize="13" VerticalAlignment="Center" Margin="0,0,16,0"/>'
            u'{badges}'
            u'</StackPanel>').format(
                sn=_xesc(sn), name=_xesc(name), badges=badges_xaml)

        # Change rows — data baked in as TextBlock content
        rows_xaml = u""
        for icon, view_name, detail, color in changes:
            rows_xaml += (
                u'<Border Background="#12131F" BorderBrush="#2A2D47" '
                u'BorderThickness="0,0,0,1" Padding="28,0,16,0">'
                u'<Grid MinHeight="26">'
                u'<Grid.ColumnDefinitions>'
                u'<ColumnDefinition Width="22"/>'
                u'<ColumnDefinition Width="*"/>'
                u'<ColumnDefinition Width="220"/>'
                u'</Grid.ColumnDefinitions>'
                u'<TextBlock Grid.Column="0" Text="{icon}" FontSize="12" '
                u'VerticalAlignment="Center"/>'
                u'<TextBlock Grid.Column="1" Text="{view}" Foreground="#E8EBF5" '
                u'FontFamily="Segoe UI" FontSize="12" VerticalAlignment="Center" '
                u'Margin="0,0,8,0" TextTrimming="CharacterEllipsis"/>'
                u'<TextBlock Grid.Column="2" Text="{det}" Foreground="{color}" '
                u'FontFamily="Segoe UI" FontSize="12" VerticalAlignment="Center" '
                u'TextTrimming="CharacterEllipsis"/>'
                u'</Grid>'
                u'</Border>').format(
                    icon=_xesc(icon), view=_xesc(view_name),
                    det=_xesc(detail), color=color)

        out += (
            u'<Expander IsExpanded="{expanded}" Margin="0,0,0,2">'
            u'<Expander.Header>{header}</Expander.Header>'
            u'<StackPanel>{rows}</StackPanel>'
            u'</Expander>').format(
                expanded=u"True" if expanded else u"False",
                header=header_xaml, rows=rows_xaml)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 7. HTML report builder (for Export PDF button — sheet-based)
# ─────────────────────────────────────────────────────────────────────────────

def _esc(text):
    """Minimal HTML escaping for text nodes."""
    if text is None:
        return u""
    return (unicode(text)
            .replace(u"&", u"&amp;")
            .replace(u"<", u"&lt;")
            .replace(u">", u"&gt;"))


def build_html_report():
    """Sheet-based HTML report; open in browser and Ctrl+P → Save as PDF."""
    parts = []

    def badge_html(label, color):
        return (u'<span style="display:inline-block;background:#F0F0FA;'
                u'border:1px solid {c};color:{c};border-radius:3px;'
                u'padding:1px 7px;font-size:0.78em;margin-left:6px;'
                u'font-family:Consolas,monospace">{l}</span>').format(
                    c=color, l=_esc(label))

    def sheet_section(data_list, section_title, section_color):
        """Render per-sheet expanders: sheet header + change rows table."""
        if not data_list:
            return u""
        out = (u'<div style="margin-bottom:0.5em;padding:6px 0 4px;'
               u'border-bottom:2px solid {c};font-size:0.95em;font-weight:700;'
               u'color:#1E2235;margin-top:1.4em">{title}</div>').format(
                   c=section_color, title=_esc(section_title))
        for sn, name, changes, badges in data_list:
            badge_items = u""
            for label, fg, _bg, _bd in badges:
                badge_items += badge_html(label, fg)
            out += (
                u'<div style="margin:0.8em 0 0;padding:5px 0 5px 6px;'
                u'border-left:3px solid #9013FE;page-break-inside:avoid">'
                u'<span style="font-weight:700;color:#1E2235;font-size:0.91em">'
                u'{sn}</span>'
                u'<span style="color:#444;font-size:0.91em;margin-left:8px">'
                u'{name}</span>'
                u'{badges}'
                u'</div>').format(
                    sn=_esc(sn), name=_esc(name), badges=badge_items)
            if changes:
                rows_html = u""
                for i, (icon, view_name, detail, color) in enumerate(changes):
                    bg = u"#FAFAFA" if i % 2 == 0 else u"#FFFFFF"
                    rows_html += (
                        u'<tr style="background:{bg}">'
                        u'<td style="width:22px;padding:4px 6px;'
                        u'font-size:0.88em">{icon}</td>'
                        u'<td style="padding:4px 8px;font-size:0.88em;'
                        u'color:#1E2235;max-width:340px;overflow:hidden;'
                        u'text-overflow:ellipsis;white-space:nowrap">{view}</td>'
                        u'<td style="padding:4px 8px;font-size:0.88em;'
                        u'color:{color};white-space:nowrap">{detail}</td>'
                        u'</tr>').format(
                            bg=bg, icon=icon,
                            view=_esc(view_name), detail=_esc(detail),
                            color=color)
                out += (
                    u'<table style="border-collapse:collapse;width:100%;'
                    u'margin-left:16px;margin-bottom:6px">'
                    u'<tbody>{}</tbody></table>').format(rows_html)
        return out

    parts.append(sheet_section(
        sheet_change_data, u"Sheet content changes", u"#9013FE"))
    parts.append(sheet_section(
        renum_change_data,
        u"Detail-number renumbering only (cosmetic)", u"#5B8EC4"))

    def simple_section(title, color, items, c1_attr, c2_attr):
        if not items:
            return u""
        rows_html = u""
        for i, r in enumerate(items):
            bg = u"#FAFAFA" if i % 2 == 0 else u"#FFFFFF"
            rows_html += (
                u'<tr style="background:{bg}">'
                u'<td style="width:22px;padding:4px 6px;font-size:0.88em">'
                u'{icon}</td>'
                u'<td style="padding:4px 8px;font-size:0.88em;color:#1E2235">'
                u'{c1}</td>'
                u'<td style="padding:4px 8px;font-size:0.88em;color:#555">'
                u'{c2}</td>'
                u'</tr>').format(
                    bg=bg,
                    icon=getattr(r, "Icon", u""),
                    c1=_esc(getattr(r, c1_attr, u"")),
                    c2=_esc(getattr(r, c2_attr, u"")))
        return (
            u'<div style="margin-bottom:0.5em;padding:6px 0 4px;'
            u'border-bottom:2px solid {c};font-size:0.95em;font-weight:700;'
            u'color:#1E2235;margin-top:1.4em">{title}</div>'
            u'<table style="border-collapse:collapse;width:100%;'
            u'margin-bottom:1em">'
            u'<tbody>{rows}</tbody></table>').format(
                c=color, title=_esc(title), rows=rows_html)

    parts.append(simple_section(
        u"New sheets", u"#27AE60", list(new_sheets_oc), "Col1", "Col2"))
    parts.append(simple_section(
        u"Removed sheets", u"#E74C3C", list(rem_sheets_oc), "Col1", "Col2"))
    parts.append(simple_section(
        u"New views (not on any sheet before)", u"#27AE60",
        list(new_views_oc), "Col1", "Col2"))
    parts.append(simple_section(
        u"Views no longer used on any sheet", u"#9099C8",
        list(gone_views_oc), "Col1", "Col2"))

    body_parts = [p for p in parts if p]
    body_html = (
        u'<p style="color:#27AE60;font-size:1em">&#x2705; No changes detected '
        u'between the two snapshots.</p>'
        if not body_parts else u"".join(body_parts))

    summary_html = u" &nbsp;•&nbsp; ".join(_esc(b) for b in headline_bits)

    return u"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Updates Report</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 2.2cm 2.5cm;
         color: #1E2235; font-size: 13px; line-height: 1.5; }}
  h1 {{ font-size: 1.35em; font-weight: 700; color: #1E2235;
       border-bottom: 3px solid #9013FE; padding-bottom: 0.35em;
       margin-bottom: 0.25em; }}
  .meta {{ color: #666; font-size: 0.87em; margin-bottom: 1em; }}
  .summary {{ background: #F5F3FC; border-left: 4px solid #9013FE;
              padding: 0.55em 1em; margin-bottom: 1.8em;
              font-size: 0.92em; color: #333; }}
  @media print {{
    body {{ margin: 1.5cm; }}
    .no-print {{ display: none; }}
    a {{ text-decoration: none; color: inherit; }}
    @page {{ margin: 1.5cm; }}
  }}
</style>
</head>
<body>
  <div class="no-print" style="background:#1E2235;color:#E8EBF5;
       padding:10px 16px;margin-bottom:1.5em;border-radius:4px;font-size:0.9em">
    &#x1F4BE; File saved.
    &nbsp;&nbsp;&#x1F5A8; To save as PDF: <strong>Ctrl+P</strong>
    &rarr; choose <em>Save as PDF</em> (or Microsoft Print to PDF).
  </div>
  <h1>Detail Sheets Update Report</h1>
  <div class="meta">
    <strong>Previous:</strong> {prev_date}
    &nbsp;&nbsp;&#x2022;&nbsp;&nbsp;
    <strong>Current:</strong> {curr_date}
    &nbsp;&nbsp;&#x2022;&nbsp;&nbsp;
    Generated: {today}
    &nbsp;&nbsp;&#x2022;&nbsp;&nbsp;
    {unchanged} sheet(s) unchanged
  </div>
  <div class="summary"><strong>Summary:</strong> {summary}</div>
  {body}
  <hr style="border:none;border-top:1px solid #DDD;margin:2em 0 0.8em">
  <div style="font-size:0.75em;color:#AAA;text-align:right">
    Common Details &mdash; Updates Report
  </div>
</body>
</html>""".format(
        prev_date=_esc(prev_date),
        curr_date=_esc(curr_date),
        today=_esc(TODAY_LABEL),
        unchanged=len(unchanged_sheets),
        summary=summary_html,
        body=body_html)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Build WPF window — XAML (sheet-based, per-sheet content baked in)
# ─────────────────────────────────────────────────────────────────────────────

_BODY_PREFIX = u"""
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

    <!-- Row template (Icon | Col1 | Col2) — used by static OC sections -->
    <DataTemplate x:Key="wideRowTpl">
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
            <ColumnDefinition Width="220"/>
          </Grid.ColumnDefinitions>
          <TextBlock Grid.Column="0" Text="{Binding Icon}" FontSize="12"
                     VerticalAlignment="Center"/>
          <TextBlock Grid.Column="1" Text="{Binding Col1}" Foreground="#E8EBF5"
                     FontFamily="Segoe UI" FontSize="12"
                     VerticalAlignment="Center" Margin="0,0,8,0"
                     TextTrimming="CharacterEllipsis"/>
          <TextBlock Grid.Column="2" Text="{Binding Col2}" Foreground="#9099C8"
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
    <Border x:Name="badgeNoChangeBorder" Background="#122E1C" BorderBrush="#50E898"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeNoChange" Foreground="#50E898"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeChangedBorder" Background="#2A1C0E" BorderBrush="#E87E20"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeChanged" Foreground="#E87E20"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeRenumBorder" Background="#1A2535" BorderBrush="#5B8EC4"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeRenum" Foreground="#5B8EC4"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeNewSheetsBorder" Background="#122E1C" BorderBrush="#50E898"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeNewSheets" Foreground="#50E898"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeRemSheetsBorder" Background="#3C1212" BorderBrush="#FF7070"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeRemSheets" Foreground="#FF7070"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeNewViewsBorder" Background="#122E1C" BorderBrush="#50E898"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeNewViews" Foreground="#50E898"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
    <Border x:Name="badgeGoneViewsBorder" Background="#1A1D30" BorderBrush="#9099C8"
            BorderThickness="1" CornerRadius="4" Padding="10,4" Margin="0,0,8,0"
            Visibility="Collapsed">
      <TextBlock x:Name="badgeGoneViews" Foreground="#9099C8"
                 FontFamily="Segoe UI" FontSize="13"/>
    </Border>
  </StackPanel>

  <!-- Scrollable body -->
  <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto">
    <StackPanel>

      <TextBlock x:Name="lblNoChanges" FontFamily="Segoe UI" FontSize="14"
                 Foreground="#50E898" TextWrapping="Wrap"
                 Margin="4,12,0,0" Visibility="Collapsed">
        &#x2705;  No changes detected between the two snapshots.
      </TextBlock>
"""

# Per-sheet expanders: structural changes (expanded) + renumbering (collapsed outer)
_sheet_expanders_xaml = _build_sheet_expanders(sheet_change_data, expanded=True)
_renum_inner_xaml     = _build_sheet_expanders(renum_change_data, expanded=False)
_renum_outer_xaml     = u""
if renum_change_data:
    _renum_outer_xaml = (
        u'<Expander IsExpanded="False" Margin="0,8,0,2">'
        u'<Expander.Header>'
        u'<StackPanel Orientation="Horizontal">'
        u'<Border Background="#1A2535" BorderBrush="#5B8EC4" BorderThickness="1"'
        u' CornerRadius="4" Padding="7,2" Margin="0,0,10,0">'
        u'<TextBlock Text=" {n} " Foreground="#5B8EC4"'
        u' FontFamily="Consolas" FontSize="12"/>'
        u'</Border>'
        u'<TextBlock Text="&#x1F522;  Detail-number renumbering only (cosmetic)"'
        u' Foreground="#E8EBF5" FontFamily="Segoe UI" FontSize="13"'
        u' VerticalAlignment="Center"/>'
        u'</StackPanel>'
        u'</Expander.Header>'
        u'<StackPanel>' + _renum_inner_xaml + u'</StackPanel>'
        u'</Expander>').format(n=len(renum_change_data))

_BODY_SUFFIX = u"""
      <!-- New sheets -->
      <Expander x:Name="expNewSheets" IsExpanded="True" Visibility="Collapsed"
                Margin="0,8,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#122E1C" BorderBrush="#50E898" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrNewSheetsCount" Foreground="#50E898"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x1F195;  New sheets" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icNewSheets" ItemTemplate="{StaticResource wideRowTpl}"/>
      </Expander>

      <!-- Removed sheets -->
      <Expander x:Name="expRemSheets" IsExpanded="True" Visibility="Collapsed"
                Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#3C1212" BorderBrush="#FF7070" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrRemSheetsCount" Foreground="#FF7070"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x1F5D1;  Removed sheets" Foreground="#E8EBF5"
                       FontFamily="Segoe UI" FontSize="13" VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icRemSheets" ItemTemplate="{StaticResource wideRowTpl}"/>
      </Expander>

      <!-- New views (brand new, never on a sheet before) -->
      <Expander x:Name="expNewViews" IsExpanded="False" Visibility="Collapsed"
                Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#122E1C" BorderBrush="#50E898" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrNewViewsCount" Foreground="#50E898"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x2728;  New views (not on any sheet before)"
                       Foreground="#E8EBF5" FontFamily="Segoe UI" FontSize="13"
                       VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icNewViews" ItemTemplate="{StaticResource wideRowTpl}"/>
      </Expander>

      <!-- Retired views (no longer on any sheet) -->
      <Expander x:Name="expGoneViews" IsExpanded="False" Visibility="Collapsed"
                Margin="0,0,0,2">
        <Expander.Header>
          <StackPanel Orientation="Horizontal">
            <Border Background="#1A1D30" BorderBrush="#9099C8" BorderThickness="1"
                    CornerRadius="4" Padding="7,2" Margin="0,0,10,0">
              <TextBlock x:Name="hdrGoneViewsCount" Foreground="#9099C8"
                         FontFamily="Consolas" FontSize="12"/>
            </Border>
            <TextBlock Text="&#x1F9F9;  Views no longer used on any sheet"
                       Foreground="#E8EBF5" FontFamily="Segoe UI" FontSize="13"
                       VerticalAlignment="Center"/>
          </StackPanel>
        </Expander.Header>
        <ItemsControl x:Name="icGoneViews" ItemTemplate="{StaticResource wideRowTpl}"/>
      </Expander>

    </StackPanel>
  </ScrollViewer>
</Grid>
"""

_BODY_XAML = _BODY_PREFIX + _sheet_expanders_xaml + _renum_outer_xaml + _BODY_SUFFIX

_FOOTER_XAML = u"""
<Grid>
  <StackPanel HorizontalAlignment="Left" Orientation="Horizontal">
    <Button x:Name="btnCopy" Content="Copy Teams message" Style="{StaticResource BtnGhost}"/>
  </StackPanel>
  <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
    <Button x:Name="btnExportPdf" Content="Export PDF&#x2026;"
            Style="{StaticResource BtnGhost}" Margin="0,0,8,0"/>
    <Button x:Name="btnClose" Content="Close" Style="{StaticResource BtnPrimary}"/>
  </StackPanel>
</Grid>
"""

win = ui.parse(
    u"Updates Report",
    subtitle,
    _BODY_XAML,
    _FOOTER_XAML,
    width=1020,
    height=660,
    context=u"Diff of two 'Sheets with Views' JSON snapshots. Only the sheets "
            u"section is compared (master_views is not used here). Use 'Copy "
            u"Teams message' to copy a formatted summary ready to paste into "
            u"Teams or email. New/retired-views and renumbering sections are "
            u"collapsed by default — expand them if needed."
)

# ─── Wire badges and sections ─────────────────────────────────────────────────

no_changes = not (sheet_change_data or renum_change_data or
                  new_sheets_oc.Count or rem_sheets_oc.Count or
                  new_views_oc.Count or gone_views_oc.Count)

if no_changes:
    win.FindName("badgeNoChangeBorder").Visibility = Visibility.Visible
    win.FindName("badgeNoChange").Text = u"✅  no changes"
    win.FindName("lblNoChanges").Visibility = Visibility.Visible


def _show_badge(border_name, txt_name, text):
    win.FindName(border_name).Visibility = Visibility.Visible
    win.FindName(txt_name).Text = text


# Per-sheet structural changes (content baked into XAML — just show the badge)
if sheet_change_data:
    n = len(sheet_change_data)
    _show_badge("badgeChangedBorder", "badgeChanged",
                u"✏  {} sheet{}".format(n, u"s" if n != 1 else u""))

# Renumbering-only (content baked into outer expander — just show the badge)
if renum_change_data:
    n = len(renum_change_data)
    _show_badge("badgeRenumBorder", "badgeRenum",
                u"🔢  {} renum".format(n))


def _wire_oc(oc, badge_border, badge_txt, badge_val,
             exp_name, hdr_name, ic_name):
    """Show badge + expander and bind OC to ItemsControl."""
    if oc.Count == 0:
        return
    _show_badge(badge_border, badge_txt, badge_val)
    win.FindName(exp_name).Visibility = Visibility.Visible
    win.FindName(hdr_name).Text = u" {} ".format(oc.Count)
    win.FindName(ic_name).ItemsSource = oc


_wire_oc(new_sheets_oc, "badgeNewSheetsBorder", "badgeNewSheets",
         u"🆕  {} new".format(new_sheets_oc.Count),
         "expNewSheets", "hdrNewSheetsCount", "icNewSheets")
_wire_oc(rem_sheets_oc, "badgeRemSheetsBorder", "badgeRemSheets",
         u"🗑  {} removed".format(rem_sheets_oc.Count),
         "expRemSheets", "hdrRemSheetsCount", "icRemSheets")
_wire_oc(new_views_oc,  "badgeNewViewsBorder",  "badgeNewViews",
         u"✨  {} new views".format(new_views_oc.Count),
         "expNewViews",  "hdrNewViewsCount",  "icNewViews")
_wire_oc(gone_views_oc, "badgeGoneViewsBorder", "badgeGoneViews",
         u"🧹  {} retired".format(gone_views_oc.Count),
         "expGoneViews", "hdrGoneViewsCount", "icGoneViews")

# ─── Copy handler ─────────────────────────────────────────────────────────────

def on_copy(s, e):
    try:
        WinFormsClipboard.SetText(teams_plain)
    except Exception:
        pass
    btn = win.FindName("btnCopy")
    btn.Content = u"Copied ✓"


def on_export_pdf(s, e):
    dlg = SaveFileDialog()
    dlg.Title  = u"Export Updates Report"
    dlg.Filter = u"HTML files (*.html)|*.html"
    dlg.FileName = u"Updates Report — {} to {}.html".format(prev_date, curr_date)
    if dlg.ShowDialog() != DialogResult.OK:
        return
    html = build_html_report()
    with open(dlg.FileName, "w") as fh:
        fh.write(html.encode("utf-8"))
    try:
        os.startfile(dlg.FileName)
    except Exception:
        pass
    btn = win.FindName("btnExportPdf")
    btn.Content = u"Exported ✓"


win.FindName("btnCopy").Click      += on_copy
win.FindName("btnExportPdf").Click += on_export_pdf
win.FindName("btnClose").Click     += lambda s, e: win.Close()

win.ShowDialog()
