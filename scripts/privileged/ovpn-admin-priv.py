#!/usr/bin/env python3
import hashlib, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

# sudo 默认会清理大部分环境变量；root helper 只读取 root-owned 配置文件，
# 不接受调用方通过环境变量覆盖关键路径。
PRIV_ENV_FILE = Path("/etc/openvpn-admin/privileged.env")

def load_privileged_env():
    """Read only a small, root-owned, non-writable regular config file."""
    try:
        st = PRIV_ENV_FILE.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RuntimeError(f"cannot inspect privileged config: {exc}")
    if not __import__("stat").S_ISREG(st.st_mode) or st.st_uid != 0 or (st.st_mode & 0o022):
        raise RuntimeError("privileged config must be a root-owned regular file, not group/world-writable")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(PRIV_ENV_FILE), flags)
    try:
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino) or not __import__("stat").S_ISREG(current.st_mode):
            raise RuntimeError("privileged config changed while opening")
        if current.st_size > 16384:
            raise RuntimeError("privileged config is too large")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            for raw in stream.read(16385).splitlines():
                raw = raw.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                key, value = raw.split("=", 1)
                if re.fullmatch(r"OVPM_[A-Z0-9_]+", key):
                    os.environ[key] = value
    finally:
        if fd >= 0:
            os.close(fd)

try:
    load_privileged_env()
except Exception as exc:
    print(f"invalid privileged config: {exc}", file=sys.stderr)
    sys.exit(2)

SERVICE_USER = os.environ.get("OVPM_SERVICE_USER", "ovpn-admin")
EASYRSA_DIR = Path(os.environ.get("OVPM_EASYRSA_DIR", "/etc/openvpn/server/easy-rsa"))
EASYRSA_BIN = Path(os.environ.get("OVPM_EASYRSA_BIN", EASYRSA_DIR / "easyrsa"))
OPENVPN_DIR = Path(os.environ.get("OVPM_OPENVPN_DIR", "/etc/openvpn/server"))
OPENVPN_SERVICE = os.environ.get("OVPM_OPENVPN_SERVICE", "openvpn-server@server")
DATA_DIR = Path(os.environ.get("OVPM_DATA_DIR", "/opt/openvpn-admin/data"))
CRL_FILE = Path(os.environ.get("OVPM_CRL_FILE", OPENVPN_DIR / "crl.pem"))
CLIENT_CERT_DIR = Path(os.environ.get("OVPM_CLIENT_CERT_DIR", EASYRSA_DIR / "pki" / "inline" / "private"))
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
IP_RE = re.compile(r"^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$")

def fail(msg):
    print(msg, file=sys.stderr); return 2

def root_only():
    return 0 if os.geteuid() == 0 else fail("must run as root")

def run(argv, cwd=None, env=None, timeout=30):
    return subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)

def service_action(service, action):
    if service not in {OPENVPN_SERVICE} or action not in {"reload", "restart", "is-active"}: return fail("service/action not allowed")
    r=run(["/usr/bin/systemctl", action, service], timeout=30); sys.stdout.write(r.stdout); sys.stderr.write(r.stderr); return r.returncode

def easyrsa(argv):
    if not argv or len(argv) > 6: return fail("invalid easyrsa args")
    allowed={"build-client-full","revoke","gen-crl","show-cert"}
    # 注意：子命令本身形如 build-client-full 也匹配 NAME_RE，
    # 提取客户端名时必须排除子命令与 nopass，否则永远报 client name required。
    clean=[]; sub=None; name=None; name_count=0
    for a in argv:
        if not isinstance(a,str) or len(a)>128 or any(ord(c)<32 for c in a): return fail("invalid argument")
        if a.startswith("--"):
            if not (a == "--batch" or re.fullmatch(r"--days=\d{1,5}", a)): return fail("option not allowed")
            clean.append(a); continue
        if sub is None:
            sub=a
            if sub not in allowed: return fail("subcommand not allowed")
            clean.append(a)
            continue
        if a == "nopass" and sub == "build-client-full":
            clean.append(a); continue
        if NAME_RE.fullmatch(a):
            clean.append(a)
            name=a; name_count+=1
            continue
        return fail("argument not allowed")
    if sub in {"build-client-full","revoke","show-cert"}:
        if name_count!=1 or not name: return fail("client name required")
    env=os.environ.copy(); env["EASYRSA_BATCH"]="1"; env["EASYRSA_PKI"]=str(EASYRSA_DIR/"pki")
    r=run([str(EASYRSA_BIN),"--batch"]+[a for a in clean if a!="--batch"], cwd=str(EASYRSA_DIR), env=env, timeout=240)
    sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
    if r.returncode==0 and sub=="build-client-full" and name:
        pki=EASYRSA_DIR/"pki"
        for target in (pki/"issued"/(name+".crt"),pki/"private"/(name+".key"),pki/"inline"/"private"/(name+".inline")):
            if target.exists() and shutil.which("setfacl"):
                subprocess.run(["/usr/bin/setfacl","-m",f"u:{SERVICE_USER}:r",str(target)],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return r.returncode

def copy_client_files(name):
    """将新签发的客户端 cert/key 复制到受控的客户端副本目录。

    该目录可能位于 /etc/openvpn/server/client-configs，Web 用户不应直接
    获得写权限，因此复制动作只允许由 root helper 执行。
    """
    if not NAME_RE.fullmatch(name):
        return fail("invalid client name")

    pki = (EASYRSA_DIR / "pki").resolve()
    target_dir = CLIENT_CERT_DIR.resolve()

    # inline/private 本身就在 PKI 内，不做重复复制。
    if target_dir == pki or str(target_dir).startswith(str(pki) + os.sep):
        return 0

    # 只允许 OpenVPN 目录下的客户端副本目录，禁止通过配置/参数越界。
    openvpn_root = OPENVPN_DIR.resolve()
    if not (target_dir == openvpn_root or str(target_dir).startswith(str(openvpn_root) + os.sep)):
        return fail("client certificate directory not allowed")

    cert = pki / "issued" / f"{name}.crt"
    key = pki / "private" / f"{name}.key"
    if not cert.is_file() or not key.is_file():
        return fail("client certificate material not found")

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        # 副本目录仅服务于管理平台，root 可写、服务组可读；不开放其他用户访问。
        try:
            import grp
            gid = grp.getgrnam(SERVICE_USER).gr_gid
            os.chown(target_dir, 0, gid)
            os.chmod(target_dir, 0o750)
        except Exception:
            pass

        cert_dst = target_dir / f"{name}.crt"
        key_dst = target_dir / f"{name}.key"
        shutil.copy2(cert, cert_dst)
        shutil.copy2(key, key_dst)

        try:
            import grp
            gid = grp.getgrnam(SERVICE_USER).gr_gid
            os.chown(cert_dst, 0, gid)
            os.chown(key_dst, 0, gid)
        except Exception:
            pass
        os.chmod(cert_dst, 0o640)
        os.chmod(key_dst, 0o640)
        return 0
    except OSError as e:
        return fail(f"copy client files failed: {e}")

def remove_key(name):
    if not NAME_RE.fullmatch(name): return fail("invalid client name")
    for x in (EASYRSA_DIR/"pki"/"private"/(name+".key"), EASYRSA_DIR/"pki"/"reqs"/(name+".req")):
        try: x.unlink(missing_ok=True)
        except OSError as e: return fail(str(e))
    return 0

def read_client_file(path_str):
    """读取 Web 必需的公开/客户端材料；严格拒绝 CA/server 私钥及符号链接。"""
    if not path_str or "\x00" in path_str:
        return fail("invalid path")
    try:
        raw = Path(path_str)
        if raw.is_symlink():
            return fail("symlink not allowed")
        p = raw.resolve(strict=True)
    except (OSError, RuntimeError):
        return fail("invalid path")

    # status/log：只允许读取状态日志，不允许借 /etc/openvpn 目录读取任意文件。
    for root_s in ("/var/log/openvpn", "/run/openvpn-server", "/var/log/openvpn-server"):
        root = Path(root_s).resolve()
        if p == root or str(p).startswith(str(root) + os.sep):
            if p.suffix.lower() != ".log" or not p.is_file() or p.is_symlink():
                return fail("status file not allowed")
            try:
                sys.stdout.write(p.read_text(encoding="utf-8", errors="replace"))
                return 0
            except OSError as e:
                return fail(str(e))

    # EasyRSA：只允许 CA 证书、索引、客户端 cert/key/inline。明确禁止 ca.key。
    pki = (EASYRSA_DIR / "pki").resolve()
    if p == pki / "ca.crt" or p == pki / "index.txt" or p == pki / "serial":
        allowed_file = True
    elif p.parent == pki / "issued" and p.name.endswith(".crt") and NAME_RE.fullmatch(p.stem):
        allowed_file = True
    elif p.parent == pki / "private" and p.name.endswith(".key") and NAME_RE.fullmatch(p.stem) and p.stem != "ca":
        allowed_file = True
    elif p.parent == pki / "inline" / "private" and p.name.endswith(".inline") and NAME_RE.fullmatch(p.stem):
        allowed_file = True
    else:
        allowed_file = False
    if allowed_file:
        if not p.is_file() or p.is_symlink():
            return fail("file not found or symlink")
        try:
            sys.stdout.write(p.read_text(encoding="utf-8", errors="replace"))
            return 0
        except OSError as e:
            return fail(str(e))

    # OpenVPN 客户端生成确实需要的固定文件；禁止 server.key、dh.pem 等。
    openvpn_root = OPENVPN_DIR.resolve()
    fixed = {"ca.crt", "client-common.txt", "tc.key", "ta.key", "crl.pem"}
    if p.parent == openvpn_root and p.name in fixed and not p.is_symlink() and p.is_file():
        try:
            sys.stdout.write(p.read_text(encoding="utf-8", errors="replace"))
            return 0
        except OSError as e:
            return fail(str(e))
    return fail("path not allowed")

def copy_crl():
    src=EASYRSA_DIR/"pki"/"crl.pem"
    if not src.is_file(): return fail("CRL not found")
    CRL_FILE.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,CRL_FILE)
    try:
        import pwd, grp; u=pwd.getpwnam("nobody"); g=grp.getgrnam("nogroup"); os.chown(CRL_FILE,u.pw_uid,g.gr_gid)
    except Exception: pass
    os.chmod(CRL_FILE,0o644); return 0

def _kick_comment(name):
    if not NAME_RE.fullmatch(name):
        return None
    return "ovpn-admin-kick:" + name

def kick_unblock(ip, name):
    if not IP_RE.fullmatch(ip): return fail("invalid IPv4")
    comment = _kick_comment(name)
    if not comment: return fail("invalid client name")
    check = ["/usr/sbin/iptables","-w","5","-C","INPUT","-s",ip,"-m","comment","--comment",comment,"-j","DROP"]
    delete = ["/usr/sbin/iptables","-w","5","-D","INPUT","-s",ip,"-m","comment","--comment",comment,"-j","DROP"]
    while run(check, timeout=20).returncode == 0:
        result = run(delete, timeout=20)
        if result.returncode != 0:
            sys.stderr.write(result.stderr); return result.returncode
    return 0

def kick_block(ip, name, ttl_text):
    if not IP_RE.fullmatch(ip): return fail("invalid IPv4")
    comment = _kick_comment(name)
    if not comment: return fail("invalid client name")
    try: ttl = int(ttl_text)
    except ValueError: return fail("invalid kick TTL")
    if not 30 <= ttl <= 3600: return fail("invalid kick TTL")
    check = ["/usr/sbin/iptables","-w","5","-C","INPUT","-s",ip,"-m","comment","--comment",comment,"-j","DROP"]
    if run(check, timeout=20).returncode == 0:
        return 0
    add = ["/usr/sbin/iptables","-w","5","-I","INPUT","-s",ip,"-m","comment","--comment",comment,"-j","DROP"]
    result = run(add, timeout=20)
    if result.returncode != 0:
        sys.stderr.write(result.stderr); return result.returncode
    unit = "ovpn-admin-kick-" + hashlib.sha256((ip+"|"+name).encode()).hexdigest()[:16]
    timer = run([
        "/usr/bin/systemd-run", "--quiet", "--collect", "--unit="+unit,
        "--on-active="+str(ttl)+"s", "/usr/local/sbin/ovpn-admin-priv",
        "kick-unblock", ip, name,
    ], timeout=20)
    if timer.returncode != 0:
        run(["/usr/sbin/iptables","-w","5","-D","INPUT","-s",ip,"-m","comment","--comment",comment,"-j","DROP"], timeout=20)
        sys.stderr.write("temporary rule scheduler failed: "+timer.stderr)
        return timer.returncode or 1
    return 0

def blacklist_sync():
    sync = Path("/usr/local/sbin/ovpn-admin-blacklist-sync")
    if not sync.is_file(): return fail("blacklist sync service not installed")
    r=run([str(sync)], timeout=90)
    sys.stdout.write(r.stdout); sys.stderr.write(r.stderr); return r.returncode

def blacklist_clear():
    r=run(["/usr/sbin/nft","flush","set","inet","ovpn_admin","blocked_ipv4"], timeout=20)
    r2=run(["/usr/sbin/nft","flush","set","inet","ovpn_admin","blocked_ipv6"], timeout=20)
    if r.returncode!=0 or r2.returncode!=0:
        sys.stderr.write((r.stderr or "")+(r2.stderr or "")); return 1
    return 0

def main():
    rc=root_only()
    if rc: return rc
    if len(sys.argv)<2: return fail("command required")
    op=sys.argv[1]
    if op in {"reload-openvpn","restart-openvpn"}: return service_action(OPENVPN_SERVICE,"reload" if op=="reload-openvpn" else "restart")
    if op=="easyrsa": return easyrsa(sys.argv[2:])
    if op=="remove-client-key" and len(sys.argv)==3: return remove_key(sys.argv[2])
    if op=="copy-client-files" and len(sys.argv)==3: return copy_client_files(sys.argv[2])
    if op=="read-client-file" and len(sys.argv)==3: return read_client_file(sys.argv[2])
    if op=="copy-crl" and len(sys.argv)==2: return copy_crl()
    if op=="kick-block" and len(sys.argv)==5: return kick_block(sys.argv[2],sys.argv[3],sys.argv[4])
    if op=="kick-unblock" and len(sys.argv)==4: return kick_unblock(sys.argv[2],sys.argv[3])
    if op=="blacklist-sync" and len(sys.argv)==2: return blacklist_sync()
    if op=="blacklist-clear" and len(sys.argv)==2: return blacklist_clear()
    return fail("operation not allowed")

if __name__=="__main__": sys.exit(main())

