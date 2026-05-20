# -*- coding: utf-8 -*-
__title__ = 'Align\nTitleblocks'
__doc__ = ('Moves titleblocks on selected sheets to match the position '
           'of the titleblock on a reference sheet. '
           'Select a reference sheet, then select the target sheets to align.')

import sys
import os as _os
from pyrevit import revit, DB, script

_script_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir = _script_dir
while _ext_dir and not _ext_dir.endswith('.extension'):
    _ext_dir = _os.path.dirname(_ext_dir)
sys.path.append(_os.path.join(_ext_dir, 'lib'))
from magictools import ui

doc = revit.doc
SEP = u"─" * 55

# ─────────────────────────────────────────────────────────────────────────────
# 1. Collect all sheets
# ─────────────────────────────────────────────────────────────────────────────

all_sheets = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
all_sheets = sorted(all_sheets, key=lambda s: s.SheetNumber)

sheet_options   = []
sheet_by_option = {}
for s in all_sheets:
    label = "{} - {}".format(s.SheetNumber, s.Name)
    sheet_options.append(label)
    sheet_by_option[label] = s

# ─────────────────────────────────────────────────────────────────────────────
# 2. Pick reference sheet
# ─────────────────────────────────────────────────────────────────────────────

chosen_ref = ui.pick_list(
    sheet_options,
    "Select Reference Sheet",
    button_name="Next",
    multiselect=False,
    context=u"Reference sheet: the tool reads the exact position of the titleblock "
            u"on this sheet and uses it as the target position for all the other "
            u"sheets you'll pick next."
)
if not chosen_ref:
    script.exit()

ref_sheet = sheet_by_option[chosen_ref]

# Get titleblock position from reference sheet
ref_tb = None
ref_tb_family_name = None
tb_collector = DB.FilteredElementCollector(doc, ref_sheet.Id)\
    .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)\
    .WhereElementIsNotElementType()\
    .ToElements()

if not tb_collector:
    ui.alert("No titleblock found on reference sheet.", title="Align Titleblocks")
    script.exit()

ref_tb       = list(tb_collector)[0]
ref_location = ref_tb.Location.Point
ref_type_id  = ref_tb.GetTypeId()
ref_type     = doc.GetElement(ref_type_id)
ref_tb_family_name = ref_type.FamilyName if ref_type else ""

# ─────────────────────────────────────────────────────────────────────────────
# 3. Pick sheets to align
# ─────────────────────────────────────────────────────────────────────────────

target_options = [o for o in sheet_options if o != chosen_ref]

chosen_targets = ui.pick_list(
    target_options,
    "Select Sheets to Align",
    button_name="Align",
    context=u"Sheets to align. The titleblock on each one will be moved to match "
            u"the position of the reference sheet's titleblock. Useful after "
            u"importing sheets from a JSON, where titleblocks may end up offset."
)
if not chosen_targets:
    script.exit()

target_sheets = [sheet_by_option[o] for o in chosen_targets]

# ─────────────────────────────────────────────────────────────────────────────
# 4. Align titleblocks
# ─────────────────────────────────────────────────────────────────────────────

lines = []
lines.append(u"Reference: {} - {}".format(ref_sheet.SheetNumber, ref_sheet.Name))
lines.append(u"Titleblock: {}".format(ref_tb_family_name))
lines.append(u"Position: X={:.4f}  Y={:.4f}".format(ref_location.X, ref_location.Y))
lines.append(u"Sheets to align: {}".format(len(target_sheets)))
lines.append(u"")
lines.append(SEP)

moved   = 0
skipped = 0
errors  = []

with revit.Transaction("Align Titleblocks"):
    for sheet in target_sheets:
        try:
            tbs = list(DB.FilteredElementCollector(doc, sheet.Id)
                .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                .WhereElementIsNotElementType()
                .ToElements())

            if not tbs:
                lines.append(u"  ⚠  No titleblock: {} - {}".format(
                    sheet.SheetNumber, sheet.Name))
                skipped += 1
                continue

            tb       = tbs[0]
            curr_loc = tb.Location.Point

            if (abs(curr_loc.X - ref_location.X) < 1e-6 and
                    abs(curr_loc.Y - ref_location.Y) < 1e-6):
                lines.append(u"  ⏭  Already aligned: {} - {}".format(
                    sheet.SheetNumber, sheet.Name))
                skipped += 1
                continue

            delta = DB.XYZ(
                ref_location.X - curr_loc.X,
                ref_location.Y - curr_loc.Y,
                0
            )
            tb.Location.Move(delta)
            lines.append(u"  ✅ Aligned: {} - {}  (moved {:.4f}, {:.4f})".format(
                sheet.SheetNumber, sheet.Name, delta.X, delta.Y))
            moved += 1

        except Exception as e:
            errors.append(u"{} - {}: {}".format(sheet.SheetNumber, sheet.Name, str(e)))
            lines.append(u"  ❌ Error: {} - {}".format(sheet.SheetNumber, sheet.Name))

# ─────────────────────────────────────────────────────────────────────────────
# 5. Report
# ─────────────────────────────────────────────────────────────────────────────

lines.append(u"")
lines.append(SEP)
lines.append(u"✅  {} titleblocks aligned".format(moved))
lines.append(u"⏭️  {} already correct, skipped".format(skipped))

if errors:
    lines.append(u"❌  {} error(s):".format(len(errors)))
    for e in errors:
        lines.append(u"  - {}".format(e))

ui.show_report(
    text     = u"\n".join(lines),
    title    = u"Align Titleblocks",
    subtitle = u"Reference: {} - {}".format(ref_sheet.SheetNumber, ref_sheet.Name),
    summary  = u"✅ {}  ⏭️ {}  ❌ {}".format(moved, skipped, len(errors)),
)
