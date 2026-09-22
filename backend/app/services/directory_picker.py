"""本机目录选择窗口（tkinter 实现）。

本项目是本地优先的桌面工具：后端与浏览器同机运行，所以"弹出系统
原生的文件夹选择窗口"由后端代劳——浏览器出于安全限制拿不到
D:\dir 这样的完整绝对路径（File System Access API 只返回句柄和
文件夹名），而 tkinter 是标准库，能在 Windows/macOS/Linux 弹出
原生选择窗口，返回用户本机的真实路径。

窗口以置顶方式打开，用户取消时返回 None；后端进程没有桌面环境
（如被服务化）时抛 DirectoryPickerError，由调用方转成明确提示。
"""


class DirectoryPickerError(Exception):
    """无法打开系统目录选择窗口（缺少 tkinter 或没有桌面环境）。"""


def ask_directory(title: str = "选择文件夹") -> str | None:
    """弹出一个原生目录选择窗口；返回选中目录的绝对路径，取消时返回 None。

    阻塞到用户完成选择为止，调用方（路由）负责放在线程池里执行，
    并自行加锁避免同一时刻叠出多个选择窗口。
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as error:  # 精简的 Python 发行版可能不带 tkinter
        raise DirectoryPickerError("本机 Python 缺少 tkinter，无法打开目录选择窗口，请手动输入路径") from error

    try:
        root = tk.Tk()
        try:
            root.withdraw()  # 只留对话框，不显示空的 Tk 主窗口
            root.attributes("-topmost", True)  # 置顶，避免被浏览器窗口挡住
            path = filedialog.askdirectory(title=title, mustexist=True, parent=root)
        finally:
            root.destroy()
    except Exception as error:  # 无桌面环境（tk.TclError）等运行时故障
        raise DirectoryPickerError(
            "无法打开系统目录选择窗口，请手动输入工作区目录路径"
        ) from error

    return path or None
