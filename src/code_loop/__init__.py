"""Code Loop: evidence-driven code journey analysis."""

import os
import sys

# Textual enables Kitty's REPORT_ALL_KEYS + REPORT_ASSOCIATED_TEXT flags by
# default. In iTerm2 on macOS those flags expose IME candidate-number and
# input-source Caps Lock events as editable text. This environment variable is
# read when Textual is imported, so it must be set at package initialization.
# setdefault keeps an explicit user override intact.
if sys.platform == "darwin":
    os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1")

__version__ = "0.1.0"
