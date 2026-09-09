import os
import subprocess
import time
import shutil
import sys

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from owner_profile import COMPANY_AREA  # noqa: E402

def get_markitdown_command():
    """Return argv list for the current markitdown (updated library, no more fragile hardcoded venv)."""
    # 1. Best: command in PATH (after pip --user or pipx install + PATH update in cron_wrapper.sh)
    if cmd := shutil.which("markitdown"):
        return [cmd]

    # 2. Current interpreter has the package
    try:
        import markitdown  # noqa: F401
        return [sys.executable, "-m", "markitdown"]
    except ImportError:
        pass

    # 3. Common --user install locations for the CLI wrapper (Python 3.14 / 3.11 on macOS)
    for ver in ("3.14", "3.11", "3.12", "3.13"):
        candidate = os.path.expanduser(f"~/Library/Python/{ver}/bin/markitdown")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return [candidate]

    # 4. Legacy fallback (will be removed later)
    legacy = os.path.expanduser("~/Projects/markitdown/.venv/bin/markitdown")
    if os.path.isfile(legacy):
        return [legacy]

    raise RuntimeError(
        "markitdown CLI not found.\n"
        "Update with: python3 -m pip install --break-system-packages --user markitdown\n"
        "Then restart any watchers/cron."
    )


TARGET_DIRS = [
    os.path.join(VAULT, COMPANY_AREA, "Finance/Resources"),
    os.path.join(VAULT, "Library"),
    os.path.join(VAULT, "Personal"),
    os.path.join(VAULT, "_attachments"),
]

SUPPORTED_EXTENSIONS = {
    '.pdf', '.docx', '.doc', '.txt',  # focus: pdf, docx, doc + text docs
    '.epub', '.xlsx', '.xls', '.pptx', '.ppt',
    '.jpg', '.jpeg', '.png', '.wav', '.mp3', '.html', '.htm',
    '.json', '.xml', '.csv', '.mp4'
}

def process_file(filepath):
    filename = os.path.basename(filepath)
    file_basename, ext = os.path.splitext(filename)
    
    if ext.lower() not in SUPPORTED_EXTENSIONS:
        return

    parent_dir = os.path.dirname(filepath)
    out_dir = os.path.join(parent_dir, file_basename)
    out_md_path = os.path.join(out_dir, f"{file_basename}.md")
    
    if os.path.exists(out_md_path):
        # Already exists, skipping
        return

    try:
        os.makedirs(out_dir, exist_ok=True)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Converting {filepath}")
        markit_cmd = get_markitdown_command()
        subprocess.run(markit_cmd + [filepath, "-o", out_md_path], check=True)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Success: {out_md_path}")
    except subprocess.CalledProcessError as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error converting {filepath}: {e}")
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Unexpected error on {filepath}: {e}")

def main():
    for target_dir in TARGET_DIRS:
        print(f"Starting batch conversion in {target_dir}")
        if not os.path.exists(target_dir):
            print(f"Directory not found: {target_dir}")
            continue
        for root, dirs, files in os.walk(target_dir):
            for file in files:
                if file.startswith('.'):
                    continue
                filepath = os.path.join(root, file)
                process_file(filepath)
    print("Batch conversion completed.")

if __name__ == "__main__":
    main()
