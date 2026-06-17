# Common Details — Workflow Overview (video outline)

> Insumo para el equipo HMC. Overview del flujo core de las tools de Common Details.
> Idioma del video: **English**. Alcance: **core happy-path** (5 etapas).

**Target length:** ~6–8 min · **Audience:** HMC Revit users · **Goal:** understand the
end-to-end flow, not every button.

The 5 stages map to the four Main Tools buttons (`Create Detail Views`, `Export Details`,
`Pre-Import Audit`, `Import Details`) plus the `Assign Detail IDs` prep step.

---

## 0. Cold open — the one idea (~45s)
- **The mental model to land first:** Common Details (CD) is the *coordinate hub*. Every
  building model links CD. Details live once in CD and get pushed/pulled between models
  through that shared coordinate space.
- Why it matters: draw a detail once → reuse across buildings, no manual re-placing,
  coordinates stay aligned.
- Show: the ribbon tab `WIP-ComDet` with the 4 Main Tools buttons. Tell the viewer these
  are the only 4 they need for the core loop.

## 1. Create Detail Views (~1.5 min)
- Where: inside a master view in CD.
- **Prep shown on screen:** draw rectangles with the reserved LineStyle; drop a TextNote
  inside each one to name the future dependent view.
- Action: select the rectangles **and** the name TextNotes → run `Create Detail Views` →
  pickers offer only the LineStyles / TextNoteTypes found in the selection.
- Result: dependent views created; source rectangles + labels auto-deleted.
- Takeaway: "rectangle = a view's crop; textnote = its name."

## 2. Assign Detail IDs (~45s)
- Where: in CD, **before exporting** (this is the prep gate, not optional).
- Action: select sheets → run `Assign Detail IDs` → every view without one gets a
  sequential 4-digit ID.
- Why: the ID is the stable key that survives the export/import round-trip and renames.
  No ID → things can't be matched downstream.

## 3. Export Details (~1 min)
- Action: run `Export Details` → bundles the dependent views + sheet layout into a single JSON.
- **Concept to stress:** the JSON stores coordinates *relative to CD*, so it's
  direction-agnostic — CD→building, building→CD, building→building, or CD→CD round-trip.
- Show: where the JSON lands / how to hand it off.

## 4. Pre-Import Audit (~1.5 min)
- Frame it as **the gate between Export and Import** — run it in the destination model
  before importing.
- Action: point it at the JSON → it compares against the destination and reports: missing
  sheets, views not in model, views not placed, moved/removed viewports, detail-number and
  viewport-type mismatches.
- Highlight the **"Create" action** that materializes missing sheets from a reference sheet.
- Takeaway: "green light here = clean import. Don't skip it."

## 5. Import Details (~1.5 min)
- Where: in the destination model (a building, or back into CD).
- Action: run `Import Details` → reads the JSON → recreates dependent views + sheet layout
  in the right coordinate space.
- Show the picker choice that selects the CD link (or the "None — importing into Common
  Details itself" option) so viewers connect it back to the coordinate-hub idea from the
  cold open.
- Result: details land placed and aligned, no manual repositioning.

## 6. Close — the loop in one breath (~30s)
- Recap as a single line: **Create → Assign IDs → Export → Audit → Import.**
- One sentence on what lives beyond this video (Adjustments + Audit panels: detail numbers,
  titleblock alignment, renames, change reports) → "covered separately."
- Point to where the extension / docs live.

---

## Recording tips for clarity
- Use one real detail you carry through all 5 stages — same view, same name, start to
  finish. Continuity beats coverage.
- Keep the ribbon visible; zoom the cursor onto each button before clicking.
- After each tool, cut to the *result* (the created view / the JSON / the audit table)
  before moving on.
