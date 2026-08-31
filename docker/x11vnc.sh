#!/bin/sh
# Start x11vnc once Xvfb has created its socket, with a password when one is set.
set -e

for _ in $(seq 1 30); do
    [ -e "/tmp/.X11-unix/X${DISPLAY#:}" ] && break
    sleep 1
done

COMMON="-display ${DISPLAY} -rfbport 5900 -localhost -forever -shared -noxdamage -repeat"

if [ -n "${DOUBAO_NOVNC_PASSWORD}" ]; then
    x11vnc -storepasswd "${DOUBAO_NOVNC_PASSWORD}" /tmp/.x11vnc.pass >/dev/null 2>&1
    exec x11vnc $COMMON -rfbauth /tmp/.x11vnc.pass
fi

echo "WARNING: DOUBAO_NOVNC_PASSWORD is empty - the VNC session accepts anyone who can reach port 6080." >&2
exec x11vnc $COMMON -nopw
