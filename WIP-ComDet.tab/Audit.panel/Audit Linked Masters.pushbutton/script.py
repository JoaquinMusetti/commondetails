# -*- coding: utf-8 -*-
__title__ = u'Audit Linked\nMasters'
__doc__ = (u'Receiver-model health check for the Common Details master views. '
           u'Scoped to the CD-system masters listed in master_map.json. Checks:\n'
           u'  1. Master views with Crop View ACTIVE (makes their dependents draw empty).\n'
           u'  2. The Common Details link is NOT set "By linked view" to the linked '
           u'view of the same scale (makes details come out empty at unused scales).\n'
           u'Detect + optional auto-fix. Fix #2 uses master_map.json to pick the '
           u'matching CD linked view per master.')

import sys
import os as _os
import json
from pyrevit import revit, DB, script, forms

_ext_dir = _os.path.dirname(_os.path.abspath(__file__))
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

doc = revit.doc

# ─────────────────────────────────────────────────────────────────────────────
# 1. Pick the Common Details link (likely candidate listed first)
# ─────────────────────────────────────────────────────────────────────────────

def _lname(li):
    # RevitLinkInstance.Name is like "file.rvt : 25 : location Internal" — keep the file part.
    try:
        n = li.Name
        return n.split(u" : ")[0] if n else u"?"
    except Exception:
        return u"?"

def _looks_cd(nm):
    u = nm.upper()
    return ("COMMON" in u) and ("DETAIL" in u)

links = list(DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance).ToElements())
if not links:
    ui.alert(u"This model has no Revit links — nothing to audit.",
             title=u"Audit Linked Masters")
    script.exit()

links_sorted = sorted(links, key=lambda li: (0 if _looks_cd(_lname(li)) else 1, _lname(li)))
cd_link = ui.pick_list(
    links_sorted, u"Pick the Common Details link",
    multiselect=False, name_fn=_lname,
    context=(u"Pick the linked Common Details model. The audit checks that each "
             u"master view shows it 'By linked view' at the matching scale. The "
             u"likely CD link is listed first.")
)
if not cd_link:
    script.exit()

# ─────────────────────────────────────────────────────────────────────────────
# 2. Load master_map.json → which masters belong to the CD system + name mapping
# ─────────────────────────────────────────────────────────────────────────────

mm_path = forms.pick_file(
    file_ext="json",
    title="Select master_map.json (CD ↔ building master-view map)"
)
if not mm_path:
    script.exit()
try:
    with open(mm_path, "r") as _f:
        _picked = json.load(_f)
except Exception as ex:
    ui.alert(u"Could not read that JSON:\n{}".format(ex), title=u"Audit Linked Masters")
    script.exit()

# Accept either master_map.json directly, OR the Sheets-with-Views JSON (then load
# master_map.json from the same folder). Guards against picking the wrong file
# (the sheets JSON has list values → would crash building the dict).
raw_map = None
if isinstance(_picked, dict) and _picked.get("format") == "sheets_with_views":
    _mm = _os.path.join(_os.path.dirname(mm_path), "master_map.json")
    if _os.path.isfile(_mm):
        try:
            with open(_mm, "r") as _f:
                raw_map = json.load(_f)
        except Exception:
            raw_map = None
    if raw_map is None:
        ui.alert(u"You picked the Sheets-with-Views JSON, but there's no readable "
                 u"master_map.json next to it. Pick master_map.json directly, or put "
                 u"it in the same folder.", title=u"Audit Linked Masters")
        script.exit()
elif isinstance(_picked, dict):
    raw_map = _picked   # assume it's the flat master_map

if (not isinstance(raw_map, dict) or not raw_map
        or not all(isinstance(k, basestring) and isinstance(v, basestring)
                   for k, v in raw_map.items())):
    ui.alert(u"That file is not a master_map (expected a flat {CD name: building name} "
             u"object). Pick master_map.json.", title=u"Audit Linked Masters")
    script.exit()

b2c = {v: k for k, v in raw_map.items()}        # building_name -> CD_name
relevant_names = set(raw_map.values())          # CD-system masters in this building

# Resolve names → master views (single pass, stops once all found)
masters = []
seen = set()
for v in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
    try:
        if v.IsTemplate:
            continue
        nm = v.Name
        if nm in relevant_names and nm not in seen:
            seen.add(nm)
            masters.append(v)
            if len(seen) == len(relevant_names):
                break
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# 3. Checks
# ─────────────────────────────────────────────────────────────────────────────

# Per-view link overrides are keyed by the link TYPE id, not the instance id
# (confirmed via probe: GetLinkOverrides(instanceId)=None; GetLinkOverrides(typeId)=real).
cd_link_id = cd_link.GetTypeId()
try:
    cd_doc = cd_link.GetLinkDocument()
except Exception:
    cd_doc = None
# Enum is DB.LinkVisibility (the *property* is LinkVisibilityType). Tolerate either name.
_LV = getattr(DB, "LinkVisibility", None) or getattr(DB, "LinkVisibilityType", None)
_HAS_LINK_API = (hasattr(DB, "RevitLinkGraphicsSettings")
                 and _LV is not None and hasattr(_LV, "ByLinkView"))

# CHECK 1: Crop View active
crop_bad = []
for v in masters:
    try:
        if v.CropBoxActive:
            crop_bad.append(v)
    except Exception:
        pass

# CHECK 2: CD link must show a linked view of the matching scale.
# OK whether the link visibility is ByLinkView OR Custom — Custom is used to also
# override model categories (e.g. turn off a line type that's on in the link) but
# still picks a linked view. The bad case is ByHostView (no linked view at all),
# which shows up as no/invalid LinkedViewId. So we judge by the LinkedViewId itself.
linkview_bad = []         # (master_view, reason)
cd_view_by_name = {}
if _HAS_LINK_API and cd_doc is not None:
    for v in masters:
        try:
            rgs = v.GetLinkOverrides(cd_link_id)
            lvid = rgs.LinkedViewId if rgs is not None else None
            lv = (cd_doc.GetElement(lvid)
                  if lvid and lvid != DB.ElementId.InvalidElementId else None)
            if lv is None:
                linkview_bad.append((v, u"no linked view set (link is 'By host view')"))
            elif lv.Scale != v.Scale:
                linkview_bad.append((v, u"linked view scale {} ≠ master {}".format(lv.Scale, v.Scale)))
            # else OK — has a linked view of matching scale (ByLinkView or Custom)
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────────────────────
# 4. Report
# ─────────────────────────────────────────────────────────────────────────────

rep = []
rep.append(u"AUDIT LINKED MASTERS — {}".format(_lname(cd_link)))
rep.append(u"CD-system masters found in this model: {} (of {} in master_map)".format(
    len(masters), len(relevant_names)))
rep.append(u"")
rep.append(u"1) Master views with Crop View ACTIVE  ->  {}".format(len(crop_bad)))
for v in crop_bad:
    rep.append(u"     - {}".format(v.Name))
rep.append(u"")
if not _HAS_LINK_API:
    rep.append(u"2) Linked-view check  ->  SKIPPED (API not available in this Revit)")
elif cd_doc is None:
    rep.append(u"2) Linked-view check  ->  SKIPPED (the CD link is not loaded)")
else:
    rep.append(u"2) Masters w/ wrong CD linked view  ->  {}".format(len(linkview_bad)))
    for v, r in linkview_bad:
        rep.append(u"     - {}  ({})".format(v.Name, r))
if not crop_bad and not linkview_bad:
    rep.append(u"")
    rep.append(u"✅ All audited masters are healthy.")

ui.show_report(u"\n".join(rep), title=u"Audit Linked Masters (WIP)")

# ─────────────────────────────────────────────────────────────────────────────
# 5. Fixes (each gated by a confirm)
# ─────────────────────────────────────────────────────────────────────────────

if crop_bad and ui.confirm(
        u"Untick 'Crop View' on {} master view(s)?".format(len(crop_bad)),
        title=u"Audit Linked Masters — fix crop"):
    t = DB.Transaction(doc, u"Untick Crop View on masters")
    t.Start()
    n = 0
    for v in crop_bad:
        try:
            v.CropBoxActive = False
            n += 1
        except Exception:
            pass
    t.Commit()
    ui.alert(u"Crop View unticked on {} view(s).".format(n), title=u"Audit Linked Masters")

# ── Fix flagged masters → ByLinkView + the linked view of the matching scale ──
# HARD API LIMIT: Revit does NOT allow setting 'Custom' link overrides
# ("Setting link overrides to type Custom is not supported via the API"). So:
#   - We can only set ByLinkView here (NOT Custom). We therefore touch ONLY the
#     flagged masters (those with NO linked view / 'By host view') to rescue their
#     empty details — and we leave correct Custom/ByLinkView masters untouched so we
#     don't downgrade them.
#   - The hidden '01 HMC Hidden - Red' line is a Custom override → can't be set via
#     API. Bake it into the linked CD view (so every ByLinkView master inherits it
#     hidden — recommended), or switch those masters to Custom manually.
if linkview_bad and cd_doc is not None and _HAS_LINK_API and ui.confirm(
        u"Set {} flagged master(s) to 'By linked view' at the matching scale "
        u"(linked view chosen via master_map)?\n\n"
        u"Note: the Revit API can't set 'Custom' link overrides, so this sets "
        u"ByLinkView only. To hide the '01 HMC Hidden - Red' line, hide it in the "
        u"linked Common Details view (recommended) or set Custom manually.".format(
            len(linkview_bad)),
        title=u"Audit Linked Masters — fix linked view"):
    if not cd_view_by_name:
        for cv in DB.FilteredElementCollector(cd_doc).OfClass(DB.View).ToElements():
            try:
                if not cv.IsTemplate:
                    cd_view_by_name[cv.Name] = cv
            except Exception:
                pass
    t = DB.Transaction(doc, u"Set CD linked view on flagged masters")
    t.Start()
    fixed = 0
    manual = []
    for v, r in linkview_bad:
        cd_name = b2c.get(v.Name)
        lv = cd_view_by_name.get(cd_name) if cd_name else None
        if lv is None:
            lv = cd_view_by_name.get(v.Name)     # fallback: same-name CD view
        if lv is None:
            manual.append(v.Name)
            continue
        try:
            # Reuse the view's existing settings object (avoids constructor questions);
            # flip to ByLinkView + the matching linked view. SET by INSTANCE id.
            rgs = v.GetLinkOverrides(cd_link_id)     # GET by TYPE id
            if rgs is None:
                rgs = DB.RevitLinkGraphicsSettings()
            rgs.LinkVisibilityType = _LV.ByLinkView
            rgs.LinkedViewId = lv.Id
            v.SetLinkOverrides(cd_link.Id, rgs)      # SET by INSTANCE id
            fixed += 1
        except Exception as ex:
            manual.append(u"{} ({})".format(v.Name, ex))
    t.Commit()
    msg = u"Set 'By linked view' on {} master(s).".format(fixed)
    if manual:
        msg += u"\n\nCould not update ({} — no master_map match or API error):\n{}".format(
            len(manual), u"\n".join(u"  - " + m for m in manual))
    ui.alert(msg, title=u"Audit Linked Masters")
