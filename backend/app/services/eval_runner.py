"""评测执行器：进程内顺序执行评测任务的每条用例（阶段十二）。

复用统一 AgentRuntime：每条用例以"独立上下文"发起一次运行（不继承任何
历史），通过 RunRecorder 关联一条来源为评测的 Run 记录；Agent 正常完成
后，才把问题、期望答案、评分要求与实际回答交给评分模型（eval_scorer，
走统一模型调用基础设施，不执行工具循环）。目标 Agent 失败、取消或达到
轮数限制时保留已有输出与 Run，标记执行失败并继续下一条用例。

- 允许工具关闭时，通过 RuntimeRequest 的运行级限制收窄权限：
  tool_keys 与 mcp_server_ids 置空（Runtime 只做收窄，不会额外授予），
  因此不向目标 Agent 提供任何内置 / MCP 工具定义；Skills 仍按 Agent
  配置加载。开启时使用 Agent 自身配置（已绑定且当前启用的能力）。
- 用例材料来自任务创建时的快照（结果行），编辑数据集不影响运行中任务。
- 状态与结果逐条持久化：页面刷新 / 关闭不影响执行；服务重启后由
  recover_interrupted_evaluations 把遗留任务标记为已中断（保留已有
  结果，不做自动续跑）。
- 不引入独立 Worker / 并发调度：一次一个任务，用例顺序执行。
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core.secret_box import SecretBox, SecretBoxError
from app.db.models.evaluation import (
    CASE_STATUS_EXEC_FAILED,
    CASE_STATUS_EXECUTING,
    CASE_STATUS_FAILED,
    CASE_STATUS_PASSED,
    CASE_STATUS_PENDING,
    CASE_STATUS_SCORING,
    CASE_STATUS_SCORING_FAILED,
    CASE_TERMINAL_STATUSES,
    RUN_SOURCE_EVALUATION,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_INTERRUPTED,
    TASK_STATUS_RUNNING,
    EvalTask,
    EvalTaskResult,
)
from app.db.models.agent import Agent, CAPABILITY_MCP, CAPABILITY_SKILL, CAPABILITY_TOOL
from app.db.models.model_config import ModelConfig
from app.db.models.mcp_server import McpServer
from app.db.models.run_record import AgentRun, AgentRunEvent
from app.db.models.skill import Skill
from app.db.models.tool import Tool
from app.services.agent_runtime import (
    APPROVAL_REQUEST,
    STATUS_COMPLETED,
    STATUS_STAGE_COMPLETE,
    TOOL_CALL_END,
    AgentRuntime,
    RunLimits,
    RunResult,
    RuntimeRequest,
)
from app.services.eval_scorer import (
    ScoringRequestError,
    ScoringValidationError,
    score_case,
)
from app.services.run_recorder import RunRecorder

logger = logging.getLogger(__name__)

# 服务重启 / 关闭导致任务无法继续时的统一说明
INTERRUPTED_EXEC_ERROR = "服务重启，本次用例的执行中断"
INTERRUPTED_SCORE_ERROR = "服务重启，本次用例的评分中断"


class EvaluationStartError(Exception):
    """任务无法启动（不存在 / 状态不允许），message 为可直接展示的原因。"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clip_error(text: str, limit: int = 500) -> str:
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _exec_error_text(result: RunResult) -> str:
    """把未正常完成的运行结果整理为可读的失败原因。"""
    if result.status == "cancelled":
        return "运行被取消"
    parts: list[str] = []
    if result.error_message:
        parts.append(result.error_message)
    if result.error_hint:
        parts.append(f"提示：{result.error_hint}")
    return _clip_error("；".join(parts)) if parts else "运行未正常完成"


@dataclass
class _TaskContext:
    """一次任务执行需要的固定输入（启动时一次载入）。"""

    task_id: int
    session_factory: sessionmaker
    secret_box: SecretBox
    skills_dir: Path


class EvaluationManager:
    """进程内单例：管理评测任务的执行；同一任务同时只允许一次执行。"""

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._stopped = False

    def is_running(self, task_id: int) -> bool:
        return task_id in self._tasks

    # ---- 启动 ----

    def start(
        self,
        task_id: int,
        session_factory: sessionmaker,
        secret_box: SecretBox,
        skills_dir: Path,
    ) -> None:
        """启动一个待运行任务；非 pending 状态一律拒绝（后端状态校验）。"""
        if task_id in self._tasks:
            raise EvaluationStartError("任务已在运行中，不能重复启动")
        # 正常启动时清除关闭标记（close 只在应用关闭时调用）
        self._stopped = False
        db = session_factory()
        try:
            task = db.get(EvalTask, task_id)
            if task is None:
                raise EvaluationStartError("评测任务不存在或已被删除")
            if task.status == TASK_STATUS_RUNNING:
                raise EvaluationStartError("任务已在运行中，不能重复启动")
            if task.status == TASK_STATUS_COMPLETED:
                raise EvaluationStartError(
                    "任务已完成，不能再次启动并覆盖原结果；需要重新评测请新建任务"
                )
            if task.status == TASK_STATUS_INTERRUPTED:
                raise EvaluationStartError(
                    "任务已中断且本阶段不支持续跑；需要重新评测请新建任务"
                )
            task.status = TASK_STATUS_RUNNING
            task.started_at = utcnow()
            task.updated_at = task.started_at
            db.commit()
        finally:
            db.close()
        context = _TaskContext(
            task_id=task_id,
            session_factory=session_factory,
            secret_box=secret_box,
            skills_dir=skills_dir,
        )
        self._tasks[task_id] = asyncio.create_task(self._execute(context))

    async def close(self) -> None:
        """应用关闭时请求停止全部执行中的任务并等待收尾。"""
        self._stopped = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---- 恢复 ----

    def recover_interrupted(self, session_factory: sessionmaker) -> int:
        """应用启动时调用：遗留 running 的任务标记为已中断，保留已有结果。

        停留在执行中 / 评分中的用例得到明确终态（执行失败 / 评分失败），
        其余用例保持原状态；本阶段不做自动续跑。
        """
        db = session_factory()
        recovered = 0
        try:
            rows = db.query(EvalTask).filter(EvalTask.status == TASK_STATUS_RUNNING).all()
            now = utcnow()
            for task in rows:
                task.status = TASK_STATUS_INTERRUPTED
                task.finished_at = now
                task.updated_at = now
                for result in task.results:
                    if result.status == CASE_STATUS_EXECUTING:
                        result.status = CASE_STATUS_EXEC_FAILED
                        result.exec_error = INTERRUPTED_EXEC_ERROR
                        result.updated_at = now
                    elif result.status == CASE_STATUS_SCORING:
                        result.status = CASE_STATUS_SCORING_FAILED
                        result.score_error = INTERRUPTED_SCORE_ERROR
                        result.updated_at = now
                recovered += 1
            if rows:
                db.commit()
                logger.warning("已把 %d 个遗留评测任务标记为已中断", recovered)
        except Exception:
            db.rollback()
            logger.exception("标记遗留评测任务失败")
        finally:
            db.close()
        return recovered

    # ---- 执行 ----

    async def _execute(self, context: _TaskContext) -> None:
        completed = False
        try:
            completed = await self._run_all_cases(context)
        except asyncio.CancelledError:
            logger.info("评测任务被取消（服务关闭） task_id=%s", context.task_id)
        except Exception:
            logger.exception("评测任务出现未预期异常 task_id=%s", context.task_id)
        finally:
            self._finalize_task(context, completed=completed)
            self._tasks.pop(context.task_id, None)

    def _finalize_task(self, context: _TaskContext, *, completed: bool) -> None:
        """任务收尾：正常跑完为已完成；取消 / 异常 / 中途停止为已中断（幂等）。"""
        db = context.session_factory()
        try:
            task = db.get(EvalTask, context.task_id)
            if task is None or task.status != TASK_STATUS_RUNNING:
                return
            now = utcnow()
            task.status = TASK_STATUS_COMPLETED if completed else TASK_STATUS_INTERRUPTED
            task.finished_at = now
            task.updated_at = now
            if not completed:
                # 收尾时把仍在执行 / 评分的用例落一个明确终态
                for result in task.results:
                    if result.status == CASE_STATUS_EXECUTING:
                        result.status = CASE_STATUS_EXEC_FAILED
                        result.exec_error = INTERRUPTED_EXEC_ERROR
                        result.updated_at = now
                    elif result.status == CASE_STATUS_SCORING:
                        result.status = CASE_STATUS_SCORING_FAILED
                        result.score_error = INTERRUPTED_SCORE_ERROR
                        result.updated_at = now
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("保存评测任务终态失败 task_id=%s", context.task_id)
        finally:
            db.close()

    async def _run_all_cases(self, context: _TaskContext) -> bool:
        """顺序执行全部用例；返回任务是否正常跑完（未被停止或取消）。"""
        factory = context.session_factory
        db = factory()
        try:
            task = db.get(EvalTask, context.task_id)
            if task is None:
                return False
            result_ids = [row.id for row in task.results]
            pass_score = task.pass_score
            allow_tools = task.allow_tools
            agent_id = task.agent_id
            scorer_model_id = task.scorer_model_id
        finally:
            db.close()

        # 评分模型配置与密钥一次载入；运行中模型被删除只影响后续评分
        scorer = self._load_scorer(factory, context.secret_box, scorer_model_id)

        runtime = AgentRuntime(
            session_factory=factory,
            secret_box=context.secret_box,
            skills_dir=context.skills_dir,
        )
        limits = None
        if not allow_tools:
            # 只收窄：清空内置工具与 MCP 集合；Skills 不限制（按 Agent 配置加载）
            limits = RunLimits(tool_keys=set(), mcp_server_ids=set())

        for result_id in result_ids:
            if self._stopped:
                return False
            await self._run_one_case(
                runtime, context, result_id, agent_id, pass_score, limits, scorer
            )
        return not self._stopped

    def _load_scorer(
        self, factory: sessionmaker, secret_box: SecretBox, model_id: int
    ) -> dict[str, str | None] | None:
        """加载评分模型配置；不存在或密钥无法解密时返回 None（评分全部失败）。"""
        db = factory()
        try:
            model = db.get(ModelConfig, model_id)
            if model is None:
                return None
            api_key: str | None = None
            if model.secret is not None:
                try:
                    api_key = secret_box.decrypt(model.secret.key_nonce, model.secret.key_blob)
                except SecretBoxError:
                    return None
            return {
                "base_url": model.base_url,
                "model_identifier": model.model_identifier,
                "api_key": api_key,
            }
        finally:
            db.close()

    def _decrypt_agent_api_key(
        self, factory: sessionmaker, secret_box: SecretBox, agent_id: int
    ) -> str | None:
        """目标 Agent 模型的密钥（仅用于记录脱敏；失败不影响运行本身）。"""
        db = factory()
        try:
            agent = db.get(Agent, agent_id)
            if agent is None:
                return None
            model = db.get(ModelConfig, agent.model_id)
            if model is None or model.secret is None:
                return None
            try:
                return secret_box.decrypt(model.secret.key_nonce, model.secret.key_blob)
            except SecretBoxError:
                return None
        finally:
            db.close()

    async def _run_one_case(
        self,
        runtime: AgentRuntime,
        context: _TaskContext,
        result_id: int,
        agent_id: int,
        pass_score: int,
        limits: RunLimits | None,
        scorer: dict[str, str | None] | None,
    ) -> None:
        factory = context.session_factory
        db = factory()
        try:
            result = db.get(EvalTaskResult, result_id)
            if result is None or result.status in CASE_TERMINAL_STATUSES:
                return
            result.status = CASE_STATUS_EXECUTING
            result.updated_at = utcnow()
            db.commit()
            question = result.question
            expected = result.expected_answer
            requirements = result.scoring_requirements
        finally:
            db.close()

        run_uid = uuid.uuid4().hex
        agent_snapshot = self._agent_snapshot(factory, agent_id)
        recorder = RunRecorder(
            factory,
            run_uid,
            session_id=None,
            agent_id=agent_id,
            agent_name=agent_snapshot["agent_name"],
            model_id=agent_snapshot["model_id"],
            model_name=agent_snapshot["model_name"],
            model_identifier=agent_snapshot["model_identifier"],
            user_input=question,
            api_key=self._decrypt_agent_api_key(factory, context.secret_box, agent_id),
            source=RUN_SOURCE_EVALUATION,
        )
        recorder.start()
        # run_end 事件与 Run 终态必须在同一事务落库（与聊天路径一致）：
        # 事件经 record_event 持久化，终态由 finish 统一写入
        run_end_events: list[dict] = []

        def on_runtime_event(event: dict) -> None:
            if event.get("type") == "run_end":
                run_end_events.append(event)
            recorder.record_event(event)

        try:
            run_result = await runtime.run(
                RuntimeRequest(
                    agent_id=agent_id,
                    user_input=question,
                    history=[],
                    limits=limits,
                ),
                on_event=on_runtime_event,
                run_id=run_uid,
            )
        except Exception:
            logger.exception("评测用例执行出现未预期异常 result_id=%s", result_id)
            recorder.finish(
                {
                    "type": "run_end",
                    "runId": run_uid,
                    "status": "error",
                    "reason": "runtime_error",
                    "content": "",
                    "error": {"type": "server", "message": "运行过程中出现未预期的错误", "hint": None},
                }
            )
            self._save_case_fields(
                factory,
                result_id,
                status=CASE_STATUS_EXEC_FAILED,
                run_uid=run_uid,
                run_id=self._lookup_run_id(factory, run_uid),
                exec_error="执行过程中出现未预期的错误",
            )
            return

        # 运行结束：把终态与指标写入 Run 记录（幂等）
        if run_end_events:
            recorder.finish(run_end_events[-1])

        if run_result.status not in (STATUS_COMPLETED, STATUS_STAGE_COMPLETE):
            # 失败 / 取消 / 轮数耗尽：保留已有输出与 Run，不作为正常答案评分
            self._save_case_fields(
                factory,
                result_id,
                status=CASE_STATUS_EXEC_FAILED,
                actual_answer=run_result.content or None,
                run_uid=run_uid,
                run_id=self._lookup_run_id(factory, run_uid),
                exec_reason=run_result.reason,
                exec_error=_exec_error_text(run_result),
            )
            return

        # 正常完成：记录回答与 Run，进入评分；过程摘要来自本 Run 的真实
        # 工具事件（阶段十五过程评分的评分材料）
        run_row_id = self._lookup_run_id(factory, run_uid)
        process_summary = self._process_digest(factory, run_uid)
        self._save_case_fields(
            factory,
            result_id,
            status=CASE_STATUS_SCORING,
            actual_answer=run_result.content,
            run_uid=run_uid,
            run_id=run_row_id,
            exec_reason=run_result.reason,
        )

        if scorer is None:
            self._save_case_fields(
                factory,
                result_id,
                status=CASE_STATUS_SCORING_FAILED,
                score_error="评分模型不存在、已被删除或密钥无法解密，无法评分",
            )
            return

        try:
            outcome = await score_case(
                base_url=scorer["base_url"] or "",
                model_identifier=scorer["model_identifier"] or "",
                api_key=scorer["api_key"],
                question=question,
                expected_answer=expected,
                scoring_requirements=requirements,
                actual_answer=run_result.content,
                process_summary=process_summary,
            )
        except ScoringRequestError as error:
            message = error.message + (f"（提示：{error.hint}）" if error.hint else "")
            self._save_case_fields(
                factory,
                result_id,
                status=CASE_STATUS_SCORING_FAILED,
                score_error=_clip_error(f"评分请求失败：{message}"),
            )
            return
        except ScoringValidationError as error:
            self._save_case_fields(
                factory,
                result_id,
                status=CASE_STATUS_SCORING_FAILED,
                score_error=_clip_error(f"评分结果不符合要求：{error.message}"),
                scoring_duration_ms=error.duration_ms,
            )
            return

        passed = outcome.score >= pass_score
        self._save_case_fields(
            factory,
            result_id,
            status=CASE_STATUS_PASSED if passed else CASE_STATUS_FAILED,
            score=outcome.score,
            reason=outcome.reason,
            process_score=outcome.process_score,
            process_reason=outcome.process_reason,
            scoring_duration_ms=outcome.duration_ms,
            scoring_total_tokens=outcome.total_tokens,
            scoring_attempts=outcome.attempts,
        )

    def _process_digest(self, factory: sessionmaker, run_uid: str) -> str:
        """把一次 Run 的工具事件整理成过程评分材料（摘要行，脱敏已在入库前完成）。

        只取工具调用结束事件与审批请求事件；没有工具事件时返回空串
        （评分模型收不到过程材料，按提示词给 0 分）。
        """
        db = factory()
        try:
            rows = (
                db.query(AgentRunEvent.event_type, AgentRunEvent.payload_json)
                .filter(
                    AgentRunEvent.run_uid == run_uid,
                    AgentRunEvent.event_type.in_((TOOL_CALL_END, APPROVAL_REQUEST)),
                )
                .order_by(AgentRunEvent.seq)
                .all()
            )
        finally:
            db.close()

        lines: list[str] = []
        for event_type, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            if event_type == APPROVAL_REQUEST:
                name = str(payload.get("displayName") or payload.get("toolName") or "?")
                args = payload.get("arguments")
                brief = ""
                if isinstance(args, dict):
                    first = next((v for v in args.values() if isinstance(v, str) and v.strip()), "")
                    brief = " ".join(first.split())[:80]
                lines.append(f"审批请求：{name}{'：' + brief if brief else ''}")
                continue
            name = str(payload.get("toolName") or "?")
            status = str(payload.get("status") or "?")
            duration = payload.get("durationMs")
            duration_text = f"{duration}ms" if isinstance(duration, int) else ""
            summary = " ".join(str(payload.get("summary") or "").split())[:120]
            parts = [f"工具 {name} → {status}"]
            if duration_text:
                parts.append(duration_text)
            if summary:
                parts.append(summary)
            lines.append("，".join(parts))
        return "\n".join(lines[:50])

    def _agent_snapshot(self, factory: sessionmaker, agent_id: int) -> dict:
        """目标 Agent 与模型的名称快照（运行记录用；缺失时给占位值）。"""
        db = factory()
        try:
            agent = db.get(Agent, agent_id)
            if agent is None:
                return {
                    "agent_name": "（Agent 已删除）",
                    "model_id": None,
                    "model_name": "",
                    "model_identifier": "",
                }
            model = db.get(ModelConfig, agent.model_id)
            return {
                "agent_name": agent.name,
                "model_id": model.id if model else None,
                "model_name": model.display_name if model else "",
                "model_identifier": model.model_identifier if model else "",
            }
        finally:
            db.close()

    def _lookup_run_id(self, factory: sessionmaker, run_uid: str) -> int | None:
        db = factory()
        try:
            row = db.query(AgentRun.id).filter(AgentRun.run_uid == run_uid).first()
            return row[0] if row else None
        finally:
            db.close()

    def _save_case_fields(self, factory: sessionmaker, result_id: int, **fields) -> None:
        """把一次阶段的结果写入用例行（短事务；updated_at 统一刷新）。"""
        db = factory()
        try:
            result = db.get(EvalTaskResult, result_id)
            if result is None:
                return
            for key, value in fields.items():
                setattr(result, key, value)
            result.updated_at = utcnow()
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("保存评测用例结果失败 result_id=%s", result_id)
        finally:
            db.close()


def build_agent_config_snapshot(db: Session, agent_id: int, *, allow_tools: bool) -> str:
    """任务创建时保存实际使用的 Agent 配置快照（JSON 文本）。

    工具 / Skill / MCP 集合记录"绑定且当前启用"的实际生效项；是否允许
    工具单独记录（关闭时运行时还会通过运行级限制清空工具与 MCP）。
    与调用方共用同一会话 / 事务，读取一致的配置。
    """
    agent = db.get(Agent, agent_id)
    if agent is None:
        return "{}"
    model = db.get(ModelConfig, agent.model_id)
    bound_tools = {b.ref_key for b in agent.bindings if b.capability_type == CAPABILITY_TOOL}
    bound_skills = {b.ref_key for b in agent.bindings if b.capability_type == CAPABILITY_SKILL}
    bound_mcp = {
        int(b.ref_key)
        for b in agent.bindings
        if b.capability_type == CAPABILITY_MCP and str(b.ref_key).isdigit()
    }
    tool_keys = sorted(
        row.key
        for row in db.query(Tool).filter(Tool.key.in_(bound_tools), Tool.enabled.is_(True)).all()
    ) if bound_tools else []
    skill_dirs = sorted(
        row.dir_name
        for row in db.query(Skill).filter(Skill.dir_name.in_(bound_skills), Skill.enabled.is_(True)).all()
    ) if bound_skills else []
    mcp_ids = sorted(
        row.id
        for row in db.query(McpServer).filter(McpServer.id.in_(bound_mcp), McpServer.enabled.is_(True)).all()
    ) if bound_mcp else []
    snapshot = {
        "agentId": agent.id,
        "agentName": agent.name,
        "promptVersion": agent.prompt_version,
        "systemPrompt": agent.system_prompt,
        "maxRounds": agent.max_rounds,
        "modelName": model.display_name if model else "",
        "modelIdentifier": model.model_identifier if model else "",
        "allowTools": allow_tools,
        "toolKeys": tool_keys,
        "skillDirNames": skill_dirs,
        "mcpServerIds": mcp_ids,
    }
    return json.dumps(snapshot, ensure_ascii=False)


# 进程内共享单例；应用关闭时在 main.py 的 lifespan 里统一收尾
evaluation_manager = EvaluationManager()
