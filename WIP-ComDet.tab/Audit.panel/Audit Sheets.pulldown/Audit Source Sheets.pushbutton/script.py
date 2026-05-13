# -*- coding: utf-8 -*-
__title__ = 'Updates\nReport'
__doc__ = ('Compares two Sheet Layout JSON snapshots (previous vs. current) '
           'exported from the SOURCE model and builds a change report for the '
           'Production Team (Teams-ready, copy & paste).\n\n'
           'Detects:\n'
           '  - Views added / removed per sheet\n'
           '  - Views moved between sheets\n'
           '  - Views no longer used anywhere\n'
           '  - View type changes (e.g. Section -> FloorPlan)\n'
           '  - Detail number changes on views that stayed on the same sheet')

import json
import os
import re
from datetime import date, datetime
from pyrevit import script, forms

try:
    # System.Windows.Forms.Clipboard is available under IronPython / pyRevit
    import clr
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import Clipboard, DataFormats, DataObject
    HAS_CLIPBOARD = True
except Exception:
    HAS_CLIPBOARD = False

output = script.get_output()

# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick the two JSON snapshots
# ─────────────────────────────────────────────────────────────────────────────

prev_path = forms.pick_file(
    file_ext="json",
    title="Select PREVIOUS Sheet Layout JSON (old snapshot)"
)
if not prev_path:
    script.exit()

curr_path = forms.pick_file(
    file_ext="json",
    title="Select CURRENT Sheet Layout JSON (new snapshot)"
)
if not curr_path:
    script.exit()

with open(prev_path, "r") as f:
    prev_layout = json.load(f)
with open(curr_path, "r") as f:
    curr_layout = json.load(f)

def _format_date(dt):
    # "13 April 2026" — avoids locale issues by using month name from a fixed list
    months = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    return "{} {} {}".format(dt.day, months[dt.month - 1], dt.year)

def _extract_date(path):
    """Extract date from filename prefix 'YYYYMMDD_...', fallback to mtime."""
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
#     sheet_index[sheet_number] = {
#         "sheet_name":    str,
#         "views": { view_name: {detail_number, view_type, viewport_type} }
#     }
#     view_location[view_name] = list of sheet_numbers that contain it
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

sheet_reports = []   # list of dicts with keys: sheet_number, sheet_name, added, removed, view_type_changes, detail_changes, status
sheets_new         = []   # sheets in current but not in previous
sheets_removed     = []   # sheets in previous but not in current

# A "move" is when a view was removed from sheet A and added to sheet B in the same audit.
# Build move map from set differences across all sheets.

removed_view_to_sheet = {}   # view_name -> previous sheet
added_view_to_sheet   = {}   # view_name -> current sheet

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

    # View type changes on views that stayed
    view_type_changes = []   # (vname, old, new)
    detail_changes    = []   # (vname, old, new)
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

# Resolve moves: a view that was removed from one sheet AND added to another
moves = []   # list of (vname, from_sheet, to_sheet)
moved_view_names = set()

for vname, removed_from in removed_view_to_sheet.items():
    if vname in added_view_to_sheet:
        added_to = added_view_to_sheet[vname]
        # Pair them up (most of the time it's 1:1)
        for i, rs in enumerate(removed_from):
            if i < len(added_to):
                moves.append((vname, rs, added_to[i]))
                moved_view_names.add(vname)

# Views no longer used anywhere (were placed on any sheet before, not placed anywhere now)
views_removed_entirely = sorted([
    v for v in prev_view_loc.keys()
    if v not in curr_view_loc
])

# Views newly placed (were not in any sheet before, now placed)
views_added_entirely = sorted([
    v for v in curr_view_loc.keys()
    if v not in prev_view_loc
])

# ─────────────────────────────────────────────────────────────────────────────
# 4. Print report in pyRevit output
# ─────────────────────────────────────────────────────────────────────────────

changed_sheets = [r for r in sheet_reports if r["changed"]]
unchanged_sheets = [r for r in sheet_reports if not r["changed"]]

# ─────────────────────────────────────────────────────────────────────────────
# 4a. Build derived views for a cleaner, concrete report
# ─────────────────────────────────────────────────────────────────────────────

# Move pairs grouped by (from_sheet -> to_sheet)
move_pairs = {}  # (from, to) -> [view_names...]
moved_views_set = set()
for vname, frm, to in moves:
    move_pairs.setdefault((frm, to), []).append(vname)
    moved_views_set.add((vname, frm, to))

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

# Per-sheet classify: real_added / real_removed / moves_in / moves_out / renumbering
for r in sheet_reports:
    sn = r["sheet_number"]
    real_added   = []
    real_removed = []
    moves_in     = {}   # from_sheet -> [view_names]
    moves_out    = {}   # to_sheet   -> [view_names]
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
    # A sheet is "renumbering only" if no real adds/removes, no moves, only detail changes
    r["renumbering_only"] = (
        not real_added and not real_removed
        and not moves_in and not moves_out
        and not r["view_type_changes"]
        and r["prev_name"] == r["curr_name"]
        and bool(r["detail_changes"])
    )
    # If the sheet had moves or adds/removes, detail renumbering is expected noise
    r["detail_noise"] = bool(moves_in or moves_out or real_added or real_removed) and bool(r["detail_changes"])

# Sheets with substantive changes (not just renumbering)
substantive_sheets = [
    r for r in changed_sheets
    if not r["renumbering_only"]
]
renumbering_sheets = [
    r for r in changed_sheets
    if r["renumbering_only"]
]

# ─────────────────────────────────────────────────────────────────────────────
# 4b. Print report in pyRevit output (rich)
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("# 📋 Detail Sheets Update Report")

# Summary headline
headline_bits = []
if sheets_new:        headline_bits.append("{} new sheet(s)".format(len(sheets_new)))
if sheets_removed:    headline_bits.append("{} removed".format(len(sheets_removed)))
if substantive_sheets: headline_bits.append("{} sheet(s) with content changes".format(len(substantive_sheets)))
if views_added_entirely: headline_bits.append("{} new view(s) placed".format(len(views_added_entirely)))
if moves:             headline_bits.append("{} view(s) moved".format(len(moves)))
if views_removed_entirely: headline_bits.append("{} view(s) no longer used".format(len(views_removed_entirely)))
if renumbering_sheets: headline_bits.append("{} sheet(s) with detail renumbering only".format(len(renumbering_sheets)))
if not headline_bits: headline_bits.append("no changes")

output.print_md("**{}**".format("  •  ".join(headline_bits)))
output.print_md("_{} unchanged_".format(len(unchanged_sheets)))

if sheets_new:
    output.print_md("\n## 🆕 New sheets")
    for sn in sheets_new:
        output.print_md("  - **{}** — {}".format(sn, curr_sheets[sn]["sheet_name"]))

if sheets_removed:
    output.print_md("\n## 🗑️ Removed sheets")
    for sn in sheets_removed:
        output.print_md("  - **{}** — {}".format(sn, prev_sheets[sn]["sheet_name"]))

if move_pairs:
    output.print_md("\n## ↔️ Moves (grouped by sheets)")
    for (frm, to), vs in sorted(move_pairs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        output.print_md("  - **{} → {}**  ({} view{})".format(
            frm, to, len(vs), "" if len(vs) == 1 else "s"))
        for v in sorted(vs):
            output.print_md("      - *{}*".format(v))

if views_removed_entirely:
    output.print_md("\n## 🧹 Views no longer used on any sheet")
    for v in views_removed_entirely:
        output.print_md("  - *{}*".format(v))

if views_added_entirely:
    output.print_md("\n## ✨ New views placed")
    real_new = [v for v in views_added_entirely]
    for v in real_new:
        output.print_md("  - *{}*".format(v))

if substantive_sheets:
    output.print_md("\n## 🔁 Sheets with content changes")
    for r in substantive_sheets:
        sn = r["sheet_number"]
        output.print_md("### {} — {}".format(sn, r["curr_name"]))
        if r["prev_name"] != r["curr_name"]:
            output.print_md("  📝 Sheet renamed: `{}` → `{}`".format(
                r["prev_name"], r["curr_name"]))
        for v in r["real_added"]:
            output.print_md("  ➕ **+** *{}*".format(v))
        for v in r["real_removed"]:
            output.print_md("  ➖ **−** *{}*  _(no longer used)_".format(v))
        if r["moves_in"]:
            total = sum(len(vs) for vs in r["moves_in"].values())
            srcs = ", ".join("{} ({})".format(k, len(v)) for k, v in r["moves_in"].items())
            output.print_md("  ↘️ Received {} view(s) from: {}".format(total, srcs))
        if r["moves_out"]:
            total = sum(len(vs) for vs in r["moves_out"].values())
            dsts = ", ".join("{} ({})".format(k, len(v)) for k, v in r["moves_out"].items())
            output.print_md("  ↗️ Sent {} view(s) to: {}".format(total, dsts))
        for vname, old, new in r["view_type_changes"]:
            output.print_md("  🔄 **View type:** *{}*  `{}` → `{}`".format(vname, old, new))
        if r["view_type_changes"] == [] and r["detail_changes"] and not r["detail_noise"]:
            for vname, old, new in r["detail_changes"]:
                output.print_md("  🔢 Detail #: *{}*  `{}` → `{}`".format(
                    vname, old or "-", new or "-"))

if renumbering_sheets:
    output.print_md("\n## 🔢 Sheets with detail-number renumbering only")
    output.print_md("_(cosmetic — no views added/removed/moved)_")
    for r in renumbering_sheets:
        sn = r["sheet_number"]
        pairs = ", ".join("{}: {}→{}".format(vn, o or "-", n or "-")
                          for vn, o, n in r["detail_changes"])
        output.print_md("  - **{}** — {}  ({})".format(sn, r["curr_name"], pairs))

# ─────────────────────────────────────────────────────────────────────────────
# 5. Build Teams-ready message (concise, structured, copy to clipboard)
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

# Collect real content changes (adds/removes that are NOT moves) across all sheets
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
# 6. Build HTML version of the report (so Teams renders bold & lists)
# ─────────────────────────────────────────────────────────────────────────────

def _html_escape(s):
    if s is None:
        return ""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))

html_parts = []
html_parts.append("<div style=\"font-family:Segoe UI,Arial,sans-serif;font-size:10pt;\">")
html_parts.append("<p><b>Detail Sheets Update Report &mdash; {}</b><br>"
                  "<i>Previous snapshot: {} &nbsp;&bull;&nbsp; Current snapshot: {}</i></p>"
                  .format(_html_escape(TODAY_LABEL),
                          _html_escape(prev_date), _html_escape(curr_date)))

# Summary
html_parts.append("<p><b>Summary:</b> {}</p>".format(
    " &nbsp;&bull;&nbsp; ".join(_html_escape(b) for b in headline_bits)))

if sheets_new:
    html_parts.append("<p><b>&#127381; New sheets</b></p><ul>")
    for sn in sheets_new:
        html_parts.append("<li>{} &mdash; {}</li>".format(
            _html_escape(sn), _html_escape(curr_sheets[sn]["sheet_name"])))
    html_parts.append("</ul>")

if sheets_removed:
    html_parts.append("<p><b>&#128465;&#65039; Removed sheets</b></p><ul>")
    for sn in sheets_removed:
        html_parts.append("<li>{} &mdash; {}</li>".format(
            _html_escape(sn), _html_escape(prev_sheets[sn]["sheet_name"])))
    html_parts.append("</ul>")

if move_pairs:
    html_parts.append(
        "<p><b>&#8596;&#65039; Moves ({} views across {} sheet pair{})</b></p><ul>".format(
            len(moves), len(move_pairs), "" if len(move_pairs) == 1 else "s"))
    for (frm, to), vs in sorted(move_pairs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        html_parts.append(
            "<li><b>{} &rarr; {}</b> ({} view{})<ul>".format(
                _html_escape(frm), _html_escape(to),
                len(vs), "" if len(vs) == 1 else "s"))
        for v in sorted(vs):
            html_parts.append("<li>{}</li>".format(_html_escape(v)))
        html_parts.append("</ul></li>")
    html_parts.append("</ul>")

if real_adds_by_sheet or real_removes_by_sheet or view_type_changes_all or renames:
    html_parts.append("<p><b>&#10133;&#10134; Content changes (Views added or removed from sheets)</b></p><ul>")
    for sn, old, new in renames:
        html_parts.append("<li>{}: sheet renamed &nbsp; {} &rarr; {}</li>".format(
            _html_escape(sn), _html_escape(old), _html_escape(new)))
    for sn, name, vs in real_adds_by_sheet:
        html_parts.append("<li>{} ({}) &mdash; <b>added</b>:<ul>".format(
            _html_escape(sn), _html_escape(name)))
        for v in vs:
            html_parts.append("<li>+ {}</li>".format(_html_escape(v)))
        html_parts.append("</ul></li>")
    for sn, name, vs in real_removes_by_sheet:
        html_parts.append("<li>{} ({}) &mdash; <b>removed</b> (no longer used):<ul>".format(
            _html_escape(sn), _html_escape(name)))
        for v in vs:
            html_parts.append("<li>&minus; {}</li>".format(_html_escape(v)))
        html_parts.append("</ul></li>")
    for sn, (vname, old, new) in view_type_changes_all:
        html_parts.append(
            "<li>{}: view type changed &mdash; {} ({} &rarr; {})</li>".format(
                _html_escape(sn), _html_escape(vname),
                _html_escape(old), _html_escape(new)))
    html_parts.append("</ul>")

if views_added_entirely:
    html_parts.append(
        "<p><b>&#10024; New views placed</b> "
        "<i>(not on any sheet before)</i></p><ul>")
    for v in views_added_entirely:
        html_parts.append("<li>{}</li>".format(_html_escape(v)))
    html_parts.append("</ul>")

if views_removed_entirely:
    html_parts.append("<p><b>&#129529; Views no longer used on any sheet</b></p><ul>")
    for v in views_removed_entirely:
        html_parts.append("<li>{}</li>".format(_html_escape(v)))
    html_parts.append("</ul>")

if renumbering_sheets:
    html_parts.append(
        "<p><b>&#128290; Detail-number renumbering only</b> "
        "<i>(cosmetic)</i></p><ul>")
    for r in renumbering_sheets:
        n = len(r["detail_changes"])
        html_parts.append("<li>{} &mdash; {} ({} view{})</li>".format(
            _html_escape(r["sheet_number"]), _html_escape(r["curr_name"]),
            n, "" if n == 1 else "s"))
    html_parts.append("</ul>")

if not (sheets_new or sheets_removed or move_pairs or real_adds_by_sheet
        or real_removes_by_sheet or view_type_changes_all or renames
        or views_added_entirely or views_removed_entirely or renumbering_sheets):
    html_parts.append("<p><i>No changes detected between the two snapshots.</i></p>")

html_parts.append("</div>")
report_html = "".join(html_parts)


# Build a plain-text fallback (no markdown asterisks, just clean text)
def _strip_md(txt):
    # Remove **bold** and _italic_ markers for the plain-text fallback
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", txt)
    out = re.sub(r"_(.+?)_", r"\1", out)
    return out

plain_text = _strip_md(teams_message)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Copy to clipboard as HTML (Teams will render it) + plain-text fallback
# ─────────────────────────────────────────────────────────────────────────────

def _build_cf_html(html_body):
    """
    Build the CF_HTML clipboard payload with the required header.
    Header offsets are computed against the byte length of the final string.
    """
    # Wrap the fragment markers around the body
    wrapped = (
        "<html><body>\r\n"
        "<!--StartFragment-->{0}<!--EndFragment-->\r\n"
        "</body></html>"
    ).format(html_body)
    header_template = (
        "Version:0.9\r\n"
        "StartHTML:{0:010d}\r\n"
        "EndHTML:{1:010d}\r\n"
        "StartFragment:{2:010d}\r\n"
        "EndFragment:{3:010d}\r\n"
    )
    # First pass with placeholder header to compute lengths in UTF-8 bytes
    placeholder = header_template.format(0, 0, 0, 0)
    body_bytes = wrapped.encode("utf-8")
    header_len = len(placeholder.encode("utf-8"))
    start_html     = header_len
    start_fragment = header_len + wrapped.index("<!--StartFragment-->") + len("<!--StartFragment-->")
    # Recompute as byte offsets (ASCII portion before StartFragment is 1:1 with UTF-8 here)
    pre = wrapped[: wrapped.index("<!--EndFragment-->")]
    end_fragment = header_len + len(pre.encode("utf-8"))
    end_html = header_len + len(body_bytes)
    final_header = header_template.format(
        start_html, end_html, start_fragment, end_fragment)
    return final_header + wrapped


copied = False
copied_format = None
if HAS_CLIPBOARD:
    try:
        cf_html = _build_cf_html(report_html)
        data = DataObject()
        data.SetData(DataFormats.Html, cf_html)
        data.SetData(DataFormats.UnicodeText, plain_text)
        Clipboard.SetDataObject(data, True)
        copied = True
        copied_format = "HTML + plain-text"
    except Exception as e:
        output.print_md("\n[!] HTML clipboard failed ({}). Falling back to plain text.".format(e))
        try:
            Clipboard.SetText(plain_text)
            copied = True
            copied_format = "plain-text"
        except Exception as e2:
            output.print_md("\n[!] Could not copy to clipboard: {}".format(e2))

output.print_md("\n---")
if copied:
    output.print_md(
        "[OK] **Report copied to clipboard ({}).** Paste it into Teams "
        "(Ctrl+V) — bold and bullets will render. "
        "If Teams strips formatting, paste with **Ctrl+Shift+V** (keep formatting).".format(
            copied_format))
else:
    output.print_md("**Report** (copy manually from below):")

output.print_md("\n```\n{}\n```".format(plain_text))


# ─────────────────────────────────────────────────────────────────────────────
# 7. PDF export (pure-Python, no external dependencies)
# ─────────────────────────────────────────────────────────────────────────────

def _pdf_escape(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

def _to_winansi(s):
    if s is None:
        return ""
    try:
        return s.encode("cp1252", "replace").decode("cp1252")
    except Exception:
        out = []
        for ch in s:
            out.append(ch if ord(ch) < 128 else "?")
        return "".join(out)

def write_pdf_report(path, title, subtitle, blocks):
    """
    blocks: list of (type, text) tuples where type is one of:
      'h2', 'h3', 'body', 'bullet', 'sub_bullet', 'muted', 'spacer', 'rule'
    """
    PAGE_W, PAGE_H = 612, 792           # Letter
    ML, MR, MT, MB = 54, 54, 54, 54
    USABLE_W = PAGE_W - ML - MR

    SIZES = {"title": 20, "subtitle": 11, "h2": 13, "h3": 11,
             "body": 10, "bullet": 10, "sub_bullet": 10, "muted": 9,
             "spacer": 10, "rule": 10}
    LEADING = {"title": 26, "subtitle": 18, "h2": 22, "h3": 17,
               "body": 14, "bullet": 14, "sub_bullet": 13, "muted": 12,
               "spacer": 8, "rule": 10}
    BOLD = set(["title", "h2", "h3"])
    INDENT = {"bullet": 12, "sub_bullet": 28}

    def char_w(sz): return sz * 0.52  # Helvetica rough avg

    def wrap(text, sz, width):
        text = _to_winansi(text)
        max_chars = max(1, int(width / char_w(sz)))
        words = text.split(" ")
        out = []
        cur = ""
        for w in words:
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= max_chars:
                cur = cur + " " + w
            else:
                out.append(cur)
                cur = w
        if cur:
            out.append(cur)
        return out if out else [""]

    # Commands: list of ("text", font, sz, x, y, str) OR ("line", x1, y1, x2, y2)
    pages = [[]]
    y_state = [PAGE_H - MT]

    def new_page():
        pages.append([])
        y_state[0] = PAGE_H - MT

    def ensure(space):
        if y_state[0] - space < MB:
            new_page()

    # Title
    ensure(LEADING["title"])
    pages[-1].append(("text", "F2", SIZES["title"], ML, y_state[0], _to_winansi(title)))
    y_state[0] -= LEADING["title"]

    if subtitle:
        ensure(LEADING["subtitle"])
        pages[-1].append(("text", "F1", SIZES["subtitle"], ML, y_state[0], _to_winansi(subtitle)))
        y_state[0] -= LEADING["subtitle"]

    # Rule under header
    ensure(12)
    pages[-1].append(("line", ML, y_state[0] + 4, PAGE_W - MR, y_state[0] + 4))
    y_state[0] -= 10

    for (btype, text) in blocks:
        if btype == "spacer":
            y_state[0] -= LEADING["spacer"]
            continue
        if btype == "rule":
            ensure(8)
            pages[-1].append(("line", ML, y_state[0], PAGE_W - MR, y_state[0]))
            y_state[0] -= 6
            continue
        sz = SIZES.get(btype, SIZES["body"])
        leading = LEADING.get(btype, LEADING["body"])
        bold = btype in BOLD
        indent = INDENT.get(btype, 0)
        prefix = ""
        if btype == "bullet":
            prefix = "- "
        elif btype == "sub_bullet":
            prefix = "- "
        wrapped = wrap(prefix + text, sz, USABLE_W - indent)
        for ln in wrapped:
            ensure(leading)
            pages[-1].append(("text", "F2" if bold else "F1", sz, ML + indent, y_state[0], ln))
            y_state[0] -= leading

    # Assemble PDF bytes
    chunks = []
    def emit_str(s):
        chunks.append(s.encode("latin-1"))
    def emit_bytes(b):
        chunks.append(b)
    def total_len():
        n = 0
        for c in chunks:
            n += len(c)
        return n

    offsets = {}
    def start_obj(n):
        offsets[n] = total_len()
        emit_str("{0} 0 obj\n".format(n))
    def end_obj():
        emit_str("\nendobj\n")

    emit_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    start_obj(1)
    emit_str("<< /Type /Catalog /Pages 2 0 R >>")
    end_obj()

    start_obj(3)
    emit_str("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    end_obj()

    start_obj(4)
    emit_str("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    end_obj()

    page_nums = [5 + 2 * i for i in range(len(pages))]
    kids = " ".join("{0} 0 R".format(n) for n in page_nums)
    start_obj(2)
    emit_str("<< /Type /Pages /Count {0} /Kids [{1}] >>".format(len(pages), kids))
    end_obj()

    for i, page in enumerate(pages):
        pn = 5 + 2 * i
        cn = pn + 1
        ops = ["BT"]
        current_font = None
        for cmd in page:
            if cmd[0] == "text":
                _, font, sz, x, y, text = cmd
                if font != current_font or True:
                    ops.append("/{0} {1} Tf".format(font, sz))
                    current_font = font
                ops.append("1 0 0 1 {0} {1} Tm ({2}) Tj".format(x, y, _pdf_escape(text)))
        ops.append("ET")
        # Draw lines (outside BT/ET)
        for cmd in page:
            if cmd[0] == "line":
                _, x1, y1, x2, y2 = cmd
                ops.append("0.7 0.7 0.7 RG 0.5 w {0} {1} m {2} {3} l S".format(x1, y1, x2, y2))
        stream = "\n".join(ops).encode("cp1252", "replace")

        start_obj(cn)
        emit_str("<< /Length {0} >>\nstream\n".format(len(stream)))
        emit_bytes(stream)
        emit_str("\nendstream")
        end_obj()

        start_obj(pn)
        emit_str(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {0} {1}] "
            "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            "/Contents {2} 0 R >>".format(PAGE_W, PAGE_H, cn))
        end_obj()

    xref_pos = total_len()
    max_n = max(offsets.keys())
    emit_str("xref\n0 {0}\n".format(max_n + 1))
    emit_str("0000000000 65535 f \n")
    for i in range(1, max_n + 1):
        emit_str("{0:010d} 00000 n \n".format(offsets.get(i, 0)))
    emit_str(
        "trailer\n<< /Size {0} /Root 1 0 R >>\nstartxref\n{1}\n%%EOF\n".format(
            max_n + 1, xref_pos))

    with open(path, "wb") as f:
        for c in chunks:
            f.write(c)


# Build PDF blocks from the same structured data
def build_pdf_blocks():
    B = []
    B.append(("h2", "Summary"))
    for bit in headline_bits:
        B.append(("bullet", bit))
    B.append(("muted", "{} sheets unchanged".format(len(unchanged_sheets))))
    B.append(("spacer", ""))

    if sheets_new:
        B.append(("h2", "New sheets"))
        for sn in sheets_new:
            B.append(("bullet", "{} - {}".format(sn, curr_sheets[sn]["sheet_name"])))
        B.append(("spacer", ""))

    if sheets_removed:
        B.append(("h2", "Removed sheets"))
        for sn in sheets_removed:
            B.append(("bullet", "{} - {}".format(sn, prev_sheets[sn]["sheet_name"])))
        B.append(("spacer", ""))

    if move_pairs:
        B.append(("h2", "Moves ({} views across {} sheet pair{})".format(
            len(moves), len(move_pairs), "" if len(move_pairs) == 1 else "s")))
        for (frm, to), vs in sorted(move_pairs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            B.append(("bullet", "{} -> {}  ({} view{})".format(
                frm, to, len(vs), "" if len(vs) == 1 else "s")))
            for v in sorted(vs):
                B.append(("sub_bullet", v))
        B.append(("spacer", ""))

    if real_adds_by_sheet or real_removes_by_sheet or view_type_changes_all or renames:
        B.append(("h2", "Content changes (Views added or removed from sheets)"))
        for sn, old, new in renames:
            B.append(("bullet", "{}: sheet renamed  {} -> {}".format(sn, old, new)))
        for sn, name, vs in real_adds_by_sheet:
            B.append(("bullet", "{} ({}) - added:".format(sn, name)))
            for v in vs:
                B.append(("sub_bullet", "+ " + v))
        for sn, name, vs in real_removes_by_sheet:
            B.append(("bullet", "{} ({}) - removed (no longer used):".format(sn, name)))
            for v in vs:
                B.append(("sub_bullet", "- " + v))
        for sn, (vname, old, new) in view_type_changes_all:
            B.append(("bullet", "{}: view type changed - {} ({} -> {})".format(
                sn, vname, old, new)))
        B.append(("spacer", ""))

    if views_added_entirely:
        B.append(("h2", "New views placed (not on any sheet before)"))
        for v in views_added_entirely:
            B.append(("bullet", v))
        B.append(("spacer", ""))

    if views_removed_entirely:
        B.append(("h2", "Views no longer used on any sheet"))
        for v in views_removed_entirely:
            B.append(("bullet", v))
        B.append(("spacer", ""))

    if renumbering_sheets:
        B.append(("h2", "Detail-number renumbering only (cosmetic)"))
        for r in renumbering_sheets:
            n = len(r["detail_changes"])
            B.append(("bullet", "{} - {} ({} view{})".format(
                r["sheet_number"], r["curr_name"], n, "" if n == 1 else "s")))
        B.append(("spacer", ""))

    if not B or all(b[0] == "spacer" for b in B[1:]):
        B.append(("body", "No changes detected between the two snapshots."))
    return B


# Offer to save PDF
output.print_md("\n---")
if forms.alert(
        "Do you want to save this report as a PDF?",
        title="Export PDF",
        options=["Yes, save PDF", "No"]) == "Yes, save PDF":
    default_name = "{0}_DetailSheetsUpdateReport.pdf".format(
        date.today().strftime("%Y%m%d"))
    pdf_path = forms.save_file(
        file_ext="pdf",
        default_name=default_name,
        title="Save Audit Report PDF")
    if pdf_path:
        try:
            subtitle_pdf = "Previous snapshot: {}     Current snapshot: {}     Generated: {}".format(
                prev_date, curr_date, TODAY_LABEL)
            write_pdf_report(
                pdf_path,
                "Detail Sheets Update Report",
                subtitle_pdf,
                build_pdf_blocks())
            output.print_md("📄 **PDF saved:** `{}`".format(pdf_path))
        except Exception as e:
            output.print_md("❌ PDF export failed: {}".format(e))
