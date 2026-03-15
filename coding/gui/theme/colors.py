"""
Color palette for the luxury dark theme.

Defines all colors used throughout the application.
"""


class Colors:
    """
    Midnight navy luxury theme — Theme B.

    Deep navy backgrounds, warm platinum gold accents, Inter + Playfair Display fonts.
    """

    # Background colors
    BACKGROUND_PRIMARY = "#080D18"
    BACKGROUND_SECONDARY = "#0A1020"
    BACKGROUND_TERTIARY = "#0D1428"
    BACKGROUND_ELEVATED = "#111E35"

    # Surface colors (for cards, panels)
    SURFACE = "#0D1428"
    SURFACE_HOVER = "#111E35"
    SURFACE_ACTIVE = "#192840"

    # Border colors
    BORDER = "#141E30"
    BORDER_SUBTLE = "#0F1828"
    BORDER_FOCUS = "#C9A84C"  # Use rgba(201,168,76,0.4) in QSS for alpha variant

    # Text colors
    TEXT_PRIMARY = "#E8EAF0"
    TEXT_SECONDARY = "#5A6A7C"
    TEXT_MUTED = "#2A3848"
    TEXT_DISABLED = "#1A2638"

    # Accent colors (luxury metallic gold)
    ACCENT = "#C9A84C"
    ACCENT_HOVER = "#DBBF5A"
    ACCENT_MUTED = "#9A7A2E"

    # Status colors (unchanged)
    SUCCESS = "#2ECC71"
    SUCCESS_MUTED = "#1E8449"
    WARNING = "#F39C12"
    WARNING_MUTED = "#B7950B"
    ERROR = "#E74C3C"
    ERROR_MUTED = "#A93226"
    INFO = "#3498DB"
    INFO_MUTED = "#2171A9"

    # Input colors
    INPUT_BACKGROUND = "#0A1020"
    INPUT_BORDER = "#141E30"
    INPUT_FOCUS = "#C9A84C"

    # Button colors
    BUTTON_PRIMARY = "#C9A84C"
    BUTTON_PRIMARY_HOVER = "#DBBF5A"
    BUTTON_SECONDARY = "#111E35"
    BUTTON_SECONDARY_HOVER = "#192840"

    # Scrollbar colors
    SCROLLBAR_TRACK = "#0A1020"
    SCROLLBAR_HANDLE = "#141E30"
    SCROLLBAR_HANDLE_HOVER = "#1E2D45"

    # Financial colors (unchanged)
    PROFIT = "#2ECC71"
    LOSS = "#E74C3C"
