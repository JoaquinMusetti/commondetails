# Common Details — BACKLOG (📋 Pendientes)

> Pendientes de esta extensión. Convenciones del monorepo en el CLAUDE.md del root.

## Deployment & sincronización (fuente WIP ↔ ACC)

- [ ] **Verificar que la copia deployada en ACC esté al día con la fuente.** La extensión en
  `…\04.0 ARCHITECTURAL\Scripts\Common Details.extension` es del ~31-may/1-jun; el repo `commondetails`
  tiene commits posteriores (ej. `7de189f` remove stray positions.json). Confirmar si HMC está corriendo
  una versión atrasada y re-deployar si hace falta.
- [ ] **Documentar (o automatizar) el mecanismo de deploy WIP → ACC.** Hoy es manual y no está escrito
  cómo se copia la fuente a `Common Details.tab` en ACC. Definir el procedimiento anti-drift (análogo al
  `git pull` de Magic Tools) para que la copia compartida nunca se edite in situ.
- [ ] **Drift de los PDFs de HMC.** `Common Details — Tools Guide.pdf` y `Tutorial Guide.pdf` (ACC) son
  del 1-jun; hubo refactor de tools después. Validar que sigan reflejando el set actual de 19 tools y
  regenerarlos si quedaron desactualizados.

## Documentación

- [ ] **Grabar y publicar el video de overview** del workflow core (guion en
  `WORKFLOW-VIDEO-OUTLINE.md`). Joaquín graba con OBS; falta editar (title cards + timestamps) y
  compartir con HMC.
- [ ] **Mantener el conteo de tools al día** en `CLAUDE.md` (hoy 4 paneles · 19 pushbuttons) cuando se
  agreguen/quiten tools.

## Higiene de código

- [ ] **Normalizar el `sys.path` hardcodeado** en los scripts que apuntan al path absoluto de `lib/` —
  migrar al patrón de subir hasta `*.extension` (como `Export`/`Import`). Anti-patrón heredado.
- [ ] **Sincronizar `lib/magictools/ui.py`** con la versión canónica del resto del monorepo (chequear
  que tenga `context=` y `show_report`).
- [ ] **`master_map.json`** — documentar/versionar el contrato y validar que cubra todos los CD-system
  masters actuales (si falta uno, queda fuera del scope de `Audit Linked Masters`).

## Producto / convenciones

- [ ] **Sacar el prefijo `WIP-` de la tab** cuando la extensión se considere estable
  (`WIP-ComDet.tab` → `ComDet.tab`). Coordinar con el nombre de la tab deployada (`Common Details.tab`).
- [ ] **Completar `extension.json`** — `author_profile`, `url`, `website`, `image` están vacíos.
- [ ] **Auditar QPI vs. carpetas reales** con la skill `/audit-tools` una vez estabilizada, para que el
  índice del `CLAUDE.md` no derive de las `.pushbutton` en disco.
