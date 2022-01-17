#!/usr/bin/env sh
if [ -e agent1.down ]; then
  echo "check-webserver   CRIT: Webserver is down"
  exit 2
else
  echo "check-webserver   OK: Webserver is OK"
fi