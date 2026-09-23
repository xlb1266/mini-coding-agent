"""工具执行审批：挂起请求注册表与决议（模块二）。

开启审批的 Agent 在执行高风险工具（Shell 命令、文件写入、edit_file）前，
Runtime 发出 approval_request 事件后经本模块的注册表挂起等待：

- 聊天生成为每个审批请求创建一个 PendingApproval（含 asyncio.Future），
  事件流把请求下发给前端，审批卡片展示工具名与整理后的参数；
- 用户在聊天页批准/拒绝时，决议接口经 resolve() 完成对应 Future，
  Runtime 收到决定后继续执行或把拒绝结果回给模型；
- 等待超时由 Runtime 控制（ApprovalHooks.timeout_seconds），超时与用户
  停止生成都经 settle() 把挂起请求标记终态并清理，保证后台任务不悬挂；
- 注册表是进程内的：后端重启后运行本身会被标记中断（mark_interrupted_runs），
  挂起的审批随之消失；页面刷新通过生成快照与查询接口恢复展示。

注册表按 session_id / run_id 索引，决议前必须校验请求归属的会话。
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---- 审批决定 ----
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_TIMEOUT = "timeout"
DECISION_CANCELLED = "cancelled"

DECISIONS = (DECISION_APPROVED, DECISION_REJECTED, DECISION_TIMEOUT, DECISION_CANCELLED)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class PendingApproval:
    """一个等待用户决议的审批请求。

    arguments 是 Runtime 整理后的展示参数（已脱敏、截断），与前端
    审批卡片展示的内容一致；future 在用户决议（approved/rejected）、
    超时（timeout）或运行取消（cancelled）时完成。
    """

    request_id: str
    session_id: int
    run_id: str | None
    tool_name: str
    display_name: str
    tool_type: str
    arguments: Any
    created_at: datetime = field(default_factory=utcnow)
    # 创建发生在 Runtime 的异步任务里（事件循环内），Future 绑定当前循环
    future: asyncio.Future = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )

    def payload(self) -> dict[str, Any]:
        """给前端的审批请求载荷（驼峰字段，与 SSE 事件一致）。"""
        return {
            "requestId": self.request_id,
            "callId": "",
            "toolName": self.tool_name,
            "displayName": self.display_name,
            "toolType": self.tool_type,
            "arguments": self.arguments,
        }

    def settle(self, decision: str) -> None:
        """完成 Future（幂等）：仅第一个决定生效。"""
        if self.future.done():
            return
        self.future.set_result(decision)


class ApprovalRegistry:
    """进程内的挂起审批注册表；实例长期复用，允许并发访问（单事件循环）。"""

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}

    # ---- 挂起 ----

    def create(
        self,
        session_id: int,
        run_id: str | None,
        info: dict[str, Any],
    ) -> PendingApproval:
        """登记一个挂起审批请求并返回；info 为 Runtime 的 approval_request 事件载荷。"""
        approval = PendingApproval(
            request_id=str(info.get("requestId") or uuid.uuid4().hex),
            session_id=session_id,
            run_id=run_id,
            tool_name=str(info.get("toolName") or ""),
            display_name=str(info.get("displayName") or info.get("toolName") or ""),
            tool_type=str(info.get("toolType") or "unknown"),
            arguments=info.get("arguments"),
        )
        self._pending[approval.request_id] = approval
        return approval

    # ---- 查询 ----

    def get(self, request_id: str) -> PendingApproval | None:
        return self._pending.get(request_id)

    def pending_for_session(self, session_id: int) -> list[PendingApproval]:
        return [a for a in self._pending.values() if a.session_id == session_id]

    def pending_for_run(self, run_id: str | None) -> list[PendingApproval]:
        if run_id is None:
            return []
        return [a for a in self._pending.values() if a.run_id == run_id]

    # ---- 决议 ----

    def resolve(self, request_id: str, approved: bool) -> PendingApproval | None:
        """用户决议：完成 Future 并移出注册表；请求不存在或已处理返回 None。"""
        approval = self._pending.pop(request_id, None)
        if approval is None:
            return None
        approval.settle(DECISION_APPROVED if approved else DECISION_REJECTED)
        return approval

    def settle(self, request_id: str, decision: str) -> PendingApproval | None:
        """超时/取消的清理：标记终态并移出注册表（幂等，请求不存在时忽略）。"""
        approval = self._pending.pop(request_id, None)
        if approval is None:
            return None
        approval.settle(decision)
        logger.info(
            "审批请求已按 %s 处理 request_id=%s session_id=%s",
            decision,
            request_id,
            approval.session_id,
        )
        return approval

    def cancel_for_session(self, session_id: int) -> int:
        """把一个会话的全部挂起审批标记为取消（停止生成/收尾时调用）。"""
        cancelled = 0
        for approval in self.pending_for_session(session_id):
            self.settle(approval.request_id, DECISION_CANCELLED)
            cancelled += 1
        return cancelled

    def cancel_for_run(self, run_id: str | None) -> int:
        """把一次运行的全部挂起审批标记为取消（运行收尾时调用）。"""
        if run_id is None:
            return 0
        cancelled = 0
        for approval in self.pending_for_run(run_id):
            self.settle(approval.request_id, DECISION_CANCELLED)
            cancelled += 1
        return cancelled


# 进程内共享单例：聊天生成登记挂起请求，决议接口与收尾路径访问它
approval_registry = ApprovalRegistry()
