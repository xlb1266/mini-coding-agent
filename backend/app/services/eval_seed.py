"""示例评测数据集的幂等种子。

示例数据集通过稳定标识（key = builtin-eval-basics）保证初始化幂等：
- 只在标识不存在时插入，重复启动、重复调用都不会重复创建；
- 已存在的示例行（即使用户改过名称、说明或用例内容）不做任何更新，
  保证用户修改不被覆盖。

迁移 0010 与应用启动（lifespan）都调用 ensure_example_dataset：
迁移负责存量库升级时补种，启动函数覆盖不走迁移的库（如测试内存库）
并兜底保证最终一致。
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from app.db.models.evaluation import EXAMPLE_DATASET_KEY, EvalCase, EvalDataset

logger = logging.getLogger(__name__)

EXAMPLE_DATASET_NAME = "示例评测集：基础问答"
EXAMPLE_DATASET_DESCRIPTION = (
    "内置示例：三条基础问答用例，可用于快速体验评测流程（可编辑）。"
)


def example_case_rows() -> list[dict[str, str]]:
    """示例用例内容（问题 / 期望答案 / 评分要求），迁移与种子函数共用。"""
    return [
        {
            "question": "计算 23 乘以 47。",
            "expected_answer": "1081",
            "scoring_requirements": "计算结果正确，表达简洁。",
        },
        {
            "question": "将“今天下午三点开会”翻译成英文。",
            "expected_answer": "The meeting is at 3 p.m. today.",
            "scoring_requirements": "准确表达会议时间，允许自然且含义一致的译法。",
        },
        {
            "question": "小明有 5 个苹果，送给小红 2 个，还剩几个？只回答数字。",
            "expected_answer": "3",
            "scoring_requirements": "结果正确，并且只输出数字。",
        },
    ]


def ensure_example_dataset(session_factory: sessionmaker) -> bool:
    """按稳定标识幂等补种示例数据集；已存在时不做任何修改。

    返回是否发生了插入（便于测试与启动日志）。
    """
    db = session_factory()
    try:
        exists = (
            db.query(EvalDataset.id)
            .filter(EvalDataset.key == EXAMPLE_DATASET_KEY)
            .first()
            is not None
        )
        if exists:
            return False
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        dataset = EvalDataset(
            name=EXAMPLE_DATASET_NAME,
            description=EXAMPLE_DATASET_DESCRIPTION,
            key=EXAMPLE_DATASET_KEY,
            created_at=now,
            updated_at=now,
        )
        db.add(dataset)
        db.flush()
        for row in example_case_rows():
            db.add(
                EvalCase(
                    dataset_id=dataset.id,
                    question=row["question"],
                    expected_answer=row["expected_answer"],
                    scoring_requirements=row["scoring_requirements"],
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()
        logger.info("已补种示例评测数据集 key=%s", EXAMPLE_DATASET_KEY)
        return True
    except Exception:
        db.rollback()
        logger.exception("补种示例评测数据集失败")
        return False
    finally:
        db.close()
