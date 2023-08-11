# VrfCharmCharm

VrfCharmCharm is a Juju charm that sets up Virtual Routing and Forwarding (VRF) on Linux-based systems. It allows you to create separate routing tables, providing isolation between network interfaces and services.

## Usage
```
charmcraft pack
juju deploy ./vrf-charm_ubuntu-22.04-amd64_ubuntu-20.04-amd64.charm
```

Configuration

The charm supports the following configuration options:
* vrf_name: The name of the VRF to create.
* target_cidr: The CIDR of the target network to associate with the VRF.
* systemd_units: A comma-separated list of systemd service units to be affected by the VRF.

Actions

The charm provides the following action:

* restart-units: This action restarts the specified systemd units affected by the VRF.

To use the action, run:

```
juju run-action vrf-charm/0 restart-units
```

## Other resources
- [Read more](https://example.com)

- [Contributing](CONTRIBUTING.md) <!-- or link to other contribution documentation -->

- See the [Juju SDK documentation](https://juju.is/docs/sdk) for more information about developing and improving charms.
