# Copyright 2023 pjds
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing


import unittest
from ops import ErrorStatus
from ops.testing import Harness
from ops.model import BlockedStatus
from unittest.mock import patch, MagicMock
from charm import VrfCharm


class TestVrfCharmCharm(unittest.TestCase):

    def setUp(self):
        self.harness = Harness(VrfCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_config_changed_invalid_log_level(self):
        config = {"log-level": "invalid"}
        self.harness.update_config(config)
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(
            self.harness.charm.unit.status,
            ErrorStatus(
                "log-level not valid: set log-level to: ['info', 'debug', 'warning', 'error', 'critical']"),
        )

    def test_config_changed_without_target_cidr(self):
        config = {"log-level": "info", "vrf_name": "test_vrf"}
        self.harness.update_config(config)
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(
            self.harness.charm.unit.status,
            BlockedStatus("Setup a target CIDR."),
        )

    @patch("charm.NetplanHandler", autospec=True)
    def test_config_changed_with_valid_config(self, mock_netplan_handler_cls):
        mock_netplan_handler_instance = MagicMock()
        mock_netplan_handler_cls.return_value = mock_netplan_handler_instance
        mock_netplan_handler_instance.find_nic.return_value = "eth0"

        config = {"log-level": "info", "vrf_name": "test_vrf",
                  "target_cidr": "192.168.1.0/24"}

        mock_netplan_handler_instance.load_netplan.return_value = {
            "network": {
                "ethernets": {
                    "eth0": {
                        "addresses": ["192.168.1.2/24"],
                        "gateway4": "192.168.1.1",
                    }
                },
                "version": 2
            }
        }

        self.harness.charm.netplan_handler = mock_netplan_handler_instance
        self.harness.update_config(config)
        self.harness.charm.on.config_changed.emit()

        expected_netplan = {
            "network": {
                "ethernets": {
                    "eth0": {
                        "addresses": ["192.168.1.2/24"],
                        "gateway4": "192.168.1.1",
                    }
                },
                "version": 2
            }
        }
        self.assertEqual(
            self.harness.charm.netplan_handler.netplan, expected_netplan)


if __name__ == "__main__":
    unittest.main()
