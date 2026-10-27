"""Agent 评测路由：数据集与用例管理、任务创建 / 启动 / 详情（阶段十二）。

约定：
- 用例校验：问题必填；期望答案与评分要求至少填写一项；
- 数据集删除保护：被评测任务引用的数据集不能删除（任务与结果快照
  依赖数据集行的存在；任务历史本身不回查数据集内容）；
- 任务创建时把用例内容与 Agent 配置快照进任务（结果行），后续编辑
  数据集不影响已有任务；空数据集不能创建任务；
- 启动任务只允许 pending 状态（后端状态校验，重复启动返回 409）；
  已完成 / 已中断的任务要重新评测请新建任务；
- 任务执行在进程内后台顺序进行，执行中页面刷新不影响。
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routes.skills import get_skills_dir
from app.core.secret_box import get_secret_box
from app.db.models.agent import Agent
from app.db.models.evaluation import (
    CASE_STATUS_EXEC_FAILED,
    CASE_STATUS_EXECUTING,
    CASE_STATUS_FAILED,
    CASE_STATUS_PASSED,
    CASE_STATUS_PENDING,
    CASE_STATUS_SCORING,
    CASE_STATUS_SCORING_FAILED,
    CASE_TERMINAL_STATUSES,
    EXAMPLE_DATASET_KEY,
    TASK_STATUS_PENDING,
    EvalCase,
    EvalDataset,
    EvalTask,
    EvalTaskResult,
)
from app.db.models.model_config import ModelConfig
from app.db.session import get_db, get_session_factory
from app.schemas.evaluation import (
    CaseResultOut,
    CompareCaseOut,
    DatasetCreateRequest,
    DatasetDetailOut,
    DatasetOut,
    DatasetUpdateRequest,
    EvalCaseCreateRequest,
    EvalCaseOut,
    EvalCaseUpdateRequest,
    TaskCompareOut,
    TaskCreateRequest,
    TaskDetailOut,
    TaskOut,
    TaskStatsOut,
)
from app.services.eval_runner import (
    EvaluationStartError,
    build_agent_config_snapshot,
    evaluation_manager,
)

router = APIRouter(prefix="/evaluations", tags=["evaluations"])

NOT_FOUND_STATUS = 404
CONFLICT_STATUS = 409
FIELD_ERROR_STATUS = 422

# 用例状态 → 展示分组（统计口径）
_STATS_KEYS = {
    CASE_STATUS_PENDING: "pending_cases",
    CASE_STATUS_EXECUTING: "executing_cases",
    CASE_STATUS_SCORING: "scoring_cases",
    CASE_STATUS_PASSED: "passed_count",
    CASE_STATUS_FAILED: "failed_count",
    CASE_STATUS_EXEC_FAILED: "exec_failed_count",
    CASE_STATUS_SCORING_FAILED: "scoring_failed_count",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _field_error(errors: dict[str, str], message: str = "表单校验未通过，请检查填写内容") -> HTTPException:
    return HTTPException(status_code=FIELD_ERROR_STATUS, detail={"message": message, "fields": errors})


# ---------------------------------------------------------------------------
# 数据集与用例
# ---------------------------------------------------------------------------


def _dataset_out(db: Session, dataset: EvalDataset) -> DatasetOut:
    case_count = db.query(EvalCase.id).filter(EvalCase.dataset_id == dataset.id).count()
    return DatasetOut(
        id=dataset.id,
        name=dataset.name,
        description=dataset.description,
        case_count=case_count,
        is_example=dataset.key == EXAMPLE_DATASET_KEY,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


def _case_out(row: EvalCase) -> EvalCaseOut:
    return EvalCaseOut(
        id=row.id,
        dataset_id=row.dataset_id,
        question=row.question,
        expected_answer=row.expected_answer,
        scoring_requirements=row.scoring_requirements,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_dataset_or_404(db: Session, dataset_id: int) -> EvalDataset:
    dataset = db.get(EvalDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=NOT_FOUND_STATUS, detail="数据集不存在或已被删除")
    return dataset


def _validate_case_payload(payload: EvalCaseCreateRequest | EvalCaseUpdateRequest) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not payload.question.strip():
        errors["question"] = "请填写用户问题"
    if not payload.expected_answer.strip() and not payload.scoring_requirements.strip():
        errors["expectedAnswer"] = "期望答案与评分要求至少填写一项"
    return errors


@router.get("/datasets")
def list_datasets(db: Session = Depends(get_db)) -> list[DatasetOut]:
    datasets = db.query(EvalDataset).order_by(EvalDataset.id).all()
    return [_dataset_out(db, dataset) for dataset in datasets]


@router.post("/datasets", status_code=201)
def create_dataset(payload: DatasetCreateRequest, db: Session = Depends(get_db)) -> DatasetOut:
    if not payload.name.strip():
        raise _field_error({"name": "请填写名称"})
    now = _utcnow()
    dataset = EvalDataset(
        name=payload.name.strip(),
        description=payload.description.strip(),
        key=None,
        created_at=now,
        updated_at=now,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return _dataset_out(db, dataset)


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: int, db: Session = Depends(get_db)) -> DatasetDetailOut:
    dataset = _get_dataset_or_404(db, dataset_id)
    base = _dataset_out(db, dataset)
    cases = db.query(EvalCase).filter(EvalCase.dataset_id == dataset_id).order_by(EvalCase.id).all()
    return DatasetDetailOut(**base.model_dump(), cases=[_case_out(row) for row in cases])


@router.put("/datasets/{dataset_id}")
def update_dataset(
    dataset_id: int, payload: DatasetUpdateRequest, db: Session = Depends(get_db)
) -> DatasetOut:
    dataset = _get_dataset_or_404(db, dataset_id)
    if not payload.name.strip():
        raise _field_error({"name": "请填写名称"})
    dataset.name = payload.name.strip()
    dataset.description = payload.description.strip()
    dataset.updated_at = _utcnow()
    db.commit()
    db.refresh(dataset)
    return _dataset_out(db, dataset)


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: int, db: Session = Depends(get_db)) -> None:
    dataset = _get_dataset_or_404(db, dataset_id)
    referenced = db.query(EvalTask.id).filter(EvalTask.dataset_id == dataset_id).first() is not None
    if referenced:
        raise HTTPException(
            status_code=CONFLICT_STATUS,
            detail="该数据集已被评测任务引用，无法删除；如需停止使用可先清空用例",
        )
    db.delete(dataset)
    db.commit()


@router.post("/datasets/{dataset_id}/cases", status_code=201)
def create_case(
    dataset_id: int, payload: EvalCaseCreateRequest, db: Session = Depends(get_db)
) -> EvalCaseOut:
    dataset = _get_dataset_or_404(db, dataset_id)
    errors = _validate_case_payload(payload)
    if errors:
        raise _field_error(errors)
    now = _utcnow()
    row = EvalCase(
        dataset_id=dataset.id,
        question=payload.question.strip(),
        expected_answer=payload.expected_answer.strip(),
        scoring_requirements=payload.scoring_requirements.strip(),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    dataset.updated_at = now
    db.commit()
    db.refresh(row)
    return _case_out(row)


def _get_case_or_404(db: Session, dataset_id: int, case_id: int) -> EvalCase:
    row = db.get(EvalCase, case_id)
    if row is None or row.dataset_id != dataset_id:
        raise HTTPException(status_code=NOT_FOUND_STATUS, detail="用例不存在或已被删除")
    return row


@router.put("/datasets/{dataset_id}/cases/{case_id}")
def update_case(
    dataset_id: int,
    case_id: int,
    payload: EvalCaseUpdateRequest,
    db: Session = Depends(get_db),
) -> EvalCaseOut:
    row = _get_case_or_404(db, dataset_id, case_id)
    errors = _validate_case_payload(payload)
    if errors:
        raise _field_error(errors)
    row.question = payload.question.strip()
    row.expected_answer = payload.expected_answer.strip()
    row.scoring_requirements = payload.scoring_requirements.strip()
    row.updated_at = _utcnow()
    dataset = db.get(EvalDataset, dataset_id)
    if dataset is not None:
        dataset.updated_at = row.updated_at
    db.commit()
    db.refresh(row)
    return _case_out(row)


@router.delete("/datasets/{dataset_id}/cases/{case_id}", status_code=204)
def delete_case(dataset_id: int, case_id: int, db: Session = Depends(get_db)) -> None:
    row = _get_case_or_404(db, dataset_id, case_id)
    db.delete(row)
    dataset = db.get(EvalDataset, dataset_id)
    if dataset is not None:
        dataset.updated_at = _utcnow()
    db.commit()


# ---------------------------------------------------------------------------
# 评测任务
# ---------------------------------------------------------------------------


def _stats_out(results: list[EvalTaskResult]) -> TaskStatsOut:
    counts = {key: 0 for key in _STATS_KEYS.values()}
    scores: list[float] = []
    process_scores: list[float] = []
    for row in results:
        key = _STATS_KEYS.get(row.status)
        if key is not None:
            counts[key] += 1
        if row.score is not None:
            scores.append(row.score)
        if row.process_score is not None:
            process_scores.append(row.process_score)
    return TaskStatsOut(
        total_cases=len(results),
        completed_cases=sum(1 for row in results if row.status in CASE_TERMINAL_STATUSES),
        average_score=(sum(scores) / len(scores)) if scores else None,
        average_process_score=(sum(process_scores) / len(process_scores)) if process_scores else None,
        **counts,
    )


def _task_out(task: EvalTask, results: list[EvalTaskResult]) -> TaskOut:
    return TaskOut(
        id=task.id,
        dataset_id=task.dataset_id,
        dataset_name=task.dataset_name,
        agent_id=task.agent_id,
        agent_name=task.agent_name,
        scorer_model_id=task.scorer_model_id,
        scorer_model_name=task.scorer_model_name,
        scorer_model_identifier=task.scorer_model_identifier,
        pass_score=task.pass_score,
        allow_tools=task.allow_tools,
        status=task.status,
        stats=_stats_out(results),
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
    )


def _result_out(row: EvalTaskResult) -> CaseResultOut:
    return CaseResultOut(
        id=row.id,
        case_order=row.case_order,
        question=row.question,
        expected_answer=row.expected_answer,
        scoring_requirements=row.scoring_requirements,
        status=row.status,
        actual_answer=row.actual_answer,
        score=row.score,
        reason=row.reason,
        process_score=row.process_score,
        process_reason=row.process_reason,
        run_id=row.run_id,
        run_uid=row.run_uid,
        exec_reason=row.exec_reason,
        exec_error=row.exec_error,
        score_error=row.score_error,
        scoring_duration_ms=row.scoring_duration_ms,
        scoring_total_tokens=row.scoring_total_tokens,
        scoring_attempts=row.scoring_attempts,
    )


def _get_task_or_404(db: Session, task_id: int) -> EvalTask:
    task = db.get(EvalTask, task_id)
    if task is None:
        raise HTTPException(status_code=NOT_FOUND_STATUS, detail="评测任务不存在或已被删除")
    return task


@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db)) -> list[TaskOut]:
    tasks = db.query(EvalTask).order_by(EvalTask.id.desc()).all()
    results = db.query(EvalTaskResult).order_by(EvalTaskResult.case_order).all()
    grouped: dict[int, list[EvalTaskResult]] = {}
    for row in results:
        grouped.setdefault(row.task_id, []).append(row)
    return [_task_out(task, grouped.get(task.id, [])) for task in tasks]


@router.post("/tasks", status_code=201)
def create_task(payload: TaskCreateRequest, db: Session = Depends(get_db)) -> TaskOut:
    errors: dict[str, str] = {}
    agent = db.get(Agent, payload.agent_id)
    if agent is None:
        errors["agentId"] = "所选 Agent 不存在或已被删除"
    dataset = db.get(EvalDataset, payload.dataset_id)
    if dataset is None:
        errors["datasetId"] = "所选数据集不存在或已被删除"
    elif not dataset.cases:
        errors["datasetId"] = "数据集没有测试用例，不能启动评测"
    scorer_model = db.get(ModelConfig, payload.scorer_model_id)
    if scorer_model is None:
        errors["scorerModelId"] = "所选评分模型不存在或已被删除"
    if errors:
        raise _field_error(errors)

    now = _utcnow()
    task = EvalTask(
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        agent_id=agent.id,
        agent_name=agent.name,
        scorer_model_id=scorer_model.id,
        scorer_model_name=scorer_model.display_name,
        scorer_model_identifier=scorer_model.model_identifier,
        pass_score=payload.pass_score,
        allow_tools=payload.allow_tools,
        # Agent 配置快照与任务在同一会话 / 事务中生成，保证数据一致
        agent_config_json=build_agent_config_snapshot(
            db, agent.id, allow_tools=payload.allow_tools
        ),
        status=TASK_STATUS_PENDING,
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.flush()
    for order, case in enumerate(dataset.cases, start=1):
        db.add(
            EvalTaskResult(
                task_id=task.id,
                case_id=case.id,
                case_order=order,
                question=case.question,
                expected_answer=case.expected_answer,
                scoring_requirements=case.scoring_requirements,
                status=CASE_STATUS_PENDING,
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()
    db.refresh(task)
    results = (
        db.query(EvalTaskResult)
        .filter(EvalTaskResult.task_id == task.id)
        .order_by(EvalTaskResult.case_order)
        .all()
    )
    return _task_out(task, results)


@router.get("/tasks/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskDetailOut:
    task = _get_task_or_404(db, task_id)
    results = (
        db.query(EvalTaskResult)
        .filter(EvalTaskResult.task_id == task_id)
        .order_by(EvalTaskResult.case_order)
        .all()
    )
    base = _task_out(task, results)
    try:
        agent_config = json.loads(task.agent_config_json or "{}")
        if not isinstance(agent_config, dict):
            agent_config = {}
    except json.JSONDecodeError:
        agent_config = {}
    return TaskDetailOut(
        **base.model_dump(),
        agent_config=agent_config,
        results=[_result_out(row) for row in results],
    )


@router.post("/tasks/{task_id}/start")
async def start_task(
    task_id: int,
    db: Session = Depends(get_db),
    session_factory=Depends(get_session_factory),
    secret_box=Depends(get_secret_box),
    skills_dir=Depends(get_skills_dir),
) -> TaskOut:
    """启动待运行任务；重复启动、已完成、已中断都返回 409。"""
    task = _get_task_or_404(db, task_id)
    _ = task  # 状态校验在 manager.start 内的同一事务完成
    try:
        evaluation_manager.start(task_id, session_factory, secret_box, skills_dir)
    except EvaluationStartError as error:
        raise HTTPException(status_code=CONFLICT_STATUS, detail=str(error)) from None
    results = (
        db.query(EvalTaskResult)
        .filter(EvalTaskResult.task_id == task_id)
        .order_by(EvalTaskResult.case_order)
        .all()
    )
    task = _get_task_or_404(db, task_id)
    return _task_out(task, results)


@router.get("/tasks/{base_task_id}/compare/{target_task_id}")
def compare_tasks(
    base_task_id: int, target_task_id: int, db: Session = Depends(get_db)
) -> TaskCompareOut:
    """回归对比（阶段十五）：对同一数据集的两个任务按用例顺序对照得分。

    base 为旧版本（V1），target 为新版本（V2）；差值 = target - base。
    通过率口径：通过用例数 / 总用例数（任一侧总用例数为 0 时不计算差值）。
    """
    base_task = _get_task_or_404(db, base_task_id)
    target_task = _get_task_or_404(db, target_task_id)
    if base_task.dataset_id != target_task.dataset_id:
        raise HTTPException(
            status_code=FIELD_ERROR_STATUS,
            detail="两个任务来自不同的数据集，无法按用例对比；请选择同一数据集的任务",
        )
    base_results = (
        db.query(EvalTaskResult)
        .filter(EvalTaskResult.task_id == base_task.id)
        .order_by(EvalTaskResult.case_order)
        .all()
    )
    target_results = (
        db.query(EvalTaskResult)
        .filter(EvalTaskResult.task_id == target_task.id)
        .order_by(EvalTaskResult.case_order)
        .all()
    )
    base_stats = _stats_out(base_results)
    target_stats = _stats_out(target_results)
    target_by_order = {row.case_order: row for row in target_results}

    cases: list[CompareCaseOut] = []
    for base_row in base_results:
        target_row = target_by_order.get(base_row.case_order)
        cases.append(
            CompareCaseOut(
                case_order=base_row.case_order,
                question=base_row.question,
                base_status=base_row.status,
                target_status=target_row.status if target_row else "（缺少）",
                base_score=base_row.score,
                target_score=target_row.score if target_row else None,
                base_process_score=base_row.process_score,
                target_process_score=target_row.process_score if target_row else None,
            )
        )
    # target 多出的用例（数据集后来加过用例）也列出来，分数一侧为空
    for order, target_row in sorted(target_by_order.items()):
        if all(case.case_order != order for case in cases):
            cases.append(
                CompareCaseOut(
                    case_order=order,
                    question=target_row.question,
                    base_status="（缺少）",
                    target_status=target_row.status,
                    target_score=target_row.score,
                    target_process_score=target_row.process_score,
                )
            )
    cases.sort(key=lambda case: case.case_order)

    def _delta(base: float | None, target: float | None) -> float | None:
        if base is None or target is None:
            return None
        return round(target - base, 1)

    base_pass_rate = (
        base_stats.passed_count / base_stats.total_cases if base_stats.total_cases else None
    )
    target_pass_rate = (
        target_stats.passed_count / target_stats.total_cases if target_stats.total_cases else None
    )
    return TaskCompareOut(
        base_task_id=base_task.id,
        target_task_id=target_task.id,
        base_stats=base_stats,
        target_stats=target_stats,
        average_score_delta=_delta(base_stats.average_score, target_stats.average_score),
        average_process_score_delta=_delta(
            base_stats.average_process_score, target_stats.average_process_score
        ),
        pass_rate_delta=_delta(
            round(base_pass_rate * 100, 1) if base_pass_rate is not None else None,
            round(target_pass_rate * 100, 1) if target_pass_rate is not None else None,
        ),
        cases=cases,
    )
