---
name: E.V.E. document + slide design system
description: When generating Google Docs or Slides, apply the E.V.E. visual design (color palette, typography, real headings/tables, cover pages) instead of dumping markdown plaintext
type: feedback
---

When generating any Google Doc or Slides presentation, you MUST apply the E.V.E. design system rather than dumping markdown-flavored plaintext via `create_doc` / `create_presentation`. The default "# Title" text doesn't render as a heading in Docs — it's literal characters. Tables drawn with pipes don't get cell borders. Customers see something that looks like raw text, not a real document.

Apply this every time, even for "throwaway" docs — consistency builds trust.

## Color palette (named, with both formats)

| Name | Hex | rgb floats (for Slides API) | Use |
|---|---|---|---|
| Navy | `#1a3a52` | `0.102, 0.227, 0.322` | H1 titles, cover banners |
| Slate | `#334155` | `0.200, 0.255, 0.341` | H2 + body text |
| Charcoal | `#64748b` | `0.392, 0.455, 0.545` | H3, captions, secondary text |
| Accent blue | `#4285f4` | `0.259, 0.522, 0.957` | Vendor / cloud-layer boxes, links |
| Accent green | `#34a853` | `0.204, 0.659, 0.325` | Customer / instance-layer boxes, success badges |
| Accent gray | `#5f6368` | `0.373, 0.388, 0.408` | Internet / middle-layer boxes |
| Accent amber | `#f59e0b` | `0.961, 0.620, 0.043` | Warnings, "expiring" status |
| Accent red | `#ef4444` | `0.937, 0.267, 0.267` | Errors, "expired" status |
| Paper | `#fafafa` | `0.980, 0.980, 0.980` | Page background, callout fills |

Never invent new colors for one-off elements. If the palette doesn't have what you need, the design needs to change, not the palette.

## Typography

- **Body font:** Inter (or system default if unavailable)
- **Mono font:** JetBrains Mono (for code blocks, file paths, registration tokens)
- **Sizes in Docs:** H1 = 24pt, H2 = 18pt, H3 = 14pt, body = 11pt, small/caption = 9pt
- **Sizes in Slides:** Cover title = 36pt, slide title = 22pt, section header = 16pt, body = 14pt, caption = 10pt

## Google Docs — patterns I MUST use

### Real headings (not "# markdown")

After `create_doc`, fire `batch_update_doc` with `update_paragraph_style` ops carrying `named_style_type: "HEADING_1" | "HEADING_2" | "HEADING_3"`. Then `update_text_style` to set the foreground color (Navy for H1, Slate for H2, Charcoal for H3) and bold = true.

Example shape:
```json
{"type": "update_paragraph_style",
 "start_index": <idx>, "end_index": <idx+len>,
 "style": {"named_style_type": "HEADING_1"},
 "fields": "named_style_type"}
```

### Real tables (not pipe-character markdown)

Use `insert_table` with rows + columns; then `insert_text` per cell with `cellLocation`. Header row gets a slate fill + white bold text via `update_table_cell_style`. Body rows get alternating paper / white shading for readability.

### Cover page (any doc longer than 2 pages)

Top of doc:
- Title (H1, Navy, centered)
- Subtitle (Slate, smaller, centered)
- Empty paragraph
- "Prepared by Eve · for [Customer] · YYYY-MM-DD" line (Charcoal, smaller, right-aligned)
- Page break before main content

### Callout boxes

A shaded paragraph block for key takeaways. Use `update_paragraph_style` with `shading_color` = Paper for default, or Accent amber-light for warnings, Accent green-light for confirmations. Left-padded with `indent_start` ~ 24pt.

### Bold key terms inline

Use `update_text_style` with `bold: true` on the terms a skim-reader needs to catch. Don't rely on the reader picking up "important phrases" from prose.

### Headers + footers

`update_doc_headers_footers` to set: header = "[Doc title]", footer = "E.V.E. · YYYY-MM-DD · page N of M".

## Google Slides — patterns I MUST use

### Use the layout enum when possible

`createSlide` with `predefinedLayout: "TITLE_ONLY" | "TITLE_AND_BODY" | "TITLE_AND_TWO_COLUMNS" | "SECTION_HEADER" | "BLANK"`. BLANK only for diagram slides where I need full positioning control.

### Cover slide

Layout = SECTION_HEADER. Title text = the deck title (36pt, Navy, bold, centered). Subtitle below it (14pt, Slate). Optional date footer (10pt, Charcoal).

### Section dividers (for decks > 5 slides)

Navy full-bleed rectangle background, white centered title, 32pt. Used to break long decks into chapters.

### Architecture / layered diagrams

The 3-band pattern I built for the E.V.E. architecture slide is the template:
- Title bar at top (Navy bg, white text, 22pt bold)
- 3 horizontal bands (Vendor / Internet / Customer or equivalent), each with:
  - Header text in the band's accent color (blue/gray/green)
  - 3-4 rounded rectangles inside, accent-colored fill, white text
  - Content alignment MIDDLE so text vertically centers in each box
- Optional arrows between bands

### Side-by-side comparison

Layout = TITLE_AND_TWO_COLUMNS. Each column has its own header + bullet list. Useful for "Option A vs Option B" framing.

### Status footer on every content slide

Small text box (10pt, Charcoal, right-aligned) at the bottom: "E.V.E. · slide N of M". Skip on cover + section dividers.

### Object IDs must be ≥ 5 characters

The Slides API rejects shorter IDs. Use descriptive names like `cover_title`, `band_vendor_bg`, `box_dashboard`, not `t1` or `b2`.

## Pre-flight checklist

Before completing a `create_doc` / `create_presentation` task, verify:

1. [ ] Headings rendered as real headings (paragraph style, not markdown)
2. [ ] Tables are real tables, not pipes
3. [ ] Color palette used consistently (no off-palette one-offs)
4. [ ] Cover page or cover slide for anything > 2 pages / 5 slides
5. [ ] Footer with "E.V.E. · YYYY-MM-DD" on docs; "slide N of M" on slides
6. [ ] Key terms bolded inline so skim-readers catch them
7. [ ] At least one visual element per slide (diagram, table, icon) — pure-text slides should be the exception

## Why this exists

Customers judge the product by its outputs. A doc that looks like a markdown dump signals "this AI is amateur" even if the content is great. A doc that looks designed signals "this AI is serious." The compounding effect across hundreds of customer-Eve-generated documents is significant — same way every Apple keynote slide looks like an Apple slide because the template is dialed in, not because each was hand-crafted.

## See also

- `eve-tools/eve_style.py` — Python helpers for box-side scripts that generate docs/slides programmatically. Same palette and patterns.
