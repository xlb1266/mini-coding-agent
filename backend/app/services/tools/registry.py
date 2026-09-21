"""内置工具注册表：工具元数据与执行函数的唯一来源。

迁移 0003 从 builtin_tool_rows() 读取定义写入 tools 表，
统一执行入口按 key 绑定 handler。新增内置工具时：在这里加定义，
再新增一个迁移把对应行插入 tools 表。

handler 统一签名 (params, context)：context 是运行级 ToolContext
（决定文件沙箱根与 Shell 工作目录），调用方未提供时传 None，
handler 内部回落全局默认目录。
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.services.tools.base import ToolContext, ToolParamSpec
from app.services.tools.handlers import (
    run_current_time,
    run_edit_file,
    run_file,
    run_grep,
    run_list_dir,
    run_shell,
)


@dataclass(frozen=True)
class ToolDefinition:
    """一个内置工具的完整定义：展示信息、参数规格与执行函数。"""

    key: str
    name: str
    description: str
    params: list[ToolParamSpec]
    handler: Callable[[dict, ToolContext | None], dict]


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
    ToolDefinition(
        key="builtin_edit_file",
        name="文件编辑",
        description=(
            "对授权工作区内已有的文本文件做精确字符串替换，返回统一 diff 与替换次数。"
            "适用场景：修改代码中的一处逻辑、调整配置项、重命名标识符等需要保留"
            "文件其余内容不动的小步编辑。输入要求：path 为相对工作区根目录的路径"
            "或工作区内的绝对路径；old_string 必须与文件中的原文完全一致（包括空格、"
            "缩进与换行），new_string 为替换后的内容（传空串即删除该段）；replace_all"
            " 默认 false，为 true 时替换全部出现位置。使用限制：old_string 在文件中"
            "出现 0 次会报「未找到」，出现多处且未开 replace_all 会报错提示提供更长"
            "上下文；单次编辑的文件与替换后内容均不超过 512KB；编辑会产生 diff 输出，"
            "超长会截断。"
        ),
        params=[
            ToolParamSpec(
                name="path",
                type="string",
                required=True,
                description="相对授权工作区根目录的路径，或工作区内的绝对路径",
                example="src/main.py",
            ),
            ToolParamSpec(
                name="old_string",
                type="string",
                required=True,
                description="要替换的原内容，必须与文件原文完全一致（含空格与换行）",
            ),
            ToolParamSpec(
                name="new_string",
                type="string",
                required=True,
                description="替换后的内容，传空串表示删除 old_string",
            ),
            ToolParamSpec(
                name="replace_all",
                type="boolean",
                required=False,
                description="是否替换全部出现位置；默认 false（出现多处时会报错）",
                example="false",
            ),
        ],
        handler=run_edit_file,
    ),
    ToolDefinition(
        key="builtin_list_dir",
        name="目录列表",
        description=(
            "列出授权工作区内一个目录的内容，返回条目的相对路径、类型与大小。"
            "适用场景：浏览项目结构、查找文件位置、确认操作前的目录状态。"
            "输入要求：path 为相对工作区根目录的子目录路径，留空时列出工作区根目录；"
            "recursive 为可选开关，开启后递归列出子目录。使用限制：recursive 最多"
            "下探 3 层、最多返回 500 个条目，超限时截断并标记 truncated；目录条目的"
            "大小记为 0；列表顺序为目录在前、同类按名称排序。"
        ),
        params=[
            ToolParamSpec(
                name="path",
                type="string",
                required=False,
                description="要列出的子目录，留空时列出工作区根目录",
                example="src",
            ),
            ToolParamSpec(
                name="recursive",
                type="boolean",
                required=False,
                description="是否递归列出子目录（深度最多 3 层，条目最多 500 个）",
                example="true",
            ),
        ],
        handler=run_list_dir,
    ),
    ToolDefinition(
        key="builtin_grep",
        name="内容搜索",
        description=(
            "在授权工作区的文件内容中按正则表达式递归搜索，返回匹配的文件、行号与"
            "该行内容。适用场景：查找函数定义、追踪变量引用、定位报错关键字等。"
            "输入要求：pattern 为合法的正则表达式；path 为可选的子目录（留空搜整个"
            "工作区）；glob 为可选的文件名过滤（如 *.py）；case_insensitive 开启后"
            "忽略大小写。使用限制：max_results 为返回条数上限（1–200，默认 50），"
            "超过上限时截断并标记 truncated；二进制文件、超过 512KB 的文件会被跳过；"
            "只搜索文本内容，不理解代码语义。"
        ),
        params=[
            ToolParamSpec(
                name="pattern",
                type="string",
                required=True,
                description="要搜索的正则表达式，例如 def\\s+\\w+\\(",
                example="TODO",
            ),
            ToolParamSpec(
                name="path",
                type="string",
                required=False,
                description="要搜索的子目录，留空时搜索整个工作区",
                example="src",
            ),
            ToolParamSpec(
                name="glob",
                type="string",
                required=False,
                description="文件名过滤，支持通配符，例如 *.py",
                example="*.py",
            ),
            ToolParamSpec(
                name="case_insensitive",
                type="boolean",
                required=False,
                description="是否忽略大小写，默认 false",
                example="true",
            ),
            ToolParamSpec(
                name="max_results",
                type="integer",
                required=False,
                description="返回条数上限，1–200，默认 50",
                example="50",
            ),
        ],
        handler=run_grep,
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
