# Copyright 2023 pjds
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

import ipaddress
import subprocess
import json
import yaml
import pathlib
import logging
logger = logging.getLogger(__name__)


class NetplanHandler:
    def __init__(self, path="/etc/netplan/"):
        self.netplan_configdir = pathlib.Path(path)
        self.configs = list(self.netplan_configdir.iterdir())
        self.netplan = None

    def load_netplan(self):
        """Load Netplan configuration."""
        if not self.configs:
            return None

        with self.configs[0].open("r") as file:
            return yaml.safe_load(file)

    def find_nic(self, target_cidr):
        """Find the target NIC and gateway in the Netplan configuration."""
        if not target_cidr:
            return None, None

        netplan = self.load_netplan()
        if not netplan:
            return None, None

        target_mgmt_cidr = ipaddress.IPv4Network(target_cidr)
        target_nic = None

        for interface, nic_def in netplan["network"]["ethernets"].items():
            if "addresses" in nic_def and ipaddress.IPv4Address(nic_def["addresses"][0].split("/")[0]) in target_mgmt_cidr:
                target_nic = interface
                break

        return target_nic

    def find_gateway(self):
        target_gateway = None
        routes = json.loads(subprocess.check_output(
            ["ip", "-j", "route", "show", "default"]).decode())
        if routes:
            if len(routes) > 1:
                logger.debug(
                    "WARNING: More than one route avaiable.\
                                        Heuristic may fail.")
            target_gateway = routes[0]["gateway"]

        return target_gateway

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
            logger.warn(
                "WARNING: More than one netplan. \
                Picking the first one and merging")

        target_netplan = self.configs[0]
        target_netplan.write_text(yaml.safe_dump(self.netplan))
