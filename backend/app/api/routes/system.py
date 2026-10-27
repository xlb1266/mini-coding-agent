"""本机系统能力路由：为本地优先的页面提供浏览器做不到的桌面交互。

目录选择：前端表单点「选择目录」→ 调用本端点 → 后端在本机弹出
原生"选择文件夹"窗口 → 把选中的绝对路径返回给前端回填。窗口由
tkinter 实现，选择期间请求保持挂起（前端 fetch 无超时，可等用户
慢慢挑）；取消选择返回 path=null，打不开窗口返回 500 及原因。

路由是同步 def（FastAPI 自动放线程池执行，弹窗等待不阻塞事件
循环）；线程锁保证同一时刻至多一个选择窗口，重复请求返回 409。
"""

import threading

from fastapi import APIRouter, HTTPException

from app.services.directory_picker import DirectoryPickerError, ask_directory

router = APIRouter(prefix="/system", tags=["system"])

_PICK_LOCK = threading.Lock()


@router.post("/pick-directory")
def pick_directory() -> dict:
    """弹出本机的目录选择窗口；返回 {"path": 绝对路径或 null}。"""
    if not _PICK_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="目录选择窗口已打开，请先完成选择或取消")
    try:
        path = ask_directory("选择工作目录")
    except DirectoryPickerError as error:
        raise HTTPException(status_code=500, detail=str(error)) from None
    finally:
        _PICK_LOCK.release()
    return {"path": path}
