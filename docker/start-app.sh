#!/bin/sh
# Chromium refuses to open a profile whose SingletonLock names a different
# host, and a rebuilt container is always a different host. Nothing else can
# be holding this profile: one browser per container, and it is not running
# yet. Only the lock trio is touched, never the session data beside it.
set -e

PROFILE="${DOUBAO_BROWSER_DATA:-/data/browser}"
rm -f "${PROFILE}/SingletonLock" "${PROFILE}/SingletonCookie" "${PROFILE}/SingletonSocket"

exec python -m doubao2api
