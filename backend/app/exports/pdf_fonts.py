"""Shared Unicode font registration for server-generated PDFs (fpdf2).

DejaVu Sans covers both the Serbian Latin diacritics (č/ć/š/ž/đ) and the Cyrillic block,
so the same font serves the activity report and the custody-chain report - no need for a
second candidate list per script.
"""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF


UNICODE_FONT_CANDIDATES: tuple[tuple[Path, Path], ...] = (
    (Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')),
    (Path('/usr/share/fonts/dejavu/DejaVuSans.ttf'), Path('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf')),
    (Path('C:/Windows/Fonts/arial.ttf'), Path('C:/Windows/Fonts/arialbd.ttf')),
)


def register_unicode_font(pdf: FPDF) -> str:
    """Registers a Unicode TTF on `pdf` and returns the family name to use; falls back to
    an ASCII-only core font only if no TTF candidate is present on the host."""
    for regular_path, bold_path in UNICODE_FONT_CANDIDATES:
        if not regular_path.exists():
            continue
        pdf.add_font('LusiSans', '', str(regular_path))
        pdf.add_font('LusiSans', 'B', str(bold_path if bold_path.exists() else regular_path))
        return 'LusiSans'
    return 'helvetica'
