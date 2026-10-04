"""Shell 工程能力测试（阶段十四）：后台任务、整树击杀、保险丝与通知。

后台命令用真实的 python 子进程验证生命周期；时序断言统一走 wait_for
轮询并留足余量，避免慢机上 flaky。
"""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.db.models.tool import Tool
from app.services.agent_runtime import _ContextPlan, _ToolEntry, AgentRuntime, _builtin_summary
from app.services.tools.base import ERROR_BLOCKED, ToolContext
from app.services.tools.executor import execute_tool
from app.services.tools.registry import get_definition
from app.services.tools.shell_jobs import SHELL_JOBS, ShellJobRegistry


def make_tool(key: str) -> Tool:
    definition = get_definition(key)
    return Tool(key=key, name=definition.name, description="d", parameters_json="[]", enabled=True)


def run_in_workspace(key: str, params: dict, workspace: Path):
    return execute_tool(make_tool(key), params, ToolContext(workspace_root=workspace))


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def read_log(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


@pytest.fixture()
def runtime(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core.secret_box import SecretBox
    from app.db.base import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    try:
        yield AgentRuntime(
            session_factory=sessionmaker(bind=engine),
            secret_box=SecretBox(tmp_path / "key"),
            skills_dir=tmp_path / "skills",
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 毛病 4：整树击杀
# ---------------------------------------------------------------------------


class TestTreeKill:
    def test_grandchild_killed_on_timeout(self, workspace: Path) -> None:
        """超时后孙进程（手底下雇的人）也必须死：心跳文件停止增长。"""
        workspace.mkdir(parents=True, exist_ok=True)
        heartbeat = workspace / "heartbeat.txt"
        grand = workspace / "grand.py"
        grand.write_text(
            "import time, pathlib\n"
            f"hb = pathlib.Path(r'{heartbeat}')\n"
            "for _ in range(600):\n"
            "    hb.write_text(str(time.time()))\n"
            "    time.sleep(0.1)\n",
            encoding="utf-8",
        )
        spawner = workspace / "spawner.py"
        spawner.write_text(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, r'{grand}'])\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        result = run_in_workspace("builtin_shell", {"command": "python spawner.py", "timeout": 2}, workspace)

        assert result.ok is True
        assert result.data["timedOut"] is True

        # 树杀生效后心跳停止：间隔两次读取内容不再变化
        assert wait_for(lambda: heartbeat.exists())
        time.sleep(1.0)
        first = heartbeat.read_text(encoding="utf-8")
        time.sleep(1.2)
        second = heartbeat.read_text(encoding="utf-8")
        assert first == second


# ---------------------------------------------------------------------------
# 毛病 1/3：显式超时、UTF-8、尾部读取
# ---------------------------------------------------------------------------


class TestForegroundQuality:
    def test_utf8_output(self, workspace: Path) -> None:
        result = run_in_workspace(
            "builtin_shell", {"command": "python -c \"print('中文测试')\""}, workspace
        )

        assert result.ok is True
        assert "中文测试" in result.data["stdout"]

    def test_tail_lines_reads_end_of_file(self, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "log.txt").write_text(
            "\n".join(str(i) for i in range(1, 301)), encoding="utf-8"
        )
        result = run_in_workspace(
            "builtin_file", {"action": "read", "path": "log.txt", "tail_lines": 5}, workspace
        )

        assert result.ok is True
        assert result.data["content"].splitlines() == ["296", "297", "298", "299", "300"]
        assert result.data["truncatedStart"] is False

    def test_tail_lines_out_of_range_rejected(self, workspace: Path) -> None:
        for bad in (0, 501):
            result = run_in_workspace(
                "builtin_file", {"action": "read", "path": "log.txt", "tail_lines": bad}, workspace
            )
            assert result.ok is False
            assert result.error_type == "invalid_params"


# ---------------------------------------------------------------------------
# 毛病 2：后台任务生命周期
# ---------------------------------------------------------------------------


class TestBackgroundJobs:
    def test_background_lifecycle(self, workspace: Path) -> None:
        started = run_in_workspace(
            "builtin_shell",
            {"action": "background", "command": "python -c \"print('hello-job'); import time; time.sleep(30)\""},
            workspace,
        )

        assert started.ok is True
        data = started.data
        assert data["status"] == "running"
        assert data["taskId"]
        log = Path(data["outputFile"])
        assert log.exists()
        assert log.parent == workspace / settings.tool_shell_job_dir

        # 用文件工具读日志尾部（模型查日志的真实路径）
        assert wait_for(lambda: "hello-job" in read_log(log))
        read = run_in_workspace(
            "builtin_file", {"action": "read", "path": str(log), "tail_lines": 10}, workspace
        )
        assert "hello-job" in read.data["content"]

        # list 可见，stop 后移除
        listing = run_in_workspace("builtin_shell", {"action": "list"}, workspace)
        assert any(job["taskId"] == data["taskId"] for job in listing.data["jobs"])

        stopped = run_in_workspace(
            "builtin_shell", {"action": "stop", "taskId": data["taskId"]}, workspace
        )
        assert stopped.ok is True
        assert stopped.data["status"] == "stopped"

        listing_after = run_in_workspace("builtin_shell", {"action": "list"}, workspace)
        assert listing_after.data["jobs"] == []

    def test_stop_missing_task(self, workspace: Path) -> None:
        result = run_in_workspace(
            "builtin_shell", {"action": "stop", "taskId": "nonexistent00"}, workspace
        )

        assert result.ok is True
        assert result.data["status"] == "not_found"

    def test_blocked_background_command(self, workspace: Path) -> None:
        result = run_in_workspace(
            "builtin_shell", {"action": "background", "command": "rm -rf /"}, workspace
        )

        assert result.ok is False
        assert result.error_type == ERROR_BLOCKED

    def test_action_validation(self, workspace: Path) -> None:
        bad_action = run_in_workspace("builtin_shell", {"action": "pause"}, workspace)
        assert bad_action.error_type == "invalid_params"

        stop_without_id = run_in_workspace("builtin_shell", {"action": "stop"}, workspace)
        assert stop_without_id.error_type == "invalid_params"

    def test_orphan_meta_recovered(self, workspace: Path) -> None:
        """后端重启后按登记文件找回任务：存活探测 + 尽力停止。"""
        jobs_dir = workspace / settings.tool_shell_job_dir
        jobs_dir.mkdir(parents=True)
        dead_pid = 999999  # 测试机器上几乎不可能存在的 PID
        meta = {
            "taskId": "orphan1234ab",
            "command": "echo orphan",
            "pid": dead_pid,
            "startedAt": int(time.time()),
            "workspaceRoot": str(workspace),
        }
        (jobs_dir / "orphan1234ab.json").write_text(json.dumps(meta), encoding="utf-8")

        listing = run_in_workspace("builtin_shell", {"action": "list"}, workspace)
        entry = next(job for job in listing.data["jobs"] if job["taskId"] == "orphan1234ab")
        assert entry["status"] == "dead"

        stopped = run_in_workspace(
            "builtin_shell", {"action": "stop", "taskId": "orphan1234ab"}, workspace
        )
        assert stopped.data["status"] == "stopped"


# ---------------------------------------------------------------------------
# 毛病 2：前台阻塞预算保险丝
# ---------------------------------------------------------------------------


class TestFuse:
    def test_long_command_auto_backgrounded(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "tool_shell_blocking_budget_seconds", 1)
        result = run_in_workspace(
            "builtin_shell",
            {"command": "python -c \"print('fuse-start'); import time; time.sleep(20)\"", "timeout": 60},
            workspace,
        )

        data = result.data
        assert data["autoBackgrounded"] is True
        assert data["status"] == "running"
        assert "未被终止" in data["message"]
        log = Path(data["outputFile"])
        assert wait_for(lambda: log.exists() and "fuse-start" in read_log(log))

        stopped = run_in_workspace("builtin_shell", {"action": "stop", "taskId": data["taskId"]}, workspace)
        assert stopped.data["status"] == "stopped"

    def test_wait_natured_command_not_backgrounded(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ping 这类本应等待的命令不转后台，等满超时后整树终止。"""
        monkeypatch.setattr(settings, "tool_shell_blocking_budget_seconds", 1)
        result = run_in_workspace(
            "builtin_shell", {"command": "ping -n 30 127.0.0.1", "timeout": 2}, workspace
        )

        assert result.ok is True
        assert result.data.get("autoBackgrounded") is not True
        assert result.data["timedOut"] is True


# ---------------------------------------------------------------------------
# 退出通知
# ---------------------------------------------------------------------------


class TestNotifications:
    def test_exit_notification_registered(self, workspace: Path) -> None:
        started = run_in_workspace(
            "builtin_shell", {"action": "background", "command": "python -c \"print('bye')\""}, workspace
        )
        task_id = started.data["taskId"]

        taken: list[dict] = []

        def poll() -> bool:
            taken.extend(SHELL_JOBS.drain_notifications(workspace))
            return bool(taken)

        assert wait_for(poll, timeout=15.0)
        notification = taken[0]
        assert notification["taskId"] == task_id
        assert notification["exitCode"] == 0

    def test_notifications_filtered_by_workspace(self, workspace: Path, tmp_path: Path) -> None:
        other = tmp_path / "other-workspace"
        other.mkdir()
        SHELL_JOBS.push_notification({"taskId": "t1", "workspaceRoot": str(other), "exitCode": 0})

        assert SHELL_JOBS.drain_notifications(workspace) == []
        taken = SHELL_JOBS.drain_notifications(other)
        assert len(taken) == 1

    def test_runtime_injects_notifications_into_plan(self, runtime, workspace: Path) -> None:
        SHELL_JOBS.push_notification(
            {
                "taskId": "job-abc",
                "command": "npm run dev",
                "exitCode": 1,
                "outputFile": "log",
                "workspaceRoot": str(workspace),
            }
        )
        state = SimpleNamespace(run_id="run-1")
        config = SimpleNamespace(tool_context=ToolContext(workspace_root=workspace))
        plan = _ContextPlan(system_message=None, summary_message=None)

        runtime._inject_job_notifications(state, config, plan)

        assert len(plan.run_messages) == 1
        message = plan.run_messages[0]
        assert message["role"] == "system"
        assert "【后台任务通知】" in message["content"]
        assert "job-abc" in message["content"]
        # 注入即清空：再次注入不应重复
        plan2 = _ContextPlan(system_message=None, summary_message=None)
        runtime._inject_job_notifications(state, config, plan2)
        assert plan2.run_messages == []


# ---------------------------------------------------------------------------
# 审批规则与展示摘要
# ---------------------------------------------------------------------------


def shell_entry(ref_key: str) -> _ToolEntry:
    return _ToolEntry(name=ref_key, tool_type="builtin", display_name=ref_key, descriptor={}, ref_key=ref_key)


class TestApprovalRules:
    def test_shell_approval_by_action(self, runtime) -> None:
        requires = runtime.tool_requires_approval
        assert requires(shell_entry("builtin_shell"), {"action": "run"}) is True
        assert requires(shell_entry("builtin_shell"), {}) is True  # 缺省视为 run
        assert requires(shell_entry("builtin_shell"), {"action": "background"}) is True
        assert requires(shell_entry("builtin_shell"), {"action": "stop"}) is True
        assert requires(shell_entry("builtin_shell"), {"action": "list"}) is False

    def test_other_tools_unchanged(self, runtime) -> None:
        requires = runtime.tool_requires_approval
        assert requires(shell_entry("builtin_file"), {"action": "write"}) is True
        assert requires(shell_entry("builtin_file"), {"action": "read"}) is False
        assert requires(shell_entry("builtin_edit_file"), {}) is True
        assert requires(shell_entry("builtin_list_dir"), {}) is False


class TestSummary:
    def test_shell_summaries_by_action(self) -> None:
        background = _builtin_summary(
            "builtin_shell", {"action": "background", "command": "npm run dev", "taskId": "t1"}
        )
        assert "后台启动" in background and "t1" in background

        auto = _builtin_summary(
            "builtin_shell", {"action": "run", "autoBackgrounded": True, "command": "npm run dev", "taskId": "t2"}
        )
        assert "已自动转后台" in auto

        stop = _builtin_summary("builtin_shell", {"action": "stop", "taskId": "t1", "status": "stopped"})
        assert "停止" in stop and "stopped" in stop

        listing = _builtin_summary("builtin_shell", {"action": "list", "totalJobs": 2})
        assert "共 2 个" in listing

    def test_foreground_summaries(self) -> None:
        timeout = _builtin_summary("builtin_shell", {"action": "run", "timedOut": True, "command": "sleep"})
        assert "超时" in timeout

        normal = _builtin_summary("builtin_shell", {"action": "run", "command": "git status", "exitCode": 0, "stdout": "ok"})
        assert "git status" in normal and "退出码 0" in normal
