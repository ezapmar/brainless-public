# Lite

brainless grew up on three machines. A phone that captures, a MacBook where I write, and
a laptop running Arch Linux that never sleeps and works the night shift. Thirty-four
timers, a chat relay, six persona agents, and a private network holding it all together.
It works for me. I would not ask anyone to copy it.

So there is a lighter way to run it. Same brainless, one computer. A folder of notes,
whichever model you already use, and one entry in the scheduler. No second machine and
no phone bot. Obsidian if you like it, any editor if you do not.

This is what the installer sets up by default.

## What you keep

The part that matters survives the diet. Notes go into a folder and get summarised,
linked and compiled into a wiki you can search. The six voices still argue with your
thesis. Decisions still carry a prediction and a review date, and `brainless calibrate`
still nags you when the date passes. The slash commands work in Claude Code or Gemini CLI
exactly as before.

What goes is everything that needs a second machine or a chat relay: the Telegram
capture, the threads on the phone, the morning three, the encrypted backups. Hidden, not
deleted. `brainless profile` lists them, and `brainless profile enable <command>` brings
one back if you have the pieces it needs.

## Install

You need git and Python 3.11 or newer. On a Mac that is `brew install python`.

```bash
curl -fsSL https://raw.githubusercontent.com/ezapmar/brainless-public/main/install.sh | bash
```

The script sets up Python and the `brainless` command, then hands over to
`brainless init`. It asks a handful of questions, in this order.

1. **Your name, and the language it should write in.** The folders are named in that
   language. In English you get `Inbox`, `Notes`, `Library` and `Thinking`. In Turkish,
   `Gelen`, `Notlar`, `Kütüphane` and `Düşünce`. The questions themselves are in English
   for now.
2. **Notes you already have.** Point it at an old Obsidian vault or any folder of
   Markdown. It is linked under `Library/_linked/` and read. Nothing is ever written
   there.
3. **The model.** Three kinds, in the table further down. If you pick Ollama and it is not
   installed, it asks before installing it. If you pick an API, the key goes into the
   macOS Keychain, or libsecret on Linux. It never touches the folder, because the folder
   is a git repository, and a git repository is where keys go to leak.
4. **A test call.** One tiny prompt, so you learn now, and not at nine in the evening,
   that the key had a typo.
5. **Background runs.** One scheduler entry. On a Mac, if your folder lives in
   `~/Documents`, `~/Desktop` or iCloud Drive, macOS will ask for permission every time a
   background job opens it. `init` warns you and can open the right settings page.
   `~/brainless` avoids the whole conversation.
6. **A first note and a first run**, if you want to watch it work.

Every answer lands in `brainless.toml` at the top of the folder. Edit it by hand whenever
you like; `brainless config show` prints what it sets. Running `init` again keeps the
file unless you pass `--force`.

For a script, or a machine you set up often, the same questions take flags:

```bash
bash install.sh --vault ~/brainless --yes -- --lang en --provider ollama --model qwen3:8b
bash install.sh --yes -- --provider anthropic --api-key-env ANTHROPIC_API_KEY --no-schedule
```

## A day with it

```bash
brainless add "the thing I keep thinking about in the shower"
brainless add ~/Downloads/board-pack.pdf
brainless search "what did I decide about hiring"
brainless dialectic "We should hire before we have the revenue"
```

A thought goes into today's notes. A document goes into the inbox and becomes Markdown
within the hour. The real work happens later. From nine o'clock the digest reads
the day's notes and the compile folds them into the wiki.

Laptops spend more evenings shut than open. So nothing here runs on
the clock. Every 15 minutes, and whenever you log in, `brainless tick` asks what has not
run since it was last due, and runs that. A missed night runs the next morning when you
open the lid. On a train, the jobs that need the network wait for Wi-Fi and the rest go
ahead. `brainless tick` also runs everything that is due right now, if you are impatient.

## When something breaks

```bash
brainless doctor            # one line per check
brainless doctor --probe    # plus one real call to the model
brainless queue             # what ran, what is waiting, what failed and why
brainless queue retry       # try the failed ones again
```

A failed job waits 5 minutes, then 30, then 2 hours, then 6, and then it stops and waits
for you. I would rather it gave up than kept paying for the same error all night.

## Concepts

When several notes circle the same idea and nothing in the wiki covers it, the compile
proposes a concept page. In my own setup the proposal arrives on my phone and I answer in
a thread. Here you answer in the terminal.

```bash
brainless concepts            # what is waiting
brainless concepts review     # yes, no or skip, one at a time
```

At most five proposals wait at once, and new ones stop until you decide. That limit came
from my own vault. In September five proposals sat there for three days before I
noticed any of them.

## Choosing a model

I run it on Claude through a subscription. You do not have to.

| You choose | What it needs | Worth knowing |
|---|---|---|
| `ollama` | Ollama on this machine | Nothing leaves the laptop. Slower, and a small model writes thinner summaries. Something around 8 billion parameters is a fair start. |
| `anthropic` | an API key | The default model is `claude-opus-5`. |
| `openai`, `grok`, `gemini`, `openrouter` | an API key | You pick the model during `init`; `python3 tools/llm.py --models <provider>` lists them. |
| `claude-cli` | Claude Code, signed in | Uses the subscription you already pay for. It is also the only one here that can search the web and read photos. |
| `codex-cli`, `gemini-cli` | that tool, signed in | Each call starts in an empty folder, Codex inside its read-only sandbox. They are agents, which is a looser fence than the others, so I would keep them away from anything that reads untrusted text. |

Each provider reads its own key and nobody else's, so a mistake in one setting cannot send
your OpenAI key to xAI. Keys are set with `brainless config secret set <name>`, using
`anthropic_api_key`, `openai_api_key`, `xai_api_key`, `gemini_api_key` or
`openrouter_api_key`. One kind of job can also run on its own model, the short
classifications on something local and the long summaries on something big. See
[Local Inference](local-inference.md) and `[llm.lanes]` in `brainless.toml.example`.

## Removing it

```bash
brainless schedule uninstall
rm -rf ~/.local/bin/brainless ~/.config/brainless
```

That stops the background runs and removes the command. Your notes are still in
`~/brainless`, and I would leave them there. The keys stay in the Keychain under
`brainless` until you delete them, or run `brainless config secret delete <name>` first.

## What I have not tested

Honest limits, as of September 2026.

- Photos are read only with `claude-cli`. With any other model they wait in the inbox.
- Web research needs `claude-cli` too. Without it that job is skipped and says so.
- Windows does not work yet.
- The person who has installed lite most often is me, into a scratch folder with a fake
  home directory. If you are among the first strangers to try it, tell me where it broke
  in the [issues](https://github.com/ezapmar/brainless-public/issues). That is the test I
  have not been able to run myself.
