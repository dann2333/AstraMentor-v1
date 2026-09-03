# AstraMentor 数据归属、角色与作业体系设计

日期：2026-09-03
状态：已实现

## 1. 背景与目标

上一轮改造引入了账号与登录（PR #1），但只有账号本身进了 SQLite。真正的学习数据仍然停留在旧模型上：

1. **学习数据没有归属。** 星图与学习者状态以 JSON 文件保存在 `test_data/`，文件名只由 topic（必要时加 course_id）决定。任何人访问 `/api/graph/save?topic=递归` 都会写到同一个文件，也能读到别人的内容。
2. **会话历史有两套后端。** SQLite 版 `/api/me/sessions` 按账号隔离，但前端实际调用的是完全匿名的文件版 `/api/sessions`。两份数据、两种隔离语义。
3. **文档模式互相串号。** `doc_id` 是 PDF 文件内容的 MD5，两个账号上传同一份教材会得到同一个 `doc_id`，于是共用一份上下文缓存、一个磁盘文件；其中一人删除会连带删掉另一人的。
4. **没有角色，也没有师生关系。** 代码里的 `TeacherAgent` 是"讲课的 AI"，不是"人类老师"。系统里不存在教师账号、班级、学生名册。
5. **没有作业。** 无法布置任务、收取提交或给出成绩。

本次改造的目标是：**所有持久化数据都有明确归属**，并在此基础上建立**真实的师生关系与作业闭环**。

## 2. 关键设计决策

### 2.1 访客也要有归属，而不是"没有归属"

前端此前没有登录界面，直接要求全站登录会让应用不可用。但"未登录 = 不隔离"又会退回原来的问题。

采用的方案是：迁移时写入一行**预留的访客账号**（`id = 'anonymous'`，用户名 `__anonymous__`）。

- 未登录请求的 `owner_id` 就是这一行的 id，因此访客数据同样带归属，与任何真实账号互相隔离；
- 外键与级联行为对访客数据同样成立；
- 该行 `is_active = 0` 且 `password_hash` 是无法解析的 `'!'`，因此**永远签发不出令牌、也登录不进去**；用户名以下划线开头，不符合 `USERNAME_PATTERN`，注册接口也占不到；
- `list_users` / `count_users` / `authenticate` 都排除系统账号，管理员看不到它，也无法停用或删除它。

配套 `ASTRA_ALLOW_ANONYMOUS` 开关：设为 `false` 后未携带令牌的请求一律 401，一键切换为强制登录部署。班级与作业接口**不受此开关影响，始终要求登录** —— 访客身份是共享的，无法用来记名。

### 2.2 无效令牌必须 401，不能降级成访客

`get_optional_user` 只在**完全没有** `Authorization` 头时返回 `None`。带了但无效（过期、被吊销、账号停用）一律报错。

否则令牌过期后，用户界面上仍显示已登录，而写入的数据却悄悄落进了共享的访客空间——这类"静默降级"造成的数据错位比一个明确的 401 难排查得多。

### 2.3 存储键：从"文件路径"到"(owner_id, scope_key)"

原先靠文件名做隔离，因此必须防路径穿越（`_data_path` 里有 `relative_to` 检查）。改为 SQLite 后，隔离由主键承担：

```text
knowledge_graphs (owner_id, scope_key)   scope_key = "graph:<course_id>_<sha256(topic)[:16]>"
learner_states   (owner_id, scope_key)   scope_key = "state:..." 或 "state:default"
documents        (owner_id, doc_id)
user_sessions    (user_id,  session_id)
```

`_scoped_topic` 的原有逻辑（有课程时用 `course_id + topic 摘要`，否则走 legacy 清洗）完整保留，只是结果不再拼成路径。恶意 topic 现在是一个无害的标量键，而不是需要防御的路径片段。

### 2.4 LearnerState 的后端可替换

`LearnerState._auto_save()` 在业务代码里有二十多处调用点，散布在 `learning_service.py`、`api.py`、`doc_api.py`。逐个改写调用点风险高、收益低。

改为在 `LearnerState` 内部引入一个只有 `read()` / `write()` 两个方法的 `LearnerStateStore` 协议：

- 服务端注入 `SqlLearnerStateStore(owner_id, scope_key)`；
- CLI 与离线脚本仍用 `JsonFileStateStore`，但写入改成"临时文件 + `os.replace`"，避免进程中途退出留下半个 JSON；
- 两者都不给时状态只存在内存里。

结果是**二十多处调用点一行未改**，而持久化行为完全换掉了。

同时给 `KnowledgePoint.history` 加了 200 条上限：整份状态是一个 JSON blob，每次评分都整体重写，历史无上限时这个 blob 会随使用无限膨胀。

### 2.5 授权集中在服务层，并且不泄露存在性

班级与作业的每个读写方法都要求调用者身份，SQL 一律带归属条件。**无权访问与不存在返回同一种错误**：

- 另一个老师访问你的班级 → `ClassroomNotFound`（404），不是 403；
- 学生访问未发布的作业 → `AssignmentNotFound`（404），不泄露"存在一份草稿"；
- 非本班学生访问作业 → 同样 404。

id 都是 uuid4 十六进制，本身不可枚举；统一 404 让状态码也不成为探测手段。

### 2.6 学生的请求体里不存在评分字段

防篡改不靠"记得过滤"，靠**模型层面就没有这个字段**：

- `SubmitAssignmentRequest` 只有 `content` 和 `session_id`；
- `GradeSubmissionRequest` 只有 `score` 和 `feedback`，且挂在 `require_teacher` 后面；
- 两者都是 `extra="forbid"` —— 多传一个字段直接 422，而不是被静默丢弃。

`RegisterRequest` 同理：`role` 是 `Literal["student", "teacher"]`，`admin` 在类型层面就传不进来；`allow_admin_role` 只在服务层内部可用，HTTP 路由永远不传。

### 2.7 邀请码的防护

邀请码只有 8 位（32 字符表，约 10^12 组合），必须防枚举：

- 按账号限速：15 分钟窗口内最多 10 次失败，超出返回 429 + `Retry-After`；
- **格式错误也计入**，否则用非法格式刷接口就能绕过计数；
- 成功入班清空计数；
- 老师可随时换码，旧码立即失效（用于码泄露后止损）；
- 字符表去掉了 `I`/`O`/`0`/`1`，邀请码要能口头念给学生；比对时大小写不敏感、忽略分隔符。

## 3. 数据模型

迁移以 `PRAGMA user_version` 驱动，只追加不改历史条目。

**v2 —— 归属**

```sql
ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'student';
INSERT OR IGNORE INTO users (...) VALUES ('anonymous', '__anonymous__', ..., '!', 0, ...);

CREATE TABLE knowledge_graphs (owner_id, scope_key, topic, course_id, payload, ...,
                               PRIMARY KEY (owner_id, scope_key));
CREATE TABLE learner_states   (owner_id, scope_key, payload, ...,
                               PRIMARY KEY (owner_id, scope_key));
CREATE TABLE documents        (owner_id, doc_id, filename, total_pages, chunk_count,
                               payload, ..., PRIMARY KEY (owner_id, doc_id));

-- 首页历史栏要用的字段改存列，避免列表 20 条会话就读 20 份最大 4MB 的 payload
ALTER TABLE user_sessions ADD COLUMN last_node_id   TEXT;
ALTER TABLE user_sessions ADD COLUMN last_node_name TEXT;
ALTER TABLE user_sessions ADD COLUMN current_step   INTEGER;
ALTER TABLE user_sessions ADD COLUMN total_steps    INTEGER;
ALTER TABLE user_sessions ADD COLUMN average_mastery REAL NOT NULL DEFAULT 0.0;
```

**v3 —— 班级与作业**

```sql
CREATE TABLE classrooms              (id, teacher_id→users, name, description,
                                      join_code UNIQUE, is_archived, ...);
CREATE TABLE classroom_members       (classroom_id→classrooms, student_id→users, joined_at,
                                      PRIMARY KEY (classroom_id, student_id));
CREATE TABLE classroom_join_attempts (user_id→users PRIMARY KEY, attempts, window_started_at);
CREATE TABLE assignments             (id, classroom_id→classrooms, title, instructions,
                                      target_kind, target_topic, target_course_id, target_node,
                                      due_at, max_score, is_published, ...);
CREATE TABLE assignment_submissions  (id, assignment_id→assignments, student_id→users,
                                      content, session_id, status, is_late, submitted_at,
                                      score, feedback, graded_by→users ON DELETE SET NULL,
                                      graded_at, ..., UNIQUE (assignment_id, student_id));
```

所有归属外键都是 `ON DELETE CASCADE`：删号即清空该账号的全部数据；删班连带清掉成员、作业与提交。`graded_by` 用 `SET NULL`，老师离职不该销毁学生的成绩。

## 4. 接口

学习类接口全部注入 `get_owner_id`（登录用账号 id，未登录用访客 id）。班级与作业接口如下：

```text
POST   /api/classrooms                                    老师建班
GET    /api/classrooms/taught                             老师的班级（含邀请码）
GET    /api/classrooms/enrolled                           学生在读的班级（不含邀请码）
GET    /api/classrooms/{id}                               老师或在册学生可见
PATCH  /api/classrooms/{id}                               改名 / 归档
DELETE /api/classrooms/{id}                               删班
POST   /api/classrooms/{id}/join-code/rotate              换邀请码
GET    /api/classrooms/{id}/members                       名册（仅本班老师）
DELETE /api/classrooms/{id}/members/{student_id}          移出班级
GET    /api/classrooms/{id}/progress                      班级完成度面板
POST   /api/classrooms/join                               学生凭码入班
POST   /api/classrooms/{id}/leave                         学生退班

POST   /api/classrooms/{id}/assignments                   布置作业
GET    /api/classrooms/{id}/assignments                   老师视角（含草稿与统计）
PATCH  /api/assignments/{id}                              改作业
DELETE /api/assignments/{id}                              删作业
GET    /api/assignments/{id}/submissions                  全班提交（仅本班老师）
PUT    /api/assignments/{id}/submissions/{student_id}/grade   批改

GET    /api/me/assignments                                学生的作业清单（含自己的提交）
GET    /api/me/assignments/{id}                           作业详情
PUT    /api/me/assignments/{id}/submission                提交 / 重交
GET    /api/me/assignments/{id}/submission                查看自己的提交与成绩

GET    /api/admin/users                                   管理员：账号列表
PUT    /api/admin/users/{id}/role                         管理员：授予角色（admin 的唯一来源）
```

## 5. 作业语义

- **逾期不拒收**，只打 `is_late` 标记交给老师判断。硬性拒收会把"网络卡了三十秒"变成零分。
- **重新提交会清空既有分数与评语**：那份评分针对的是旧答案，留着会让学生误以为新答案已被认可。
- **`score` 传 `null` 表示撤回分数**，状态回到 `submitted`，但评语保留 —— 支持"先给意见、让学生重做"。
- **草稿（`is_published = 0`）对学生完全不存在**，连"有一份未发布作业"都不泄露。

## 6. 前端

- 令牌存在 `localStorage`，由 axios 请求拦截器统一挂上；SSE 走的是 `fetch`、绕开拦截器，因此单独复用同一份头部构造函数。
- 响应拦截器遇到 401 立即清掉本地令牌并退出登录状态，与 2.2 的后端策略对齐。
- 会话历史与学习状态的加载依赖 `user?.id`：登录、切号、退出都会换掉数据归属，必须重新拉取，否则界面上留着的是上一个身份的数据。
- 班级工作台按角色分成老师侧与学生侧，但**真正的授权在后端** —— 前端少判一次条件，接口也只会返回 403/404，不会漏数据。

## 7. 界面：iOS 风格液态玻璃

### 7.1 为什么不换 Flutter

这套界面的核心是 `@antv/g6` 星图（含 3D 扩展）、Monaco 编辑器、
`react-markdown` + KaTeX + 语法高亮，以及 SSE 流式渲染。换 Flutter 等于把这些
全部重写，而其中几项在 Flutter 生态里并无对等实现。Flutter Web 又通过 canvas
出字，文字清晰度与无障碍都明显更差。而毛玻璃本身就是 Web 原生能力
（`backdrop-filter`），没有理由为它绕开整个技术栈。

### 7.2 材质由四件事构成

常见的"毛玻璃"只做了模糊。真正让它成立的是四件事同时发生：

| 层 | 做法 |
| --- | --- |
| 折射 | SVG `feDisplacementMap` + 径向遮罩，只在边缘挤压背后的画面，中间保持清晰 |
| 高光 | 顶亮边 + 底暗边，外加跟随指针的镜面柔光 |
| 景深 | `blur` + `saturate(180%)` + `brightness(1.06)`，让背后的颜色透上来而不是灰掉 |
| 投影 | 三层柔和阴影，把面板从背景里抬起来 |

只做第三件就是廉价毛玻璃。折射是"有厚度的玻璃"与"整块高斯模糊"的分界线。

另有两件配套的事：细颗粒噪点去掉塑料感；背景放几团缓慢漂移的氛围光 ——
毛玻璃需要背后有东西可透，纯色背景上再精细的材质也看不出来。

材质分三档（`thin` / `regular` / `thick`），对应元件离用户的远近。
所有变量都基于既有主题色，因此暗色与护眼亮色两套主题共用一套定义。

### 7.3 落地方式

改的是共享原语（按钮、输入框、文本域、卡片、对话框），因此全站几十处调用点
一行未改就换了材质。动效统一用 Motion（framer-motion v13）的弹簧参数，
包含页面转场、模态进出、列表交错入场与按压涟漪，并完整支持
`prefers-reduced-motion`。

指针高光走 CSS 变量而不是 React state：它每一帧都在变，走 state 会让整棵子树
跟着重渲染。

### 7.4 过程中修掉的三个坑

1. **全局 `border-radius: 2px !important`**（上一版像素风的兜底）会盖掉每一个
   玻璃元件的圆角，配套的 `translate(2px,2px)` 按下效果也会顶掉弹性按压。
2. **`glass.css` 一开始没有放进 `@layer`。** 无层级规则胜过任何 Tailwind 工具类，
   于是 `.glass` 的 `position: relative` 把对话框的 `fixed` 顶掉，模态直接掉到
   页面底部。放进 `@layer components` 后工具类恢复优先。
3. **模态居中偏移翻倍。** 关键帧里写了 `transform: translate(-50%,-50%)`，而
   Tailwind v4 的 `-translate-x-1/2` 用的是独立的 `translate` 属性，两者相加。
   关键帧改为只管缩放与纵向位移。

后两条都是只有把页面真正跑起来、量出元素坐标才会暴露的问题。

## 8. 已知取舍

- **旧的 JSON 数据不自动迁移。** `test_data/*.json` 与 `user_data/sessions/*.json` 在改造后不再被读取。这些文件本来就没有归属信息，无法判断该归给谁；强行归给某个账号是猜测，归给访客则等于公开。文件仍在磁盘上，需要时可人工导入。
- **学习者状态仍是整块 JSON 写入。** 改成按知识点分行会牵动评分与教学计划的读写路径，收益不足以抵消风险。当前实现至少把"写一半留下坏文件"变成了单行 UPSERT 的原子写入，并给历史加了上限。
- **邀请码限速是按账号的，不是按 IP。** 未登录无法调用入班接口，因此攻击者必须先注册；注册本身另有限制。按 IP 限速需要引入反向代理信息，留给部署层。
