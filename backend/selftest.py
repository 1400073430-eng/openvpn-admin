# -*- coding: utf-8 -*-
"""
内置自测模块
覆盖需求中的 4 项自测：
1. 域名校验与解析结果处理（不执行真实网络拦截）
2. 客户端名校验与证书模块接口（不签发或吊销真实证书）
3. openvpn-status.log 解析测试
4. JWT 鉴权测试（未登录拒绝）

运行方式:
    python -m backend.selftest
或通过 API:
    POST /api/selftest  （需登录，便于部署后验证）
"""
import logging
import os
import sys
from pathlib import Path
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "backend"

logger = logging.getLogger("ovpn.selftest")


class SelfTestResult:
    """单项自测结果。"""
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.message = ""

    def ok(self, msg: str = "通过"):
        self.passed = True
        self.message = msg
        return self

    def fail(self, msg: str):
        self.passed = False
        self.message = msg
        return self


def test_1_dns_blacklist() -> SelfTestResult:
    r = SelfTestResult("自测1-域名黑名单")
    try:
        from .security import is_valid_domain
        from .services.blacklist_service import normalize_domain, resolve_domain
        from . import config
        assert is_valid_domain("example.com")
        assert not is_valid_domain("bad_domain!")
        assert normalize_domain("Example.COM.") == "example.com"
        from unittest.mock import patch
        import socket
        with patch("socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 0, 0, 0)),
        ]):
            result = resolve_domain("example.com")
        assert result["domain"] == "example.com"
        assert isinstance(result["addresses"], list)
        assert result["addresses"] == ["192.0.2.1", "2001:db8::1"]
        msg = "域名校验与 A/AAAA 结果处理正常（模拟解析；真实 DNS 与防火墙待部署验收）"
        assert config.NFT_TABLE == "ovpn_admin"
        assert config.NFT_SET_V4 == "blocked_ipv4"
        assert config.NFT_SET_V6 == "blocked_ipv6"
        return r.ok(msg)
    except Exception as e:
        return r.fail(f"黑名单逻辑自测失败: {e}")

def test_2_vpn_client_cert() -> SelfTestResult:
    """
    自测2：新增VPN客户端证书生成、吊销证书完整流程
    （仅测试名称校验与 EasyRSA 模块可导入性，真实生成需在部署环境）
    """
    r = SelfTestResult("自测2-VPN客户端证书")
    try:
        from .security import is_valid_client_name, is_safe_path_component

        assert is_valid_client_name("client1"), "合法客户端名被拒绝"
        assert is_valid_client_name("test_user-01"), "含下划线中划线被拒绝"
        assert not is_valid_client_name("a"), "过短名称被接受"
        assert not is_valid_client_name("a" * 33), "过长名称被接受"
        assert not is_valid_client_name("has space"), "含空格被接受"
        assert not is_safe_path_component("../etc/passwd"), "路径穿越未拦截"

        # 验证 easyrsa 模块可导入且有关键函数
        from .services import easyrsa_service
        assert hasattr(easyrsa_service, "generate_client_cert")
        assert hasattr(easyrsa_service, "revoke_client_cert")

        return r.ok("名称校验与证书模块接口正常（真实生成请在部署环境执行）")

    except AssertionError as e:
        return r.fail(f"断言失败: {e}")
    except Exception as e:
        return r.fail(f"异常: {e}")


def test_3_status_parser() -> SelfTestResult:
    """
    自测3：读取 openvpn 状态文件解析测试
    使用临时模拟数据验证解析器容错能力。
    """
    r = SelfTestResult("自测3-状态文件解析")
    try:
        from .services.status_parser import parse_status_log, format_bytes

        # 场景A：文件不存在
        result = parse_status_log(Path("/nonexistent/openvpn-status.log"))
        assert result == [], "文件不存在时应返回空列表"

        # 场景B：空文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            empty_path = f.name
        try:
            result = parse_status_log(Path(empty_path))
            assert result == [], "空文件时应返回空列表"
        finally:
            os.unlink(empty_path)

        # 场景C：标准格式
        sample = """OpenVPN CLIENT LIST
Updated,2024-06-01 12:00:00
Common Name,Real Address,Bytes Received,Bytes Sent,Connected Since
client1,1.2.3.4:5678,1024,2048,2024-06-01 11:00:00
client2,5.6.7.8:9012,4096,8192,2024-06-01 11:30:00
ROUTING TABLE
Virtual Address,Common Name,Real Address,Last Ref
10.8.0.6,client1,1.2.3.4:5678,2024-06-01 12:00:00
10.8.0.10,client2,5.6.7.8:9012,2024-06-01 12:00:00
GLOBAL STATS
Max bcast/mcast queue length,0
END
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(sample)
            sample_path = f.name
        try:
            result = parse_status_log(Path(sample_path))
            assert len(result) == 2, f"应解析 2 条，实际 {len(result)}"
            assert result[0].common_name == "client1"
            assert result[0].virtual_address == "10.8.0.6"
            assert result[0].bytes_received == 1024
        finally:
            os.unlink(sample_path)

        # 场景D：OpenVPN 将无可用 CN 的会话标记为 UNDEF，不得伪装成真实客户端
        undef_v1 = """OpenVPN CLIENT LIST
Updated,2024-06-01 12:00:00
Common Name,Real Address,Bytes Received,Bytes Sent,Connected Since
UNDEF,9.8.7.6:1234,10,20,2024-06-01 11:00:00
valid-client,1.2.3.4:5678,100,200,2024-06-01 11:30:00
ROUTING TABLE
Virtual Address,Common Name,Real Address,Last Ref
10.8.0.6,valid-client,1.2.3.4:5678,2024-06-01 12:00:00
GLOBAL STATS
END
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(undef_v1)
            undef_v1_path = f.name
        try:
            result = parse_status_log(Path(undef_v1_path))
            assert [c.common_name for c in result] == ["valid-client"], f"UNDEF 不应作为客户端显示: {[c.common_name for c in result]}"
        finally:
            os.unlink(undef_v1_path)

        undef_v2 = """TITLE\tOpenVPN 2.5
TIME\t2024-06-01 12:00:00\t1717243200
HEADER\tCLIENT_LIST\tCommon Name\tReal Address\tVirtual Address\tVirtual IPv6 Address\tBytes Received\tBytes Sent\tConnected Since\tConnected Since (time_t)\tUsername\tClient ID\tPeer ID
CLIENT_LIST\tUNDEF\t9.8.7.6:1234\t10.8.0.9\t\t10\t20\t2024-06-01 11:00:00\t1717239600\tUNDEF\t1\t1
CLIENT_LIST\tvalid-client\t1.2.3.4:5678\t10.8.0.6\t\t100\t200\t2024-06-01 11:30:00\t1717241400\tvalid-client\t2\t2
END
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(undef_v2)
            undef_v2_path = f.name
        try:
            result = parse_status_log(Path(undef_v2_path))
            assert [c.common_name for c in result] == ["valid-client"], f"status v2 中 UNDEF 不应作为客户端显示: {[c.common_name for c in result]}"
        finally:
            os.unlink(undef_v2_path)

        # 场景E：格式错乱，不崩溃
        messy = """OpenVPN CLIENT LIST
Updated,garbage
Common Name,Real Address
!!!broken,,,
ROUTING TABLE
END
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(messy)
            messy_path = f.name
        try:
            result = parse_status_log(Path(messy_path))
            assert isinstance(result, list), "错乱格式应返回 list"
        finally:
            os.unlink(messy_path)

        # 流量格式化
        assert format_bytes(500) == "500 B"
        assert "KB" in format_bytes(2048)

        return r.ok("状态解析正常（不存在/空/标准/UNDEF过滤/status-v2/错乱）")

    except AssertionError as e:
        return r.fail(f"断言失败: {e}")
    except Exception as e:
        return r.fail(f"异常: {e}")






def test_4_jwt_auth() -> SelfTestResult:
    """Exercise the real JWT and missing-cookie path; no substitute implementation."""
    r = SelfTestResult("自测4-JWT鉴权")
    try:
        from .auth import create_access_token, decode_token, get_current_user
        from fastapi import HTTPException
        from types import SimpleNamespace
        token = create_access_token("selftest_user", expires_minutes=5)
        assert decode_token(token) == "selftest_user"
        assert decode_token(token[:-5] + "XXXXX") is None
        assert decode_token("") is None
        assert decode_token("not.a.jwt") is None
        coro = get_current_user(SimpleNamespace(cookies={}))
        try:
            try:
                coro.send(None)
            except HTTPException as exc:
                assert exc.status_code == 401
            else:
                return r.fail("未登录未被拒绝")
        finally:
            coro.close()
        return r.ok("真实 JWT 签名/篡改拒绝和无 Cookie 返回 401 正常；不替代会话集成测试")
    except Exception as exc:
        return r.fail("真实鉴权自测失败：" + str(exc))


def run_all_tests() -> dict:
    """运行全部自测，返回结果汇总。"""
    tests = [
        test_1_dns_blacklist(),
        test_2_vpn_client_cert(),
        test_3_status_parser(),
        test_4_jwt_auth(),
    ]
    results = []
    all_passed = True
    for t in tests:
        results.append({
            "name": t.name,
            "passed": t.passed,
            "message": t.message,
        })
        if not t.passed:
            all_passed = False
        status = "【成功】" if t.passed else "【失败】"
        print(f"{status} {t.name}: {t.message}")

    return {
        "all_passed": all_passed,
        "total": len(tests),
        "passed": sum(1 for t in tests if t.passed),
        "results": results,
    }


if __name__ == "__main__":
    # 允许直接运行: python -m backend.selftest
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    summary = run_all_tests()
    sys.exit(0 if summary["all_passed"] else 1)
