import os
import time
import sys

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from owner_profile import COMPANY_AREA  # noqa: E402
from markitdown_native import convert_to_file  # noqa: E402


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
        convert_to_file(filepath, out_md_path)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Success: {out_md_path}")
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
