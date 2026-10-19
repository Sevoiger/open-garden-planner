# Provenance — plant sprites

| What | Detail |
| ---- | ------ |
| Files | `species/*.svg` (246), `categories/*.svg` (15) |
| Generator | `scripts/generate_plant_sprites.py` + `illustrations.json` recipes (this repo — reproducible provenance) |
| Method | Procedural SVG generation, seeded per sprite name, deterministic bytes |
| Style | "Lush Sprite" — approved by the project owner 2026-07-26 in the #281 style lab (contact-sheet sign-off) |
| External assets | None. No image-model output, no traced artwork, no third-party SVGs |
| License | Project license (GPL-3.0) — all geometry and palettes authored in-repo |
| History | Replaces the hand-authored 2026-02/2026-03 SVG set (Phases 6/11); those files carried no external provenance either |

Regenerate: `venv/Scripts/python.exe scripts/generate_plant_sprites.py`
Verify no drift: `… --check` (also enforced by `tests/unit/test_plant_sprite_conformance.py`).

Personal fork extension, 2026-10-19: 138 new seeded recipes reuse the existing
Lush Sprite primitives. Owner approved the complete QtSvg contact sheet (256/64 px,
rotated lawn/soil samples) before integration. These are conventional plan symbols,
not botanical plates or cultivar depictions. No third-party assets or new licenses.
