"""阶段十四：更新 Shell 与文件读写工具的定义（后台任务与尾部读取）。

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-04

"""
from collections.abc import Sequence
from typing import Union

import json

import sqlalchemy as sa
from alembic import op

from app.services.tools.registry import get_definition, parameters_json

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UPDATED_KEYS = ("builtin_shell", "builtin_file")

# 旧定义（升级前的描述与参数），downgrade 用它恢复，保持与迁移前逐字一致
_OLD = {
    "builtin_shell": {
        "description": (
            "在本机执行 shell 命令并返回标准输出、错误输出与退出码。适用场景："
            "查看系统信息、搜索文件、运行脚本、执行测试等需要操作本地环境的任务。"
            "输入要求：command 为完整的命令字符串，按系统默认 shell（Windows 为 cmd，"
            "macOS/Linux 为 sh）执行；timeout 为可选的超时秒数（1–120，默认 30）。"
            "使用限制：以下危险命令会在执行层被直接拦截并返回 blocked 错误——"
            "递归删除根目录或系统目录（如 rm -rf /）、格式化磁盘或直接写入磁盘设备"
            "（format、mkfs、dd of=/dev/…）、修改关键系统权限（icacls、takeown，"
            "以及对系统目录的 chmod/chown）、关闭杀毒软件或防火墙、读取或外传密钥"
            "（.ssh、.aws、.env、secret.key 等路径，以及 printenv）、未经检查直接"
            "执行远程下载的脚本（如 curl … | sh、Invoke-Expression）；输出超过上限"
            "会被截断。拦截规则是尽力而为的黑名单，不能替代操作系统级的隔离。"
        ),
        "parameters": [
            {"name": "command", "type": "string", "required": True, "description": "要执行的完整命令字符串", "example": "git status"},
            {"name": "timeout", "type": "integer", "required": False, "description": "超时秒数，1–120；留空时使用系统默认值 30 秒", "example": "30"},
        ],
    },
    "builtin_file": {
        "description": (
            "读取、创建和修改授权工作区内的文本文件。适用场景：查看代码或配置内容、"
            "写入笔记与运行结果、创建或修改项目文件。输入要求：action 为 read（读取）"
            "或 write（写入）；path 支持两种写法——相对工作区根目录的相对路径"
            "（如 notes/todo.md）或工作区内的绝对路径，不允许访问工作区之外的文件；"
            "write 时必须提供 content，内容以 UTF-8 保存。使用限制：单次读取或写入"
            "不超过 512KB，读取大文件请改用 Shell 工具分段查看；读取的文件必须已存在；"
            "写入模式 mode=overwrite 覆盖已有文件（不存在则连同父目录一起创建），"
            "mode=create 仅在文件不存在时创建（已存在则报错，需要覆盖请改用 overwrite），"
            "mode=append 追加到文件末尾（不存在则创建）。"
        ),
        "parameters": [
            {"name": "action", "type": "string", "required": True, "description": "read（读取）或 write（写入）", "example": "read"},
            {"name": "path", "type": "string", "required": True, "description": "相对授权工作区根目录的路径，或工作区内的绝对路径", "example": "notes/todo.md"},
            {"name": "content", "type": "string", "required": False, "description": "要写入的文本内容，仅 action=write 时需要", "example": None},
            {"name": "mode", "type": "string", "required": False, "description": "写入模式：overwrite（覆盖，默认）、create（仅新建）、append（追加）；仅 action=write 时有效", "example": "overwrite"},
        ],
    },
}


def _update_row(key: str, description: str, parameters: list | str) -> None:
    """按 key 更新工具行的描述与参数 JSON（不动 updated_at）。"""
    parameters_json_text = (
        parameters if isinstance(parameters, str) else json.dumps(parameters, ensure_ascii=False)
    )
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE tools SET description = :description, parameters_json = :parameters WHERE key = :key")
        .bindparams(description=description, parameters=parameters_json_text, key=key)
    )


def upgrade() -> None:
    # 新定义与 app/services/tools/registry.py 同源，避免定义漂移
    for key in _UPDATED_KEYS:
        definition = get_definition(key)
        assert definition is not None
        _update_row(key, definition.description, parameters_json(definition))


def downgrade() -> None:
    for key, old in _OLD.items():
        _update_row(key, old["description"], old["parameters"])
