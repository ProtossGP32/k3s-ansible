#!/usr/bin/env bash

# Jetect regression checks for the Jetson role wiring.
#
# The roles are the single hardware detection path now that the site pre-tasks
# gate has been removed: the role itself must keep the device-tree based
# primitives, site.yml must stop gating on fact vars, and reset.yml must still
# apply the role so a Jetson is fully torn down.

set -Eeuo pipefail

repo_root="$(git rev-parse --show-toplevel)"
jetson_main="$repo_root/roles/jetson/tasks/main.yml"
jetson_setup="$repo_root/roles/jetson/tasks/setup.yml"
jetson_defaults="$repo_root/roles/jetson/defaults/main.yml"
jetson_handlers="$repo_root/roles/jetson/handlers/main.yml"
site_yml="$repo_root/site.yml"
reset_yml="$repo_root/reset.yml"
server_post_jetson="$repo_root/roles/k3s_server_post/tasks/jetson.yml"

# Detection primitives must survive in the role itself.
grep -Fq -- '/proc/device-tree/model' "$jetson_main" || {
  printf 'jetson role lost the device-tree model detection\n' >&2
  exit 1
}
grep -Fq -- '/proc/device-tree/compatible' "$jetson_main" || {
  printf 'jetson role lost the device-tree compatible detection\n' >&2
  exit 1
}
grep -Fq -- '/etc/nv_tegra_release' "$jetson_main" || {
  printf 'jetson role lost the /etc/nv_tegra_release probe\n' >&2
  exit 1
}
grep -Fq -- 'Set is_jetson fact to true' "$jetson_main" || {
  printf 'jetson role lost the is_jetson fact wiring\n' >&2
  exit 1
}

# site.yml must not gate the roles behind the removed pre-tasks facts.
grep -Eq -- 'when:.*is_(jetson|raspberry_pi)' "$site_yml" && {
  printf 'site.yml still gates a role behind the removed detection facts\n' >&2
  exit 1
}
grep -Eq -- 'Read device tree model from remote host' "$site_yml" && {
  printf 'site.yml still contains the removed pre-tasks detection block\n' >&2
  exit 1
}
grep -Eq -- '^\s+- role: (jetson|raspberrypi)$' "$site_yml" || {
  printf 'site.yml no longer applies the (raspberrypi|jetson) roles unconditionally\n' >&2
  exit 1
}

# reset.yml must still tear the role down.
if ! grep -Eq -- 'role: jetson' "$reset_yml" || ! grep -Eq -- 'state: absent' "$reset_yml"; then
  printf 'reset.yml does not apply the jetson role with state: absent\n' >&2
  exit 1
fi

# Role-aware GPU advertisement default must reference node role membership.
grep -Fq -- 'group_names' "$jetson_setup" || {
  printf 'jetson setup lost the role-aware label default\n' >&2
  exit 1
}
grep -Fq -- "groups['k3s_cluster']" "$jetson_setup" || {
  printf 'jetson setup lost the single-node cluster check\n' >&2
  exit 1
}

# Device plugin is on by default and no longer has a static label default.
grep -Eq -- '^jetson_deploy_device_plugin: true' "$jetson_defaults" || {
  printf 'jetson defaults do not enable the device plugin by default\n' >&2
  exit 1
}
grep -Eq -- '^jetson_label_node:' "$jetson_defaults" && {
  printf 'jetson defaults still define a static jetson_label_node value\n' >&2
  exit 1
}

# The role reboot handler must survive slow NVMe-origin reboots.
grep -Fq -- 'reboot_timeout: 3600' "$jetson_handlers" || {
  printf 'jetson reboot handler lacks reboot_timeout: 3600\n' >&2
  exit 1
}

# The device-plugin manifest staging must not loop over the master group.
grep -Eq -- 'with_items:.*group_name_master' "$server_post_jetson" && {
  printf 'k3s_server_post/jetson.yml still loops over the master group\n' >&2
  exit 1
}

printf 'Jetson role wiring regression test passed\n'
