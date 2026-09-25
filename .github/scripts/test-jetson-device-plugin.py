#!/usr/bin/env python3
"""Regression-test the Jetson device-plugin DaemonSet and its deploy guard.

The DaemonSet must tolerate both the `nvidia.com/gpu` taint and the
control-plane taint (so a labeled master or a single-node cluster can run it),
and only schedule on nodes labeled `nvidia.com/gpu.present=true`. The deploy
task in `k3s_server_post` must additionally be restricted to clusters that
actually contain a Jetson node, so non-Jetson users never get an inert NVIDIA
DaemonSet in `kube-system`.
"""

from __future__ import print_function

import os
import subprocess

import yaml
from jinja2 import Environment, Undefined


def repo_root():
    return subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()


def fail(message):
    raise SystemExit("Jetson device-plugin test failed: " + message)


def fake_bool(value):
    # Minimal stand-in for Ansible's truthiness filter used by the guard.
    if isinstance(value, bool):
        return value
    return bool(value)


def fake_extract(item, container, key):
    # Ansible's `extract` filter returns an undefined marker for missing keys;
    # the subsequent `default` filter coalesces it. Mirror that so the guard
    # expression can be evaluated with real Jinja map/select filters.
    cell = container.get(item)
    if not isinstance(cell, dict) or key not in cell:
        return Undefined(name=key)
    return cell[key]


def load_manifest():
    path = os.path.join(
        repo_root(), "roles", "k3s_server_post", "templates",
        "nvidia-device-plugin.yaml.j2",
    )
    with open(path, encoding="utf-8") as fobj:
        return fobj.read()


def load_deploy_when():
    path = os.path.join(repo_root(), "roles", "k3s_server_post", "tasks", "main.yml")
    with open(path, encoding="utf-8") as fobj:
        tasks = yaml.safe_load(fobj)
    for task in tasks:
        if task.get("name") == "Deploy NVIDIA device plugin for Jetson":
            when = task["when"]
            if not isinstance(when, list) or len(when) != 2:
                fail("device plugin deploy task does not use a two-part when")
            return when
    fail("device plugin deploy task not found in k3s_server_post")


def evaluate_guard(env, when_conditions, context):
    return all(
        env.compile_expression(cond)(**context) for cond in when_conditions
    )


def main():
    env = Environment()
    env.filters["extract"] = fake_extract
    env.filters["bool"] = fake_bool

    # --- Manifest shape and tolerations --------------------------------
    manifest = load_manifest()
    if "key: node-role.kubernetes.io/control-plane" not in manifest:
        fail("manifest is missing the control-plane toleration")
    if "key: nvidia.com/gpu" not in manifest:
        fail("manifest is missing the nvidia.com/gpu taint toleration")
    if manifest.count("effect: NoSchedule") != 2:
        fail("manifest does not tolerate both taints with NoSchedule")
    if "nvidia.com/gpu.present: \"true\"" not in manifest:
        fail("manifest nodeSelector does not match the Jetson node label")
    if "nvcr.io/nvidia/k8s-device-plugin:v0.17.1" not in manifest:
        fail("manifest device-plugin image tag is not v0.17.1")
    if "--fail-on-init-error=false" not in manifest:
        fail("manifest is missing --fail-on-init-error=false")
    if "--pass-device-specs=false" not in manifest:
        fail("manifest is missing --pass-device-specs=false")

    # --- Deploy guard: cluster must contain a Jetson node ---------------
    toggle, guard = load_deploy_when()
    base = {
        "groups": {
            "k3s_cluster": ["m1", "w1"],
        },
        "hostvars": {
            "m1": {"is_jetson": False},
            "w1": {"is_jetson": True},
        },
    }

    # Worker Jetson present: guard passes when the toggle is on.
    context = dict(base)
    context["jetson_deploy_device_plugin"] = True
    if not evaluate_guard(env, [toggle, guard], context):
        fail("guard rejects a cluster that contains a Jetson worker")

    # No Jetson node anywhere: guard must refuse to deploy.
    context = dict(base)
    context["jetson_deploy_device_plugin"] = True
    context["hostvars"] = {"m1": {"is_jetson": False}, "w1": {"is_jetson": False}}
    if evaluate_guard(env, [toggle, guard], context):
        fail("guard deploys the DaemonSet on a cluster without Jetson nodes")

    # Toggle off wins even when the cluster has Jetson nodes.
    context = dict(base)
    context["jetson_deploy_device_plugin"] = False
    if evaluate_guard(env, [toggle, guard], context):
        fail("guard deploys the DaemonSet while the toggle is off")

    # A host missing the is_jetson fact must not break the guard.
    context = dict(base)
    context["jetson_deploy_device_plugin"] = True
    context["hostvars"] = {"m1": {}, "w1": {"is_jetson": True}}
    if not evaluate_guard(env, [toggle, guard], context):
        fail("guard breaks when a node lacks the is_jetson fact")

    # No k3s_cluster group at all: guard must not error and must refuse.
    context = {
        "groups": {},
        "hostvars": base["hostvars"],
        "jetson_deploy_device_plugin": True,
    }
    if evaluate_guard(env, [toggle, guard], context):
        fail("guard deploys the DaemonSet without a k3s_cluster group")

    print("Jetson device-plugin regression test passed")


if __name__ == "__main__":
    main()
