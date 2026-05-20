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
from datetime import date, datetime
from pyrevit import script, forms

sys.path.append(os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
    'lib'
))
from magictools import ui

try:
    import clr
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import Clipboard, DataFormats, DataObject
    HAS_CLIPBOARD = True
except Exception:
    HAS_CLIPBOARD = False

output = script.get_output()
output.close()

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

# Extract the sheets sections — the audit only diffs sheets.
prev_layout = prev_data.get("sheets", [])
curr_layout = curr_data.get("sheets", [])


def _format_date(dt):
    months = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    return "{} {} {}".format(dt.day, months[dt.month - 1], dt.year)

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
        return "unknown"

TODAY_LABEL = _format_date(datetime.today())

prev_date = _extract_date(prev_path)
curr_date = _extract_date(curr_path)

output.print_md("**Previous snapshot:** {}  ({} sheets)".format(prev_date, len(prev_layout)))
output.print_md("**Current snapshot:**  {}  ({} sheets)".format(curr_date, len(curr_layout)))

# ─────────────────────────────────────────────────────────────────────────────
# 2. Index both snapshots
# ─────────────────────────────────────────────────────────────────────────────

def build_index(layout):
    sheets = {}
    view_loc = {}
    for s in layout:
        sn   = s["sheet_number"]
        name = s.get("sheet_name", "")
        views = {}
        for vp in s.get("viewports", []):
            vname = vp.get("view_name", "")
            if not vname:
                continue
            views[vname] = {
                "detail_number": vp.get("detail_number", ""),
                "view_type":     vp.get("view_type", ""),
                "viewport_type": vp.get("viewport_type", ""),
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

sheet_reports   = []
sheets_new      = []
sheets_removed  = []
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
        if p.get("view_type", "") != c.get("view_type", "") and p.get("view_type") and c.get("view_type"):
            view_type_changes.append((vname, p["view_type"], c["view_type"]))
        if p.get("detail_number", "") != c.get("detail_number", ""):
            detail_changes.append((vname, p.get("detail_number", ""), c.get("detail_number", "")))

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

# Moves
moves = []
moved_view_names = set()
for vname, removed_from in removed_view_to_sheet.items():
    if vname in added_view_to_sheet:
        added_to = added_view_to_sheet[vname]
        for i, rs in enumerate(removed_from):
            if i < len(added_to):
                moves.append((vname, rs, added_to[i]))
                moved_view_names.add(vname)

views_removed_entirely = sorted([v for v in prev_view_loc.keys() if v not in curr_view_loc])
views_added_entirely   = sorted([v for v in curr_view_loc.keys() if v not in prev_view_loc])

# ─────────────────────────────────────────────────────────────────────────────
# 4. Print report in pyRevit output
# ─────────────────────────────────────────────────────────────────────────────

changed_sheets   = [r for r in sheet_reports if r["changed"]]
unchanged_sheets = [r for r in sheet_reports if not r["changed"]]

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
    r["detail_noise"] = bool(moves_in or moves_out or real_added or real_removed) and bool(r["detail_changes"])

substantive_sheets = [r for r in changed_sheets if not r["renumbering_only"]]
renumbering_sheets = [r for r in changed_sheets if r["renumbering_only"]]

output.print_md("\n---")
output.print_md("# 📋 Detail Sheets Update Report")

headline_bits = []
if sheets_new:             headline_bits.append("{} new sheet(s)".format(len(sheets_new)))
if sheets_removed:         headline_bits.append("{} removed".format(len(sheets_removed)))
if substantive_sheets:     headline_bits.append("{} sheet(s) with content changes".format(len(substantive_sheets)))
if views_added_entirely:   headline_bits.append("{} new view(s) placed".format(len(views_added_entirely)))
if moves:                  headline_bits.append("{} view(s) moved".format(len(moves)))
if views_removed_entirely: headline_bits.append("{} view(s) no longer used".format(len(views_removed_entirely)))
if renumbering_sheets:     headline_bits.append("{} sheet(s) with detail renumbering only".format(len(renumbering_sheets)))
if not headline_bits:      headline_bits.append("no changes")

output.print_md("**{}**".format("  •  ".join(headline_bits)))
output.print_md("_{} unchanged_".format(len(unchanged_sheets)))

# ─────────────────────────────────────────────────────────────────────────────
# 5. Build Teams-ready message
# ─────────────────────────────────────────────────────────────────────────────

lines = []
lines.append("**Detail Sheets Update Report — {}**".format(TODAY_LABEL))
lines.append("_Previous snapshot: {}  •  Current snapshot: {}_".format(prev_date, curr_date))
lines.append("")
lines.append("**Summary:** {}".format("  •  ".join(headline_bits)))
lines.append("")

if sheets_new:
    lines.append("**🆕 New sheets**")
    for sn in sheets_new:
        lines.append("- {} — {}".format(sn, curr_sheets[sn]["sheet_name"]))
    lines.append("")

if sheets_removed:
    lines.append("**🗑️ Removed sheets**")
    for sn in sheets_removed:
        lines.append("- {} — {}".format(sn, prev_sheets[sn]["sheet_name"]))
    lines.append("")

if move_pairs:
    lines.append("**↔️ Moves ({} views across {} sheet pair{})**".format(
        len(moves), len(move_pairs), "" if len(move_pairs) == 1 else "s"))
    for (frm, to), vs in sorted(move_pairs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append("- **{} → {}** ({} view{})".format(
            frm, to, len(vs), "" if len(vs) == 1 else "s"))
        for v in sorted(vs):
            lines.append("    - {}".format(v))
    lines.append("")

real_adds_by_sheet    = [(r["sheet_number"], r["curr_name"], r["real_added"])
                         for r in substantive_sheets if r["real_added"]]
real_removes_by_sheet = [(r["sheet_number"], r["curr_name"], r["real_removed"])
                         for r in substantive_sheets if r["real_removed"]]
view_type_changes_all = [(r["sheet_number"], vtc) for r in substantive_sheets for vtc in r["view_type_changes"]]
renames = [(r["sheet_number"], r["prev_name"], r["curr_name"])
           for r in substantive_sheets if r["prev_name"] != r["curr_name"]]

if real_adds_by_sheet or real_removes_by_sheet or view_type_changes_all or renames:
    lines.append("**➕➖ Content changes (Views added or removed from sheets)**")
    for sn, old, new in renames:
        lines.append("- {}: sheet renamed  {} → {}".format(sn, old, new))
    for sn, name, vs in real_adds_by_sheet:
        lines.append("- {} ({}) — added:".format(sn, name))
        for v in vs:
            lines.append("    + {}".format(v))
    for sn, name, vs in real_removes_by_sheet:
        lines.append("- {} ({}) — removed (no longer used):".format(sn, name))
        for v in vs:
            lines.append("    − {}".format(v))
    for sn, (vname, old, new) in view_type_changes_all:
        lines.append("- {}: view type changed — {} ({} → {})".format(sn, vname, old, new))
    lines.append("")

if views_added_entirely:
    lines.append("**✨ New views placed** _(not on any sheet before)_")
    for v in views_added_entirely:
        lines.append("- {}".format(v))
    lines.append("")

if views_removed_entirely:
    lines.append("**🧹 Views no longer used on any sheet**")
    for v in views_removed_entirely:
        lines.append("- {}".format(v))
    lines.append("")

if renumbering_sheets:
    lines.append("**🔢 Detail-number renumbering only** _(cosmetic)_")
    for r in renumbering_sheets:
        n = len(r["detail_changes"])
        lines.append("- {} — {} ({} view{})".format(
            r["sheet_number"], r["curr_name"], n, "" if n == 1 else "s"))
    lines.append("")

if not (sheets_new or sheets_removed or move_pairs or real_adds_by_sheet
        or real_removes_by_sheet or view_type_changes_all or renames
        or views_added_entirely or views_removed_entirely or renumbering_sheets):
    lines.append("_No changes detected between the two snapshots._")

teams_message = "\r\n".join(lines).rstrip()

# ─────────────────────────────────────────────────────────────────────────────
# 6. Strip markdown for clipboard-friendly plain text
# ─────────────────────────────────────────────────────────────────────────────

def _strip_md(txt):
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", txt)
    out = re.sub(r"_(.+?)_", r"\1", out)
    return out

plain_text = _strip_md(teams_message)

copied = False
copied_format = None
if HAS_CLIPBOARD:
    try:
        Clipboard.SetText(plain_text)
        copied = True
        copied_format = "plain-text"
    except Exception as e:
        output.print_md("\n[!] Could not copy to clipboard: {}".format(e))

copy_subtitle = u"Copied to clipboard ({}) ✓".format(copied_format) if copied else u"Could not copy to clipboard"

ui.show_report(
    text     = plain_text,
    title    = u"Updates Report",
    subtitle = copy_subtitle,
    summary  = u"  •  ".join(headline_bits) if headline_bits else u"No changes",
    context  = u"Diff of two 'Sheets with Views' JSON snapshots. The text below "
               u"is already copied to your clipboard — paste straight into Teams "
               u"or email. The 'sheets' section of each JSON is what's compared; "
               u"the master_views section is not used here."
)
