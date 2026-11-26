# -*- coding: utf-8 -*-
"""
安全的系统命令执行模块
核心原则：
1. 严禁 shell=True，一律使用参数列表
2. 统一超时、输出捕获
3. 失败返回友好错误，日志记录原始信息（不向前端暴露堆栈）
"""
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional, Sequence

logger = logging.getLogger("ovpn.cmd")


class CommandError(Exception):
    """对外友好的命令执行异常。"""
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail  # 仅写日志，不返回前端


@dataclass
class CommandResult:
    success: bool
    stdout: str
    stderr: str
    returncode: int


def run_command(
    args: Sequence[str],
    timeout: int = 60,
    cwd: Optional[str] = None,
    input_text: Optional[str] = None,
    check: bool = False,
) -> CommandResult:
    """
    以参数列表方式执行命令（绝不拼接 shell 字符串）。

    参数:
        args: 命令及参数列表，例如 ["systemctl", "restart", "openvpn-server@server"]
        timeout: 超时秒数
        cwd: 工作目录
        input_text: 标准输入文本
        check: 为 True 时失败抛 CommandError

    返回:
        CommandResult
    """
    # 防御：args 必须是列表/元组，且每项是字符串
    if not args or not isinstance(args, (list, tuple)):
        raise CommandError("命令参数无效")
    for a in args:
        if not isinstance(a, str):
            raise CommandError("命令参数类型错误")
        # 禁止 NUL / 换行 / 控制字符（防止参数注入、sudo env 注入）
        if any(ord(c) < 32 or ord(c) == 127 for c in a):
            raise CommandError("命令参数含非法字符")

    cmd_display = " ".join(args)
    logger.info("执行命令: %s", cmd_display)

    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            input=input_text,
            shell=False,  # 明确禁止 shell
            env=None,     # 继承当前环境
        )
        result = CommandResult(
            success=(proc.returncode == 0),
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            returncode=proc.returncode,
        )
        if not result.success:
            logger.warning(
                "命令失败 rc=%s cmd=%s stderr=%s",
                proc.returncode, cmd_display, (proc.stderr or "")[:500],
            )
            if check:
                raise CommandError("命令执行失败", detail=proc.stderr or "")
        return result

    except subprocess.TimeoutExpired:
        logger.error("命令超时: %s", cmd_display)
        if check:
            raise CommandError("命令执行超时")
        return CommandResult(success=False, stdout="", stderr="timeout", returncode=-1)
    except FileNotFoundError:
        logger.error("命令不存在: %s", cmd_display)
        if check:
            raise CommandError("命令不存在或未安装")
        return CommandResult(success=False, stdout="", stderr="not found", returncode=-1)
    except CommandError:
        raise
    except Exception as e:
        logger.exception("命令执行异常: %s", cmd_display)
        if check:
            raise CommandError("命令执行异常")
        return CommandResult(success=False, stdout="", stderr=str(e)[:200], returncode=-1)


def _process_has_nnp() -> bool:
    """检测本进程 NoNewPrivs（systemd/容器开启后 sudo 必失败）。"""
    try:
        with open("/proc/self/status", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("NoNewPrivs:"):
                    return line.split()[1] == "1"
    except OSError:
        return False
    return False


def _run_via_job_queue(args: Sequence[str], timeout: int = 60, input_text: Optional[str] = None) -> CommandResult:
    """
    NoNewPrivileges 环境下 sudo 会失败。
    回退：把任务写入 root 任务队列 /var/spool/ovpn-admin，
    由 ovpn-admin-jobs.service（root）调用 helper 执行。
    """
    import json
    import os
    import secrets
    import tempfile
    import time
    from pathlib import Path

    spool = Path("/var/spool/ovpn-admin")
    try:
        if not spool.is_dir():
            logger.error("任务队列目录不存在: %s（请执行 install.sh --fix-certs）", spool)
            return CommandResult(False, "", "job queue not available", returncode=-1)
    except OSError:
        logger.error("任务队列目录不可访问: %s", spool)
        return CommandResult(False, "", "job queue not available", returncode=-1)
    # Fail closed under queue pressure. This is a per-process check, so the
    # root worker also validates every job; the limit is a safety bound, not
    # a replacement for spool-directory permissions.
    try:
        pending = sum(1 for _ in spool.glob("*.job"))
    except OSError:
        pending = 10**9
    if pending >= 64:
        logger.warning("特权任务队列已达到上限，拒绝新任务")
        return CommandResult(False, "", "job queue busy; retry later", returncode=-1)

    logger.info("任务队列提交: %s", list(args)[:4])
    jid = f"{os.getpid()}-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    job = spool / f"{jid}.job"
    res = spool / f"{jid}.result"
    if input_text is not None and len(input_text) > 1024 * 1024:
        return CommandResult(False, "", "job input too large", returncode=-1)
    # 把调用方超时写入任务，root worker 按任务预算执行；
    # 避免 API 120s / worker 固定 180s 造成超时预算不一致。
    job_timeout = max(5, min(int(timeout), 240))
    payload = {"argv": list(args), "timeout": job_timeout}
    if input_text is not None:
        payload["stdin"] = input_text
    try:
        # 原子提交任务：先写隐藏临时文件，fsync 后再 rename 成 *.job。
        # path unit 可能在文件刚创建时就触发 worker；直接 write_text(job)
        # 会产生“worker 读到半截 JSON -> 500”的竞态。
        fd, tmp = tempfile.mkstemp(prefix=".job-", dir=str(spool), text=True)
        try:
            os.fchmod(fd, 0o640)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fd = -1
                f.write(json.dumps(payload, ensure_ascii=False))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, job)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
    except OSError as e:
        return CommandResult(False, "", f"job write failed: {e}", returncode=-1)

    # worker 需要极短的收尾时间（写 result + fsync），避免恰好在 deadline
    # 写回结果时 API 先判定 timeout。
    # Use monotonic time so wall-clock changes cannot extend the wait.
    deadline = time.monotonic() + max(5, timeout) + 5
    while time.monotonic() < deadline:
        try:
            done = res.exists()
        except OSError:
            done = False
        if done:
            try:
                data = json.loads(res.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            try:
                job.unlink(missing_ok=True)
                res.unlink(missing_ok=True)
            except OSError:
                pass
            return CommandResult(
                success=bool(data.get("ok")),
                stdout=str(data.get("stdout") or ""),
                stderr=str(data.get("stderr") or ""),
                returncode=int(data.get("rc", 1 if not data.get("ok") else 0)),
            )
        time.sleep(0.05)
    return CommandResult(False, "", "job timeout", returncode=-1)


def run_as_root(args: Sequence[str], timeout: int = 60, input_text: Optional[str] = None) -> CommandResult:
    """通过 root 任务队列执行极少数受控高权限动作。

    Web 进程永远不直接 sudo。即使调用方是非 root，也只可把参数提交给
    root worker；最终操作仍由 root-owned helper 的白名单再次校验。
    这样可以让 systemd 服务保持 NoNewPrivileges=true，避免给 Web 进程
    提供通用提权入口。
    """
    import os
    from .. import config

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return run_command(args, timeout=timeout, input_text=input_text)

    helper = str(config.PRIV_HELPER)
    if not os.path.isabs(helper):
        raise CommandError("root helper 路径无效")

    return _run_via_job_queue(list(args), timeout=timeout, input_text=input_text)


