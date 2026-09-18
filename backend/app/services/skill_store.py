"""Skill 文件存储层：目录扫描、SKILL.md 解析/写回与 ZIP 导入。

Skill 以文件为唯一数据源：workspace/skills/<目录名>/SKILL.md，
frontmatter 提供 name 与 description，其余为 Markdown 指令正文。
数据库只登记目录名、缓存名称/说明/更新时间，并持久化启用状态。
"""

import io
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models.skill import Skill

SKILL_FILE_NAME = "SKILL.md"

# 压缩包大小上限，防止误传超大文件拖垮本地服务
MAX_IMPORT_BYTES = 50 * 1024 * 1024

# ZIP 内无需解压的垃圾文件
_JUNK_NAMES = {".DS_Store", "Thumbs.db"}

# 目录名中的非法字符（Windows 保留字符 + 空白 + 控制字符）统一替换为 -
_INVALID_DIR_CHARS = re.compile(r'[\\/:*?"<>|\s\x00-\x1f]+')

NAME_MAX_LENGTH = 64
DESCRIPTION_MAX_LENGTH = 200
INSTRUCTIONS_MAX_LENGTH = 200_000


class SkillImportError(Exception):
    """导入失败：压缩包格式、结构或内容不符合要求（HTTP 422）。"""


class SkillExistsError(SkillImportError):
    """同名 Skill 已存在，不覆盖（HTTP 409）。"""


@dataclass
class SkillFileMeta:
    """SKILL.md 解析结果；name/description 为空表示 frontmatter 未提供。"""

    name: str
    description: str
    body: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


def _one_line(value: str) -> str:
    """frontmatter 值必须单行：换行折叠为空格。"""
    return " ".join(value.split())


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_skill_markdown(text: str) -> SkillFileMeta:
    """解析 SKILL.md：首行 --- 开头的 frontmatter 取 name/description，其余为正文。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return SkillFileMeta(name="", description="", body=text)

    for index in range(1, len(lines)):
        if lines[index].strip() != "---":
            continue
        meta = SkillFileMeta(name="", description="", body="\n".join(lines[index + 1:]).lstrip("\n"))
        for line in lines[1:index]:
            key, sep, value = line.partition(":")
            if not sep:
                continue
            key = key.strip().lower()
            value = _unquote(value.strip())
            if key == "name" and not meta.name:
                meta.name = value
            elif key == "description" and not meta.description:
                meta.description = value
        return meta

    # 只有起始 --- 没有闭合，整体按正文处理，避免把内容误当元数据丢弃
    return SkillFileMeta(name="", description="", body=text)


def render_skill_markdown(name: str, description: str, instructions: str) -> str:
    body = instructions.rstrip()
    return (
        f"---\nname: {_one_line(name)}\ndescription: {_one_line(description)}\n"
        f"---\n\n{body}\n"
    )


def find_skill_file(skill_dir: Path) -> Path | None:
    """定位目录内的 SKILL.md；大小写不敏感，兼容跨平台手工创建的文件。"""
    if not skill_dir.is_dir():
        return None
    for candidate in (skill_dir / SKILL_FILE_NAME, skill_dir / SKILL_FILE_NAME.lower()):
        if candidate.is_file():
            return candidate
    for child in skill_dir.iterdir():
        if child.is_file() and child.name.lower() == SKILL_FILE_NAME.lower():
            return child
    return None


def sanitize_dir_name(raw: str) -> str:
    cleaned = _INVALID_DIR_CHARS.sub("-", raw).strip("-.")
    return cleaned[:80] or "skill"


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SkillImportError("无法识别 SKILL.md 的文本编码，请使用 UTF-8 编码后重试")


def read_skill_meta(skill_file: Path) -> SkillFileMeta:
    """读取并解析 SKILL.md；解码失败时返回空元数据，不中断扫描。"""
    try:
        return parse_skill_markdown(_decode(skill_file.read_bytes()))
    except (OSError, SkillImportError):
        return SkillFileMeta(name="", description="", body="")


def sync_skills(db: Session, skills_dir: Path) -> list[Skill]:
    """重新扫描 skills 目录并同步数据库：登记新目录、刷新文件信息、清理已删除目录。

    目录中不含 SKILL.md 的子目录视为无效 Skill，不登记；
    已登记目录的启用状态不受扫描影响。
    """
    skills_dir.mkdir(parents=True, exist_ok=True)
    on_disk: dict[str, Path] = {}
    for child in skills_dir.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        skill_file = find_skill_file(child)
        if skill_file is not None:
            on_disk[child.name] = skill_file

    for dir_name, skill_file in on_disk.items():
        meta = read_skill_meta(skill_file)
        row = db.query(Skill).filter(Skill.dir_name == dir_name).first()
        updated_at = _naive_utc(skill_file.stat().st_mtime)
        if row is None:
            db.add(
                Skill(
                    dir_name=dir_name,
                    name=meta.name or dir_name,
                    description=meta.description,
                    enabled=True,
                    created_at=_utcnow(),
                    updated_at=updated_at,
                )
            )
        else:
            row.name = meta.name or dir_name
            row.description = meta.description
            row.updated_at = updated_at

    for row in db.query(Skill).all():
        if row.dir_name not in on_disk:
            db.delete(row)

    db.commit()
    return list(db.query(Skill).order_by(Skill.updated_at.desc(), Skill.dir_name.asc()).all())


def write_skill_file(skill_file: Path, name: str, description: str, instructions: str) -> datetime:
    """把编辑内容写回 SKILL.md，返回新的文件修改时间（naive UTC）。"""
    skill_file.write_text(
        render_skill_markdown(name, description, instructions), encoding="utf-8", newline="\n"
    )
    return _naive_utc(skill_file.stat().st_mtime)


def _safe_entry_names(archive: zipfile.ZipFile) -> list[str]:
    """过滤垃圾项并做路径安全检查，返回统一使用 / 分隔的条目名。

    拒绝包含 .. 段或冒号的路径，确保解压不会写出目标目录之外。
    """
    names: list[str] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        parts = [p for p in info.filename.replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts:
            continue
        if parts[0] == "__MACOSX" or parts[-1] in _JUNK_NAMES:
            continue
        if any(p == ".." or ":" in p for p in parts):
            raise SkillImportError("压缩包内包含不安全的文件路径，无法导入")
        names.append("/".join(parts))
    return names


def _skill_md_entry(entries: list[str], prefix: str) -> str | None:
    """在 prefix 下（不递归）查找 SKILL.md，大小写不敏感。"""
    for name in entries:
        if name.startswith(prefix) and name[len(prefix):].lower() == SKILL_FILE_NAME.lower():
            return name
    return None


def _locate_skill_root(entries: list[str]) -> tuple[str, str | None]:
    """确定 Skill 在压缩包内的位置，返回 (条目前缀, 顶层文件夹名)。

    允许两种结构：SKILL.md 位于压缩包根目录（前缀为空串），或位于唯一的
    顶层文件夹内（返回该文件夹名，作为目标目录名的第一来源）。
    """
    if _skill_md_entry(entries, "") is not None:
        return "", None

    tops = {name.split("/")[0] for name in entries}
    if len(tops) == 1:
        top = next(iter(tops))
        if _skill_md_entry(entries, f"{top}/") is not None:
            return f"{top}/", top
        raise SkillImportError("压缩包中未找到 SKILL.md，请检查文件结构")
    if len(tops) > 1:
        raise SkillImportError(
            "压缩包结构不符合要求：SKILL.md 应位于根目录，或只放在一个顶层文件夹内"
        )
    raise SkillImportError("压缩包中未找到 SKILL.md，请检查文件结构")


def import_skill_zip(db: Session, skills_dir: Path, data: bytes, fallback_name: str) -> Skill:
    """校验并解压 ZIP 导入 Skill，登记后返回；任何失败都不产生半成品目录。"""
    if len(data) > MAX_IMPORT_BYTES:
        raise SkillImportError("压缩包超过 50MB 大小限制，请精简后重试")
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise SkillImportError("请上传有效的 ZIP 格式压缩包")

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = _safe_entry_names(archive)
        prefix, wrapped_top = _locate_skill_root(entries)

        skill_md = _skill_md_entry(entries, prefix)
        assert skill_md is not None  # _locate_skill_root 已保证存在
        text = _decode(archive.read(skill_md))
        if not text.strip():
            raise SkillImportError("SKILL.md 内容为空，请补充说明后再导入")

        meta = parse_skill_markdown(text)
        # 目录名优先级：压缩包内的顶层文件夹 > frontmatter name > ZIP 文件名
        dir_name = sanitize_dir_name(wrapped_top or meta.name or fallback_name)
        target = skills_dir / dir_name
        if target.exists():
            raise SkillExistsError(f"已存在同名 Skill「{dir_name}」，导入不会覆盖，请先删除或重命名")

        target.mkdir(parents=True)
        try:
            for entry in entries:
                if not entry.startswith(prefix):
                    continue
                rel_parts = [p for p in entry[len(prefix):].split("/") if p]
                if not rel_parts or rel_parts[-1] in _JUNK_NAMES:
                    continue
                dest = target.joinpath(*rel_parts)
                # 双重保险：即便前面的检查有遗漏，也不允许写出目标目录
                if not dest.resolve().is_relative_to(target.resolve()):
                    raise SkillImportError("压缩包内包含不安全的文件路径，无法导入")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(archive.read(entry))
        except SkillImportError:
            shutil.rmtree(target, ignore_errors=True)
            raise
        except OSError:
            shutil.rmtree(target, ignore_errors=True)
            raise SkillImportError("解压失败，请检查压缩包是否损坏后重试")

    skill_file = find_skill_file(target)
    if skill_file is None:  # 理论不可达：SKILL.md 已随条目解压
        shutil.rmtree(target, ignore_errors=True)
        raise SkillImportError("SKILL.md 未能写入，请重试")

    now = _utcnow()
    row = Skill(
        dir_name=dir_name,
        name=meta.name or dir_name,
        description=meta.description,
        enabled=True,
        created_at=now,
        updated_at=_naive_utc(skill_file.stat().st_mtime),
    )
    db.add(row)
    try:
        db.commit()
    except Exception:
        # 极端情况：磁盘允许大小写不同的同名目录（仅 Linux），数据库唯一约束兜底
        db.rollback()
        raise SkillExistsError(f"已存在同名 Skill「{dir_name}」，导入不会覆盖，请先删除或重命名")
    db.refresh(row)
    return row
