"""eve_style.py — E.V.E. design-system constants + request builders.

Box-side scripts that generate Google Docs / Slides import from here so
every output across the customer fleet looks the same. See the
companion memory file `feedback_doc_slide_design_system.md` for the
narrative rules; this module is the executable form.

The constants (COLORS, FONTS, SIZES) are the source of truth. The
`doc_*` and `slide_*` helpers return Google Workspace API request
objects ready to pass into `batch_update_doc` / `batch_update_presentation`.

Quick example:

    from eve_style import COLORS, doc_heading, doc_callout

    requests = [
        *doc_heading("E.V.E. Architecture", level=1),
        *doc_paragraph("This document covers..."),
        *doc_callout("Key takeaway: ...", tone="success"),
    ]
    # then: batch_update_doc(document_id=..., requests=requests)

Each builder returns a *list* of requests because some operations
require multiple (an insert_text + an update_paragraph_style + an
update_text_style for a single styled heading). Callers concat the
lists together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


# ────────────────────────────────────────────────────────────────────────
# Design constants — color palette
# ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Color:
    """A palette color in three formats: hex string for human eyes, rgb
    floats in 0..1 for the Slides API, rgb ints in 0..255 for occasional
    code that needs them. All three derive from the hex."""

    hex: str

    @property
    def rgb_floats(self) -> dict[str, float]:
        h = self.hex.lstrip("#")
        return {
            "red": int(h[0:2], 16) / 255,
            "green": int(h[2:4], 16) / 255,
            "blue": int(h[4:6], 16) / 255,
        }

    @property
    def rgb_color(self) -> dict[str, dict[str, float]]:
        """Shape Google APIs expect: {"rgbColor": {"red": 0.1, ...}}."""
        return {"rgbColor": self.rgb_floats}


# Named palette. Don't add a one-off color for a single doc — extend
# the palette here so every future output benefits.
COLORS = {
    "navy": Color("#1a3a52"),       # H1, cover banners
    "slate": Color("#334155"),      # H2, body
    "charcoal": Color("#64748b"),   # H3, captions
    "vendor": Color("#4285f4"),     # vendor/cloud-layer accents
    "instance": Color("#34a853"),   # customer/instance-layer accents
    "internet": Color("#5f6368"),   # middle-layer / neutral accents
    "warn": Color("#f59e0b"),       # warnings, "expiring"
    "error": Color("#ef4444"),      # errors, "expired"
    "paper": Color("#fafafa"),      # page bg, callout fills
    "white": Color("#ffffff"),      # text on dark backgrounds
}


# ────────────────────────────────────────────────────────────────────────
# Typography
# ────────────────────────────────────────────────────────────────────────


FONTS = {
    "body": "Inter",
    "mono": "JetBrains Mono",
}

# In pt for Docs.
DOC_SIZES = {"h1": 24, "h2": 18, "h3": 14, "body": 11, "small": 9}

# In pt for Slides text.
SLIDE_SIZES = {
    "cover_title": 36,
    "slide_title": 22,
    "section_header": 16,
    "body": 14,
    "caption": 10,
}


# ────────────────────────────────────────────────────────────────────────
# Google Docs — request builders
# ────────────────────────────────────────────────────────────────────────


HeadingLevel = Literal[1, 2, 3]


def doc_heading(text: str, level: HeadingLevel = 1) -> list[dict]:
    """Insert a styled heading at the end of the body.

    Note: caller is responsible for index tracking across multiple
    operations — these builders use `endOfSegmentLocation` where
    supported and return ops that can be safely concatenated.
    """
    color_key = {1: "navy", 2: "slate", 3: "charcoal"}[level]
    size_key = {1: "h1", 2: "h2", 3: "h3"}[level]
    style_name = f"HEADING_{level}"
    return [
        {"type": "insert_text", "end_of_segment": True, "text": text + "\n"},
        # The styles below get re-applied via batch_update_doc by
        # re-walking the doc with inspect_doc_structure to get exact
        # indices. For most cases callers should pair this with a
        # follow-up format_text + update_paragraph_style after building
        # the doc skeleton.
        {
            "type": "_pending_style",
            "marker": f"heading_{level}",
            "text": text,
            "named_style_type": style_name,
            "size_pt": DOC_SIZES[size_key],
            "foreground_color": COLORS[color_key].rgb_color,
            "bold": True,
        },
    ]


def doc_paragraph(text: str, bold_terms: list[str] | None = None) -> list[dict]:
    """A normal-body paragraph. `bold_terms` is a list of substrings to
    bold inline (useful for skim-readers)."""
    ops: list[dict] = [
        {"type": "insert_text", "end_of_segment": True, "text": text + "\n"}
    ]
    if bold_terms:
        for term in bold_terms:
            ops.append(
                {
                    "type": "_pending_format",
                    "marker": "bold_term",
                    "term": term,
                    "bold": True,
                }
            )
    return ops


CalloutTone = Literal["info", "success", "warning", "error"]


def doc_callout(text: str, tone: CalloutTone = "info") -> list[dict]:
    """A shaded paragraph block for key takeaways. The shading color and
    left-indent mark it visually distinct from regular paragraphs."""
    tone_to_color = {
        "info": "paper",
        "success": "instance",
        "warning": "warn",
        "error": "error",
    }
    shade = COLORS[tone_to_color[tone]]
    return [
        {"type": "insert_text", "end_of_segment": True, "text": text + "\n"},
        {
            "type": "_pending_paragraph_style",
            "marker": "callout",
            "shading_color": shade.rgb_floats,
            "indent_start_pt": 24,
            "indent_end_pt": 24,
        },
    ]


def doc_cover_page(
    title: str,
    subtitle: str | None = None,
    prepared_for: str | None = None,
    author: str = "Eve",
    date: str | None = None,
) -> list[dict]:
    """Front-of-doc cover with title + subtitle + prepared-for/date block.

    Adds a page break at the end so main content starts on page 2.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    meta = f"Prepared by {author}"
    if prepared_for:
        meta += f" · for {prepared_for}"
    meta += f" · {date}"
    ops: list[dict] = [
        *doc_heading(title, level=1),
    ]
    if subtitle:
        ops.append(
            {"type": "insert_text", "end_of_segment": True, "text": subtitle + "\n"}
        )
        ops.append(
            {
                "type": "_pending_format",
                "marker": "subtitle",
                "term": subtitle,
                "font_size_pt": DOC_SIZES["body"] + 2,
                "foreground_color": COLORS["slate"].rgb_color,
            }
        )
    ops.append({"type": "insert_text", "end_of_segment": True, "text": "\n"})
    ops.append({"type": "insert_text", "end_of_segment": True, "text": meta + "\n"})
    ops.append(
        {
            "type": "_pending_format",
            "marker": "cover_meta",
            "term": meta,
            "font_size_pt": DOC_SIZES["small"],
            "foreground_color": COLORS["charcoal"].rgb_color,
        }
    )
    ops.append({"type": "insert_page_break", "end_of_segment": True})
    return ops


# ────────────────────────────────────────────────────────────────────────
# Google Slides — request builders
# ────────────────────────────────────────────────────────────────────────


# Default slide page size (Google Slides widescreen 16:9).
SLIDE_W_EMU = 9_144_000
SLIDE_H_EMU = 5_143_500


def slide_color_fill(color_key: str) -> dict:
    """Returns a shapeBackgroundFill block keyed to the palette."""
    return {
        "solidFill": {"color": COLORS[color_key].rgb_color},
    }


def slide_text_style(
    color_key: str,
    size_pt: int,
    *,
    bold: bool = False,
) -> dict:
    """Returns a textStyle block for `updateTextStyle.style`."""
    return {
        "foregroundColor": {"opaqueColor": COLORS[color_key].rgb_color},
        "fontSize": {"magnitude": size_pt, "unit": "PT"},
        "bold": bold,
        "fontFamily": FONTS["body"],
    }


def emu_transform(x_emu: int, y_emu: int, scale: float = 1.0) -> dict:
    """Standard transform helper for createShape elementProperties."""
    return {
        "scaleX": scale,
        "scaleY": scale,
        "translateX": x_emu,
        "translateY": y_emu,
        "unit": "EMU",
    }


def emu_size(width_emu: int, height_emu: int) -> dict:
    """Standard size block for createShape elementProperties."""
    return {
        "width": {"magnitude": width_emu, "unit": "EMU"},
        "height": {"magnitude": height_emu, "unit": "EMU"},
    }


@dataclass(frozen=True)
class DiagramBand:
    """One horizontal band in an architecture-style slide. `accent_color_key`
    drives the color of the boxes inside; `boxes` is a list of text strings
    (or multi-line) to render inside each box."""

    header: str
    accent_color_key: str
    boxes: list[str]


def slide_architecture_diagram(
    page_object_id: str,
    title: str,
    bands: list[DiagramBand],
    id_prefix: str = "arch",
) -> list[dict]:
    """Build the 3-band architecture diagram pattern (vendor/internet/
    customer or any layered system).

    Returns a list of `createShape` + `insertText` + `updateShapeProperties`
    requests ready to pass into batch_update_presentation. Caller is
    responsible for pre-positioning the page (page_object_id) and
    ensuring the diagram fits the standard 9144000 x 5143500 EMU slide.

    Object IDs are derived from id_prefix; all are ≥ 5 chars per the
    Slides API requirement.
    """
    ops: list[dict] = []

    # Title bar at top
    title_bg_id = f"{id_prefix}_title_bg"
    title_txt_id = f"{id_prefix}_title_txt"
    ops += _band_title(title_bg_id, title_txt_id, title, page_object_id)

    # Bands — distribute vertically below the title
    title_h = 520_000
    margin_left = 200_000
    margin_right = 200_000
    content_w = SLIDE_W_EMU - margin_left - margin_right
    content_top = title_h + 100_000
    content_bottom = SLIDE_H_EMU - 100_000
    n = len(bands)
    if n == 0:
        return ops
    band_h = (content_bottom - content_top) // n
    band_inner_pad = 60_000

    for i, band in enumerate(bands):
        band_top = content_top + band_h * i
        # Band header
        header_id = f"{id_prefix}_band_{i}_hdr"
        ops.append(
            {
                "createShape": {
                    "objectId": header_id,
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": page_object_id,
                        "size": emu_size(content_w, 240_000),
                        "transform": emu_transform(margin_left, band_top),
                    },
                }
            }
        )
        ops.append({"insertText": {"objectId": header_id, "text": band.header}})
        ops.append(
            {
                "updateTextStyle": {
                    "objectId": header_id,
                    "style": slide_text_style(
                        band.accent_color_key, SLIDE_SIZES["section_header"], bold=True
                    ),
                    "textRange": {"type": "ALL"},
                    "fields": "foregroundColor,fontSize,bold,fontFamily",
                }
            }
        )
        # Boxes within the band
        box_top = band_top + 260_000
        box_h = band_h - 320_000
        box_count = max(1, len(band.boxes))
        box_gap = 100_000
        total_gap = box_gap * (box_count - 1)
        box_w = (content_w - total_gap) // box_count
        for j, text in enumerate(band.boxes):
            box_id = f"{id_prefix}_b_{i}_{j}"
            box_x = margin_left + (box_w + box_gap) * j
            ops.append(
                {
                    "createShape": {
                        "objectId": box_id,
                        "shapeType": "ROUND_SAME_SIDE_RECTANGLE"
                        if box_count == 1
                        else "RECTANGLE",
                        "elementProperties": {
                            "pageObjectId": page_object_id,
                            "size": emu_size(box_w, box_h),
                            "transform": emu_transform(box_x, box_top + band_inner_pad),
                        },
                    }
                }
            )
            ops.append({"insertText": {"objectId": box_id, "text": text}})
            ops.append(
                {
                    "updateShapeProperties": {
                        "objectId": box_id,
                        "shapeProperties": {
                            "shapeBackgroundFill": slide_color_fill(
                                band.accent_color_key
                            ),
                            "contentAlignment": "MIDDLE",
                        },
                        "fields": "shapeBackgroundFill,contentAlignment",
                    }
                }
            )
            ops.append(
                {
                    "updateTextStyle": {
                        "objectId": box_id,
                        "style": slide_text_style(
                            "white", SLIDE_SIZES["body"] - 4, bold=False
                        ),
                        "textRange": {"type": "ALL"},
                        "fields": "foregroundColor,fontSize,bold,fontFamily",
                    }
                }
            )
            ops.append(
                {
                    "updateParagraphStyle": {
                        "objectId": box_id,
                        "style": {"alignment": "CENTER"},
                        "textRange": {"type": "ALL"},
                        "fields": "alignment",
                    }
                }
            )

    return ops


def _band_title(
    bg_id: str,
    txt_id: str,
    title: str,
    page_object_id: str,
) -> list[dict]:
    """Helper: navy title bar across the top of an architecture slide."""
    return [
        {
            "createShape": {
                "objectId": bg_id,
                "shapeType": "RECTANGLE",
                "elementProperties": {
                    "pageObjectId": page_object_id,
                    "size": emu_size(SLIDE_W_EMU, 520_000),
                    "transform": emu_transform(0, 0),
                },
            }
        },
        {
            "updateShapeProperties": {
                "objectId": bg_id,
                "shapeProperties": {
                    "shapeBackgroundFill": slide_color_fill("navy"),
                },
                "fields": "shapeBackgroundFill",
            }
        },
        {
            "createShape": {
                "objectId": txt_id,
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": page_object_id,
                    "size": emu_size(SLIDE_W_EMU - 400_000, 400_000),
                    "transform": emu_transform(200_000, 80_000),
                },
            }
        },
        {"insertText": {"objectId": txt_id, "text": title}},
        {
            "updateShapeProperties": {
                "objectId": txt_id,
                "shapeProperties": {"contentAlignment": "MIDDLE"},
                "fields": "contentAlignment",
            }
        },
        {
            "updateTextStyle": {
                "objectId": txt_id,
                "style": slide_text_style("white", SLIDE_SIZES["slide_title"], bold=True),
                "textRange": {"type": "ALL"},
                "fields": "foregroundColor,fontSize,bold,fontFamily",
            }
        },
        {
            "updateParagraphStyle": {
                "objectId": txt_id,
                "style": {"alignment": "CENTER"},
                "textRange": {"type": "ALL"},
                "fields": "alignment",
            }
        },
    ]


# ────────────────────────────────────────────────────────────────────────
# Footer helper — both Docs and Slides
# ────────────────────────────────────────────────────────────────────────


def footer_text(project_name: str = "E.V.E.", date: str | None = None) -> str:
    """Standard footer line for any doc or slide we generate."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    return f"{project_name} · {date}"
