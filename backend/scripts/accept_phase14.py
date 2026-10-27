"""阶段十四真实验收驱动：用工作台里真实的 coding-agent 跑一遍后台任务闭环。

不做任何 mock：真实模型、真实审批配置、真实子进程。跑完后从消息时间线
提取工具调用结果，并独立核验日志文件。
"""
import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8001"
PORT = 8901
WORKSPACE = Path("D:/zhuomian/mini-coding-agent/workspace")

INSTRUCTION = (
    "验收任务，严格按步骤执行，不要做任务之外的任何事：\n"
    f"1) 用 Shell 工具 action=background 执行 python -m http.server {PORT}（工作目录就是当前工作区）；\n"
    "2) 用文件读写工具读取上一步返回的输出文件，确认服务已启动；\n"
    f"3) 用 Shell 工具前台执行 curl -s http://127.0.0.1:{PORT}/ 确认能访问；\n"
    "4) 用 Shell 工具 action=stop 停止该后台任务；\n"
    "5) 只汇报：每步是否成功、后台任务 taskId、curl 是否拿到内容。不要输出其他解释。"
)


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=30) as client:
        session = client.post("/api/chat/sessions", json={"agent_id": 1}).json()
        session_id = session["id"]
        print(f"会话: {session_id}")

        sent = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": INSTRUCTION})
        sent.raise_for_status()
        print("消息已发送，等待生成完成...")

        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            state = client.get(f"/api/chat/sessions/{session_id}/messages").json()
            if state.get("generation") is None:
                break
            time.sleep(3)
        else:
            raise SystemExit("超时：生成未在 300 秒内完成")

        # 独立核验：日志文件与时间线里的工具结果
        jobs_dir = WORKSPACE / ".mini-agent" / "jobs"
        logs = sorted(jobs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)[-1:]
        for log in logs:
            text = log.read_text(encoding="utf-8", errors="replace")
            print(f"日志 {log.name}: {'Serving HTTP' in text}（含 Serving HTTP）")

        timeline_tools = []
        for message in state["messages"]:
            for item in message.get("timeline") or []:
                if item.get("type") == "tool_call":
                    timeline_tools.append(item)
        for item in timeline_tools:
            args = item.get("arguments") or {}
            print(
                f"工具: {item.get('toolName')} | 参数: {json.dumps(args, ensure_ascii=False)[:110]}"
            )
        final = state["messages"][-1]["content"]
        print("---- Agent 最终汇报 ----")
        print(final[:600])


if __name__ == "__main__":
    main()
