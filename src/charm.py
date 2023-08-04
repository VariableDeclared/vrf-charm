#!/usr/bin/env python3
# Copyright 2023 pjds
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""VrfCharm to setup vrf."""
import logging
import os
import pathlib
import re
import subprocess

import ops
from charms.operator_libs_linux.v1.systemd import daemon_reload, service_failed, service_reload
from netplan import NetplanHandler

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

VALID_LOG_LEVELS = ["info", "debug", "warning", "error", "critical"]


class VrfCharm(ops.CharmBase):
    """VrfCharm to setup vrf."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.start, self._on_config_changed)
        self.framework.observe(
            self.on.restart_services_action, self._on_restart_services_action)
        self.netplan_handler = NetplanHandler()

    def _on_restart_services_action(self, event):
        self.restart_units()

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        """Handle changed configuration."""
        log_level = self.model.config["log-level"].lower()
        if log_level not in VALID_LOG_LEVELS:
            self.unit.status = ops.ErrorStatus(
                f"log-level not valid: set log-level to: {VALID_LOG_LEVELS}"
            )
            event.defer()
            return

        self.setup_vrfs(event)

    def find_matching_service_file(self, pattern, systemd_dir=pathlib.Path("/etc/systemd/system")):
        """Given a service name find the full path to it."""
        matching_files = list(
            filter(
                lambda path: path.is_file() and re.match(
                    pattern, path.name), systemd_dir.iterdir()
            )
        )
        logger.debug(f"Matches for {pattern}: {matching_files}")
        return matching_files[0] if matching_files else None

    def rewrite_systemd_service(self):
        """Rewrite systemd services."""
        vrf_name = self.model.config["vrf_name"]
        systemd_dir = pathlib.Path("/etc/systemd/system")
        systemd_units = [
            element.strip() for element in self.model.config["systemd-units"][1:-1].split(",")
        ]

        logger.debug(f"Rewriting this units: {systemd_units}")

        def read_file_content(path):
            with open(path, "r") as fh:
                return fh.read()

        files_path = []
        for service in systemd_units:
            path = self.find_matching_service_file(
                f"{service}.*.service", systemd_dir)
            if path is not None:
                files_path.append(path)

        if not files_path:
            logger.error(f"ERROR: No units found in {systemd_dir}")
            return

        for path in files_path:
            logger.debug(f"Working on {path}")
            content = read_file_content(path)
            match = re.search(r"ExecStart=(.+)", content, re.MULTILINE)
            if match:
                logger.debug(f"Match found on {path}")
                override = (
                    "[Service]\nExecStart=\n"
                    + f"ExecStart=/bin/ip vrf exec {vrf_name} {match.group(1)}"
                )
                logger.debug(f"Creating folder {path}.d/")
                os.makedirs(f"{path}.d/", exist_ok=True)
                with open(f"{path}.d/override.conf", "w") as f:
                    f.write(override)

    def setup_vrfs(self, event):
        """Set up VRF in netplan.

        This module provides functions to set up VRF in netplan.
        """
        self.netplan_handler.netplan = self.netplan_handler.load_netplan()

        target_cidr = self.model.config["target_cidr"]
        if not target_cidr:
            self.unit.status = ops.BlockedStatus("Setup a target CIDR.")
            event.defer()
            return

        target_nic = self.netplan_handler.find_nic(target_cidr)
        if not target_nic:
            self.unit.status = ops.ErrorStatus("No NIC found in target CIDR.")
            event.defer()
            return

        target_gateway = self.netplan_handler.find_gateway()

        vrf_name = self.model.config["vrf_name"]
        vrf_config = self.netplan_handler.generate_vrf_config(
            target_nic, target_gateway, vrf_name)

        self.netplan_handler.netplan["network"].update(vrf_config)
        self.netplan_handler.save_netplan()

        self.rewrite_systemd_service()

        self.unit.status = ops.WaitingStatus(
            "VRFs configured. Units configured, \
            run restart-units to finish configuration."
        )

    def restart_units(self):
        """Restart SSHD and JujuD units."""
        subprocess.check_call(["sudo", "netplan", "apply"])
        systemd_units = [
            element.strip() for element in self.model.config["systemd-units"][1:-1].split(",")
        ]

        self.unit.status = ops.ActiveStatus()
        daemon_reload()
        for service in systemd_units:
            path = self.find_matching_service_file(f"{service}.*.service")
            if path is not None:
                unit = os.path.basename(path)
                logger.info(f"Restarting: {unit}")
                service_reload(unit)
                if service_failed(unit):
                    logging.error(f"Failed to restart unit: {unit}")
                    self.unit.status = ops.ErrorStatus(
                        f"Failed to restart unit: {unit}")


if __name__ == "__main__":
    ops.main(VrfCharm)
