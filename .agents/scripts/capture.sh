#!/bin/bash
# Every 2 minutes (launchd local.brainless.capture): the one Telegram
# poller. Notes in, replies answered, undelivered notices retried. No git here:
# the night shift commits once a day.
source "$(dirname "$0")/env.sh"
python3 tools/net_wait.py --wait || exit 0   # just woke, no network yet
python3 .agents/scripts/telegram_capture.py >> logs/telegram.log 2>&1
