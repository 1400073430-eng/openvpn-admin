"""Manual server egress lookup; no client-supplied URLs or scan targets."""
import ipaddress
import json
import subprocess
import threading
import time
from datetime import datetime, timezone

_lock = threading.Lock()
_cached = None
_cached_at = 0.0


def _fetch(url, family=4):
    result = subprocess.run(
        ['curl', '--disable', '-' + str(family), '--silent', '--show-error', '--fail',
         '--noproxy', '*', '--proto', '=https', '--connect-timeout', '3',
         '--max-time', '5', '--max-filesize', '32768', url],
        capture_output=True, timeout=7, check=True,
    )
    if len(result.stdout) > 32768:
        raise ValueError('response too large')
    return result.stdout.decode('utf-8')


def _family(version):
    result = {'version': version, 'status': 'unavailable', 'ip': None, 'metadata_status': 'not_checked'}
    try:
        host = 'api.ipify.org' if version == 4 else 'api6.ipify.org'
        address = ipaddress.ip_address(_fetch('https://' + host, version).strip())
        if address.version != version or not address.is_global:
            raise ValueError('unexpected address')
        result.update(ip=str(address), status='ok')
    except (OSError, subprocess.SubprocessError, ValueError):
        result['note'] = '查询失败或该协议出口不可用；不能据此判断 IP 信誉。'
        return result
    try:
        data = json.loads(_fetch('https://ipwho.is/' + str(address)))
        if data.get('success') is not True or ipaddress.ip_address(data.get('ip', '')) != address:
            raise ValueError('lookup failed')
        connection = data.get('connection') or {}
        result.update(metadata_status='ok', country=str(data.get('country', ''))[:100],
                      region=str(data.get('region', ''))[:100], city=str(data.get('city', ''))[:100],
                      asn=str(connection.get('asn', ''))[:30], isp=str(connection.get('isp', ''))[:200],
                      organization=str(connection.get('org', ''))[:200])
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        result['metadata_status'] = 'unavailable'
    return result


def check():
    global _cached, _cached_at
    # Single flight and five-minute cache within each Web worker.
    with _lock:
        if _cached is not None and time.monotonic() - _cached_at < 300:
            return dict(_cached, cached=True)
        result = {
            'checked_at': datetime.now(timezone.utc).isoformat(), 'cached': False,
            'results': [_family(4), _family(6)],
            'sources': ['ipify.org', 'ipwho.is'],
            'note': '仅检测服务器出口及归属信息；未检测黑名单、滥用历史、住宅/代理类型或客户端泄漏，不能证明节点干净。',
        }
        _cached = result
        _cached_at = time.monotonic()
        return result
