"""内置工具注册表：工具元数据与执行函数的唯一来源。

迁移 0003 从 builtin_tool_rows() 读取定义写入 tools 表，
统一执行入口按 key 绑定 handler。新增内置工具时：在这里加定义，
再新增一个迁移把对应行插入 tools 表。
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.services.tools.base import ToolParamSpec
from app.services.tools.handlers import run_current_time, run_file, run_shell


@dataclass(frozen=True)
class ToolDefinition:
    """一个内置工具的完整定义：展示信息、参数规格与执行函数。"""

    key: str
    name: str
    description: str
    params: list[ToolParamSpec]
    handler: Callable[[dict], dict]


BUILTIN_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        key="builtin_current_time",
        name="当前时间",
        description=(
            "查询指定时区的当前日期和时间。适用场景：需要感知当前时间的问题，"
            "例如记录事件发生时间、计算相对日期、生成带时间戳的文件名或内容。"
            "输入要求：时区参数使用 IANA 时区名称，例如 Asia/Shanghai（北京时间）、"
            "America/New_York（纽约时间）、Etc/UTC（协调世界时），不要使用 CST 等"
            "含义不明确的缩写；未提供时区参数时，使用系统配置的默认时区。"
            "使用限制：只读取系统时间，不修改任何系统状态，也不支持查询历史或未来时刻。"
        ),
        params=[
            ToolParamSpec(
                name="timezone",
                type="string",
                required=False,
                description="IANA 时区名称；留空时使用系统配置的默认时区",
                example="Asia/Shanghai",
            ),
        ],
        handler=run_current_time,
    ),
    ToolDefinition(
        key="builtin_shell",
        name="Shell 命令执行",
        description=(
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
        params=[
            ToolParamSpec(
                name="command",
                type="string",
                required=True,
                description="要执行的完整命令字符串",
                example="git status",
            ),
            ToolParamSpec(
                name="timeout",
                type="integer",
                required=False,
                description="超时秒数，1–120；留空时使用系统默认值 30 秒",
                example="30",
            ),
        ],
        handler=run_shell,
    ),
    ToolDefinition(
        key="builtin_file",
        name="文件读写",
        description=(
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
        params=[
            ToolParamSpec(
                name="action",
                type="string",
                required=True,
                description="read（读取）或 write（写入）",
                example="read",
            ),
            ToolParamSpec(
                name="path",
                type="string",
                required=True,
                description="相对授权工作区根目录的路径，或工作区内的绝对路径",
                example="notes/todo.md",
            ),
            ToolParamSpec(
                name="content",
                type="string",
                required=False,
                description="要写入的文本内容，仅 action=write 时需要",
            ),
            ToolParamSpec(
                name="mode",
                type="string",
                required=False,
                description="写入模式：overwrite（覆盖，默认）、create（仅新建）、append（追加）；仅 action=write 时有效",
                example="overwrite",
            ),
        ],
        handler=run_file,
    ),
]

_DEFINITIONS = {definition.key: definition for definition in BUILTIN_TOOLS}


def get_definition(key: str) -> ToolDefinition | None:
    return _DEFINITIONS.get(key)


def builtin_tool_rows() -> list[dict]:
    """内置工具的种子数据行（迁移与测试共用），时间戳在调用时生成。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return [
        {
            "key": definition.key,
            "name": definition.name,
            "description": definition.description,
            "parameters_json": parameters_json(definition),
            "enabled": True,
            "is_builtin": True,
            "created_at": now,
            "updated_at": now,
        }
        for definition in BUILTIN_TOOLS
    ]


def parameters_json(definition: ToolDefinition) -> str:
    """把参数规格序列化成 tools.parameters_json 存储的 JSON 字符串。"""
    return json.dumps(
        [
            {
                "name": spec.name,
                "type": spec.type,
                "required": spec.required,
                "description": spec.description,
                "example": spec.example,
            }
            for spec in definition.params
        ],
        ensure_ascii=False,
    )
