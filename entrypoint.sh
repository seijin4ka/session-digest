#!/bin/sh
chown -R appuser:appuser /tmp/session-digest
exec su-exec appuser "$@"
