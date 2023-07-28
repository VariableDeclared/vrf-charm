#!/usr/bin/env python3
# Copyright 2023 pjds
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

import logging
import pathlib
import ipaddress
import subprocess
import re
import json
import yaml
import ops

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

VALID_LOG_LEVELS = ["info", "debug", "warning", "error", "critical"]


class NetplanHandler:
    def __init__(self, debug=False):
        self.debug = debug
        self.netplan_configdir = pathlib.Path("/tmp/tests/netplan_configdir") if debug else pathlib.Path("/etc/netplan")
        self.configs = list(self.netplan_configdir.iterdir())
        self.netplan = None

    def load_netplan(self):
        """Load Netplan configuration."""
        if not self.configs:
            return None

        with self.configs[0].open("r") as file:
            return yaml.safe_load(file)

    def find_target_nic_and_gateway(self, target_cidr):
        """Find the target NIC and gateway in the Netplan configuration."""
        if not target_cidr:
            return None, None

        netplan = self.load_netplan()
        if not netplan:
            return None, None

        target_mgmt_cidr = ipaddress.IPv4Network(target_cidr)
        target_nic = None
        target_gateway = None

        for interface, nic_def in netplan["network"]["ethernets"].items():
            if "addresses" in nic_def and ipaddress.IPv4Address(nic_def["addresses"][0].split("/")[0]) in target_mgmt_cidr:
                target_nic = interface
                break

        if not target_nic:
            return None, None

        routes = json.loads(subprocess.check_output(["ip", "-j", "route", "show", "default"]).decode())
        if routes:
            if len(routes) > 1:
                logger.debug("WARNING: More than one route avaiable. Heuristic may fail.")
            target_gateway = routes[0]["gateway"]

        return target_nic, target_gateway

    def generate_vrf_config(self, target_nic, target_gateway, vrf_name):
        """Generate the VRF configuration to be added to the Netplan."""
        vrf_config = {
            "vrfs": {
                vrf_name: {
                    "table": 21,
                    "interfaces": [target_nic],
                    "routes": [
                        {
                            "to": "default",
                            "via": target_gateway,
                        }
                    ],
                    "routing-policy": [
                        {
                            "from": self.netplan["network"]["ethernets"][target_nic]["addresses"][0],
                        }
                    ],
                }
            }
        }

        return vrf_config

    def save_netplan(self):
        """Save the updated Netplan configuration."""
        if not self.netplan:
            return

        if len(self.configs) > 1:
            logger.warn("WARNING: More than one netplan. Picking the first one and merging")

        target_netplan = self.configs[0]
        if self.debug:
            with open("/tmp/test.netplan.yaml", "w") as file:
                file.write(yaml.safe_dump(self.netplan))
        else:
            target_netplan.write_text(yaml.safe_dump(self.netplan))

class VrfCharmCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.start, self._on_config_changed)
        self.framework.observe(self.on.restart_services_action, self._on_restart_services_action)
        self.netplan_handler = NetplanHandler(self.model.config['debug'])


    def _on_restart_services_action(self, event):
        self.restart_units()

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        """Handle changed configuration."""
        log_level = self.model.config["log-level"].lower()

        self.setup_vrfs(event)

    def rewrite_systemd_service(self):

        vrf_name = self.model.config['vrf_name']
        jujud_svcfile = None
        if self.model.config['debug']:
            systemd_filecollection = pathlib.Path(
                "/tmp/tests/systemd_configd"
            )
        else:
            systemd_filecollection = pathlib.Path("/etc/systemd/system")

        jujud_svcfile = list(
            filter(
                lambda path: re.match(r"jujud-machine-[0-9]{1,}\.service", path.name),
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
        if m != None:
            modified_sshd_svcfile = (
                sshd_svcfile_content[: m.start()]
                    + f"ExecStart=/bin/ip vrf exec {vrf_name} {m.group('binary')}"
                    + sshd_svcfile_content[m.end() :]
            )

        m = re.search(
            r"ExecStart=(?P<script>/etc/systemd/system/jujud-machine-[0-9]{1,}-exec-start.sh)",
            jujud_svcfile_content,
            re.MULTILINE,
        )
        #if m == None:
        #    logger.debug(
        #        "WARNING: Juju not found, the script is probably running during MAAS setup, not juju setup. Exiting gracefully"
        #    )
        #    exit(0)

        modified_jujud_svcfile = jujud_svcfile_content
        if m != None:
            modified_jujud_svcfile = (
                jujud_svcfile_content[: m.start()]
                    + f"ExecStart=/bin/ip vrf exec {vrf_name} {m.group('script')}"
                    + jujud_svcfile_content[m.end() :]
            )


        if self.model.config['debug']:
            open("/tmp/test.svcfile.jujud.service", "w").write(modified_jujud_svcfile)
            open("/tmp/test.svcfile.sshd.service", "w").write(modified_sshd_svcfile)
        else:
            open(sshd_svcfile, "w").write(modified_sshd_svcfile)
            open(jujud_svcfile, "w").write(modified_jujud_svcfile)

    def setup_vrfs(self, event):
        """Setup VRFs."""
        self.netplan_handler.netplan = self.netplan_handler.load_netplan()

        target_cidr = self.model.config['target_cidr']
        if not target_cidr:
            self.unit.status = ops.BlockedStatus("Setup a target CIDR.")
            event.defer()
            return

        target_nic, target_gateway = self.netplan_handler.find_target_nic_and_gateway(target_cidr)

        if not target_nic:
            self.unit.status = ops.ErrorStatus("No NIC found in target CIDR.")
            event.defer()
            return

        vrf_name = self.model.config['vrf_name']
        vrf_config = self.netplan_handler.generate_vrf_config(target_nic, target_gateway, vrf_name)
        self.netplan_handler.netplan['network'].update(vrf_config)
        self.netplan_handler.save_netplan()

        self.rewrite_systemd_service()

        self.unit.status = ops.WaitingStatus("VRFs configured. Units configured, run restart-units to finish configuration.")

    def restart_units(self):
        """Restart SSHD and JujuD units."""
        subprocess.check_call("sudo systemctl daemon-reload".split())

        for service in ["sshd", "jujud-*"]:
           subprocess.check_call(f"sudo systemctl restart {service}".split())

        if not self.model.config['debug']:
            subprocess.check_call(["sudo", "netplan", "apply"])


if __name__ == "__main__":
    ops.main(VrfCharmCharm)
