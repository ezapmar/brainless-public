"""macOS notification that never lets its text become AppleScript.

Building `display notification "{text}"` with an f-string turns any quote in the
text into code: a file named `x" & (do shell script "...") & ".pdf` runs a shell
command. Here the text travels as an argv item and AppleScript only reads it.
"""
import shutil
import subprocess

_SCRIPT = [
    "-e", "on run argv",
    "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
    "-e", "end run",
]


def command(message, title="brainless"):
    """The osascript argv for one notification; the text is data, never source."""
    return ["osascript", *_SCRIPT, str(message), str(title)]


def notify(message, title="brainless", timeout=10):
    """Best-effort notification; returns False when there is no osascript. Never raises."""
    if not shutil.which("osascript"):
        return False
    try:
        subprocess.run(command(message, title), check=False, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    return True
