#!/usr/bin/env python3
# Copyright 2023 pjds
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""

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


class VrfCharmCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.start, self.setup_vrfs)

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        """Handle changed configuration.

        Change this example to suit your needs. If you don't need to handle config, you can remove
        this method.

        Learn more about config at https://juju.is/docs/sdk/config
        """
        # Fetch the new config value
        log_level = self.model.config["log-level"].lower()

        # Do some validation of the configuration option
        self.setup_vrfs(event)


    def openfile_and_read(self, path):
        with open(path, "r") as fh:
            return fh.read()

    # TODO: Dynamic unit update 
    # TODO: Relations?
    def setup_vrfs(self, event):
        netplan = {}

        if self.model.config['debug']:
            netplan_configdir = pathlib.Path(
                "/tmp/tests/netplan_configdir"
            )
        else:
            netplan_configdir = pathlib.Path("/etc/netplan")
        configs = list(netplan_configdir.iterdir())

        target_netplan = None
        if len(configs) > 1:
            print("WARNING: More than one netplan. Picking the first one and merging.")
        target_netplan = configs[0]

        netplan = yaml.safe_load(self.openfile_and_read(target_netplan))
        if not self.model.config['target_cidr']:
            self.model.status = ops.BlockedStatus("Need target CIDR")
            event.defer()
            return

        target_mgmt_cidr = ipaddress.IPv4Network(self.model.config['target_cidr'])
        target_nic = None
        target_gateway = None
        vrf_name = self.model.config['vrf_name']

        for interface, nic_def in netplan["network"]["ethernets"].items():
            if (
                "addresses" in nic_def
                and ipaddress.IPv4Address(nic_def["addresses"][0].split("/")[0])
                in target_mgmt_cidr
            ):
                target_nic = interface

        if not target_nic:
            self.model.status = ops.BlockedStatus("No NIC found in target CIDR.")
            event.defer()
            return

        routes = json.loads(
            subprocess.check_output(["ip", "-j", "route", "show", "default"]).decode()
        )
        if len(routes) > 1:
            print("WARNING: More than one route avaiable. Heuristic may fail.")
        target_gateway = routes[0]["gateway"]
        vrf = {
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
                            "from": netplan["network"]["ethernets"][target_nic][
                                "addresses"
                            ][0],
                        }
                    ],
                }
            }
        }

        netplan['network'].update(vrf)

        jujud_svcfile = None
        if self.model.config['debug']:
            systemd_filecollection = pathlib.Path(
                "/tmp/tests/systemd_configd"
            )
        else:
            systemd_filecollection = pathlib.Path("/etc/systemd/system")

        # TODO: This whole section should be function calls.
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
        sshd_svcfile_content = self.openfile_and_read(sshd_svcfile)
        jujud_svcfile_content = self.openfile_and_read(jujud_svcfile)

        m = re.search(
            r"ExecStart=(?P<binary>/usr/sbin/sshd {1}-D {1}\$SSHD_OPTS)",
            sshd_svcfile_content,
            re.MULTILINE,
        )
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
        if m == None:
            print(
                "WARNING: Juju not found, the script is probably running during MAAS setup, not juju setup. Exiting gracefully"
            )
            exit(0)

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

        
        self.model.status = ops.WaitingStatus("VRFs configured. Units conmfigured, run restart-units to finish configuration.")

    # TODO: Restarting automatically fails to complete, add restart-units action.
    def restart_units(self, event):
        subprocess.check_call("sudo systemctl daemon-reload".split())
        for service in ["sshd", "jujud-\*"]:
            subprocess.check_call(f"sudo systemctl restart {service}".split())

        if self.model.config['debug']:
            open("/tmp/test.netplan.yaml", "w").write(yaml.safe_dump(netplan))
        else:
            open(target_netplan, "w").write(yaml.safe_dump(netplan))
            subprocess.check_call(["sudo", "netplan", "apply"])


if __name__ == "__main__":  # pragma: nocover
    ops.main(VrfCharmCharm)
