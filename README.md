# Common Details — Quick Reference

A pyRevit toolbar for the **Common Details (CD)** workflow: draw a detail once, reuse it across every
building model, and keep all the placements aligned automatically.

> ℹ️ **This is a quick reference for dev / onboarding.** The formal HMC user docs are the PDFs deployed
> next to the extension in ACC — `Common Details — Tools Guide.pdf` and `Common Details — Tutorial
> Guide.pdf`. This README is **not** kept in sync with them; if tools change, update both separately.
>
> 🎬 A short video overview of the core workflow is outlined in
> [WORKFLOW-VIDEO-OUTLINE.md](WORKFLOW-VIDEO-OUTLINE.md).

---

## The big idea — Common Details is the coordinate hub

Every building model **links** the Common Details (CD) file. Details live once in CD and are pushed or
pulled between models through that shared coordinate space. Because the data is stored relative to CD,
the same tools work in every direction:

- **CD → building** (publish a detail into a building)
- **building → CD** (bring a building's detail back into the hub)
- **building → building** (via CD as the common space)
- **CD → CD** (round-trip)

You never re-place details by hand — coordinates stay aligned through CD.

---

## The core loop (5 steps)

```
Create Detail Views  →  Assign Detail IDs  →  Export Details  →  Pre-Import Audit  →  Import Details
        (in CD)              (in CD)            (JSON out)         (in destination)     (in destination)
```

All four main buttons live in the **`WIP-ComDet` ribbon tab → Main Tools panel** (Assign Detail IDs is
under *Adjustments → View Tools*).

### 1. Create Detail Views — *in Common Details*
1. Open a master view in CD.
2. Draw a **rectangle** with the reserved LineStyle around each region you want as a view.
3. (Optional) Drop a **TextNote** inside each rectangle to name the future view.
4. **Select the rectangles and the name TextNotes**, then run **Create Detail Views**.
5. The tool creates the dependent views and deletes the source rectangles/labels automatically.

> Think: *rectangle = a view's crop; textnote = its name.*

### 2. Assign Detail IDs — *in Common Details, before exporting*
Select the sheets and run **Assign Detail IDs**. Every view without one gets a sequential 4-digit ID.
This ID is the stable key that lets the Audit and Import match views across models, even after renames.
**Don't skip this** — a view with no Detail ID can't be tracked downstream.

### 3. Export Details
Run **Export Details**. It bundles the dependent views + sheet layout into a single **JSON** file with
coordinates relative to CD. Note where the JSON is saved — that's what you hand off to the destination.

### 4. Pre-Import Audit — *in the destination model*
Before importing, run **Pre-Import Audit** and point it at the JSON. It compares the JSON against the
destination model and reports:
- Missing sheets — with a **Create** action that builds them from a reference sheet
- Views not in the model / views not placed
- Viewports moved or removed
- Detail number and viewport type mismatches

Treat a clean audit as your green light. Resolve the issues here so the import lands clean.

### 5. Import Details — *in the destination model*
Run **Import Details** and select the JSON. When prompted, pick the **Common Details link** so the tool
can translate coordinates into this model's space — or choose *"None — importing into Common Details
itself"* if you're importing back into CD. The views and sheet layout are recreated, placed and aligned,
with no manual repositioning.

---

## Supporting tools

You won't need these every run, but they're there when you do.

### Audit panel
- **Updates Report** — compare two export snapshots (before/after) of the source model and get a
  Teams-ready change report (views added/removed/moved, type changes, detail-number changes).
- **New Details** — compare a JSON against the current model: find views removed, new views not yet
  exported, and likely renames (generates `renames.json`).
- **Audit Linked Masters** — health check in the receiving model: flags master views with Crop View
  active (which make their dependents draw empty) and CD links not set "By linked view" at the right
  scale. Offers an auto-fix.

### Detail Performance panel
- **Scan View Density** — rank all views by how much manual drafting (lines, items, dims, text) they
  carry — find optimization candidates.
- **Scan Line Density** — find hot zones of detail curves in the active view; double-click a row to zoom
  to it.

### Adjustments panel
- **Sheet Tools:** Align Titleblocks · Apply Detail Numbers · Sheet Number Replace · Transfer Sheets
- **View Tools:** Assign Detail IDs · Clean Old Dependents · Annotation Crop Offset · Hide Crop Regions ·
  Set Title On Sheet · Rename Views

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Detail comes out **empty** in the building | The CD master view has **Crop View active**, or the CD link isn't "By linked view" at that scale | Run **Audit Linked Masters** → apply the auto-fix |
| Views don't match between models | Views were exported **without a Detail ID** | Run **Assign Detail IDs** in CD, re-export |
| View names changed in CD and the import is confused | Names changed after the last export | Run **New Details** (generates `renames.json`) → **Rename Views** in the destination → then Import |
| Import lands on a sheet that doesn't exist | Sheet missing in destination | Use the **Create** action in **Pre-Import Audit** |

---

## Requirements

- Revit + pyRevit installed, with this extension registered.
- The destination building model must **link Common Details**.
- Run the steps in order — the Detail ID and the Pre-Import Audit are the two guardrails that keep the
  round-trip clean.
