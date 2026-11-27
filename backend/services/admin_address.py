"""Print tunnel management URLs without importing application secrets or networking."""
import argparse
import ipaddress
from pathlib import Path


def tunnel_addresses(text):
    result = []
    for line in text.splitlines():
        parts = line.split('#', 1)[0].split(';', 1)[0].split()
        if not parts:
            continue
        if parts[0] == 'server' and len(parts) >= 3:
            network = ipaddress.ip_network(parts[1] + '/' + parts[2], strict=True)
            result.append(str(network.network_address + 1))
        elif parts[0] == 'server-ipv6' and len(parts) >= 2:
            network = ipaddress.ip_network(parts[1], strict=True)
            result.append(str(network.network_address + 1))
        elif parts[0] in ('ifconfig', 'ifconfig-ipv6') and len(parts) >= 2:
            result.append(str(ipaddress.ip_interface(parts[1]).ip))
    return list(dict.fromkeys(result))


def summary(env_path):
    values = {}
    for line in Path(env_path).read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    paths = [values.get('OVPM_OVPN_CONFIG', ''), '/etc/openvpn/server/server.conf', '/etc/openvpn/server.conf']
    config_path = next((Path(p) for p in paths if p and Path(p).is_file()), None)
    if not config_path:
        raise ValueError('无法读取 VPN 配置，请通过 SSH 核对服务器隧道地址；请勿使用公网管理地址')
    addresses = tunnel_addresses(config_path.read_text(encoding='utf-8'))
    if not addresses:
        raise ValueError('自定义 VPN 拓扑：请通过 SSH 核对服务器隧道地址；不回退到公网 IP')
    secure = values.get('OVPM_HTTPS_ENABLED') == '1'
    port = int(values.get('OVPM_HTTPS_PORT', '443') if secure else values.get('OVPM_WEB_PORT', '80'))
    if not 1 <= port <= 65535:
        raise ValueError('管理端口配置无效')
    urls = []
    for address in addresses:
        host = '[' + address + ']' if ':' in address else address
        urls.append(('https' if secure else 'http') + '://' + host + ':' + str(port))
    return urls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env', required=True)
    args = parser.parse_args()
    try:
        for url in summary(args.env):
            print('   ' + url)
        print('   地址根据 VPN 配置推导；如为自定义拓扑，请核对隧道接口实际地址。')
    except (OSError, ValueError) as error:
        print('   无法生成管理地址：' + str(error))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
