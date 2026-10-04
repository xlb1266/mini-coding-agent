# 阶段十四：Shell 工程能力（超时显式化、后台任务、整树击杀、截断底稿）

## 本阶段完成内容

在模块一/二/三与聊天轨迹展示之上，修复 Shell 工具面向"全栈 Web 开发"场景的四个硬伤（分支 `feature/coding-agent-mvp`）：

- **毛病 1（超时掐断 + 模型误判）**：超时上限从 120 秒放宽到 600 秒（超出上限按上限截断，不再报参数错误）；超时是显式结果类型（`timedOut: true` + 中文说明"已被整树终止"），不再伪装成错误码或空输出——消除模型"以为是自己搞砸了"的误判；
- **毛病 2（停不下来的活必死）**：Shell 增加 `action` 参数（`run` 前台执行 / `background` 后台启动 / `stop` 停止任务 / `list` 列任务），后台任务跨轮次存活，支持"启动 dev server → 下一轮读日志 → 验收 → 停止"的真实流程；另加**前台阻塞预算保险丝**（默认 15 秒）：适合后台运行的命令超时不杀、自动转后台并返回 taskId，模型忘了写 `background` 也不会死（`sleep`、`ping`、`tail -f` 等本应等待的命令豁免）；
- **毛病 3（报错只看前一半）**：超长输出内联保留头 60% + 尾 40%（错误几乎在末尾），同时全量落盘并返回 `persistedOutputPath`；配套给文件读写工具的 read 动作加 `tail_lines` 参数（只读末尾 N 行，1–500），模型用已有工具翻看后台日志；
- **毛病 4（掐断留垃圾）**：终止一律走**整树击杀**——Windows `taskkill /T /F`（连进程树），POSIX `start_new_session` 新进程组 + `killpg`（先 SIGTERM 宽限 2 秒再 SIGKILL）；前台超时、保险丝不适用、`stop` 任务共用同一套杀树函数。

设计参考 ZCode 桌面版 Bash 工具的实测方案（四条原则：不让模型干等 / 不丢信息 / 不杀一半 / 让模型知道发生了什么），按本项目"单工具 + 人工审批"模型做适配：不加新工具（工具数保持 6 个），动作模式照搬 `builtin_file` 的 action 先例。

## 接口变化（迁移 0014）

- `0014_shell_background`：仅 UPDATE `builtin_shell` 与 `builtin_file` 两行的 description 与 parameters_json（与 registry 同源生成），不插新种子、不碰 agent_bindings；downgrade 用迁移内写死的旧文案恢复；
- `builtin_shell` 参数：`action`（默认 run）、`command`（run/background 必填，由 handler 校验）、`timeout`（1–600，默认 30，超出按 600 截断）、`taskId`（stop 必填）；返回增加 `timedOut` / `autoBackgrounded` / `taskId` / `outputFile` / `persistedOutputPath` / `message` 字段；
- `builtin_file` read 动作新增 `tail_lines`（可选）：从文件末尾最多回看 1MB 取最后 N 行，不受 512KB 整读上限约束，返回 `truncatedStart` 标记窗口截断。

## 新模块 `app/services/tools/shell_jobs.py`

- `ShellJobRegistry`（进程内单例 `SHELL_JOBS`，线程锁保护，handler 跑线程池）：
  - `start`：`Popen(shell=True, cwd=工作区, stdout=stderr=日志文件, env=child_env(), start_new_session=POSIX)`，立即返回不等待；日志与登记 JSON 落 `<工作区>/.mini-agent/jobs/<taskId>.log|.json`（`settings.tool_shell_job_dir` 可配）；
  - `adopt`：保险丝转后台的接管入口——前台输出已由收集线程实时进入内存缓冲与日志文件，adopt 时 attach 文件、补写已缓冲部分，登记任务并启动退出监视线程；
  - `stop`：注册表内活进程整树击杀；后端重启过的孤儿按登记文件里的 PID 尽力 taskkill；成功后清理登记文件；
  - `list`：合并内存注册表与本工作区 jobs 目录下的登记文件；孤儿做尽力存活探测（POSIX `kill(pid,0)` / Windows `tasklist /FI`），活着标 `running`、死了标 `dead`；
  - 退出通知：任务退出（含被 stop）时登记通知（taskId / command / exitCode / outputFile / workspaceRoot），队列上限 50 条；
- 输出收集：`OutputCollector`（单流保头 1.5MB + 保尾 512KB）+ `JobLogFile`（attach 前写操作为空操作，前台命令不落盘）+ `drain_stream`（用 `read1` 非阻塞式读，普通 `read(8192)` 在缓冲流上会等满字节数、把零星输出堵在管道里）；前台 Popen 用 `bufsize=0` 保证 `read1` 生效；
- `child_env()`：子进程环境注入 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`、`PYTHONUNBUFFERED=1`——第三项很关键：否则 python 子进程对管道输出块缓冲，`print` 的内容憋在子进程缓冲区里，转后台后日志文件始终是空的。

## Runtime 改动（仅两处）

- `tool_requires_approval`：`builtin_shell` 从"一律审批"改为按动作——`run` / `background` / `stop` 审批，`list` 不审批（只读，与列目录、搜索一致）；`run` / `background` 与原行为一致，无放宽；
- `_inject_job_notifications`：每轮模型调用前（上下文管理之后）取走本工作区的待发任务退出通知，以 system 消息注入本轮请求上下文（只进模型上下文，不落聊天记录）；模型无需轮询即可知道服务崩没崩、退出码多少；
- `_builtin_summary`：按动作出展示摘要（后台启动 / 已自动转后台 / 停止 / 列表 / 超时终止 / 普通命令）。

## 前端

零改动：轨迹卡片与运行记录回放按通用结构渲染工具参数与结果，无按工具 key 的硬编码映射；工具管理页从 DB 读描述，迁移 0014 后自动展示新参数说明。

## 已知限制

- 后台任务**不会自动超时**，需要模型（或用户经审批）显式 `stop`；
- 后端重启后，内存注册表清空：`list` 能按登记文件显示孤儿任务状态、`stop` 能按 PID 尽力回收，但不保证万无一失（登记文件被删、进程已换 PID 复用等）；
- 输出底稿的内存缓冲有上限（单流保头 1.5MB + 保尾 512KB），正常命令远达不到；超过部分只计入总量，底稿不完整；
- 非 Python 系子进程的输出缓冲不受我们控制（PYTHONUNBUFFERED 只管 python），个别命令的输出要等缓冲区满才落日志；
- Windows 无进程组，`taskkill /T` 存在极小的竞态窗口（孙进程在扫描与击杀之间新起），实测足够；工业级做法是 Job Object，暂不引入。

## 后续项

- 后台任务面板 / 按会话聚合的任务视图（等评测或仪表盘阶段一起做）；
- 环境变量白名单化（治理专题：当前子进程继承后端全部环境变量，printenv 已被黑名单拦截但 shell 重定向仍可越界）；
- `persistedOutputPath` 底稿的过期清理策略。
