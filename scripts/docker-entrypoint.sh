#!/bin/sh
set -eu

upload_dir="${UPLOAD_DIR:-/app/var/uploads}"
staging_dir="${STAGING_DIR:-/app/var/staging}"

mkdir -p "$upload_dir" "$staging_dir"
chown agentcare:agentcare "$upload_dir" "$staging_dir"

exec gosu agentcare "$@"
