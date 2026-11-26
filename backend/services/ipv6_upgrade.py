#!/usr/bin/env python3
"""Standalone, root-executed dual-stack NAT66 migration. Preview is read-only."""
import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

SUBNET = 'fd76:706e:6164::/64'
START, END = '# BEGIN OVPM DUALSTACK', '# END OVPM DUALSTACK'
STATE = Path('/var/lib/ovpn-admin-dualstack')
SYSCTL = Path('/etc/sysctl.d/90-ovpn-admin-dualstack.conf')
NFTFILE = Path('/etc/openvpn-admin/dualstack.nft')
UNIT = Path('/etc/systemd/system/ovpn-admin-dualstack.service')


def run(args, timeout=10, check=True):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(p.stderr.strip()[:300] or 'command failed: ' + args[0])
    return p


def probe():
    errors = []
    addresses, routes = [], []
    for args, target in [(['ip', '-j', '-6', 'addr', 'show', 'scope', 'global'], addresses),
                         (['ip', '-j', '-6', 'route', 'show', 'default'], routes)]:
        try:
            value = json.loads(run(args).stdout)
            if not isinstance(value, list):
                raise ValueError('invalid ip output')
            target.extend(value)
        except Exception as e:
            errors.append(str(e)[:200])
    public = []
    for item in addresses:
        for addr in item.get('addr_info', []):
            try:
                ip = ipaddress.IPv6Address(addr['local'])
                if ip.is_global and not any(addr.get(k) for k in ('tentative', 'dadfailed', 'deprecated')):
                    public.append({'interface': item['ifname'], 'address': str(ip)})
            except (ValueError, KeyError):
                continue
    exit_ip = None
    try:
        # HTTPS only; no caller-supplied URL, no redirects or proxy inheritance.
        p = run(['curl', '--noproxy', '*', '-6', '--fail', '--silent', '--show-error',
                 '--connect-timeout', '4', '--max-time', '8', 'https://api64.ipify.org'])
        ip = ipaddress.IPv6Address(p.stdout.strip())
        if ip.is_global:
            exit_ip = str(ip)
    except Exception as e:
        errors.append('IPv6 HTTPS 出网检测未通过：' + str(e)[:160])
    return {'addresses': public, 'default_routes': routes, 'egress_ipv6': exit_ip,
            'ready': bool(public and routes and exit_ip and not errors), 'errors': errors,
            'provider_allocation': 'unknown',
            'provider_note': '系统只能检测已配置的 IPv6。是否可分配、是否收费或需开通，请到 VPS 服务商控制台确认。'}


def render_config(text):
    if (START in text) != (END in text) or text.count(START) > 1 or text.count(END) > 1:
        raise ValueError('managed configuration markers invalid')
    if START in text:
        text = text.split(START)[0] + text.split(END, 1)[1].lstrip('\n')
    for line in text.splitlines():
        clean = line.strip()
        if clean.startswith(('#', ';')):
            continue
        if re.match(r'(server-ipv6|ifconfig-ipv6|route-ipv6|block-ipv6)\b', clean) or (
            clean.startswith('push ') and any(x in clean for x in ('ipv6', 'block-ipv6'))):
            raise ValueError('检测到已有 IPv6 配置，请人工合并，拒绝覆盖')
        if re.match(r'(config|client-config-dir)\s', clean):
            raise ValueError('存在额外配置或客户端专用配置，请人工检查后迁移')
    return text.rstrip() + '\n\n' + START + '\nserver-ipv6 ' + SUBNET + '\npush "redirect-gateway def1 ipv6 bypass-dhcp"\n' + END + '\n'


def nft_rules(interface):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,32}', interface):
        raise ValueError('invalid interface name')
    # The blacklist's -100 priority drops blocked destinations before this chain.
    return '''table inet ovpn_dualstack {
 chain forward {
  type filter hook forward priority -50; policy accept;
  ip6 saddr SUBNET oifname "IFACE" counter accept
  ip6 daddr SUBNET ct state established,related counter accept
 }
 chain postrouting {
  type nat hook postrouting priority srcnat; policy accept;
  ip6 saddr SUBNET oifname "IFACE" counter masquerade
 }
}
'''.replace('SUBNET', SUBNET).replace('IFACE', interface)


def atomic_write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        if path.exists():
            st = path.stat()
            os.chown(name, st.st_uid, st.st_gid)
            shutil.copystat(path, name)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def find_config():
    for path, service in [('/etc/openvpn/server/server.conf', 'openvpn-server@server'),
                          ('/etc/openvpn/server.conf', 'openvpn@server')]:
        if Path(path).is_file() and run(['systemctl', 'is-active', '--quiet', service], check=False).returncode == 0:
            return Path(path), service
    raise RuntimeError('未找到受支持的运行中 OpenVPN 实例')


def rollback():
    manifest = json.loads((STATE / 'manifest.json').read_text())
    if manifest.get('status') == 'rolled_back':
        raise RuntimeError('已回滚，拒绝重复恢复旧配置')
    run(['systemctl', 'disable', '--now', 'ovpn-admin-dualstack.service'], check=False)
    run(['nft', 'delete', 'table', 'inet', 'ovpn_dualstack'], check=False)
    for item in manifest['files']:
        path = Path(item['path'])
        if item['existed']:
            shutil.copy2(STATE / item['backup'], path)
        else:
            path.unlink(missing_ok=True)
    run(['sysctl', '-w', manifest['ra_key'] + '=' + manifest['ra']])
    run(['sysctl', '-w', 'net.ipv4.ip_forward=' + manifest['v4']])
    run(['sysctl', '-w', 'net.ipv6.conf.all.forwarding=' + manifest['v6']])
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'restart', manifest['service']], timeout=35)
    manifest['status'] = 'rolled_back'
    atomic_write(STATE / 'manifest.json', json.dumps(manifest))
    print('已恢复备份及转发设置；请重新连接客户端。')


def apply():
    if (STATE / 'manifest.json').exists():
        raise RuntimeError('已有迁移备份，拒绝重复覆盖；请先检查原备份和状态')
    conf, service = find_config()
    if conf.is_symlink():
        raise RuntimeError('拒绝修改符号链接配置')
    status = probe()
    if not status['ready']:
        raise RuntimeError('服务器 IPv6 检测未通过，未修改任何网络配置')
    route = json.loads(run(['ip', '-j', '-6', 'route', 'get', '2606:4700:4700::1111']).stdout)[0]
    iface = route.get('dev', '')
    if not any(x['interface'] == iface for x in status['addresses']):
        raise RuntimeError('IPv6 出口与公网接口不一致，需人工检查')
    for svc in ('firewalld', 'ufw'):
        if run(['systemctl', 'is-active', '--quiet', svc], check=False).returncode == 0:
            raise RuntimeError('检测到 ' + svc + '，请人工合并防火墙，未修改配置')
    if run(['nft', 'list', 'table', 'inet', 'ovpn_dualstack'], check=False).returncode == 0:
        raise RuntimeError('专用 nftables 表已存在，拒绝覆盖')
    # Reject other IPv6 forwarding filters; accept in our chain cannot override them.
    ruleset = json.loads(run(['nft', '-j', 'list', 'ruleset']).stdout)
    for obj in ruleset.get('nftables', []):
        chain = obj.get('chain', {})
        if chain.get('hook') == 'forward' and chain.get('family') in ('inet', 'ip6') and chain.get('table') != 'ovpn_admin':
            raise RuntimeError('存在其他 IPv6 转发链，需人工合并后配置')
    if any(p.exists() or p.is_symlink() for p in (SYSCTL, NFTFILE, UNIT)):
        raise RuntimeError('专用配置文件已存在，拒绝覆盖')
    proposed = render_config(conf.read_text())
    rules = nft_rules(iface)
    check = subprocess.run(['nft', '--check', '-f', '-'], input=rules, text=True, capture_output=True, timeout=10)
    if check.returncode:
        raise RuntimeError('nftables 预检查失败：' + check.stderr[:200])
    # Avoid losing an RA-derived default route when forwarding is enabled.
    ra_key = 'net.ipv6.conf.' + iface + '.accept_ra'
    original_ra = run(['sysctl', '-n', ra_key]).stdout.strip()
    manifest = {'service': service, 'status': 'prepared', 'files': [],
                'v4': run(['sysctl', '-n', 'net.ipv4.ip_forward']).stdout.strip(),
                'v6': run(['sysctl', '-n', 'net.ipv6.conf.all.forwarding']).stdout.strip(),
                'ra_key': ra_key, 'ra': original_ra}
    for i, path in enumerate((conf, SYSCTL, NFTFILE, UNIT)):
        if path.is_symlink():
            raise RuntimeError('拒绝覆盖符号链接：' + str(path))
        item = {'path': str(path), 'existed': path.exists(), 'backup': str(i) + '.bak'}
        if path.exists():
            shutil.copy2(path, STATE / item['backup'])
        manifest['files'].append(item)
    atomic_write(STATE / 'manifest.json', json.dumps(manifest))
    try:
        atomic_write(conf, proposed)
        atomic_write(SYSCTL, 'net.ipv4.ip_forward=1\n' + ra_key + '=2\nnet.ipv6.conf.all.forwarding=1\n')
        atomic_write(NFTFILE, rules)
        atomic_write(UNIT, '''[Unit]
Description=OpenVPN Admin dual-stack NAT66
After=network-online.target nftables.service
Wants=network-online.target
Before=SERVICE.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/openvpn-admin/dualstack.nft
ExecStop=-/usr/sbin/nft delete table inet ovpn_dualstack
[Install]
WantedBy=multi-user.target
'''.replace('SERVICE', service), 0o644)
        run(['sysctl', '-p', str(SYSCTL)])
        if not probe()['ready']:
            raise RuntimeError('启用转发后 IPv6 出网验证失败')
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable', '--now', 'ovpn-admin-dualstack.service'])
        run(['systemctl', 'restart', service], timeout=35)
        run(['systemctl', 'is-active', '--quiet', service])
        # Refresh existing blacklist so server-ipv6 produces the matching rule.
        sync = Path('/usr/local/sbin/ovpn-admin-blacklist-sync')
        if sync.exists():
            run([str(sync)], timeout=100)
        manifest['status'] = 'applied'
        atomic_write(STATE / 'manifest.json', json.dumps(manifest))
    except Exception:
        rollback()
        raise
    print('双栈配置已应用，客户端需重新连接；仍需从客户端验证 IPv6 出口和黑名单。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true')
    group.add_argument('--rollback', action='store_true')
    parser.add_argument('--confirm-disconnect', action='store_true')
    args = parser.parse_args()
    if not args.apply and not args.rollback:
        print(json.dumps(probe(), ensure_ascii=False, indent=2))
        return
    if os.geteuid() != 0:
        parser.error('应用和回滚必须由 root 在 SSH 中执行')
    if not args.confirm_disconnect:
        parser.error('操作会重启 VPN 并断开客户端；确认后添加 --confirm-disconnect')
    if STATE.is_symlink():
        raise RuntimeError('state directory cannot be a symlink')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = STATE.stat()
    if st.st_uid != 0 or st.st_mode & 0o077:
        raise RuntimeError('state directory must be root-owned mode 0700')
    with (STATE / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.rollback:
            rollback()
        else:
            apply()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit(str(exc))
