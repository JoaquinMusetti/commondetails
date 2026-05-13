# -*- coding: utf-8 -*-
__title__ = 'Align\nTitleblocks'
__doc__ = ('Moves titleblocks on selected sheets to match the position '
           'of the titleblock on a reference sheet. '
           'Select a reference sheet, then select the target sheets to align.')

from pyrevit import revit, DB, script, forms

doc    = revit.doc
output = script.get_output()

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

chosen_ref = forms.SelectFromList.show(
    sheet_options,
    title="Select Reference Sheet",
    prompt="Select the sheet whose titleblock position is correct:",
    multiselect=False
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
    forms.alert("No titleblock found on reference sheet.", exitscript=True)

ref_tb       = list(tb_collector)[0]
ref_location = ref_tb.Location.Point
ref_type_id  = ref_tb.GetTypeId()
ref_type     = doc.GetElement(ref_type_id)
ref_tb_family_name = ref_type.FamilyName if ref_type else ""

output.print_md("**Reference sheet:** `{} - {}`".format(
    ref_sheet.SheetNumber, ref_sheet.Name))
output.print_md("**Reference titleblock:** `{}`".format(ref_tb_family_name))
output.print_md("**Reference position:** X={:.4f}  Y={:.4f}".format(
    ref_location.X, ref_location.Y))

# ─────────────────────────────────────────────────────────────────────────────
# 3. Pick sheets to align
# ─────────────────────────────────────────────────────────────────────────────

# Remove reference sheet from options
target_options = [o for o in sheet_options if o != chosen_ref]

chosen_targets = forms.SelectFromList.show(
    target_options,
    title="Select Sheets to Align",
    prompt="Select sheets to move titleblock to reference position:",
    multiselect=True
)
if not chosen_targets:
    script.exit()

target_sheets = [sheet_by_option[o] for o in chosen_targets]
output.print_md("**Sheets to align:** {}".format(len(target_sheets)))

# ─────────────────────────────────────────────────────────────────────────────
# 4. Align titleblocks
# ─────────────────────────────────────────────────────────────────────────────

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
                output.print_md("  ⚠️ No titleblock on sheet {} - {}".format(
                    sheet.SheetNumber, sheet.Name))
                skipped += 1
                continue

            tb       = tbs[0]
            curr_loc = tb.Location.Point

            if (abs(curr_loc.X - ref_location.X) < 1e-6 and
                    abs(curr_loc.Y - ref_location.Y) < 1e-6):
                output.print_md("  ⏭️ Already aligned: {} - {}".format(
                    sheet.SheetNumber, sheet.Name))
                skipped += 1
                continue

            delta = DB.XYZ(
                ref_location.X - curr_loc.X,
                ref_location.Y - curr_loc.Y,
                0
            )
            tb.Location.Move(delta)
            output.print_md("  ✅ Aligned: {} - {}  (moved {:.4f}, {:.4f})".format(
                sheet.SheetNumber, sheet.Name, delta.X, delta.Y))
            moved += 1

        except Exception as e:
            errors.append("{} - {}: {}".format(sheet.SheetNumber, sheet.Name, str(e)))
            output.print_md("  ❌ Error: {} - {}".format(sheet.SheetNumber, sheet.Name))

# ─────────────────────────────────────────────────────────────────────────────
# 5. Report
# ─────────────────────────────────────────────────────────────────────────────

output.print_md("\n---")
output.print_md("✅ **{}** titleblocks aligned".format(moved))
output.print_md("⏭️ **{}** already correct, skipped".format(skipped))

if errors:
    output.print_md("❌ **{} errors:**".format(len(errors)))
    for e in errors:
        output.print_md("  - {}".format(e))