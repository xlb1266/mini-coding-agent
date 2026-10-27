"""Shell 后台任务管理（阶段十四）：进程注册表、日志落盘、整树终止与退出通知。

设计原则（对齐 ZCode Bash 工具的实测方案）：
- 不让模型干等：background 动作 spawn 后立即返回，绝不等待退出；
- 不丢信息：输出全程落盘，随时可用文件工具翻看；
- 不杀一半：终止一律走整树击杀（Windows taskkill /T /F，POSIX 进程组）；
- 让模型知道发生了什么：任务退出时登记通知，Runtime 在下一轮模型调用前注入。

后台任务跨轮次存活（第一轮启动服务、下一轮验收/停止）；每个任务在工作区的
jobs 目录留一份登记 JSON，后端重启后 list/stop 仍能按登记文件尽力回收孤儿进程。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.services.tools.base import decode_output

# 通知队列上限：防止长期不消费时无限增长
_MAX_PENDING_NOTIFICATIONS = 50


def child_env() -> dict[str, str]:
    """子进程环境变量：继承本进程并强制 Python 系子进程输出 UTF-8 且不缓冲，
    避免中文 Windows 下 GBK 乱码，也让日志文件实时可见（否则 print 会憋在
    子进程的缓冲区里，转后台后读不到任何进展）。"""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def kill_tree(pid: int) -> None:
    """整树击杀：连「手底下雇的人」（孙进程）一起杀，不留占端口的垃圾进程。

    Windows 没有 POSIX 进程组，用 taskkill /T（进程树）/F（强制），
    taskkill 不可用时兜底直接杀；POSIX 侧进程在 spawn 时已用
    start_new_session 成为组长，负 pid 即整组命中。
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/pid", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        # 宽限期后仍存活再强制击杀
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                break
            time.sleep(0.1)
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def process_alive(pid: int) -> bool:
    """存活探测（尽力而为）：探测不出按死亡处理。"""
    if sys.platform == "win32":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return f'"{pid}"' in completed.stdout.decode("utf-8", errors="replace")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 进程存在但不属于当前用户
    except OSError:
        return False
    return True


class JobLogFile:
    """后台日志文件的可选写入口：attach 之前是空操作（前台命令不落盘）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handle = None

    def attach(self, path: Path) -> None:
        with self._lock:
            if self._handle is None:
                self._handle = open(path, "ab")

    def write(self, chunk: bytes, is_stderr: bool) -> None:
        with self._lock:
            if self._handle is not None:
                if is_stderr:
                    self._handle.write(b"[stderr] ")
                self._handle.write(chunk)
                self._handle.flush()

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None


class OutputCollector:
    """单条输出流的内存缓冲：保头（~1.5MB）+ 保尾（~512KB）。

    内联返回与全量底稿都从这里取；超出缓冲的部分只累计总量计数，
    属于尽力而为的底稿（正常命令远达不到该量级）。
    """

    _HEAD_CAP = 1536 * 1024
    _TAIL_CAP = 512 * 1024

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._head = bytearray()
        self._tail = bytearray()
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            self.total += len(chunk)
            if len(self._head) < self._HEAD_CAP:
                self._head.extend(chunk[: self._HEAD_CAP - len(self._head)])
            if len(chunk) >= self._TAIL_CAP:
                self._tail[:] = chunk[-self._TAIL_CAP :]
            else:
                self._tail.extend(chunk)
                overflow = len(self._tail) - self._TAIL_CAP
                if overflow > 0:
                    del self._tail[:overflow]

    def snapshot_text(self) -> str:
        """头尾拼接的解码文本：总量未超头部缓冲时即为全量。"""
        with self._lock:
            head = bytes(self._head)
            tail = bytes(self._tail)
            total = self.total
        if total <= len(head):
            return decode_output(head)
        omitted = total - len(head) - len(tail)
        return (
            decode_output(head)
            + f"\n…[中间省略 {omitted} 字节]…\n"
            + decode_output(tail)
        )

    def snapshot_bytes(self) -> bytes:
        return self.snapshot_text().encode("utf-8", errors="replace")


def drain_stream(stream, collector: OutputCollector, logfile: JobLogFile, is_stderr: bool) -> None:
    """持续读取子进程输出流：进内存缓冲 + 交给日志文件（未 attach 时忽略）。

    用 read1（当前有多少读多少）而不是 read(8192)——后者在缓冲流上会
    等满指定字节数，长驻命令的零星输出会被堵在管道里。
    """
    try:
        read_chunk = getattr(stream, "read1", stream.read)
        while True:
            chunk = read_chunk(8192)
            if not chunk:
                break
            collector.feed(chunk)
            logfile.write(chunk, is_stderr)
    finally:
        stream.close()


@dataclass
class ShellJob:
    """一个后台任务：进程引用、日志/登记文件位置与退出状态。"""

    task_id: str
    command: str
    pid: int
    workspace_root: str
    log_path: Path
    meta_path: Path
    process: subprocess.Popen | None  # None = 后端重启后从登记文件恢复的孤儿
    started_at: float
    exit_code: int | None = None
    notified: bool = False

    @property
    def status(self) -> str:
        if self.exit_code is not None:
            return "exited"
        if self.process is not None:
            return "running"
        return "unknown"  # 孤儿任务，进程状态探测不出

    def to_payload(self) -> dict:
        return {
            "taskId": self.task_id,
            "command": self.command,
            "pid": self.pid,
            "status": self.status,
            "exitCode": self.exit_code,
            "startedAt": int(self.started_at),
            "outputFile": str(self.log_path),
        }


class ShellJobRegistry:
    """进程内后台任务注册表（handler 跑在线程池，因此用线程锁）。

    职责：启动并登记任务、监视退出并登记通知、整树终止、合并登记文件
    列出任务（含后端重启后恢复的孤儿）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, ShellJob] = {}
        self._notifications: list[dict] = []

    # ---- 目录与登记文件 ----

    def jobs_dir(self, workspace_root: Path) -> Path:
        directory = workspace_root / settings.tool_shell_job_dir
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _write_meta(self, job: ShellJob) -> None:
        meta = {
            "taskId": job.task_id,
            "command": job.command,
            "pid": job.pid,
            "startedAt": int(job.started_at),
            "workspaceRoot": job.workspace_root,
        }
        job.meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def _remove_meta(self, job: ShellJob) -> None:
        try:
            job.meta_path.unlink(missing_ok=True)
        except OSError:
            pass

    # ---- 启动 / 恢复 ----

    def start(self, command: str, workspace_root: Path) -> ShellJob:
        """后台启动命令：spawn 后立即返回，绝不等待退出；输出全程落盘。"""
        directory = self.jobs_dir(workspace_root)
        task_id = uuid.uuid4().hex[:12]
        log_path = directory / f"{task_id}.log"
        meta_path = directory / f"{task_id}.json"

        log_handle = open(log_path, "ab")
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=workspace_root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=child_env(),
                start_new_session=(sys.platform != "win32"),  # 新进程组，树杀要用
            )
        finally:
            log_handle.close()  # 句柄已交给子进程，父进程侧立即释放

        job = ShellJob(
            task_id=task_id,
            command=command,
            pid=process.pid,
            workspace_root=str(workspace_root),
            log_path=log_path,
            meta_path=meta_path,
            process=process,
            started_at=time.time(),
        )
        self._write_meta(job)
        with self._lock:
            self._jobs[task_id] = job
        threading.Thread(target=self._watch, args=(job,), daemon=True).start()
        return job

    def adopt(
        self,
        process: subprocess.Popen,
        command: str,
        workspace_root: Path,
        logfile: JobLogFile,
        stdout_collector: OutputCollector,
        stderr_collector: OutputCollector,
    ) -> ShellJob:
        """前台转后台（保险丝）：接管已启动但没在预算内结束的进程。

        输出由调用方的收集线程实时进内存缓冲与日志文件；这里 attach 日志
        文件、补写已缓冲部分，然后登记任务并开始监视退出。
        """
        directory = self.jobs_dir(workspace_root)
        task_id = uuid.uuid4().hex[:12]
        log_path = directory / f"{task_id}.log"
        logfile.attach(log_path)
        buffered_out = stdout_collector.snapshot_bytes()
        if buffered_out:
            logfile.write(buffered_out, is_stderr=False)
        buffered_err = stderr_collector.snapshot_bytes()
        if buffered_err:
            logfile.write(b"--- buffered --- " + buffered_err, is_stderr=True)
        job = ShellJob(
            task_id=task_id,
            command=command,
            pid=process.pid,
            workspace_root=str(workspace_root),
            log_path=log_path,
            meta_path=directory / f"{task_id}.json",
            process=process,
            started_at=time.time(),
        )
        self._write_meta(job)
        with self._lock:
            self._jobs[task_id] = job
        threading.Thread(target=self._watch, args=(job,), daemon=True).start()
        return job

    def _watch(self, job: ShellJob) -> None:
        """后台任务的退出监视：进程退出后登记通知，供 Runtime 注入。"""
        assert job.process is not None
        self._finish_watch(job, job.process.wait())

    def _finish_watch(self, job: ShellJob, exit_code: int | None) -> None:
        with self._lock:
            job.exit_code = exit_code
        self._remove_meta(job)
        self.push_notification(
            {
                "taskId": job.task_id,
                "command": job.command,
                "exitCode": exit_code,
                "outputFile": str(job.log_path),
                "workspaceRoot": job.workspace_root,
            }
        )

    # ---- 停止 / 列表 ----

    def stop(self, task_id: str, workspace_root: Path) -> dict:
        """整树终止一个任务；后端重启过的孤儿按登记文件里的 PID 尽力回收。"""
        job = self._jobs.get(task_id)
        if job is None:
            job = self._load_orphan(task_id, workspace_root)
        if job is None:
            return {"taskId": task_id, "status": "not_found"}

        already_exited = job.status == "exited"
        if job.status == "running":
            kill_tree(job.pid)

        # 有进程引用时等监视线程收尾，尽力拿到退出码
        if job.process is not None:
            try:
                job.process.wait(timeout=5)
                with self._lock:
                    if job.exit_code is None:
                        job.exit_code = job.process.returncode
            except subprocess.TimeoutExpired:
                pass
        self._remove_meta(job)
        with self._lock:
            self._jobs.pop(task_id, None)
        return {
            "taskId": task_id,
            "command": job.command,
            "status": "exited" if already_exited else "stopped",
            "exitCode": job.exit_code,
            "outputFile": str(job.log_path),
        }

    def list(self, workspace_root: Path) -> list[dict]:
        """合并内存注册表与登记文件；孤儿任务做一次尽力存活探测。"""
        directory = self.jobs_dir(workspace_root)
        root_text = str(workspace_root)
        payloads: dict[str, dict] = {}
        with self._lock:
            for job in self._jobs.values():
                if job.workspace_root == root_text:
                    payloads[job.task_id] = job.to_payload()
        for meta_path in sorted(directory.glob("*.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            task_id = str(meta.get("taskId") or "")
            if not task_id or task_id in payloads:
                continue
            pid = meta.get("pid")
            payloads[task_id] = {
                "taskId": task_id,
                "command": str(meta.get("command") or ""),
                "pid": int(pid) if isinstance(pid, int) else None,
                "status": "running" if isinstance(pid, int) and process_alive(pid) else "dead",
                "exitCode": None,
                "startedAt": meta.get("startedAt"),
                "outputFile": str(meta_path.with_suffix(".log")),
            }
        return sorted(payloads.values(), key=lambda item: str(item.get("startedAt") or 0))

    def _load_orphan(self, task_id: str, workspace_root: Path) -> ShellJob | None:
        """后端重启后按登记文件恢复任务（进程引用为 None，只能按 PID 尽力操作）。"""
        meta_path = self.jobs_dir(workspace_root) / f"{task_id}.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        pid = meta.get("pid")
        if not isinstance(pid, int):
            return None
        return ShellJob(
            task_id=task_id,
            command=str(meta.get("command") or ""),
            pid=pid,
            workspace_root=str(workspace_root),
            log_path=meta_path.with_suffix(".log"),
            meta_path=meta_path,
            process=None,
            started_at=float(meta.get("startedAt") or 0),
        )

    # ---- 退出通知 ----

    def push_notification(self, notification: dict) -> None:
        with self._lock:
            self._notifications.append(notification)
            if len(self._notifications) > _MAX_PENDING_NOTIFICATIONS:
                self._notifications = self._notifications[-_MAX_PENDING_NOTIFICATIONS:]

    def drain_notifications(self, workspace_root: Path) -> list[dict]:
        """取走本工作区的待发通知（Runtime 每轮模型调用前注入，注入即清空）。"""
        root_text = str(workspace_root)
        with self._lock:
            kept: list[dict] = []
            taken: list[dict] = []
            for notification in self._notifications:
                if notification.get("workspaceRoot") == root_text:
                    taken.append(notification)
                else:
                    kept.append(notification)
            self._notifications = kept
        return taken

    # ---- 前台超限输出的全量底稿 ----

    def save_output_snapshot(self, workspace_root: Path, text: str) -> str:
        """截断发生时把全量输出落盘，返回路径；模型可用文件工具翻看底稿。"""
        directory = self.jobs_dir(workspace_root)
        path = directory / f"out-{uuid.uuid4().hex[:12]}.log"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def read_tail(self, log_path: Path, max_lines: int) -> str:
        """读日志末尾 N 行：seek 到文件末尾前 256KB 再解码，不整读大文件。"""
        try:
            size = log_path.stat().st_size
        except OSError:
            return ""
        window = 256 * 1024
        with open(log_path, "rb") as handle:
            handle.seek(max(size - window, 0))
            raw = handle.read()
        text = decode_output(raw)
        lines = text.splitlines()
        if size > window and lines:
            lines = lines[1:]  # 窗口起点大概率截断了一行，丢弃防乱码
        return "\n".join(lines[-max_lines:])


# 进程内单例：handler 与 Runtime 共用
SHELL_JOBS = ShellJobRegistry()
