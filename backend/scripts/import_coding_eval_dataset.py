"""把「Coding Agent 基础能力（v1）」评测集录入本机工作台。

等价于在评测页手动新建数据集并逐条填写用例，只是省去 15 次复制粘贴。
走工作台自己的 HTTP 接口（不直接改库），因此会经过后端校验。

前置：后端已在 127.0.0.1:8001 运行。
用法（在 backend/ 目录下）：
    uv run python scripts/import_coding_eval_dataset.py
重复执行安全：同名数据集已存在时直接跳过，不会灌入重复数据。
"""

import httpx

BASE_URL = "http://127.0.0.1:8001"
DATASET_NAME = "Coding Agent 基础能力（v1）"
DATASET_DESCRIPTION = (
    "5 条编码任务用例，覆盖列目录、文件读写、Shell 执行与代码检索；"
    "用于对比工具开关或不同模型的表现。写操作集中在工作区 eval-sandbox/ 子目录，跑完可直接删除。"
)

CASES: list[dict[str, str]] = [
    {
        "question": """
查看当前工作区的顶层结构，只依据实际查看结果回答，不要猜测：
1) 顶层有哪些子目录？
2) 顶层有哪些 .md 文件？
请说明你是用什么方式查看的。
""",
        "expectedAnswer": """
顶层子目录：backend、frontend、tests、scripts、data（若额外列出 .gitignore、.mini-agent、run.py 等隐藏项或文件，不扣分）。
顶层 .md 文件：README.md、handoff-阶段一.md、handoff-阶段二.md、handoff-阶段三.md、handoff-阶段四.md（共 5 个）。
""",
        "scoringRequirements": """
对照参考答案核对准确性：五个子目录（backend/frontend/tests/scripts/data）缺一个或错一个扣 10 分；五个 .md 文件缺一个或错一个扣 10 分。回答必须体现是通过列目录/读文件实际获得的，纯凭空罗列且与实际不符最高给 50 分。全部准确给 90-100 分。
""",
    },
    {
        "question": """
直接动手完成，不需要制定阶段计划。
在当前工作区下新建 eval-sandbox 目录，并在其中创建 stats.py，实现函数 summarize(numbers)，返回一个字典，包含 count（元素个数）、sum（总和）、max（最大值）、min（最小值）四个键；当 numbers 为空列表时，四个值都返回 0。
写完后用 Shell 实际运行一次自测，分别打印 summarize([3, 1, 4, 1, 5]) 和 summarize([]) 的结果。
回答中必须贴出 stats.py 的完整代码，以及两次真实运行的输出。
""",
        "expectedAnswer": """
stats.py 实现 summarize(numbers)：对 [3, 1, 4, 1, 5] 返回 {"count": 5, "sum": 14, "max": 5, "min": 1}；对空列表返回 {"count": 0, "sum": 0, "max": 0, "min": 0}。
""",
        "scoringRequirements": """
① 回答包含 stats.py 的完整代码，缺失最高给 50 分；
② 包含两次真实运行的输出，只声称"已测试"但没有输出最高给 70 分；
③ [3,1,4,1,5] 的结果必须为 count=5、sum=14、max=5、min=1，每错一项扣 15 分；
④ 空列表必须返回四个 0 且不抛异常，抛异常或返回 None 扣 20 分。
""",
    },
    {
        "question": """
直接动手完成，不需要制定阶段计划。
用 Shell 工具完成下面两件事，并在回答里贴出你实际执行的命令和原始输出：
1) 计算 1 到 100 所有整数之和；
2) 打印当前工作目录的绝对路径。
""",
        "expectedAnswer": """
1 到 100 之和 = 5050。
当前工作目录为工作区配置的目录 D:/zhuomian/test_workspace（显示为 D:\\zhuomian\\test_workspace 或 /d/zhuomian/test_workspace 均算正确）。
""",
        "scoringRequirements": """
① 求和结果必须是 5050，错误则最高给 50 分；
② 工作目录路径应与工作区配置一致（D 盘 zhuomian/test_workspace），路径分隔符写法不同不扣分；
③ 回答必须包含真实执行的命令与输出，只给结论不给输出最高给 70 分。
""",
    },
    {
        "question": """
直接动手完成，不需要制定阶段计划。
在 eval-sandbox 目录下创建三个 Python 文件：a.py 定义函数 add(a, b) 返回两数之和；b.py 定义函数 sub(a, b) 返回两数之差；c.py 定义函数 mul(a, b) 返回两数之积。
然后在 eval-sandbox 目录范围内搜索函数定义，回答：add、sub、mul 三个函数分别位于哪个文件？除了它们，这个目录里还有没有其他函数定义？
把搜索命令和输出贴出来。
""",
        "expectedAnswer": """
add 在 a.py、sub 在 b.py、mul 在 c.py。
若此前已跑过用例 2（存在 stats.py），则目录内还有 summarize（在 stats.py）；否则没有其他函数定义。
""",
        "scoringRequirements": """
① 三个文件均被创建（过程或回答中可见写入动作）；
② 使用搜索/读取能力得出结论，而非凭空回答；
③ 三个函数的文件归属必须正确（add→a.py、sub→b.py、mul→c.py），每错一项扣 20 分；
④ 对"是否还有其他函数定义"的回答与目录实际情况一致即可，不强制。
""",
    },
    {
        "question": """
直接动手完成，不需要制定阶段计划。
在 eval-sandbox 目录下创建 wordcount.py，实现函数 count_words(text)：按空格切分，返回单词数量；空字符串返回 0。
然后修改这个文件，为函数增加一个参数 min_len（默认值 2）：长度小于 min_len 的单词不计入。
修改完成后用 Shell 实际运行三次并贴出输出：count_words("a bb ccc")、count_words("")、count_words("a bb ccc", min_len=1)。
回答中贴出最终的完整代码。
""",
        "expectedAnswer": """
count_words("a bb ccc") = 2（"a" 长度 1 被忽略，bb、ccc 保留）
count_words("") = 0
count_words("a bb ccc", min_len=1) = 3（三个词全部计入）
""",
        "scoringRequirements": """
① 最终代码含 min_len 参数且默认值为 2，缺失最高给 50 分；
② 三次输出分别为 2、0、3，每错一个扣 15 分；
③ 体现是在已有文件上修改（而非另建无关文件）；
④ 贴出的代码应可直接运行，明显语法错误扣 30 分。
""",
    },
]


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        try:
            datasets = client.get("/api/evaluations/datasets")
            datasets.raise_for_status()
        except httpx.HTTPError as error:
            raise SystemExit(
                f"无法连接后端（{BASE_URL}）：{error}\n请先启动后端（uv run uvicorn app.main:app --port 8001）再运行本脚本。"
            ) from None

        if any(row["name"] == DATASET_NAME for row in datasets.json()):
            print(f"数据集「{DATASET_NAME}」已存在，跳过录入。如需重建请先在评测页删除它。")
            return

        created = client.post(
            "/api/evaluations/datasets",
            json={"name": DATASET_NAME, "description": DATASET_DESCRIPTION},
        )
        created.raise_for_status()
        dataset_id = created.json()["id"]
        print(f"已创建数据集「{DATASET_NAME}」id={dataset_id}")

        for index, case in enumerate(CASES, start=1):
            response = client.post(
                f"/api/evaluations/datasets/{dataset_id}/cases", json=case
            )
            response.raise_for_status()
            print(f"  用例 {index} 已录入")

        print(f"完成：共 {len(CASES)} 条。打开评测页，选择该数据集即可建任务。")


if __name__ == "__main__":
    main()
