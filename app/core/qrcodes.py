"""QR code rendering for merchant scan-to-order links.

SVG output only (via qrcode's SVG factory) — no Pillow/raster dependency, so the
image stays crisp at any print size and the module is lightweight.
"""

from __future__ import annotations

import base64
import io

import qrcode
import qrcode.image.svg


def render_qr_svg(data: str) -> str:
    """Return a standalone SVG document encoding ``data``."""
    img = qrcode.make(
        data,
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=12,
        border=2,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
    )
    buffer = io.BytesIO()
    img.save(buffer)
    return buffer.getvalue().decode("utf-8")


def render_qr_data_uri(data: str) -> str:
    """Return the QR as a data: URI suitable for an <img src> (self-contained)."""
    svg = render_qr_svg(data)
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
