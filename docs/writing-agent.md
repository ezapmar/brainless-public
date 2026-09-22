# Writing and narrative agents

> **In the loop:** an addon, outside the four lines. See [Addons](how-it-works.md#addons).

Two conversational agents live in their own Buzz channels and talk with the owner
about long-form writing. `#writing` is for essays, opinion pieces for business
publications, blog posts and book chapters. `#narratives` is for children's stories.
Social media drafts stay with the content engine and `#content`.

Each agent is the same harness the personas use (`buzz-acp` in front of Claude
Code), with three differences: it subscribes to the whole channel and answers every owner
message there without a mention (`BUZZ_ACP_SUBSCRIBE=all`, `BUZZ_ACP_NO_MENTION_FILTER`), it keeps one isolated session per thread, and it may write
files, but only inside its own drafts folder.

| Agent | Channel | Writes to | Reads first |
|---|---|---|---|
| Writer | `#writing` | `Writings/Drafts/` (`drafts_dir` in PROFILE) | `_Agent-Context/WRITING.md`, then the editor folder's README |
| Narrator | `#narratives` | `Writings/Narratives/Drafts/` (`narratives_dir` + `Drafts`) | the story guide in the narratives folder |

## Thread contract

One thread per piece. The root message carries `Title | kind | venue | status`.
Status moves idea, pitch, draft v1, draft vN, revision, sent, published. Every
agent reply names the file path and the version it wrote. A draft is never posted
into the channel; the reply carries the path, the lint counts and the questions.

Before any draft the Writer loads the voice and editing files in the order the
editor folder's README prescribes, writes the whole piece to
`Drafts/YYYY-MM-DD <slug> vN.md`, runs the editor lint on it and reports the
counts. A ceiling breach is fixed before the reply goes out. Revisions change
only what was asked and land in a new version file. Numbers and quotations carry
a vault path or a fetched URL, or are marked for checking.

## Pitch round and the writing map

`tools/writing_index.py` compiles `_Agent-Context/WRITING.md`: finished pieces
and working files, drafts in flight, open pitches, the long-form homes and the
published corpus named in `PROFILE.md` (`longform_dirs`, `corpus_dirs`), and the
editing files. It runs weekly on the worker and posts a one-line summary.

`tools/writing_ideas.py` runs every second month (the 1st, 09:00). It reads the
beliefs, seed ideas, the latest dialectic syntheses and two weeks of captures,
asks the model for three pitches, files each one in the drafts folder and posts
each as its own root message in `#writing`. `--topics` turns the owner's own ideas
into pitches instead. The owner answers in the thread; the Writer takes it from
there. Nothing is ever published by the system.

## Install

Requirements: the persona installer has run once (it creates the moderator key
and the relay membership tooling), the Buzz relay is reachable, `PROFILE.md`
names the folders. Then, on the worker:

```bash
bash .agents/buzz/install_agent_channel.sh writing
bash .agents/buzz/install_agent_channel.sh narratives
```

The installer generates the agent key once, creates the channel if missing, adds
the owner and the agent as members, fills the system prompt and the Claude
settings from `.agents/buzz/agents/<slug>/`, writes `~/.config/brainless/buzz/<slug>.env`
and enables `buzz-agent@<slug>.service`. `BUZZ_AGENT_MODEL` (default
`claude-fable-5-1`) and `BUZZ_AGENT_EFFORT` (default `high`) pin the model the
harness applies to every session. The two timers ship with the other worker
units (`bash .agents/systemd/install.sh`).

## Guard rails

- Permissions are a Claude settings file: read anything in the vault, edit only
  the drafts folder, run only the search tool, the lint tool and the Buzz CLI. No
  git, no deletes, no network tools except web fetch and search for sources.
- The polling reply worker never answers in these channels
  (`HARNESS_CHANNELS` in `tools/buzz_delivery.py`), so an owner message gets one
  reply, from the agent.
- Moving a text out of `Drafts/` into the writings folder is the owner's act.
  The agent prompt says so, and the ownership rules record the exception.
- Thread text and vault text are material for the agent, never instructions to
  change its rules.
