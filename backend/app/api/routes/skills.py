"""Skills 管理路由：扫描列表、详情、ZIP 导入、编辑、启停与删除。

列表每次请求都重新扫描 workspace/skills 目录：用户手动创建或删除的
Skill 无需重启服务即可反映出来（页面上的刷新按钮即重新拉取本接口）。
编辑只写回 SKILL.md，目录名是稳定标识，不随名称修改而变化。
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.skill import Skill
from app.db.session import get_db
from app.schemas.skill import (
    SkillDetailOut,
    SkillEnabledRequest,
    SkillOut,
    SkillUpdateRequest,
)
from app.services.agent_refs import agent_names_using_skill
from app.services.skill_store import (
    DESCRIPTION_MAX_LENGTH,
    INSTRUCTIONS_MAX_LENGTH,
    NAME_MAX_LENGTH,
    SkillExistsError,
    SkillImportError,
    find_skill_file,
    import_skill_zip,
    read_skill_meta,
    sync_skills,
    write_skill_file,
)

router = APIRouter(prefix="/skills", tags=["skills"])

# 校验失败时返回 {"detail": {"message", "fields"}}，前端据此在字段旁显示原因
FIELD_ERROR_STATUS = 422
# Skill 仍被 Agent 引用时禁止删除（引用检查在后端执行）
REFERENCE_CONFLICT_STATUS = 409


def get_skills_dir() -> Path:
    """Skill 根目录，测试中可替换为临时目录。"""
    return settings.skills_dir


def _resolve_skill_dir(skills_dir: Path, dir_name: str) -> Path:
    """校验目录名安全性并解析为 skills 目录下的路径；非法名称按不存在处理。"""
    if (
        not dir_name
        or len(dir_name) > 128
        or dir_name in (".", "..")
        or dir_name.startswith(".")
        or "/" in dir_name
        or "\\" in dir_name
    ):
        raise HTTPException(status_code=404, detail="Skill 不存在或已被删除")
    skill_dir = skills_dir / dir_name
    if not skill_dir.resolve().is_relative_to(skills_dir.resolve()):
        raise HTTPException(status_code=404, detail="Skill 不存在或已被删除")
    return skill_dir


def _get_row_or_404(db: Session, dir_name: str) -> Skill:
    row = db.query(Skill).filter(Skill.dir_name == dir_name).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Skill 不存在或已被删除")
    return row


def _to_out(row: Skill) -> SkillOut:
    return SkillOut(
        id=row.id,
        dir_name=row.dir_name,
        name=row.name,
        description=row.description,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_payload(payload: SkillUpdateRequest) -> dict[str, str]:
    errors: dict[str, str] = {}
    name = payload.name.strip()
    description = payload.description.strip()
    instructions = payload.instructions.strip()

    if not name:
        errors["name"] = "请填写名称"
    elif len(name) > NAME_MAX_LENGTH:
        errors["name"] = f"名称不能超过 {NAME_MAX_LENGTH} 个字符"

    if not description:
        errors["description"] = "请填写用途说明"
    elif len(description) > DESCRIPTION_MAX_LENGTH:
        errors["description"] = f"用途说明不能超过 {DESCRIPTION_MAX_LENGTH} 个字符"

    if not instructions:
        errors["instructions"] = "请填写详细指令"
    elif len(instructions) > INSTRUCTIONS_MAX_LENGTH:
        errors["instructions"] = f"详细指令不能超过 {INSTRUCTIONS_MAX_LENGTH} 个字符"

    return errors


@router.get("")
def list_skills(
    db: Session = Depends(get_db),
    skills_dir: Path = Depends(get_skills_dir),
) -> list[SkillOut]:
    """扫描目录并返回全部 Skill，按最近更新倒序。"""
    rows = sync_skills(db, skills_dir)
    return [_to_out(row) for row in rows]


@router.post("/import", status_code=201)
async def import_skill(
    file: UploadFile,
    db: Session = Depends(get_db),
    skills_dir: Path = Depends(get_skills_dir),
) -> SkillOut:
    """导入 ZIP 压缩包：校验结构与必要内容后解压到 skills 目录。"""
    data = await file.read()
    fallback_name = Path(file.filename or "").stem or "skill"
    try:
        row = import_skill_zip(db, skills_dir, data, fallback_name)
    except SkillExistsError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except SkillImportError as error:
        raise HTTPException(status_code=FIELD_ERROR_STATUS, detail=str(error))
    return _to_out(row)


@router.get("/{dir_name}")
def get_skill(
    dir_name: str,
    db: Session = Depends(get_db),
    skills_dir: Path = Depends(get_skills_dir),
) -> SkillDetailOut:
    """返回单个 Skill 的完整内容；正文直接读文件，保证与磁盘一致。"""
    skill_dir = _resolve_skill_dir(skills_dir, dir_name)
    skill_file = find_skill_file(skill_dir)
    if skill_file is None:
        raise HTTPException(status_code=404, detail="Skill 不存在或已被删除")

    meta = read_skill_meta(skill_file)
    row = db.query(Skill).filter(Skill.dir_name == dir_name).first()
    # 数据库无登记记录时（极端情况），用文件修改时间兜底，保证接口返回类型不变
    fallback_time = datetime.fromtimestamp(
        skill_file.stat().st_mtime, tz=timezone.utc
    ).replace(tzinfo=None)
    return SkillDetailOut(
        id=row.id if row is not None else 0,
        dir_name=dir_name,
        name=meta.name or dir_name,
        description=meta.description,
        instructions=meta.body,
        enabled=row.enabled if row is not None else True,
        created_at=row.created_at if row is not None else fallback_time,
        updated_at=row.updated_at if row is not None else fallback_time,
    )


@router.put("/{dir_name}")
def update_skill(
    dir_name: str,
    payload: SkillUpdateRequest,
    db: Session = Depends(get_db),
    skills_dir: Path = Depends(get_skills_dir),
) -> SkillOut:
    """保存编辑：写回 SKILL.md 并刷新列表信息与更新时间。"""
    row = _get_row_or_404(db, dir_name)

    errors = _validate_payload(payload)
    if errors:
        raise HTTPException(
            status_code=FIELD_ERROR_STATUS,
            detail={"message": "表单校验未通过，请检查填写内容", "fields": errors},
        )

    skill_dir = _resolve_skill_dir(skills_dir, dir_name)
    skill_file = find_skill_file(skill_dir)
    if skill_file is None:
        raise HTTPException(status_code=404, detail="Skill 不存在或已被删除")

    row.name = payload.name.strip()
    row.description = payload.description.strip()
    row.updated_at = write_skill_file(
        skill_file, row.name, row.description, payload.instructions
    )
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.put("/{dir_name}/enabled")
def set_skill_enabled(
    dir_name: str,
    payload: SkillEnabledRequest,
    db: Session = Depends(get_db),
) -> SkillOut:
    """启用/停用只改数据库状态；文件保留在磁盘上。"""
    row = _get_row_or_404(db, dir_name)
    row.enabled = payload.enabled
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/{dir_name}", status_code=204)
def delete_skill(
    dir_name: str,
    db: Session = Depends(get_db),
    skills_dir: Path = Depends(get_skills_dir),
) -> None:
    """删除 Skill 目录与登记记录，删除后无法恢复；被 Agent 引用时拒绝删除。"""
    row = _get_row_or_404(db, dir_name)

    ref_names = agent_names_using_skill(db, dir_name)
    if ref_names:
        raise HTTPException(
            status_code=REFERENCE_CONFLICT_STATUS,
            detail=f"该 Skill 正在被 Agent 使用：{'、'.join(f'「{name}」' for name in ref_names)}。"
            "请先在对应 Agent 中移除该 Skill，或删除相关 Agent 后再删除",
        )

    skill_dir = _resolve_skill_dir(skills_dir, dir_name)

    if skill_dir.exists():
        try:
            shutil.rmtree(skill_dir)
        except OSError:
            raise HTTPException(status_code=500, detail="删除 Skill 目录失败，请检查文件是否被占用")

    db.delete(row)
    db.commit()
