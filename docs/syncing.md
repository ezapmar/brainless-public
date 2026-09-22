# Syncing across devices

> **Run it:** keeping one vault in step across several machines.

The engine repo holds no notes. Your vault is a folder wherever `BRAINLESS_VAULT`
points, and syncing brainless means keeping that folder in step across the machines that
read or write it. There are three sensible transports: **Git**, **Google Drive** and
**Obsidian Sync**. Pick one per folder. Mixing two sync engines on the same folder is the
one reliable way to corrupt it.

Two things are separate and it matters:

- **The engine** (`tools/`, `.agents/` code, the `brainless` command). This always comes
  from a `git clone` of the public repo and updates with `git pull`. That is how you get
  new versions. None of the options below carry the engine; Drive and Obsidian Sync should
  never be pointed at the engine clone.
- **The vault** (your notes). This is what the three options move between devices.

## What syncs, and what must not

| Path | Sync it? | Why |
|---|---|---|
| `Work/`, `Personal/`, `Library/`, `Inbox/`, `Thinking/`, `_Agent-Context/` | yes | your notes and the shared context are the whole point |
| `.wiki/` | yes (or regenerate) | the compiled layer; it travels fine, or a machine can rebuild it with `brainless compile` |
| `.agents/state/` | **no** | seen ids, offsets, `today_queue.json`, `crm_snapshot.json`. Intentionally local per machine, gitignored. Sharing it makes two machines disagree about what they have already done |
| `~/.config/brainless/` | **no** | secrets (tokens, keys). Per device, kept outside the vault on purpose |
| `.venv/`, `__pycache__/` | **no** | per-device Python, never sync |

The git path handles all of this for you through `.gitignore`. Drive and Obsidian Sync do
not know about `.gitignore`, so with those you either keep the automation on a single
machine or use the client's own exclude list. More on that under each option.

## Option A: Git (recommended, and required for a worker)

The native path, and the only one the automation was designed for.

Your vault is a **private** git repo (for example `github.com/you/brainless-vault`, kept
private because it holds your notes; the public repo is the engine only). Each device
clones it, pulls before it works and pushes after.

```bash
# once, per device
git clone git@github.com:you/brainless-vault.git ~/vault
export BRAINLESS_VAULT=~/vault

# the rhythm, which the worker automates
git pull --rebase --autostash
# ...capture, compile, dialectic, edit...
git add -A && git commit -m "notes" && git push
```

- **Why it is the default.** Worker commits are path-scoped and serialised with `flock`
  against the backup timer, and the two-writer rule (one owner per file, append-only for
  anything two machines touch) is what stops the failure mode where two devices write the
  same file and every push is rejected. That discipline only exists in the git path. See
  [the reference setup](how-it-works.md#usage-example-our-real-setup) and
  `_Agent-Context/TRUNK-BASED-DEVELOPMENT.md`.
- **The phone.** You usually do not sync the whole vault to a phone here. Capture goes
  through the Telegram bot instead, landing in `Thinking/Daily/` on whichever machine runs
  the poller, and you read the day back in Buzz: the `#dialectic` channel, the
  [Today queue](today-queue.md) in `#tasks`, receipts in `#inbox`. Telegram never
  answers. If you do want the vault in your pocket, put
  Obsidian mobile on the phone with the community Git plugin, so the phone is just another
  git client and the transport stays uniform.
- **Pros:** conflict discipline, full history, works headless, free.
  **Cons:** you must respect the one-writer rule; two machines editing the same file still
  produce a merge conflict you resolve by hand.

## Option B: Google Drive

Treat the vault as a plain folder inside Google Drive and let Drive for Desktop sync it.

```bash
# vault lives under your Drive path; point brainless at it
export BRAINLESS_VAULT="~/Library/CloudStorage/GoogleDrive-you/My Drive/brainless-vault"
```

Honest caveats, because Drive is file sync with no idea what brainless is:

- **Do not nest a git repo inside a Drive-synced folder.** Drive syncing `.git/` is slow
  and can corrupt the repo. Choose Drive **or** git for a given folder, never both.
- **No per-file ownership.** If two machines write at the same moment, Drive keeps both as
  "conflicted copy" files rather than merging. So run the automation (the timers, the
  poller, compile) on **one** machine and let the others be read or capture only.
- **It can sync a file mid-write.** brainless keeps every source and retries on failure, so
  nothing is lost, but schedule the automation so it is not racing a large upload.
- **`.agents/state/` will sync too**, because Drive cannot read `.gitignore`. Keeping the
  automation on a single machine keeps that state coherent; some Drive clients also let you
  exclude a subfolder from sync, which is worth doing for `.agents/` and `.venv/`.
- **Good for:** a Mac plus one other machine, a single active writer, capture mostly from
  the phone over Telegram (capture only; there is no Buzz conversation without a worker),
  and no headless worker.

## Option C: Obsidian Sync (Obsidian's own cloud)

Obsidian's first-party paid sync: end-to-end encrypted, across the desktop and mobile
Obsidian apps, with version history. This is the best way to carry the full vault on a
phone or tablet.

- **Setup.** Turn on Sync in Obsidian, add the vault, and use **selective sync** to
  exclude the folders that should not travel to mobile or that are per-device: `.venv/`,
  `.agents/` (code and state), caches. Keep your notes and, if you like, `.wiki/`.
- **Strengths:** by far the best mobile experience, encrypted, no git knowledge needed,
  and it merges at the file level more gracefully than Drive.
- **Limits:**
  - It only runs where Obsidian runs. A headless Linux worker has no Obsidian, so if you
    run the always-on worker it still needs git; Obsidian Sync cannot feed it.
  - It syncs files, not the engine. The scripts run outside Obsidian, so Sync never
    triggers a compile or a dialectic; it only moves the notes those scripts produce.
  - Do not stack it on top of git for the same folder on the same machine, or the two sync
    engines will fight.
- **Good for:** you edit and read in Obsidian on a Mac and a phone, you want the notes with
  you, and you either run no worker or keep the worker as a separate git concern.

## Which to pick

| Your setup | Use |
|---|---|
| Always-on worker, or automation running on two or more machines | **Git** (required) |
| Mac plus phone, you live in Obsidian, want notes on mobile, no headless worker | **Obsidian Sync** |
| Already on Google Drive, one active writer, light automation | **Google Drive** |
| Not sure | **Git**. It is free, it is what the automation expects, and you can add Obsidian mobile on top of it later |

## Can I combine them?

Yes, carefully, under one rule: **one transport owns a given folder on a given machine.**

- **The clean combo:** git as the backbone across the Mac and the worker, plus Obsidian
  mobile with the community **Git** plugin on the phone. Every device is then a git client
  and there is only ever one sync engine per folder.
- **Avoid:** layering Drive or Obsidian Sync over a git-synced folder. Two sync engines on
  one folder produce conflicted copies and a broken `.git/`, which is exactly the mess the
  one-writer rule was written to prevent.

The [capture flow](capture-flow.md) describes how notes arrive on whichever machine holds
the vault; this page is only about keeping that vault in step across devices.
