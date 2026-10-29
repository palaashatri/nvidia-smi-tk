# GPU Monitor — visual identity

Original, independently drawn desktop icon family; muted metal, soft enamel and restrained highlights inspired by the Loom family. No third-party brand marks or stock images are embedded.

| Asset | Use |
| --- | --- |
| `app-icon.svg` | Primary scalable desktop/application icon (512-unit artboard) |
| `app-icon-light.svg` | Light Platinum-style surfaces |
| `app-icon-dark.svg` | Dark desktop surfaces |
| `app-icon-mono.svg` | Low-color dock/menu adaptations |
| `symbol.svg` | Transparent brand symbol |
| `wordmark.svg` | Horizontal identity lockup |
| `social-preview.svg` | 1200×630 repository/social artwork |
| `favicon.svg` | Browser/repository miniature |
| `palette.json` | Brand colors and typography notes |
| `export.py` | Deterministic PNG/ICO rasterization |

![GPU Monitor icon](app-icon.svg)

## Export

Install `cairosvg` and `Pillow`, then run `python assets/brand/export.py` from the repository root. Exports go to `assets/brand/generated/` and include 16/32/48/64/128/256/512/1024 PNGs, a Windows ICO, and a 1200×630 PNG banner. Preserve the SVG as the design source of truth. For macOS ICNS, use `iconutil` or an equivalent iconset conversion from the rendered PNGs.

## Visual rules

- Keep rounded-square silhouettes optically centered with padding for desktop masks.
- No external fonts are embedded; lockups fall back to Segoe UI and Arial.
- Use monochrome artwork on cramped toolbars; reserve gradients for launcher and marketing sizes.
- Do not reuse a corporate GPU vendor logo or imply third-party endorsement.
- Aim for visual consistency with Loom without copying its pixels.

Primary colors: `#364e49`, `#1c302c`; accent: `#a3c2a4`; highlight: `#e5eee1`.
