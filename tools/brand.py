"""brainless colours, one table for the terminal and any future interface.

The palette and its rules live in docs/design.md; this file is the code copy.
paint() adds 24-bit ANSI colour only on a real terminal and honours NO_COLOR.
"""
import os
import sys

PALETTE = {
    "night":     "#0A1825",  # ground: banner and icon background
    "ink":       "#042538",  # deep outline inside motifs
    "cream":     "#FBDEA5",  # nodes, wordmark on dark ground
    "bone":      "#F9EBDB",  # wordmark text
    "madder":    "#CB221F",  # error
    "vermilion": "#F24B1E",  # brand accent, the dot over the i
    "orange":    "#F97327",
    "saffron":   "#FBA335",  # warning
    "marigold":  "#FBB94B",
    "teal":      "#0C9794",  # ok, links, the one cool colour
    "sage":      "#9BB38D",  # muted secondary
}


def colour_on(stream=None):
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def paint(text, token, bold=False, stream=None):
    if not colour_on(stream):
        return text
    h = PALETTE[token].lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"\033[{'1;' if bold else ''}38;2;{r};{g};{b}m{text}\033[0m"
