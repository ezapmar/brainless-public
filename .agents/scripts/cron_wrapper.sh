#!/bin/bash
# Hourly (launchd local.brainless.hourly): cheap, mostly LLM-free work
# that should not wait for the night, plus the morning set once a day.
# No commits: the night shift (night.sh) is the single committer.
source "$(dirname "$0")/env.sh"
rotate
python3 tools/net_wait.py --wait || { python3 tools/health_check.py >> logs/health_check.log 2>&1; exit 0; }

run crm python3 .agents/scripts/crm_capture.py              # Pipedrive snapshot, no LLM
run spiky python3 .agents/scripts/spiky_capture.py          # Gmail IMAP, no LLM
run spiky python3 .agents/scripts/spiky_actions.py          # action items, local model
if [ -x "$GPY" ]; then
  run tasks "$GPY" .agents/scripts/gtasks_sync.py           # TASKS.md <-> Google Tasks
  run brief "$GPY" .agents/scripts/meeting_brief.py         # brief before the next meeting
fi

# Morning set: the first hourly run after 07:00, once per day. A Mac that was
# asleep at 07:00 catches up when it wakes, with the right date.
STAMP=.agents/state/morning_last
TODAY=$(date +%F)
if [ "$(date +%H)" -ge 7 ] && [ "$(cat "$STAMP" 2>/dev/null)" != "$TODAY" ]; then
  echo "$TODAY" > "$STAMP"
  run classify python3 tools/note_classify.py
  run morning python3 tools/think_surface.py
  run morning python3 .agents/scripts/task_reminder.py      # today queue to Telegram
  case "$(date +%u)" in
    1) run morning python3 tools/resurface.py
       run morning python3 tools/writing_index.py --post
       run morning python3 tools/build_dashboard.py ;;
    2) run weekly python3 .agents/scripts/content_engine.py ;;
    5) run weekly python3 .agents/scripts/thinker_digest.py
       run morning python3 tools/build_dashboard.py ;;
  esac
fi

python3 tools/health_check.py >> logs/health_check.log 2>&1
exit 0
