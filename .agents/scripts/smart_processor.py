import os
import re
import subprocess
import time
import shutil
import sys
import json

# Configuration
BRAINLESS_ROOT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")

sys.path.insert(0, os.path.join(BRAINLESS_ROOT, "tools"))
from resolve_bin import resolve_claude
from owner_profile import COMPANY_AREA, LANG, lang_name, output_lang_directive  # noqa: E402
from i18n import t  # noqa: E402
from markitdown_native import convert_to_file  # noqa: E402

CLAUDE_PATH = resolve_claude()

# Self-healing state: persistent record of files that fail to convert, so we
# can back off instead of re-attempting every single hourly run.
STATE_DIR = os.path.join(BRAINLESS_ROOT, ".agents", "state")
QUARANTINE_FILE = os.path.join(STATE_DIR, "failed_conversions.json")
# Guards the Inbox image loop so the on-upload watcher and the hourly backstop
# can never OCR the same image at once.
IMAGE_LOCK = os.path.join(STATE_DIR, "inbox_images.lock")

# Exponential-ish backoff between retries of a failing file. Cron runs hourly,
# so attempt N waits the Nth entry before retrying; past the list we cap at 7d.
BACKOFF_SCHEDULE = [3600, 4 * 3600, 12 * 3600, 24 * 3600, 3 * 24 * 3600]
MAX_BACKOFF = 7 * 24 * 3600


def notify(message, title="brainless / smart_processor"):
    """Best-effort macOS notification; never raises."""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}"'],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def load_quarantine():
    try:
        with open(QUARANTINE_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_quarantine(data):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(QUARANTINE_FILE, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def backoff_for(attempts):
    if attempts <= 0:
        return 0
    if attempts <= len(BACKOFF_SCHEDULE):
        return BACKOFF_SCHEDULE[attempts - 1]
    return MAX_BACKOFF


def is_backed_off(filepath, quarantine, now):
    """True if this file failed recently and we should wait before retrying."""
    rec = quarantine.get(filepath)
    if not rec:
        return False
    elapsed = now - rec.get("last_attempt", 0)
    return elapsed < backoff_for(rec.get("attempts", 0))

# High-value knowledge resources get full AI treatment (Summary + Fiche + original cleanup).
# General documents anywhere in the human homes will at least get converted to .md automatically.
HIGH_VALUE_DIRS = [
    os.path.join(BRAINLESS_ROOT, "Library/Books"),
    os.path.join(BRAINLESS_ROOT, COMPANY_AREA, "Finance/Resources"),
]

TARGET_DIRS = [
    os.path.join(BRAINLESS_ROOT, "Library/Books"),
    os.path.join(BRAINLESS_ROOT, COMPANY_AREA, "Finance/Resources"),
    os.path.join(BRAINLESS_ROOT, "Library"),
    os.path.join(BRAINLESS_ROOT, "Personal"),
    os.path.join(BRAINLESS_ROOT, "Work"),
]
SUPPORTED_EXTENSIONS = {'.pdf', '.epub', '.docx', '.doc', '.txt', '.xlsx', '.pptx', '.mp3', '.wav'}

# Handwritten/printed note photos. These are OCR'd by Claude (markitdown has no
# OCR) and, unlike documents, are handled ONLY when dropped in Inbox/ so the many
# existing vault images (book covers, diagrams, screenshots) are never touched.
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.webp'}
IMAGE_INBOX = os.path.join(BRAINLESS_ROOT, "Inbox")
CAPTURE_DIR = os.path.join(BRAINLESS_ROOT, "Thinking", "Daily")
IMAGE_ATTACH_DIR = os.path.join(BRAINLESS_ROOT, "_attachments", "handwritten")

OCR_PROMPT = """First read '_Agent-Context/CONTEXT.md' to learn the correct spellings of the owner's projects and people.
Then read the image at '{image}'. It is a photo of a handwritten or printed note ({langs}).

Transcribe every legible word faithfully - do NOT invent, translate, or summarize. For a word you cannot read, write your best guess followed by (?), or {illegible} if it is truly illegible.

Then turn the transcription into a clean vault note:
- First line: a short title starting with '# '.
- Fix obvious transcription errors in proper nouns using CONTEXT.md (e.g. "top table" -> "cap table", "o ge ka" -> "OGK").
- Use [[wikilinks]] for the projects and people mentioned.
- Write any action item as a '- [ ]' task line.
- Last line: 1-3 relevant tags (e.g. #work #personal) if appropriate.
- Never use em dashes or en dashes anywhere; use a plain hyphen.
- For the title, tags and anything you add yourself: {lang_directive}

Save ONLY the note markdown to '{out_path}' and write nothing else.
"""

SUMMARY_PROMPT = """Read the markdown file at '{filepath}'.
Provide a concise but technical summary of the key findings, data points, and actionable insights.
Focus on facts and figures.
{lang_directive}
Save the result to '{out_path}'.
"""

FICHE_PROMPT = """Read the markdown file at '{filepath}'.
Rewrite its contents into a comprehensive 'Fiche de Lecture' (French education reading card format).
Analyze it through the 'brainless' mindset: "AI is a cognitive exoskeleton", "Calm is contagious", "Irreversible decisions deserve second-order thinking", and "Success is the compound interest of small positive choices".

Format:
# Fiche de Lecture: [Title]
**Auteur/Source:** ...
**Contexte/Mindset Brainless:** ...
## 1. Présentation de l'œuvre (Overview)
## 2. Thèse de l'auteur (Core Thesis)
## 3. Résumé des idées principales (Key Ideas)
## 4. Analyse Critique & Connexions (Use [[links]] to related vault notes)

{lang_directive}
Save the result to '{out_path}'.
"""

def run_claude(prompt):
    # acceptEdits: the prompts instruct the model to write the output file
    # itself; headless mode needs edit auto-approval for that to succeed.
    try:
        subprocess.run(
            [CLAUDE_PATH, "-p", prompt, "--permission-mode", "acceptEdits"],
            cwd=BRAINLESS_ROOT,
            check=True,
            timeout=600,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except Exception as e:
        print(f"Claude error: {e}")
        return False

def process_file(filepath):
    """Process one file. Returns one of: 'skipped', 'converted', 'failed'."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return "skipped"

    base_dir = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    name_no_ext = os.path.splitext(filename)[0]

    work_dir = os.path.join(base_dir, name_no_ext)
    raw_md = os.path.join(work_dir, f"{name_no_ext}_raw.md")
    summary_md = os.path.join(work_dir, "Summary.md")
    fiche_md = os.path.join(work_dir, "Fiche_de_Lecture.md")

    is_high_value = any(filepath.startswith(d) for d in HIGH_VALUE_DIRS)

    # Idempotency: skip files already converted and up to date.
    # High-value originals are deleted after success, so reaching this point with
    # the original still present means work remains. General docs keep their
    # original, so compare mtimes to detect an already-current conversion.
    if not is_high_value and os.path.exists(raw_md):
        try:
            if os.path.getmtime(raw_md) >= os.path.getmtime(filepath):
                return "skipped"
        except OSError:
            pass

    # 1. Markitdown (updated library). Only (re)convert if the raw md is missing
    # or stale, and create the work_dir only once we are about to write output,
    # so failed conversions don't litter the vault with empty folders.
    need_convert = (not os.path.exists(raw_md)) or (
        os.path.exists(filepath)
        and os.path.getmtime(raw_md) < os.path.getmtime(filepath)
    )
    if need_convert:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Converting {filename}...")
        try:
            os.makedirs(work_dir, exist_ok=True)
            convert_to_file(filepath, raw_md)
        except Exception as e:
            print(f"Failed to convert {filename}: {e}")
            # Remove an empty work_dir we may have just created.
            try:
                os.rmdir(work_dir)
            except OSError:
                pass
            return "failed"

    if is_high_value:
        # 2. Summary (only for high-value knowledge resources)
        if not os.path.exists(summary_md):
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Generating Summary for {filename}...")
            run_claude(SUMMARY_PROMPT.format(filepath=raw_md, out_path=summary_md,
                                             lang_directive=output_lang_directive()))

        # 3. Fiche de Lecture
        if not os.path.exists(fiche_md):
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Generating Fiche de Lecture for {filename}...")
            run_claude(FICHE_PROMPT.format(filepath=raw_md, out_path=fiche_md,
                                           lang_directive=output_lang_directive()))

        # 4. Cleanup originals (only for high-value to keep the vault lean)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Cleaning up originals for {filename}...")
        if os.path.exists(raw_md):
            os.remove(raw_md)

        try:
            subprocess.run(["git", "rm", filepath], cwd=BRAINLESS_ROOT, check=True, stderr=subprocess.DEVNULL)
        except:
            if os.path.exists(filepath):
                os.remove(filepath)
    else:
        # For general documents: keep the original + raw conversion result.
        # The .md is now available next to the file in a subfolder.
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] General document converted (original preserved).")

    return "converted"

def _slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or t("smart_processor.slug_fallback")


def _to_ocr_image(filepath):
    """Return (path_the_model_can_read, made_new_file).

    The OCR model reads JPEG/PNG but not HEIC, so transcode HEIC to JPEG first.
    """
    if os.path.splitext(filepath)[1].lower() == ".heic":
        jpg = os.path.splitext(filepath)[0] + ".jpg"
        subprocess.run(["sips", "-s", "format", "jpeg", filepath, "--out", jpg],
                       check=True, capture_output=True, timeout=120)
        return jpg, True
    return filepath, False


def process_image(filepath):
    """OCR one Inbox image into a Thinking/Daily capture. -> 'converted' | 'failed'.

    The original image is archived to _attachments/handwritten/ and linked from
    the note; leaving Inbox on success is what makes this idempotent (no state
    file needed - a converted image is simply no longer in Inbox to re-scan).
    """
    stamp = time.strftime("%Y-%m-%d-%H%M")
    slug = _slugify(os.path.splitext(os.path.basename(filepath))[0])
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    note_path = os.path.join(CAPTURE_DIR, f"{stamp}-{slug}.md")
    n = 2
    while os.path.exists(note_path):
        note_path = os.path.join(CAPTURE_DIR, f"{stamp}-{slug}-{n}.md")
        n += 1

    try:
        ocr_path, made_jpeg = _to_ocr_image(filepath)
    except Exception as e:
        print(f"Image prep failed for {os.path.basename(filepath)}: {e}")
        return "failed"

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] OCR {os.path.basename(filepath)}...")
    langs = lang_name() if LANG == "en" else f"{lang_name()} and/or English"
    ok = run_claude(OCR_PROMPT.format(image=ocr_path, out_path=note_path, langs=langs,
                                      illegible=t("smart_processor.illegible_marker"),
                                      lang_directive=output_lang_directive()))
    if not ok or not os.path.exists(note_path):
        if made_jpeg and os.path.exists(ocr_path):
            os.remove(ocr_path)          # clean the transcode on failure
        return "failed"

    # Enforce house style: em/en dashes are hard-banned in vault output.
    try:
        with open(note_path) as fh:
            body = fh.read()
    except OSError:
        return "failed"
    body = body.replace("\u2014", "-").replace("\u2013", "-").strip()

    # Archive the source image next to the vault's other attachments and link it.
    os.makedirs(IMAGE_ATTACH_DIR, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(ocr_path))
    archived = os.path.join(IMAGE_ATTACH_DIR, stem + ext)
    a = 2
    while os.path.exists(archived):
        archived = os.path.join(IMAGE_ATTACH_DIR, f"{stem}-{a}{ext}")
        a += 1
    shutil.move(ocr_path, archived)
    if made_jpeg and os.path.exists(filepath):
        os.remove(filepath)              # drop the original HEIC; JPEG is archived

    footer = ("\n\n---\n" + t("smart_processor.handwritten_footer", stamp=stamp) + "\n"
              f"![[{os.path.basename(archived)}]]\n")
    with open(note_path, "w") as fh:
        fh.write(body + footer)

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Note written: "
          f"{os.path.relpath(note_path, BRAINLESS_ROOT)}")
    return "converted"


def _acquire_lock(path, stale=1800):
    """Best-effort single-runner lock. Returns an fd, or None if held.

    A lock older than `stale` seconds is assumed to be from a crashed run and
    reclaimed, so a hung OCR can never wedge the pipeline permanently.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if time.time() - os.path.getmtime(path) > stale:
            os.remove(path)
    except OSError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except FileExistsError:
        return None


def _release_lock(fd, path):
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.remove(path)
    except OSError:
        pass


def process_inbox_images(quarantine, now):
    """OCR every image sitting at the top level of Inbox/. Mutates `quarantine`.

    Returns (converted, failures, backed_off). Scoped to Inbox/ so the vault's
    many existing images are never swept in, and guarded by a single-runner lock
    so the watcher and hourly job don't process the same file twice.
    """
    converted, failures, backed_off = 0, [], 0
    if not os.path.isdir(IMAGE_INBOX):
        return converted, failures, backed_off
    lock = _acquire_lock(IMAGE_LOCK)
    if lock is None:
        return converted, failures, backed_off      # another run owns the images
    try:
        for f in sorted(os.listdir(IMAGE_INBOX)):
            if f.startswith('.'):
                continue
            filepath = os.path.join(IMAGE_INBOX, f)
            if not os.path.isfile(filepath):
                continue
            if os.path.splitext(f)[1].lower() not in IMAGE_EXTENSIONS:
                continue
            if is_backed_off(filepath, quarantine, now):
                backed_off += 1
                continue
            status = process_image(filepath)
            if status == "converted":
                converted += 1
                quarantine.pop(filepath, None)
            elif status == "failed":
                rec = quarantine.get(filepath, {"attempts": 0})
                rec["attempts"] = rec.get("attempts", 0) + 1
                rec["last_attempt"] = now
                quarantine[filepath] = rec
                failures.append(filepath)
    finally:
        _release_lock(lock, IMAGE_LOCK)
    return converted, failures, backed_off


def run_images_only():
    """Watcher entrypoint (`--images`): OCR Inbox images and commit, nothing else.

    Skips the git pull and full document walk of main() so it stays fast enough
    to fire on every upload.
    """
    quarantine = load_quarantine()
    now = time.time()
    converted, failures, backed_off = process_inbox_images(quarantine, now)
    save_quarantine(quarantine)

    if converted:
        # Commit ONLY the OCR outputs, not the whole tree - this fires on every
        # upload, so it must never sweep up whatever the user is mid-editing.
        # (The hourly job's broad `git add .` still backs up everything later.)
        subprocess.run(["git", "add", "Thinking/Daily", "_attachments/handwritten",
                        ".agents/state/failed_conversions.json"],
                       cwd=BRAINLESS_ROOT)
        subprocess.run(["git", "commit", "-m",
                        f"Auto-OCR: {converted} handwritten note(s) from Inbox"],
                       cwd=BRAINLESS_ROOT)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Inbox images: {converted} converted, "
              f"{len(failures)} failed, {backed_off} backed off. Committed.")
    else:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Inbox images: nothing to convert "
              f"({len(failures)} failed, {backed_off} backed off).")

    if failures:
        notify(f"{len(failures)} Inbox image OCR(s) failed")
        sys.exit(1)


def _github_reachable():
    """Same check as vault_backup.sh: launchd fires this job while the Mac is
    waking and Wi-Fi is not up yet; a pull then only produces ssh noise."""
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-T", "git@github.com"],
            capture_output=True, text=True, timeout=20,
        )
        return "successfully authenticated" in (r.stdout + r.stderr)
    except Exception:
        return False


def _rebase_in_progress():
    return (os.path.isdir(os.path.join(BRAINLESS_ROOT, ".git", "rebase-merge"))
            or os.path.isdir(os.path.join(BRAINLESS_ROOT, ".git", "rebase-apply")))


def git_sync():
    """Pull worker's commits before the document walk.

    2026-09-04: a bare `git pull` failed every hour for four days with
    "Need to specify how to reconcile divergent branches" (Mac and worker both
    commit; pull.rebase is unset). Mirror vault_backup.sh: rebase + autostash,
    abort a half-finished rebase instead of leaving the repo locked, and skip
    when GitHub is unreachable.
    """
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    if _rebase_in_progress():
        subprocess.run(["git", "rebase", "--abort"], cwd=BRAINLESS_ROOT)
        print(f"[{stamp}] half-finished rebase aborted")
        notify(t("smart_processor.notify_rebase_cleaned"))
    if not _github_reachable():
        print(f"[{stamp}] github unreachable, pull skipped")
        return False
    print(f"[{stamp}] Fetching updates (rebase + autostash)...")
    r = subprocess.run(["git", "pull", "--rebase", "--autostash", "--quiet", "origin", "master"],
                       cwd=BRAINLESS_ROOT)
    if _rebase_in_progress():
        subprocess.run(["git", "rebase", "--abort"], cwd=BRAINLESS_ROOT)
        print(f"[{stamp}] pull conflict, rebase aborted")
        notify(t("smart_processor.notify_pull_conflict"))
        return False
    if r.returncode != 0:
        print(f"[{stamp}] pull failed (exit {r.returncode}), continuing")
        return False
    return True


def main():
    git_sync()

    quarantine = load_quarantine()
    now = time.time()
    converted = 0
    failures = []          # newly-failed this run
    backed_off = 0         # skipped because they're in backoff

    EXCLUDE_DIR_PARTS = {"venv", ".git", ".wiki", "archive", "_backup", "__pycache__", "tasks-sync", "_attachments"}

    for t_dir in TARGET_DIRS:
        if not os.path.exists(t_dir): continue
        for root, dirs, files in os.walk(t_dir):
            # Skip hidden + known non-content directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and not any(bad in d.lower() for bad in EXCLUDE_DIR_PARTS)]
            for f in files:
                if not f.startswith('.'):
                    filepath = os.path.join(root, f)
                    if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS:
                        if any(bad in filepath.lower() for bad in EXCLUDE_DIR_PARTS):
                            continue

                        # Self-healing: a persistently failing file backs off
                        # exponentially instead of retrying every hour.
                        if is_backed_off(filepath, quarantine, now):
                            backed_off += 1
                            continue

                        status = process_file(filepath)

                        if status == "converted":
                            converted += 1
                            quarantine.pop(filepath, None)   # recovered, clear it
                        elif status == "failed":
                            rec = quarantine.get(filepath, {"attempts": 0})
                            rec["attempts"] = rec.get("attempts", 0) + 1
                            rec["last_attempt"] = now
                            quarantine[filepath] = rec
                            failures.append(filepath)

    # Handwritten/printed note photos in Inbox/. The on-upload watcher normally
    # handles these instantly; this call is the hourly backstop for anything it
    # missed (e.g. images dropped while the Mac was asleep).
    ci, fi, bi = process_inbox_images(quarantine, now)
    converted += ci
    failures.extend(fi)
    backed_off += bi

    save_quarantine(quarantine)

    if converted:
        # Git Commit, only when real work happened.
        subprocess.run(["git", "add", "."], cwd=BRAINLESS_ROOT)
        subprocess.run(["git", "commit", "-m", "Auto-process: Summaries and Fiches generated, originals removed"], cwd=BRAINLESS_ROOT)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Batch complete: {converted} converted, "
              f"{len(failures)} failed, {backed_off} backed off. Committed.")
    else:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] No new files. "
              f"{len(failures)} failed, {backed_off} backed off.")

    # Alerting: surface failures instead of letting them rot silently in the log.
    if failures:
        sample = os.path.basename(failures[0])
        more = f" (+{len(failures) - 1} more)" if len(failures) > 1 else ""
        notify(f"{len(failures)} conversion(s) failed: {sample}{more}")
        # Non-zero exit lets the cron wrapper also react / log loudly.
        sys.exit(1)

if __name__ == "__main__":
    if "--images" in sys.argv:
        run_images_only()      # on-upload watcher: Inbox images only
    else:
        main()                 # hourly: documents + Inbox-image backstop
