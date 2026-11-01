#!/bin/bash
# Checks that a built app's Info.plist carries what release_stamp.sh asks for (the same
# BUILD_ID, RELEASE_TAG and RELEASE_LABEL environment). Usage: check_stamp.sh <path to Info.plist>
set -eu

plist="$1"
status=0

check() {
  local actual
  actual=$(plutil -extract "$1" raw -o - "$plist" 2>/dev/null || true)
  if [ "$actual" = "$2" ]; then
    echo "ok    $1 = '$actual'"
  else
    echo "::error::$1 is '$actual' in the built app, expected '$2'"
    status=1
  fi
}

stamp=$(bash "$(dirname "$0")/release_stamp.sh")
if [ -z "$stamp" ]; then
  echo "Development build: the stamp keys must be empty."
  check NovelDBBuild ""
  check NovelDBTag ""
  check NovelDBLabel ""
else
  while IFS='=' read -r key value; do
    case "$key" in
      NOVELDB_BUILD) check NovelDBBuild "$value" ;;
      NOVELDB_TAG) check NovelDBTag "$value" ;;
      NOVELDB_LABEL) check NovelDBLabel "$value" ;;
      CURRENT_PROJECT_VERSION) check CFBundleVersion "$value" ;;
      MARKETING_VERSION) check CFBundleShortVersionString "$value" ;;
    esac
  done <<< "$stamp"
fi
exit "$status"
