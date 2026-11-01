#!/bin/bash
# Prints the xcodebuild settings that stamp a release build, one per line, and nothing for a
# development build. The iOS App workflow passes them to xcodebuild.
#
# Reads from the environment:
#   BUILD_ID       the build number (Unix seconds, planned by Mobile Release); empty = development build
#   RELEASE_TAG    the release tag, like v98 (display only)
#   RELEASE_LABEL  none, hotfix3, ... (display only)
#
# NOVELDB_BUILD, NOVELDB_TAG and NOVELDB_LABEL become Info.plist keys (see ios/project.yml). With a
# build number, CURRENT_PROJECT_VERSION is build / 60 (Unix minutes) and MARKETING_VERSION is the
# tag's number, so the system's own version fields follow the release.
set -eu

build="${BUILD_ID:-}"
[ -n "$build" ] || exit 0

case "$build" in
  *[!0-9]*) echo "BUILD_ID must be digits, got: $build" >&2; exit 1 ;;
esac
tag="${RELEASE_TAG:-}"
label="${RELEASE_LABEL:-}"
case "$tag" in
  *[!A-Za-z0-9._-]*) echo "RELEASE_TAG has odd characters: $tag" >&2; exit 1 ;;
esac
case "$label" in
  *[!A-Za-z0-9._-]*) echo "RELEASE_LABEL has odd characters: $label" >&2; exit 1 ;;
esac

# 10# keeps a number with leading zeros from being read as octal.
build=$((10#$build))
[ "$build" -gt 0 ] || { echo "BUILD_ID must be positive" >&2; exit 1; }

echo "NOVELDB_BUILD=$build"
echo "NOVELDB_TAG=$tag"
echo "NOVELDB_LABEL=$label"
echo "CURRENT_PROJECT_VERSION=$((build / 60))"
number="${tag#v}"
case "$number" in
  ''|*[!0-9]*) ;;
  *) echo "MARKETING_VERSION=$number" ;;
esac
