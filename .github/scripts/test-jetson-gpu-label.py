#!/usr/bin/env python3
"""Regression-test the Jetson GPU label derivation logic.

The `_jetson_add_label` fact is computed from Jinja expressions in
`roles/jetson/tasks/setup.yml`. The default is role-aware: dedicated workers
advertise the GPU, masters do not, and a single-node cluster always
advertises. An explicit `jetson_label_node` inventory value overrides the
derived default, and the label is never appended twice.

This test loads the real expressions from the task file and evaluates them
with Jinja2 over a matrix of group memberships, so a regression in the logic
or its YAML folding fails here instead of on a real board.
"""

from __future__ import print_function

import os
import subprocess

import yaml
from jinja2 import Environment, StrictUndefined


def repo_root():
    return subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()


def fail(message):
    raise SystemExit("Jetson GPU label test failed: " + message)


def fake_bool(value):
    # Minimal stand-in for Ansible's truthiness filter used by the expressions.
    if isinstance(value, bool):
        return value
    return bool(value)


def load_set_fact_expressions():
    tasks_path = os.path.join(
        repo_root(), "roles", "jetson", "tasks", "setup.yml"
    )
    with open(tasks_path, encoding="utf-8") as fobj:
        tasks = yaml.safe_load(fobj)

    default_expr = None
    label_expr = None
    for task in tasks:
        if not isinstance(task, dict):
            continue
        set_fact = None
        for key, value in task.items():
            if key == "set_fact" or key.endswith(".set_fact"):
                set_fact = value
                break
        if set_fact is None:
            continue
        if task.get("name") == "Determine default GPU advertisement for this node":
            default_expr = set_fact["_jetson_gpu_advertise_default"]
        if task.get("name") == "Determine if Jetson node label should be added":
            label_expr = set_fact["_jetson_add_label"]

    if default_expr is None or label_expr is None:
        fail("setup.yml is missing the label derivation set_fact tasks")
    return default_expr, label_expr


def make_context(k3s_cluster, group_names, group_name_master="master",
                 label_override=None, server_args="", agent_args=""):
    ctx = {
        "group_names": group_names,
        "groups": {"k3s_cluster": k3s_cluster},
        "group_name_master": group_name_master,
        "extra_server_args": server_args,
        "extra_agent_args": agent_args,
    }
    if label_override is not None:
        ctx["jetson_label_node"] = label_override
    return ctx


def evaluate(env, default_expr, label_expr, context):
    default_value = env.from_string(default_expr).render(**context) == "True"
    context = dict(context)
    context["_jetson_gpu_advertise_default"] = default_value
    label_value = env.from_string(label_expr).render(**context) == "True"
    return default_value, label_value


def main():
    env = Environment(undefined=StrictUndefined)
    env.filters["bool"] = fake_bool
    default_expr, label_expr = load_set_fact_expressions()

    # Labeled nodes in a multi-node cluster, by group membership.
    multi = ["m1", "m2", "w1"]
    cases = [
        ("multi-node worker advertises by default",
         make_context(multi, ["node"]), True),
        ("multi-node master stays unlabeled by default",
         make_context(multi, ["master"]), False),
        ("single-node master advertises by default",
         make_context(["m1"], ["master"]), True),
        ("single-node in both groups advertises by default",
         make_context(["m1"], ["master", "node"]), True),
        ("explicit override disables label on a worker",
         make_context(multi, ["node"], label_override=False), False),
        ("explicit override enables label on a master",
         make_context(multi, ["master"], label_override=True), True),
        ("master with explicit false in single-node cluster stays off",
         make_context(["m1"], ["master"], label_override=False), False),
        ("label not duplicated through server args",
         make_context(multi, ["node"],
                      server_args="--node-label nvidia.com/gpu.present=true"),
         False),
        ("label not duplicated through agent args",
         make_context(multi, ["node"],
                      agent_args="--node-label nvidia.com/gpu.present=true"),
         False),
        ("irrelevant group membership does not advertise",
         make_context(multi, ["proxmox"]), False),
        ("worker outside k3s_cluster membership still advertises",
         make_context(["w1"], ["node"]), True),
    ]

    for label, context, expected in cases:
        default_value, label_value = evaluate(
            env, default_expr, label_expr, context
        )
        if label_value != expected:
            fail(
                "case {0!r}: expected label={1} got label={2} "
                "(advertise_default={3})".format(
                    label, expected, label_value, default_value
                )
            )

    print("Jetson GPU label regression test passed")


if __name__ == "__main__":
    main()
