# 决策清单与底座同步手册

> 日期：2026-09-20
> 适用范围：mini-coding-agent（本项目）与底座 mini-agent（`D:\zhuomian\mini-agent`）之间的关系管理。
> 用途：第一部分记录已经定下、不要反复推翻的决策；第二部分是每次底座更新要合并（merge）时的操作清单，动手前照着过一遍。

---

## 一、当前已定的重要决策（2026-09-20）

1. **项目归属**：coding agent 是**独立项目**（`D:\zhuomian\mini-coding-agent`），不是 mini-agent 的子分支或子目录。mini-agent 的 `main` 定格为底座，只读不动。
2. **派生方式**：用 `git clone`（完整复制，保留全部提交历史），不用 `git archive`（会撕掉历史）。原因：需要练 git 分支/合并，历史留着才有东西可看、可练。
3. **血缘连接**：clone 自带的 `origin`（指向 mini-agent）**暂时保留**，作为以后底座更新的"上游管道"；什么时候想彻底断开，随时 `git remote remove origin`。
4. **分支作废**：mini-agent 里的 `specialized-agent` 分支（与 main 同 commit、零改动）没有存在必要，应删除：`git branch -d specialized-agent`。
5. **练习场地**：所有分支/合并/冲突练习都在本项目（mini-coding-agent）里进行，mini-agent 不再动。
6. **新项目落地三件事**（clone 后立刻做）：
   - 改项目名：`README.md`、`AGENTS.md`、`backend/pyproject.toml`、`frontend/package.json` 里的 mini-agent → mini-coding-agent
   - 重装依赖：`npm install`（frontend）、`uv sync`（backend）——clone 不带 node_modules/.venv 这些未跟踪文件
   - 派生来源记录：本项目从 mini-agent `main` 的 `869fd50`（init: mini-agent agent 底座，阶段一至十二）clone 派生

---

## 二、底座更新要合并（merge）时的注意事项

合并方向永远固定：**底座 → coding agent**（单向）。不要把 coding agent 的改动合回 mini-agent，除非哪天刻意决定。

### 合并前（准备，一分钟内做完）

1. **工作区必须干净**：`git status` 不能有未提交改动，否则冲突时容易乱。有改动就先 commit 或 stash。
2. **先看再说，别急着合**：

   ```bash
   git fetch origin                          # 只下载底座新提交，不动你的文件
   git log main..origin/main --oneline       # 底座新增了哪些提交
   git diff main...origin/main --stat        # 底座动了哪些文件（重点看改了多少、动了哪些）
   ```

   `fetch` 只是"把对面的包裹取到门口"，不拆包不搬家具；`merge` 才是真的往家里搬。先看清单再决定。

3. **在专门分支上合并**，给后悔留后路：

   ```bash
   git switch -c sync/base-20260301    # 分支名带日期，方便回看
   ```

### 合并中（冲突是正常的，不是事故）

4. **预期必冲突的文件**（两边的"项目身份证"，每次同步大概率撞车）：
   - `AGENTS.md` / `README.md` —— 项目名和约定不同
   - `backend/pyproject.toml`、`frontend/package.json`、`uv.lock`、`package-lock.json`

   **裁决口诀：身份证文件一律保留 coding agent 侧（自己的），只摘取底座的实质代码改动。**
   冲突标记里 `<<<<<<<` 到 `=======` 是你（coding agent），`=======` 到 `>>>>>>>` 是底座新版本。

5. **底座大改时要重点人工核对的文件**：
   - `frontend/src/styles/variables.css` 及组件样式 —— 本项目若改过配色/布局，需逐处裁决
   - `backend/app/services/agent_runtime.py` —— 最大单文件（2000+ 行），底座若重构它，合并后要重点测
   - `alembic/versions/` —— 两边都加过迁移时，**检查迁移链编号是否撞车**，撞了就手工改其中一个的编号，保证链条首尾相接

6. **解不动就整体放弃，重来一次**：`git merge --abort`（回到合并前状态，什么都没丢）。中途发现解错了也可以重来，合并只改当前分支，原提交都在。
7. **练习阶段只用 merge，不用 rebase**。rebase 会改写历史，新手阶段容易把历史搞乱；等 merge 玩熟了再学 rebase。

### 合并后（验证，不能省）

8. **必跑全套检查**（继承自 mini-agent 的 AGENTS.md 约定）：

   ```bash
   cd backend  && uv run pytest
   cd frontend && npm run type-check && npm run test && npm run build
   ```

   测试过了，合并才算完成；再 `git switch main && git merge sync/base-日期` 收进主干。

9. **合并提交信息写明来源**：如 `merge: 同步底座 869fd50 之后的 3 个提交（XXX 功能）`，将来回看历史能知道每次同步带来了什么。

### 长期原则

10. **小步勤同步**：底座每积累几个提交就同步一次，比攒几个月再合轻松得多——差异越大冲突越难解。
11. **合并前先想"要不要"**：底座的更新不一定都适合 coding agent（比如 mini-agent 加了评测模块的新功能，coding agent 用不上）。看第 2 步的 diff 清单时就可以挑拣：全要就整支 merge；只要一部分，就用 `git cherry-pick <提交号>` 单摘。
12. **彻底独立的那一天**：哪天确认不再需要底座同步，`git remote remove origin` 断开管道即可，之后两边彻底各过各的。
