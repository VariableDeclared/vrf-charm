#!/usr/bin/env python3
# Copyright 2023 pjds
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

import logging
import pathlib
import subprocess
import os
import re
import ops
from netplan import NetplanHandler

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

VALID_LOG_LEVELS = ["info", "debug", "warning", "error", "critical"]


class VrfCharmCharm(ops.CharmBase):
    """Charm the service."""

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
                f"log-level not valid: set log-level to: {VALID_LOG_LEVELS}")
            event.defer()
            return

        self.setup_vrfs(event)

    def rewrite_systemd_service(self):

        vrf_name = self.model.config['vrf_name']
        jujud_svcfile = None
        systemd_filecollection = pathlib.Path("/etc/systemd/system")
        jujud_svcfile = list(
            filter(
                lambda path: re.match(
                    r"jujud-machine-[0-9]{1,}\.service", path.name),
                systemd_filecollection.iterdir(),
            )
        )[0]

        sshd_svcfile = list(
            filter(
                lambda path: re.match(r"sshd.service", path.name),
                systemd_filecollection.iterdir(),
            )
        )[0]

        def openfile_and_read(path):
            with open(path, "r") as fh:
                return fh.read()

        sshd_svcfile_content = openfile_and_read(sshd_svcfile)
        jujud_svcfile_content = openfile_and_read(jujud_svcfile)

        m = re.search(
            r"ExecStart=(?P<binary>/usr/sbin/sshd {1}-D {1}\$SSHD_OPTS)",
            sshd_svcfile_content,
            re.MULTILINE,
        )
        modified_sshd_svcfile = sshd_svcfile_content
        if m is not None:
            modified_sshd_svcfile = (
                sshd_svcfile_content[: m.start()]
                + f"ExecStart=/bin/ip vrf exec {vrf_name} {m.group('binary')}"
                + sshd_svcfile_content[m.end():]
            )

        m = re.search(
            r"ExecStart=(?P<script>/etc/systemd/system/jujud-machine-[0-9]{1,}-exec-start.sh)",
            jujud_svcfile_content,
            re.MULTILINE,
        )
        # if m == None:
        #    logger.debug(
        #        "WARNING: Juju not found, the script is probably running during MAAS setup, not juju setup. Exiting gracefully"
        #    )
        #    exit(0)

        modified_jujud_svcfile = jujud_svcfile_content
        if m is not None:
            modified_jujud_svcfile = (
                jujud_svcfile_content[: m.start()]
                + f"ExecStart=/bin/ip vrf exec {vrf_name} {m.group('script')}"
                + jujud_svcfile_content[m.end():]
            )

        os.mkdir(f"{sshd_svcfile}.d/")
        os.mkdir(f"{jujud_svcfile}.d/")
        open(f"{sshd_svcfile}.d/overrides.conf",
             "w").write(modified_sshd_svcfile)
        open(f"{jujud_svcfile}.d/overrides.conf",
             "w").write(modified_jujud_svcfile)

    def setup_vrfs(self, event):
        """Setup VRFs."""
        self.netplan_handler.netplan = self.netplan_handler.load_netplan()

        target_cidr = self.model.config['target_cidr']
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

        vrf_name = self.model.config['vrf_name']
        vrf_config = self.netplan_handler.generate_vrf_config(
            target_nic, target_gateway, vrf_name)

        self.netplan_handler.netplan['network'].update(vrf_config)
        self.netplan_handler.save_netplan()

        self.rewrite_systemd_service()

        self.unit.status = ops.WaitingStatus(
            "VRFs configured. Units configured, \
            run restart-units to finish configuration.")

    def restart_units(self):
        """Restart SSHD and JujuD units."""
        subprocess.check_call("sudo systemctl daemon-reload".split())

        for service in ["sshd", "jujud-*"]:
            subprocess.check_call(f"sudo systemctl restart {service}".split())

        subprocess.check_call(["sudo", "netplan", "apply"])
        self.unit.status = ops.ActiveStatus()


if __name__ == "__main__":
    ops.main(VrfCharmCharm)
