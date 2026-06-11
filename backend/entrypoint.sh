#!/bin/sh
# Self-healing permissions + privilege drop.
#
# Named Docker volumes (e.g. /config) always init as root:root, and bind mounts
# may have arbitrary ownership. Rather than pinning `user:` in compose (which
# then can't write to a root-owned volume), we start as root, fix ownership of
# the writable mounts, set a group-writable umask, then drop to the target user.
#
# PUID/PGID default to Unraid's nobody:users so files land NAS-visible & SMB-movable.
set -e

PUID="${PUID:-99}"
PGID="${PGID:-100}"

# /config: small (session/cookies) — recursive chown is cheap and repairs any
# stragglers left by an earlier run that wrote as root.
[ -d /config ] && chown -R "$PUID:$PGID" /config 2>/dev/null || true

# /downloads: can hold tens of thousands of files — only fix the mount root, not
# the whole tree (the app writes new files as the dropped user anyway).
[ -d /downloads ] && chown "$PUID:$PGID" /downloads 2>/dev/null || true

# 0002 → dirs 775 / files 664: group 'users' can move/edit/delete over SMB.
umask "${UMASK:-0002}"

# If already unprivileged (someone still set `user:` in compose), just exec.
if [ "$(id -u)" != "0" ]; then
  exec "$@"
fi

exec gosu "$PUID:$PGID" "$@"
