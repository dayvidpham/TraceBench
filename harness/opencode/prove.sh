#!/usr/bin/env bash
# Copy the OpenCode payload into a stopped container, start it, and check.
# No network flag, no volume mount, no opencode.json.
set -euo pipefail
cd "$(dirname "$0")/../.."

payload=harness/opencode
name=tb-opencode-trial
image=tracebench-trace-propagation
ref=$(tr -d '[:space:]' < "$payload/version")
fifo=/tmp/tb-opencode.fifo

test -n "$ref"
test -d "$payload/src"
test -f "$payload/REVISION"
test -x "$payload/bin/opencode"
test -z "$(find "$payload/src" -name .git -print -quit)"

host_rev=$(sha256sum "$payload/REVISION")
host_bin=$(sha256sum "$payload/bin/opencode")
echo "host REVISION $host_rev"
echo "host binary  $host_bin"

if ! command -v podman >/dev/null 2>&1; then
  echo "podman is not installed" >&2
  exit 1
fi

podman build -t "$image" \
  -f tasks/trace-propagation/environment/Dockerfile \
  tasks/trace-propagation/environment

podman rm -f "$name" >/dev/null 2>&1 || true

# The image CMD is python3. Without --interactive, `podman start` closes
# stdin, python exits 0, and `podman exec` cannot run. -i keeps that same
# CMD up while a held fifo supplies stdin. No command override.
podman create -i --name "$name" "$image"

created=$(podman inspect --format '{{.State.Status}} {{json .Mounts}}' "$name")
echo "after create: $created"
test "$created" = "created []"

podman cp "$payload/src" "$name":/opt/opencode
podman cp "$payload/bin/opencode" "$name":/usr/local/bin/opencode
podman cp "$payload/REVISION" "$name":/opt/opencode/REVISION

rm -f "$fifo"
mkfifo "$fifo"
sleep infinity >"$fifo" &
hold_pid=$!
podman start -a "$name" <"$fifo" >/tmp/tb-opencode-start.out 2>/tmp/tb-opencode-start.err &
start_pid=$!

cleanup() {
  podman stop -t 2 "$name" >/dev/null 2>&1 || true
  kill "$hold_pid" >/dev/null 2>&1 || true
  wait "$start_pid" 2>/dev/null || true
  wait "$hold_pid" 2>/dev/null || true
  rm -f "$fifo"
}
trap cleanup EXIT

ready=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
  state=$(podman inspect --format '{{.State.Status}}' "$name")
  if [ "$state" = "running" ]; then
    ready=1
    break
  fi
  sleep 0.25
done

if [ "$ready" != 1 ]; then
  echo "container did not stay running (status=$(podman inspect --format '{{.State.Status}} {{.State.ExitCode}}' "$name"))" >&2
  echo "--- start stdout ---" >&2
  cat /tmp/tb-opencode-start.out >&2 || true
  echo "--- start stderr ---" >&2
  cat /tmp/tb-opencode-start.err >&2 || true
  exit 1
fi

mounts=$(podman inspect --format '{{json .Mounts}}' "$name")
echo "mounts: $mounts"
test "$mounts" = "[]"

podman exec "$name" sha256sum /opt/opencode/REVISION /usr/local/bin/opencode
podman exec "$name" stat -c '%n %u' /opt/opencode/REVISION /usr/local/bin/opencode

git_hits=$(podman exec "$name" find /opt/opencode -name .git -print)
printf '%s' "$git_hits"
test -z "$git_hits"

guest_rev=$(podman exec "$name" sha256sum /opt/opencode/REVISION)
guest_bin=$(podman exec "$name" sha256sum /usr/local/bin/opencode)
test "${guest_rev%% *}" = "${host_rev%% *}"
test "${guest_bin%% *}" = "${host_bin%% *}"

rev_uid=$(podman exec "$name" stat -c '%u' /opt/opencode/REVISION)
bin_uid=$(podman exec "$name" stat -c '%u' /usr/local/bin/opencode)
echo "uid REVISION=$rev_uid binary=$bin_uid"
test "$rev_uid" = 0
test "$bin_uid" = 0

ver_out=$(timeout 90 podman exec "$name" opencode --version 2>/tmp/tb-opencode-version.err)
echo "opencode --version stdout: $ver_out"
echo "opencode --version stderr:"
cat /tmp/tb-opencode-version.err || true
bare=${ref#v}
printf '%s\n' "$ver_out" | grep -Fqx "$bare"

echo "prove ok ref=$ref"
