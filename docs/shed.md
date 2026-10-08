# The AI shed: an always-on worker with agent panes

> **In the loop:** [3. Night shift](../README.md#3-night-shift) and [4. Get shit done](../README.md#4-bonus-get-shit-done). The machine the timers run on also hosts your standing agent session.

An AI shed is an always-on machine on your own Tailscale network where most of
your agents live. brainless already assumes one: the worker that runs the
timers, the capture line and the Buzz personas (see
[Reference deployment](reference-deployment.md)). This guide adds two things to
it: **herdr**, so the shed also holds standing agent panes you attach to from
anywhere, and a **billing split**, so the batch work runs on a monthly API
credit while your interactive sessions stay on the subscription.

## Why split the billing

Claude Max and Team plans include a monthly API credit for the Claude Platform.
It pays for Messages API calls made with a Console API key. It does not pay for
interactive Claude Code, and `claude -p` only draws on it when run with a key.

Two measurements decide the design:

- `claude -p` loads the Claude Code system context on every call. One
  throwaway Haiku call measured about 18k cache-write tokens. A night of
  compile and digest calls through the CLI would burn a 100 USD credit in days.
- The raw Messages API carries only your prompt. The same work costs a fraction.

So the batch brain moves to the `anthropic` provider of `tools/llm.py` (raw
API, a key, the credit) and everything interactive, including the standing
session in the shed, keeps using the subscription login. The one lane that reads
the web (`research-salvo`) stays on the CLI because only the CLI has those tools.

| Work | Provider | Who pays |
|---|---|---|
| nightly compile, digest, close-out, classify, dialectic synthesis, dreaming, content | `anthropic` | monthly API credit |
| research salvo (web) | `claude-cli` (forced) | subscription |
| dialectic personas pinned to `goose` | local | nobody |
| Buzz persona and writing agents (`buzz-acp`) | Claude Code sessions | subscription |
| the standing session in herdr, your Mac, your phone | interactive Claude Code | subscription |

## One-time setup

**Console side (you, in a browser).** Link the plan to a Console organisation
from the plan's billing page. The link is one-way and cannot be moved to another
organisation without support, so use the account the plan belongs to. In the
Console: a workspace for the worker, a spend limit equal to the monthly credit,
no purchased credits and no auto-reload. That last part is the hard guarantee:
when the credit is gone the API refuses, nothing is charged to anything else.
Create one API key in that workspace.

**Worker side.** Store the key where `llm.py` reads it, never in git:

```bash
brainless secret set anthropic_api_key
```

Then point the batch lanes at the API in `brainless.toml`:

```toml
[llm]
provider = "anthropic"
model = "claude-opus-4-8"
fallback = "claude-cli"
api_budget_usd = 100
api_budget_warn = 0.8
api_budget_stop = 0.9
api_budget_reset_day = 1        # the day your plan renews
system_prompt_file = "_Agent-Context/LLM-SYSTEM.md"
```

If the key is linked to an account (a user or a service account) rather than to
a workspace, the API wants the workspace named on every request: add
`anthropic_workspace_id = "wrkspc_..."` (Console > Settings > Workspaces). A
workspace-scoped key needs nothing.

`system_prompt_file` matters: the raw API knows nothing of your `CLAUDE.md`.
Put the house rules there (language, formatting, "sources only"), a few lines,
no personal data. The repo ships a starting point.

Check the routing and send one throwaway call:

```bash
python3 tools/llm.py --lanes
python3 tools/llm.py --probe note-classify
python3 tools/llm.py --usage
```

## The three layers that keep the credit the only money spent

1. **Console**: spend limit, no purchased credits, no auto-reload. The API
   stops by itself.
2. **Ledger and budget in `llm.py`**: every priced call appends a line to
   `.agents/state/llm_costs.jsonl` (tokens, list price, who was billed). Past
   `api_budget_warn` the health check and the worker's watchdog say so in
   `#ops`; past `api_budget_stop` every `anthropic` lane is rerouted to its
   fallback until the reset day, so a night's work is never cut off half way.
   A billing error from the API (credit gone, HTTP 402) also hands the call to
   the fallback.
3. **Visibility**: `python3 tools/llm.py --usage [--days N]` per lane, a row
   "API credit" in `HEALTH.md` (the Mac mirrors the worker's state through
   `tools/worker_reach.py`), and the Console usage page for the cross-check.

Compare the ledger with the Console weekly. The ledger prices at list; a drift
beyond about ten percent means a model's price changed, override it with
`BRAINLESS_PRICE_<MODEL>="in,out,cache_write,cache_read"` per million tokens.

## herdr on the shed

[herdr](https://herdr.dev) is a terminal multiplexer for coding agents: a
single binary whose background server keeps panes alive through disconnects
and marks each agent pane working, blocked or idle. Some distributions ship
it; otherwise install it per user with the script from herdr.dev.

On the shed:

```bash
herdr integration install claude        # Claude Code resumes its session after a restart
npx skills add herdrdev/herdr --skill herdr -g   # agents can open helper panes
systemctl --user enable --now brainless-herdr.service   # headless server at boot
```

`brainless-herdr.service` (in `.agents/systemd/`) runs `herdr server`, the form
the herdr docs name for service-style setups. `install.sh` copies it but does
not start it, like the MCP unit.

On your laptop:

```bash
brew install herdr                       # or the herdr.dev install script
herdr machine add shed --label shed      # "shed" is a Host alias in ~/.ssh/config
herdr machine status shed
herdr --remote shed
```

`machine add` wants the same herdr version on both ends and installs it per user
(`~/.local/bin/herdr`) after asking; from a script, pass `--remote-session default`
and install the matching version on the shed first with the herdr.dev script. A
distribution package may lag behind; the per-user copy shadows it because
`~/.local/bin` comes first on the unit's PATH, and the login shell needs the
same order (one `PATH` line in `~/.bashrc`).

Inside, one workspace in the vault with `claude` in the first pane is the
standing session: ask it what the vault knows, hand it a research question, come
back tomorrow and the thread is still there. Keep it to the vault; a company
repo belongs on a company machine. From a phone, any SSH client over Tailscale
and `herdr` on the shed attaches to the same panes.

## What to watch the first week

- `--usage` every day against the Console. If the monthly projection is over
  the credit, move the long lanes to a cheaper model
  (`BRAINLESS_LLM_MODEL_<LANE>=claude-sonnet-5-5`) rather than lowering quality
  everywhere.
- `lint_wiki.py` and a dash scan on the week's `.wiki` output: the raw API
  runs without the CLI's context, and the system prompt file is what replaces
  it. A lane that reads worse goes back to `claude-cli` with one lane setting.
- After the first reboot of the shed: `systemctl --user status
  brainless-herdr.service`, then attach and confirm the Claude pane resumed.
