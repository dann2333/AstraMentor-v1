# AstraMentor 数据归属、角色与作业体系实施记录

日期：2026-09-03
对应设计：`docs/superpowers/specs/2026-09-03-astramentor-ownership-roles-assignments-design.md`

## 1. 改动清单

### 新增

| 文件 | 职责 |
| --- | --- |
| `services/learning_store.py` | 星图 / 学习者状态 / 文档上下文的账号级仓库；`SqlLearnerStateStore` 把 `LearnerState` 接到 SQLite |
| `services/classroom_service.py` | 班级、成员关系、邀请码与限速；授权集中在这一层 |
| `services/assignment_service.py` | 作业、提交、批改与班级完成度聚合 |
| `backend/classroom_api.py` | 班级与管理员路由 |
| `backend/assignment_api.py` | 作业路由（老师侧与学生侧分开） |
| `frontend/src/api/auth.ts` | 令牌存取与鉴权接口 |
| `frontend/src/api/classrooms.ts` | 班级与作业接口 |
| `frontend/src/contexts/AuthContext.tsx` | 登录态 |
| `frontend/src/features/auth/AuthDialog.tsx` | 登录 / 注册对话框 |
| `frontend/src/features/auth/AccountMenu.tsx` | 头部账号入口 |
| `frontend/src/features/classroom/ClassroomWorkspace.tsx` | 班级工作台 |

### 删除

| 文件 | 原因 |
| --- | --- |
| `services/session_repository.py` | 与 `UserDataRepository` 重复的第二套会话后端，且完全没有归属概念 |

### 重要修改

- `services/database.py` —— `SCHEMA_VERSION` 1 → 3，两组新迁移；访客归属行常量与角色常量。
- `services/account_service.py` —— `User.role`、`register(role=...)`、`set_role()`、系统账号保护、列表与登录排除系统账号。
- `core/learner_state.py` —— `LearnerStateStore` 协议、`JsonFileStateStore`（原子写）、`MAX_HISTORY_ENTRIES`。
- `services/learning_service.py` —— 构造函数收 `owner_id` 与 `store`；`_graph_file`/`_state_file`/`_data_path` 换成 `_graph_scope`/`_state_scope`。
- `backend/api.py`、`backend/doc_api.py` —— 每个路由注入 `get_owner_id`；`get_service()` 首参改为 `owner_id`。
- `backend/session_api.py` —— 改用 SQLite 仓库并按归属隔离。
- `backend/dependencies.py` —— `get_optional_user` / `get_owner_id` / `require_teacher` / `require_admin`。
- `frontend/src/api/client.ts`、`stream.ts` —— 令牌透传与 401 处理。

## 2. 实施顺序与理由

1. **先落迁移**，并**双向验证**：全新库直接建到 v3；同时构造一个带真实数据的 v1 库再升级，确认数据未丢、新表齐全。归属改造的其他部分都压在这一步上，它出错后面全错。
2. **再改 `LearnerState`**，因为它决定了 `learning_service` 能怎么写。选择"可替换后端"而不是"直接改成 SQL"，正是为了让那二十多处 `_auto_save()` 调用点不动。
3. **然后是路由注入**。用 AST 静态检查兜底，确认每个挂了 `@router` / `@doc_router` 的处理函数都声明了归属依赖，且声明了就一定用到 —— 手工逐个核对 30 多个路由太容易漏。
4. **最后才是班级与作业**，它们依赖角色字段，而角色字段是第 1 步引入的。
5. **前端放在后端全绿之后**，避免在两端同时不确定的情况下调试。

## 3. 验证

### 后端

```
python -m pytest tests/ -q
→ 178 passed, 236 subtests passed
```

新增测试文件：

- `tests/test_classrooms.py` —— 22 例：邀请码格式与唯一性、换码、跨老师授权、学生越权、限速（含"格式错误也计入"与"窗口过期重置"）、级联删除。
- `tests/test_assignments.py` —— 25 例：目标字段自洽性、跨老师越权、草稿对学生不可见、重交作废旧评分、逾期标记、分数边界、聚合统计、级联。
- `tests/test_classroom_api.py` —— HTTP 层：状态码、字段可见性、多余字段是否被静默接受、跨老师一律 404。
- `tests/test_ownership_api.py` —— 同一 URL 换个令牌必须看到不同数据；访客与账号互不可见；无效令牌 401 而非降级。

重写的测试文件：

- `tests/test_sessions.py` —— 从文件仓库改为 SQLite 仓库，并补上跨账号隔离与随账号级联删除。
- `tests/test_course_scope.py` —— 原来断言的是文件路径不同；现在断言的是**存储键相同但 owner 不同时数据仍然隔离**，这是更强的保证。

### 前端

```
npx tsc -b        → 无错误
npm run lint      → 无错误、无告警
npm test          → 8 files, 37 tests passed
npm run build     → 构建通过
```

### 端到端

用 `TestClient` 跑通完整链路：老师注册 → 建班 → 学生注册 → 凭码入班 → 布置作业 → 提交 → 批改 → 学生查分 → 班级完成度；同时验证同一 `session_id` 在老师、学生、访客三个身份下是三条互不可见的记录。

### 专项验证

- **迁移**：v1（含真实用户行）→ v3 升级后数据完整、新表齐全、`user_version = 3`。
- **邀请码撞车**：注入一个必定重复的码生成器，确认同一事务内的重试真的能成功建班（SQLite 的约束冲突是语句级回滚，事务仍可用）。
- **聚合统计**：2 名学生 × 2 份已发布作业 + 1 份草稿，逐字段核对 `published_assignments` / `submitted_count` / `graded_count` / `average_score`，确认 `LEFT JOIN` 聚合没有把草稿或未提交算进去。
- **并发**：8 个线程对 4 个账号同时写学习状态与会话快照，无异常、无跨账号串写。

## 4. 顺带修复的既有缺陷

- `main.py` 的 CLI 构造函数传的是 `LearningService(state_file=...)`，而该构造函数从来没有 `state_file` 参数 —— CLI 入口一直是坏的。
- `/api/doc/graph/save` 恒返回 `{"status": "success"}`，实际什么都不写（原为 TODO）。现在会真正保存，并先确认该文档属于调用者。
- `requirements.txt` 缺 `httpx`，干净环境下接口测试直接收集失败（已在前一个提交修复）。

## 5. 后续可做

- 旧 JSON 数据的人工导入脚本（设计文档 §7 说明了为什么不自动迁移）。
- 作业与星图节点的联动：`target_kind=node` 已经存进去了，但前端还没有"点作业直接跳到对应星图节点"的入口。
- 按 IP 的注册与入班限速，需要部署层提供可信的客户端地址。
