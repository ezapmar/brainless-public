"""Native markitdown conversion.

Uses markitdown as a Python library instead of shelling out to the CLI. This
removes the fragile CLI-path discovery (PATH lookup, per-version --user bin
guesses, legacy venv fallback) and the per-file subprocess spawn: one
MarkItDown instance is built lazily and reused for every file in a run.

Install into the project venv, not the system Python (see env.sh):
    ~/.local/venvs/brainless/bin/pip install -r requirements-core.txt
Do NOT use markitdown[all] on Python 3.14, its youtube-transcript-api pin is
unsatisfiable there and pip silently downgrades markitdown to 0.0.2.
"""

_CONVERTER = None


def _converter():
    """Lazily build and cache a single MarkItDown instance for the process."""
    global _CONVERTER
    if _CONVERTER is None:
        try:
            from markitdown import MarkItDown
        except ImportError as exc:  # pragma: no cover - environment guard
            raise RuntimeError(
                "markitdown library not importable. This step must run under the "
                "project venv (BRAINLESS_SEARCH_PY in .agents/scripts/env.sh); "
                "install with: ~/.local/venvs/brainless/bin/pip install "
                "-r requirements-core.txt"
            ) from exc
        _CONVERTER = MarkItDown()
    return _CONVERTER


def convert_to_file(src_path, out_path):
    """Convert src_path to markdown and write it to out_path as UTF-8.

    Mirrors the CLI's `markitdown <src> -o <out>` contract, so callers only need
    to ensure the parent directory exists.
    """
    result = _converter().convert(src_path)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(result.markdown)
    return out_path
