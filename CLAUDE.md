# Common Details — Extension Context (for Claude)

> pyRevit extension de **/slantis** para el cliente **HMC** (repo `JoaquinMusetti/commondetails`, privado, rama `main` = canónica).
> Toolbar del workflow de **Common Details (CD)**: crear vistas dependientes, exportar/importar detalles entre modelos usando CD como hub de coordenadas, auditar y ajustar láminas/vistas.
> **Runtime:** IronPython 2.7 sobre pyRevit. **UI:** Noir WPF dark theme vía `magictools.ui`.
> Conteo actual: **4 paneles · 19 pushbuttons**. Last updated: 2026-06-16.

> 📒 Historial: [LOG.md](LOG.md) · 📋 Pendientes: [BACKLOG.md](BACKLOG.md) · 📄 Guía de usuario (HMC): [README.md](README.md) · 🎬 Outline de video: [WORKFLOW-VIDEO-OUTLINE.md](WORKFLOW-VIDEO-OUTLINE.md)

> ⚠️ **Deployment — fuente vs. copia compartida (anti-drift).** Esta carpeta (`G:\…\PROCESS\Claudio\Common Details.extension`, tab **`WIP-ComDet.tab`**, repo `commondetails`) es la **fuente/dev**. El prefijo `WIP-` evita la colisión cuando se cargan ambas a la vez en pyRevit.
> La **copia que usa HMC** vive en **ACC**: `…\04.0 ARCHITECTURAL\Scripts\Common Details.extension`, tab **`Common Details.tab`** (sin prefijo). **Nunca editar la copia de ACC in situ** — se actualiza desde la fuente, igual que la regla de Magic Tools.
> Junto a la extensión deployada en ACC viven las guías formales que recibe HMC (`Common Details — Tools Guide.pdf`, `Common Details — Tutorial Guide.pdf`) y la carpeta `Export files` (JSONs de intercambio). El [README.md](README.md) de este repo es un **quick-reference para dev/onboarding** y **no está sincronizado** con esos PDFs (los PDFs son el material canónico para HMC). Si cambian las tools, actualizar ambos por separado.

---

## Concepto central — CD como hub de coordenadas

Common Details es el **modelo hub**. Cada modelo de edificio **linkea** CD. Los detalles se dibujan
una vez (en CD o en un edificio) y se mueven entre modelos a través del **espacio de coordenadas
compartido de CD**. El Export guarda coordenadas *relativas a CD* (vía el link instance), por eso el
Import es **direction-agnostic**: funciona en las 4 direcciones — `CD→building`, `building→CD`,
`building→building` y `CD→CD` (round-trip).

La clave estable de emparejamiento entre Export/Audit/Import es el **Detail ID** (4 dígitos), no el
ElementId — los IDs difieren entre documentos. Por eso se corre `Assign Detail IDs` en CD **antes**
de exportar.

---

## Convenciones del repo

- **UI Noir.** Toda ventana usa `magictools.ui` (`ui.parse`, `ui.pick_list`, `ui.alert`, `ui.confirm`,
  `ui.ask_for_string`). Los pickers llevan `context=`. Lib en `lib/magictools/ui.py`.
- **Transacciones.** Todo cambio de modelo envuelto en `DB.Transaction`. Los reportes read-only usan
  `script.get_output()`, sin transacción.
- **Metadata.** `__title__` de 2 líneas, `__doc__` de un párrafo.
- **Path de lib.** Los scripts suben hasta `*.extension` y agregan `lib/` a `sys.path` (algunos
  hardcodean el path absoluto — anti-patrón heredado, normalizar al subir cambios).
- **Archivos de soporte en `lib/`:** `common_details_shared_params.txt` (shared params que el Import
  inyecta — p. ej. Detail ID), `master_map.json` (mapeo master view ↔ linked view de CD por escala,
  usado por `Audit Linked Masters` y el fix #2).

---

## QPI — Quick Panel Index

> Nombres = carpetas reales en `WIP-ComDet.tab/`. `__title__` entre comillas.

### `Main Tools.panel` — el flujo core
- **Create Detail Views** (`Dependent Views Creator`) — crea vistas dependientes a partir de
  rectángulos de Detail Line dibujados en la vista activa. Prep: dibujar rectángulos con un LineStyle
  reservado + (opcional) un TextNote adentro para nombrar la vista. Seleccionar rectángulos **y**
  TextNotes → correr. Borra los rectángulos/labels al terminar.
- **Export Details** (`Export Sheets with Views`) — empaqueta vistas dependientes + layout de láminas
  en un **JSON** con coordenadas relativas a CD. Hub de las 4 direcciones.
- **Pre-Import Audit** — gate entre Export e Import. Compara un JSON contra el modelo destino y reporta
  sheets faltantes (con acción **Create** desde una sheet de referencia), vistas no en el modelo, vistas
  no colocadas, viewports movidos/borrados, y mismatches de detail number / viewport type.
- **Import Details** (`Import Sheets with Views`) — lee el JSON y recrea vistas dependientes + layout en
  el documento activo, en el espacio de coordenadas correcto. Direction-agnostic. Inyecta los shared
  params desde `common_details_shared_params.txt`.

### `Audit.panel`
- **Updates Report** (`02 Audit Source Sheets`) — compara dos snapshots JSON (previo vs. actual) del
  modelo fuente y arma un change report Teams-ready: vistas agregadas/quitadas por lámina, movidas entre
  láminas, ya no usadas, cambios de tipo de vista, cambios de detail number.
- **New Details** — compara un JSON contra el modelo actual: vistas en JSON ya no en el modelo, vistas
  nuevas sin exportar, y detecta posibles renames matcheando Title on Sheet. Genera `renames.json`.
- **Audit Linked Masters** — health check del modelo receptor sobre los master views de CD (scope por
  `master_map.json`). Chequea: (1) masters con Crop View activo (hacen que sus dependientes salgan
  vacíos), (2) el link de CD NO seteado "By linked view" al linked view de la misma escala. Detect +
  auto-fix opcional.

### `Detail Performance.panel`
- **Scan View Density** — escanea todas las vistas y cuenta detail lines / detail items / dimensiones /
  text notes por vista, ordenado por líneas desc. Identifica vistas con drafting manual pesado.
- **Scan Line Density** — escanea la vista activa por zonas con alta concentración de detail curves.
  Clusteriza celdas calientes; doble-click en una fila hace zoom y selecciona sus curvas.

### `Adjustments.panel`
- **`Sheet Tools.pulldown`**
  - **Align Titleblocks** (`Align TitleBlocks`) — mueve los titleblocks de las sheets seleccionadas a la
    posición del titleblock de una sheet de referencia.
  - **Apply Detail Numbers** (`Detail Number`) — lee `positions.json` y asigna detail numbers a los
    viewports de las sheets seleccionadas (zone matching por esquina inferior-derecha; nearest neighbor
    como fallback).
  - **Sheet Number Replace** — find & replace de un string en los sheet numbers, en batch, con preview.
  - **Transfer Sheets** — transfiere sheets del modelo activo a otro proyecto abierto (con titleblocks y
    parámetros; renombra duplicados con sufijo `_1`, `_2`).
- **`View Tools.pulldown`**
  - **Assign Detail IDs** — asigna un Detail ID secuencial de 4 dígitos a toda vista dependiente que no
    tenga. **Correr en CD antes de exportar.** Clave estable del workflow.
  - **Clean Old Dependents** — lista vistas dependientes (de los masters seleccionados) que no están en
    ninguna sheet y deja elegir cuáles borrar. Higiene tras iteraciones.
  - **Annotation Crop Offset** (`Crop Offset`) — setea el annotation crop offset (en pulgadas) de las
    vistas seleccionadas. Soporta 3D, plantas, RCP, area plans, elevaciones y secciones.
  - **Hide Crop Regions** — apaga la crop region visible en todas las dependientes de los masters
    seleccionados.
  - **Set Title On Sheet** — setea el parámetro Title on Sheet de las dependientes según el view name
    (si el nombre arranca con prefijo `##_`, lo recorta).
  - **Rename Views** (`Update view names`) — lee `renames.json` y renombra vistas dependientes en el
    destino. Correr antes de Import cuando los nombres cambiaron en la fuente.

---

## Revit API Quirks — específicos de esta extensión

> Los gotchas reusables (link-override id asymmetry, `get_BoundingBox` regen, `LabelLineLength`,
> `LabelOffset` anchor, etc.) viven en el **CLAUDE.md del root**. Acá solo lo propio de CD.

### `master_map.json` — contrato del Audit Linked Masters
El audit y su fix #2 dependen de que `master_map.json` mapee cada master view de CD-system a su linked
view de la misma escala. Si un master nuevo no está en el map, queda fuera del scope del audit.

### Detail ID como clave de matching
Export / Pre-Import Audit / Import emparejan por **Detail ID**, no por ElementId ni por nombre (los
nombres se renombran). Una vista sin Detail ID no se puede trackear cross-model → siempre correr
`Assign Detail IDs` en CD antes del primer export.

### Crop View activo en masters → dependientes vacías
Un master con Crop View activo hace que **sus dependientes dibujen vacío**. Es el chequeo #1 de
`Audit Linked Masters` y una causa típica de "el detalle salió en blanco en el edificio".
