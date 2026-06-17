# -*- coding: utf-8 -*-
__title__ = 'Updates\nReport'
__doc__ = ('Compares two Sheets with Views JSON snapshots (previous vs. '
           'current) exported from the source model and builds a per-sheet '
           'change report for the Production Team (Teams-ready + PDF).\n\n'
           'Per sheet, classifies every view into five buckets:\n'
           '  - Added (new on the sheet / moved in from another)\n'
           '  - Removed or relocated (gone / moved out)\n'
           '  - Renamed (same Detail ID, different name)\n'
           '  - Renumber (same view, different detail number)\n'
           '  - Unchanged\n'
           'Renames are detected by Detail ID (carried in the JSON), so a '
           'renamed view is NOT reported as a remove+add. Sheets are matched '
           'by number suffix so the report survives a prefix migration '
           '(e.g. AA10.## -> AX10.##). Building-specific custom sheets '
           '(prefix != AX) get their own section.')

import sys
import json
import os
import re
from datetime import datetime
from collections import OrderedDict
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

COMMON_PREFIX = u"AX"

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
# 2. Detail-ID maps (name -> id), per snapshot
#    Sheet viewports don't carry detail_id, but master_views.dependent_views do.
#    Build a name->id map per snapshot so views can be matched across snapshots
#    by IDENTITY (id when present, else name). A renamed view keeps its id, so
#    it still matches — that's how a rename is told apart from a remove+add.
# ─────────────────────────────────────────────────────────────────────────────

def build_name2id(d):
    # Only IDs that map to a SINGLE name are usable as identity. A detail_id
    # shared by several distinct views (data error / legacy dupes) is ambiguous —
    # drop it so those views fall back to name matching instead of collapsing
    # into one identity and producing phantom renames.
    id2names = {}
    for mv in d.get("master_views", []):
        for dep in mv.get("dependent_views", []):
            nm  = dep.get("view_name", u"")
            did = dep.get("detail_id", u"")
            if nm and did:
                id2names.setdefault(did, set()).add(nm)
    m = {}
    for did, names in id2names.items():
        if len(names) == 1:
            m[list(names)[0]] = did
    return m


prev_name2id = build_name2id(prev_data)
curr_name2id = build_name2id(curr_data)


def _ident_prev(name):
    return prev_name2id.get(name) or (u"name::" + name)


def _ident_curr(name):
    return curr_name2id.get(name) or (u"name::" + name)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Index both snapshots — sheets keyed by NUMBER SUFFIX (survives prefix swap)
# ─────────────────────────────────────────────────────────────────────────────

def _suf(s):
    return s[2:] if len(s) > 2 else s


def _pfx(s):
    return s[:2].upper() if s else u"??"


def sheet_views(s):
    out = OrderedDict()
    for vp in s.get("viewports", []):
        n = vp.get("view_name", u"")
        if not n:
            continue
        out[n] = {"det": vp.get("detail_number", u""),
                  "vt":  vp.get("view_type", u"")}
    return out


def _index(layout):
    """Split a layout into common (AX, keyed by suffix) and custom (non-AX,
    keyed by full number) sheets."""
    common = OrderedDict()
    cust   = OrderedDict()
    for s in layout:
        sn  = s["sheet_number"]
        rec = {"number": sn, "prefix": _pfx(sn),
               "name": s.get("sheet_name", u""), "views": sheet_views(s)}
        if _pfx(sn) == COMMON_PREFIX:
            common[_suf(sn)] = rec
        else:
            cust[sn] = rec
    return common, cust


prev_common, prev_custom = _index(prev_layout)
curr_common, curr_custom = _index(curr_layout)

# Global identity -> [sheet numbers] and identity -> name, over ALL sheets
# (common + custom) of each snapshot — so moves between common and custom
# sheets are detected, and retired/new-genuine tallies are complete.
old_id_loc  = {}
old_id_name = {}
for sh in list(prev_common.values()) + list(prev_custom.values()):
    for nm in sh["views"]:
        idn = _ident_prev(nm)
        old_id_loc.setdefault(idn, []).append(sh["number"])
        old_id_name.setdefault(idn, nm)

new_id_loc  = {}
new_id_name = {}
for sh in list(curr_common.values()) + list(curr_custom.values()):
    for nm in sh["views"]:
        idn = _ident_curr(nm)
        new_id_loc.setdefault(idn, []).append(sh["number"])
        new_id_name.setdefault(idn, nm)


def _first_other(locs, this):
    o = [x for x in locs if x != this]
    return o[0] if o else (locs[0] if locs else None)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Per-sheet classification → 5 buckets
# ─────────────────────────────────────────────────────────────────────────────

def classify(old_views, new_views, this_number):
    added, removed, renamed, renumber, unchanged = [], [], [], [], []
    old_by_ident = {}      # identity -> (name, meta)
    for o, m in old_views.items():
        old_by_ident[_ident_prev(o)] = (o, m)
    new_idents = set(_ident_curr(n) for n in new_views)

    for n, mn in new_views.items():
        idn = _ident_curr(n)
        if idn in old_by_ident:
            o, mo = old_by_ident[idn]
            note = u""
            if mo["det"] != mn["det"]:
                note = u"  (det# {} → {})".format(mo["det"] or u"–",
                                                       mn["det"] or u"–")
            if o != n:
                renamed.append(u"{} → {}{}".format(o, n, note))
            else:
                if mo["det"] != mn["det"]:
                    renumber.append(u"{}: {} → {}".format(
                        n, mo["det"] or u"–", mn["det"] or u"–"))
                elif mo["vt"] and mn["vt"] and mo["vt"] != mn["vt"]:
                    unchanged.append(u"{}  (type {} → {})".format(n, mo["vt"], mn["vt"]))
                else:
                    unchanged.append(n)
        else:
            if idn in old_id_loc:                  # identity exists on another old sheet
                src = _first_other(old_id_loc.get(idn, []), this_number)
                o = old_id_name.get(idn, n)
                if o != n:
                    renamed.append(u"{} → {}{}".format(
                        o, n, u"  (from {})".format(src) if src else u""))
                else:
                    added.append(u"{}{}".format(
                        n, u"  (moved from {})".format(src) if src else u""))
            else:
                added.append(n)

    for o, mo in old_views.items():
        ido = _ident_prev(o)
        if ido in new_idents:
            continue                               # handled above
        if ido in new_id_loc:                      # exists elsewhere now
            dst = _first_other(new_id_loc.get(ido, []), this_number)
            new_nm = new_id_name.get(ido, o)
            if new_nm != o:
                pass                               # shown as renamed on destination sheet
            else:
                removed.append(u"{}  (moved to {})".format(o, dst) if dst else o)
        else:
            removed.append(o)

    return added, removed, renamed, renumber, unchanged


def _diff_pairs(prev_map, curr_map, keys):
    """Run classify over a set of keys; return (changed_blocks, unchanged_list)."""
    blocks = []
    unchanged = []
    for k in keys:
        p = prev_map.get(k)
        c = curr_map.get(k)
        ov = p["views"] if p else OrderedDict()
        nv = c["views"] if c else OrderedDict()
        number = (c or p)["number"]
        name   = (c or p)["name"]
        a, r, rn, rnum, un = classify(ov, nv, number)
        blk = {"number": number, "name": name, "prefix": (c or p)["prefix"],
               "added": a, "removed": r, "renamed": rn, "renumber": rnum,
               "unchanged": un, "is_new": (p is None), "is_removed": (c is None)}
        if a or r or rn or rnum:
            blocks.append(blk)
        else:
            unchanged.append((number, name, len(un)))
    blocks.sort(key=lambda b: b["number"])
    return blocks, unchanged


# Common (AX) sheets — matched by number suffix
sheet_blocks, common_unchanged = _diff_pairs(
    prev_common, curr_common,
    sorted(set(list(prev_common.keys()) + list(curr_common.keys()))))

# Custom (building-specific) sheets — matched by full number
custom_blocks, custom_unchanged = _diff_pairs(
    prev_custom, curr_custom,
    sorted(set(list(prev_custom.keys()) + list(curr_custom.keys()))))

unchanged_only = sorted(common_unchanged + custom_unchanged)

# Global tallies
all_old_idents = set(old_id_loc.keys())
all_new_idents = set(new_id_loc.keys())
retired     = sorted(old_id_name[i] for i in (all_old_idents - all_new_idents))
new_genuine = sorted(new_id_name[i] for i in (all_new_idents - all_old_idents))
tot_renum   = sum(len(b["renumber"]) for b in sheet_blocks + custom_blocks)
n_ren       = sum(len(b["renamed"])  for b in sheet_blocks + custom_blocks)

new_common = [b for b in sheet_blocks if b["is_new"]]
rem_common = [b for b in sheet_blocks if b["is_removed"]]
chg_common = [b for b in sheet_blocks if not b["is_new"] and not b["is_removed"]]

# Summary headline bits
summ = []
if new_common:    summ.append(u"{} new common sheet(s)".format(len(new_common)))
if rem_common:    summ.append(u"{} removed common sheet(s)".format(len(rem_common)))
if chg_common:    summ.append(u"{} common sheet(s) changed".format(len(chg_common)))
if custom_blocks: summ.append(u"{} custom sheet(s)".format(len(custom_blocks)))
if n_ren:         summ.append(u"{} renamed".format(n_ren))
if tot_renum:     summ.append(u"{} renumbered".format(tot_renum))
if retired:       summ.append(u"{} retired".format(len(retired)))
if not summ:      summ.append(u"no changes")
headline_bits = summ

subtitle = u"Previous: {}  ·  Current: {}  ·  {} unchanged".format(
    prev_date, curr_date, len(unchanged_only))

# ─────────────────────────────────────────────────────────────────────────────
# 5. Teams-ready plain text (for clipboard)
# ─────────────────────────────────────────────────────────────────────────────

T = []
ad = T.append
ad(u"Detail Sheets Update Report — {}".format(TODAY_LABEL))
ad(u"Previous: {}  •  Current: {}".format(prev_date, curr_date))
ad(u"")
ad(u"Summary: " + u"  •  ".join(summ))
ad(u"")


def _teams_sheet(b):
    ad(u"[{}] {}".format(b["number"], b["name"]))
    for v in b["renamed"]:  ad(u"   ✏ rename : {}".format(v))
    for v in b["added"]:    ad(u"   ➕ added  : {}".format(v))
    for v in b["removed"]:  ad(u"   ➖ removed: {}".format(v))
    for v in b["renumber"]: ad(u"   \U0001f522 renum  : {}".format(v))


if new_common:
    ad(u"\U0001f195 NEW COMMON SHEETS")
    for b in new_common:
        _teams_sheet(b)
    ad(u"")
if chg_common:
    ad(u"✳ CHANGED COMMON SHEETS")
    for b in chg_common:
        _teams_sheet(b)
    ad(u"")
if rem_common:
    ad(u"\U0001f5d1 REMOVED COMMON SHEETS")
    for b in rem_common:
        _teams_sheet(b)
    ad(u"")
if custom_blocks:
    ad(u"\U0001f3d7 CUSTOM SHEETS (building-specific)")
    byp = OrderedDict()
    for b in sorted(custom_blocks, key=lambda x: x["number"]):
        byp.setdefault(b["prefix"], []).append(b)
    for pf in sorted(byp):
        ad(u"  Building {}".format(pf))
        for b in byp[pf]:
            _teams_sheet(b)
    ad(u"")
if retired:
    ad(u"\U0001f9f9 VIEWS NO LONGER USED ANYWHERE ({})".format(len(retired)))
    for v in retired:
        ad(u"- {}".format(v))
    ad(u"")
if unchanged_only:
    ad(u"✓ UNCHANGED SHEETS ({})".format(len(unchanged_only)))
    for num, name, cnt in unchanged_only:
        ad(u"- {} — {} ({} views)".format(num, name, cnt))

teams_plain = u"\r\n".join(T).rstrip()

# ─────────────────────────────────────────────────────────────────────────────
# 6. WPF preview data — per-sheet expanders (content baked into XAML)
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


# changes_list: [(icon, view_name, detail_text, fg_color), ...]
# badges_list:  [(label, fg, bg, border), ...]
sheet_change_data = []
renum_change_data = []

for b in sheet_blocks + custom_blocks:
    changes = []
    for v in b["renamed"]:
        changes.append((u"✏", v, u"renamed", u"#9099C8"))
    for v in b["added"]:
        changes.append((u"➕", v, u"added", u"#50E898"))
    for v in b["removed"]:
        changes.append((u"➖", v, u"removed", u"#FF7070"))
    for v in b["renumber"]:
        changes.append((u"\U0001f522", v, u"renumber", u"#5B8EC4"))

    badges = []
    if b["is_new"]:
        badges.append((u"NEW SHEET", u"#50E898", u"#122E1C", u"#50E898"))
    if b["is_removed"]:
        badges.append((u"REMOVED", u"#FF7070", u"#3C1212", u"#FF7070"))
    if b["renamed"]:
        badges.append((u"✏ {}".format(len(b["renamed"])), u"#9099C8", u"#1A1D30", u"#9099C8"))
    if b["added"]:
        badges.append((u"+{}".format(len(b["added"])),  u"#50E898", u"#122E1C", u"#50E898"))
    if b["removed"]:
        badges.append((u"−{}".format(len(b["removed"])), u"#FF7070", u"#3C1212", u"#FF7070"))
    if b["renumber"]:
        badges.append((u"\U0001f522 {}".format(len(b["renumber"])), u"#5B8EC4", u"#1A2535", u"#5B8EC4"))

    is_renum_only = bool(b["renumber"]) and not (b["added"] or b["removed"] or b["renamed"])
    target = renum_change_data if is_renum_only else sheet_change_data
    target.append((b["number"], b["name"], changes, badges))

# Static section OCs — new/removed sheets are already shown as per-sheet blocks
# above (with NEW SHEET / REMOVED badges), so those two OCs stay empty to avoid
# double-listing. New-views and retired-views keep their own sections.
new_sheets_oc = ObservableCollection[object]()
rem_sheets_oc = ObservableCollection[object]()
new_views_oc  = ObservableCollection[object]()
gone_views_oc = ObservableCollection[object]()

for v in new_genuine:
    new_views_oc.Add(_Row(u"✨", v))
for v in retired:
    gone_views_oc.Add(_Row(u"\U0001f9f9", v))

# ─────────────────────────────────────────────────────────────────────────────
# 7. HTML report builder (Export PDF button) — 5-column matrix per sheet
# ─────────────────────────────────────────────────────────────────────────────

def _esc(text):
    if text is None:
        return u""
    return (unicode(text)
            .replace(u"&", u"&amp;")
            .replace(u"<", u"&lt;")
            .replace(u">", u"&gt;"))


CASES = [("added",     u"➕ Added",                u"#1E9E5A", u"#F0FBF4"),
         ("removed",   u"➖ Removed or relocated", u"#D24343", u"#FDF2F2"),
         ("renamed",   u"✏ Renamed",              u"#7A3FD0", u"#F7F3FD"),
         ("renumber",  u"\U0001f522 Renumber",         u"#1F6FB0", u"#EEF6FC"),
         ("unchanged", u"✓ Unchanged",            u"#7A8090", u"#F6F7F9")]


def build_html_report():
    """Landscape HTML; open in browser and Ctrl+P -> Save as PDF."""

    def col_cell(label, fg, bg, items):
        head = (u'<div style="font-weight:700;color:{fg};font-size:8.5px;'
                u'border-bottom:1.5px solid {fg};padding:2px 4px;margin-bottom:2px">'
                u'{lbl}&nbsp;({n})</div>').format(fg=fg, lbl=_esc(label), n=len(items))
        body = u""
        for it in items:
            body += (u'<div style="font-size:8px;color:#222;padding:1px 4px;'
                     u'border-bottom:1px solid #EEE;line-height:1.25">{}</div>').format(_esc(it))
        if not items:
            body = u'<div style="font-size:8px;color:#BBB;padding:1px 4px">&mdash;</div>'
        return (u'<td valign="top" style="background:{bg};border:1px solid #E3E3EA;'
                u'vertical-align:top;width:20%">{h}{b}</td>').format(bg=bg, h=head, b=body)

    def sheet_table(b):
        tag = u""
        if b["is_new"]:
            tag = (u'<span style="background:#122E1C;color:#fff;font-size:8px;'
                   u'padding:1px 6px;border-radius:3px;margin-left:8px">NEW SHEET</span>')
        elif b["is_removed"]:
            tag = (u'<span style="background:#3C1212;color:#fff;font-size:8px;'
                   u'padding:1px 6px;border-radius:3px;margin-left:8px">REMOVED SHEET</span>')
        counts = []
        for key, label, fg, _bg in CASES:
            if b[key]:
                short = label.split(u" ", 1)[1] if u" " in label else label
                counts.append(u'<span style="color:{};font-size:9px;margin-right:8px">'
                              u'{}: {}</span>'.format(fg, short, len(b[key])))
        hdr = (u'<div style="font-weight:700;font-size:11px;color:#1E2235;margin:2px 0">'
               u'{num} <span style="color:#555;font-weight:400">&mdash; {name}</span>{tag}</div>'
               u'<div style="margin-bottom:2px">{cnt}</div>').format(
                   num=_esc(b["number"]), name=_esc(b["name"]), tag=tag, cnt=u"".join(counts))
        cells = u"".join(col_cell(l, fg, bg, b[k]) for k, l, fg, bg in CASES)
        return (u'<div style="page-break-inside:avoid;margin:0 0 10px;padding:6px 8px;'
                u'border-left:3px solid #9013FE;background:#FCFCFE">'
                u'{hdr}<table style="border-collapse:collapse;width:100%;table-layout:fixed">'
                u'<tr>{cells}</tr></table></div>').format(hdr=hdr, cells=cells)

    def section(title, color):
        return (u'<div style="page-break-after:avoid;margin:1.4em 0 0.5em;padding:5px 0;'
                u'border-bottom:2px solid {c};font-size:1.05em;font-weight:700;color:#1E2235">'
                u'{t}</div>').format(c=color, t=_esc(title))

    def listblock(title, color, items, ncols):
        if not items:
            return u""
        body = u"".join(
            u'<div style="font-size:9px;color:#333;padding:1px 8px 1px 0;'
            u'break-inside:avoid;line-height:1.3">{}</div>'.format(_esc(it)) for it in items)
        return (u'<div style="page-break-inside:avoid;margin:1.3em 0 0.4em">'
                u'<div style="padding:5px 0;border-bottom:2px solid {c};font-size:1.05em;'
                u'font-weight:700;color:#1E2235;margin-bottom:6px">{t}</div>'
                u'<div style="column-count:{n};column-gap:22px">{b}</div></div>').format(
                    c=color, t=_esc(title), n=ncols, b=body)

    H = []
    if new_common:
        H.append(section(u"\U0001f195 New common sheets", u"#27AE60"))
        H += [sheet_table(b) for b in new_common]
    if chg_common:
        H.append(section(u"✳ Changed common sheets", u"#9013FE"))
        H += [sheet_table(b) for b in chg_common]
    if rem_common:
        H.append(section(u"\U0001f5d1 Removed common sheets", u"#E74C3C"))
        H += [sheet_table(b) for b in rem_common]

    if custom_blocks:
        H.append(section(u"\U0001f3d7 Custom sheets (building-specific)", u"#E87E20"))
        byp = OrderedDict()
        for b in sorted(custom_blocks, key=lambda x: x["number"]):
            byp.setdefault(b["prefix"], []).append(b)
        for pf in sorted(byp):
            H.append(u'<div style="page-break-after:avoid;margin:0.7em 0 0.2em;font-weight:700;'
                     u'color:#7A4A12;font-size:0.95em">Building {}</div>'.format(_esc(pf)))
            for b in byp[pf]:
                H.append(sheet_table(b))

    H.append(listblock(u"\U0001f9f9 Views no longer used on any sheet ({})".format(len(retired)),
                       u"#9099C8", list(retired), 4))
    H.append(listblock(u"✓ Unchanged sheets ({})".format(len(unchanged_only)), u"#7A8090",
                       [u"{} — {} ({} views)".format(num, name, cnt)
                        for num, name, cnt in unchanged_only], 3))

    body_parts = [p for p in H if p]
    body_html = (u'<p style="color:#27AE60;font-size:1em">&#x2705; No changes detected '
                 u'between the two snapshots.</p>'
                 if not body_parts else u"".join(body_parts))
    summary_html = u" &nbsp;&bull;&nbsp; ".join(_esc(x) for x in summ)

    return u"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Updates Report</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0;
         color: #1E2235; font-size: 12px; line-height: 1.4; }}
  h1 {{ font-size: 1.3em; font-weight: 700; color: #1E2235;
       border-bottom: 3px solid #9013FE; padding-bottom: 0.3em; margin: 0 0 0.2em; }}
  .meta {{ color: #666; font-size: 0.85em; margin-bottom: 0.8em; }}
  .summary {{ background: #F5F3FC; border-left: 4px solid #9013FE;
              padding: 0.5em 1em; margin-bottom: 1.2em;
              font-size: 0.9em; color: #333; }}
  @page {{ size: Letter landscape; margin: 1.1cm; }}
  @media print {{ .no-print {{ display: none; }} }}
</style>
</head>
<body>
  <div class="no-print" style="background:#1E2235;color:#E8EBF5;
       padding:10px 16px;margin-bottom:1.2em;border-radius:4px;font-size:0.9em">
    &#x1F4BE; File saved.
    &nbsp;&nbsp;&#x1F5A8; To save as PDF: <strong>Ctrl+P</strong>
    &rarr; choose <em>Save as PDF</em> (landscape).
  </div>
  <h1>Detail Sheets Update Report</h1>
  <div class="meta">
    <strong>Previous:</strong> {prev_date}
    &nbsp;&nbsp;&#x2022;&nbsp;&nbsp;
    <strong>Current:</strong> {curr_date}
    &nbsp;&nbsp;&#x2022;&nbsp;&nbsp;
    Generated: {today}
    &nbsp;&nbsp;&#x2022;&nbsp;&nbsp;
    {unchanged} unchanged sheet(s)
  </div>
  <div class="summary"><strong>Summary:</strong> {summary}</div>
  {body}
  <hr style="border:none;border-top:1px solid #DDD;margin:1.5em 0 0.6em">
  <div style="font-size:0.72em;color:#AAA;text-align:right">
    Common Details &mdash; Updates Report
  </div>
</body>
</html>""".format(
        prev_date=_esc(prev_date),
        curr_date=_esc(curr_date),
        today=_esc(TODAY_LABEL),
        unchanged=len(unchanged_only),
        summary=summary_html,
        body=body_html)


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
    context=u"Diff of two 'Sheets with Views' JSON snapshots. Renames are "
            u"detected by Detail ID, so a renamed view shows as a rename (not "
            u"remove+add); sheets are matched by number suffix so the report "
            u"survives a prefix migration. Use 'Copy Teams message' for a "
            u"paste-ready summary, or 'Export PDF' for the full per-sheet "
            u"5-column matrix (Added / Removed / Renamed / Renumber / Unchanged)."
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
                u"\U0001f522  {} renum".format(n))


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
         u"\U0001f195  {} new".format(new_sheets_oc.Count),
         "expNewSheets", "hdrNewSheetsCount", "icNewSheets")
_wire_oc(rem_sheets_oc, "badgeRemSheetsBorder", "badgeRemSheets",
         u"\U0001f5d1  {} removed".format(rem_sheets_oc.Count),
         "expRemSheets", "hdrRemSheetsCount", "icRemSheets")
_wire_oc(new_views_oc,  "badgeNewViewsBorder",  "badgeNewViews",
         u"✨  {} new views".format(new_views_oc.Count),
         "expNewViews",  "hdrNewViewsCount",  "icNewViews")
_wire_oc(gone_views_oc, "badgeGoneViewsBorder", "badgeGoneViews",
         u"\U0001f9f9  {} retired".format(gone_views_oc.Count),
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
