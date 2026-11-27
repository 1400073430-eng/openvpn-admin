"""Restrict the entire management surface to explicit VPN source networks."""
import ipaddress
import os

from .. import config


def load_networks():
    explicit = os.environ.get('OVPM_ADMIN_VPN_CIDRS', '').strip()
    if explicit:
        networks = [ipaddress.ip_network(value.strip(), strict=True) for value in explicit.split(',')]
    else:
        networks = []
        try:
            lines = config.OPENVPN_CONFIG.read_text(encoding='utf-8').splitlines()
        except OSError:
            return ()
        for line in lines:
            parts = line.split('#', 1)[0].split(';', 1)[0].split()
            if parts and parts[0] == 'server' and len(parts) >= 3:
                networks.append(ipaddress.ip_network(parts[1] + '/' + parts[2], strict=True))
            elif parts and parts[0] == 'server-ipv6' and len(parts) >= 2:
                networks.append(ipaddress.ip_network(parts[1], strict=True))
    if any(n.prefixlen == 0 or n.is_loopback or n.is_multicast or n.is_unspecified for n in networks):
        raise ValueError('管理访问必须配置实际 VPN 网段，不得使用默认路由或回环网段')
    return tuple(networks)


NETWORKS = ()


def allowed(peer, path):
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    # Keep local deployment liveness checks, never local login or proxy bypass.
    if path == '/api/health' and address.is_loopback:
        return True
    return any(address.version == network.version and address in network for network in NETWORKS)
