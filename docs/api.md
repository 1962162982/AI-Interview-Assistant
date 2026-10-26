# API 说明（v1 · 契约冻结 2026-10-23）

> **状态：已冻结（Lead 裁决）**。本文档是后端 `backend/api/` 与前端 `frontend/`、各模块服务之间的**唯一 HTTP 契约**。
> 阶段 1：除 `GET /health`、`GET /api/meta/routes` 外，四组业务路由均为 **501 薄壳占位**；T2（A/B）、T3（C）、T4（D）按此契约实现。
> Python 内部契约（`model_router` / `context_manager` / `parser`）见文末“六、Python 内部契约”。

## 一、通用约定

- **Base 前缀**：所有业务接口以 `/api` 开头；`GET /health` 保持骨架期原样（探活）。
- **统一响应壳**（所有 `/api` 接口，成功与错误一致）：

```json
{"code": 0, "message": "ok", "data": {}}
```

- `data`：成功时为对象/数组；错误时为 `null`。
- HTTP 状态码与 `code` 同时表达语义（便于前端统一拦截与日志排查）。

| code | HTTP | 含义 | 典型场景 |
| --- | --- | --- | --- |
| 0 | 200 | 成功 | 正常返回 |
| 1001 | 400 | 参数校验失败 | 缺字段 / 类型错误 / 空输入 |
| 1002 | 400 | 文件格式或解析失败 | 非 PDF/DOCX、损坏文件、扫描件降级提示 |
| 2001 | 502 | 模型调用失败 | 超时 / 限流 / 鉴权失败（重试耗尽） |
| 2002 | 502 | 模型输出 JSON 校验失败 | 修复重试后仍不符合 Schema |
| 2003 | 501 | 模块未实现 | 阶段 1 薄壳占位 |
| 3001 | 404 | 会话 / 报告不存在 | 无效 session_id / report_id |
| 5000 | 500 | 内部错误 | 未捕获异常 |

- **鉴权**：本地课程项目，暂无鉴权头；严禁任何响应 / 日志 / 文档出现 API Key。
- **CORS**：开发期默认放行 `http://localhost:5173`、`http://127.0.0.1:5173`、`http://localhost:3000`、`http://127.0.0.1:3000`；可用环境变量 `CORS_ORIGINS`（逗号分隔）覆盖。
- **深度思考开关（可选请求头）**：调用模型的 `/api` 请求可携带 `X-Thinking-Mode: enabled | disabled`（不区分大小写），
  仅对**本次请求**生效；缺省时使用服务端路由配置（临时全 DeepSeek 配置默认 `disabled`）。
  开启后响应更慢、消耗更多 token，但复杂任务可能更准确。前端顶栏「深度思考」开关会自动附加该请求头；SSE 流式请求同样适用。
- **错误示例**：

```json
{"code": 2003, "message": "简历解析尚未实现（阶段 1 薄壳占位，由 T2 负责）", "data": null}
```

## 二、元信息

### GET /health

探活接口（不属于 `/api`，保持骨架期契约不变）。

```json
{"status": "ok"}
```

### GET /api/meta/routes

返回 5 条模型路由的**非敏感**摘要（不含 Key / 不含 Key 环境变量值），供前端 / 联调自查“模块→模型”分配（决策④）。

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "routes": [
      {"route": "resume_diagnosis", "model": "deepseek-chat", "temperature": 0.3,
       "context_window_tokens": 65536, "stream": false, "timeout": 60, "max_retries": 2},
      {"route": "jd_matching", "model": "glm-4-air", "temperature": 0.2,
       "context_window_tokens": 131072, "stream": false, "timeout": 60, "max_retries": 2},
      {"route": "interview_agent", "model": "glm-4-flash", "temperature": 0.8,
       "context_window_tokens": 131072, "stream": true, "timeout": 120, "max_retries": 3},
      {"route": "evaluation", "model": "glm-4-plus", "temperature": 0.1,
       "context_window_tokens": 131072, "stream": false, "timeout": 60, "max_retries": 2},
      {"route": "cross_review", "model": "deepseek-chat", "temperature": 0.1,
       "context_window_tokens": 65536, "stream": false, "timeout": 60, "max_retries": 2}
    ]
  }
}
```

## 三、模块 A：简历诊断（`backend/api/resume.py`，实现：T2）

### POST /api/resume/parse

上传 PDF / DOCX 简历，返回纯文本 + 分段。解析失败（损坏 / 不支持格式）→ `1002`；扫描件 / 乱码 → `code=0` 且 `data.degraded=true`，前端据此提示“改用粘贴文本”。

请求：`multipart/form-data`，字段 `file`（`.pdf` / `.docx`）。

响应 `data`：

```json
{
  "filename": "resume.docx",
  "kind": "docx",
  "text": "张三\n教育经历\n……",
  "segments": [{"heading": "教育经历", "text": "某大学 计算机科学与技术 2022–2026"}],
  "page_count": null,
  "char_count": 512,
  "degraded": false,
  "warnings": []
}
```

### POST /api/resume/structure

简历纯文本 → `ResumeStructured`（`backend/schemas/resume_structured.py`）。

请求：

```json
{"resume_text": "……"}
```

响应 `data`：`ResumeStructured` 对象（`name / education[] / experiences[] / skills[] / projects[]`）。

### POST /api/resume/diagnose

简历纯文本 → 诊断报告（检查清单式，Prompt：`prompts/resume_diagnosis.md`）。

请求：

```json
{"resume_text": "……", "target_role": "后端开发实习生"}
```

`target_role` 可选。响应 `data`（**数据键已冻结**）：

```json
{
  "summary": "整体评价 1–3 句",
  "issues": [
    {
      "check_item": "量化表达",
      "priority": "high",
      "original_quote": "负责优化接口性能",
      "problem": "缺少可量化结果",
      "rewrite": "负责优化订单接口，P99 从 800ms 降至 120ms"
    }
  ],
  "strengths": ["项目经历与岗位匹配"]
}
```

`check_item` 取值建议固定为 5 项检查清单：`量化表达` / `动词弱化` / `关键词对齐` / `结构问题` / `错别字格式`；`priority` 为 `high` / `medium` / `low`。

## 四、模块 B：JD 匹配（`backend/api/jd.py`，实现：T2）

### POST /api/jd/structure

JD 纯文本 → `JDStructured`（两段式第一步，低温抽取）。

请求：`{"jd_text": "……"}`；响应 `data`：`JDStructured`（`title / responsibilities[] / capabilities[]`）。

### POST /api/jd/match

简历与 JD 逐项对齐评分（两段式第二步）。简历可传已结构化对象，也可直接传文本（服务端自行抽取）。

请求（二选一，推荐传结构化）：

```json
{
  "resume": {"name": "张三", "education": [], "experiences": [], "skills": [], "projects": []},
  "jd": {"title": "后端开发实习生", "responsibilities": [], "capabilities": []}
}
```

或：

```json
{"resume_text": "……", "jd_text": "……"}
```

响应 `data`（**数据键已冻结**）：

```json
{
  "score": 78,
  "summary": "整体匹配情况 1–3 句",
  "items": [
    {"capability": "Python 后端开发", "status": "matched", "evidence": "简历中……", "comment": "有 2 段相关项目"}
  ],
  "gaps": [
    {"capability": "高并发经验", "gap": "简历未体现 QPS / 压测", "suggestion": "补充压测数据或项目背景"}
  ]
}
```

`score` 为 0–100 整数；`status` 取值 `matched` / `partial` / `missing`。

## 五、模块 C / D：模拟面试与报告（实现：T3 / T4）

### POST /api/interview/sessions

创建面试会话（场景三选一，**默认 `intern`**；`intern` / `autumn` / `grad` 三个场景全部实现）。

请求：

```json
{
  "resume_text": "……",
  "jd_text": "……",
  "scene": "intern",
  "company": "某科技公司",
  "role": "后端开发实习生",
  "style": "friendly"
}
```

`jd_text` / `company` / `role` / `style` 均可选；`scene` 必须为 `intern` | `autumn` | `grad`，缺省 `intern`。

响应 `data`：

```json
{
  "session_id": "sess_20261023_ab12cd",
  "question": {"question_id": "q1", "text": "请先做个自我介绍", "direction": "self_intro", "follow_up_depth": 0},
  "state": {"turn_index": 0, "scene": "intern", "asked_questions": [], "context_usage_pct": 2}
}
```

### POST /api/interview/sessions/{session_id}/turns

提交候选人回答，返回下一题（非流式）。

请求：`{"answer": "……"}`

响应 `data`（**数据键已冻结**）：

```json
{
  "session_id": "sess_20261023_ab12cd",
  "turn_index": 1,
  "question": {"question_id": "q2", "text": "你刚才提到缓存，为什么选 Redis？", "direction": "tech_probe", "follow_up_depth": 1},
  "state": {"asked_questions": ["q1", "q2"], "rule_id": "tech_probe", "context_usage_pct": 8}
}
```

### POST /api/interview/sessions/{session_id}/turns/stream

同上，但以 **SSE** 流式返回。响应头 `Content-Type: text/event-stream`；每行一个 `data:` JSON 信封，**统一使用 `event` 字段**：

```
data: {"event":"delta","text":"你"}
data: {"event":"delta","text":"好"}
data: {"event":"done","question":{"question_id":"q2","text":"……"},"state":{"turn_index":1},"usage":{"prompt_tokens":120,"completion_tokens":40,"total_tokens":160}}
```

异常时（HTTP 已开始流式，无法再改状态码）：

```
data: {"event":"error","code":2001,"message":"模型调用失败，请重试","retryable":true}
```

`retryable`：超时 / 限流 / 5xx 为 `true`；参数 / 配置错误为 `false`。

### GET /api/interview/sessions/{session_id}

查询会话状态（刷新页面 / 历史记录用）。

响应 `data`：

```json
{
  "session_id": "sess_20261023_ab12cd",
  "scene": "intern",
  "turn_index": 3,
  "questions_asked": [{"question_id": "q1", "text": "……", "direction": "self_intro"}],
  "summary": "上下文摘要（如有）",
  "context_usage_pct": 18,
  "ended": false
}
```

不存在 → `3001`。

### POST /api/interview/sessions/{session_id}/end

结束面试，返回完整对话记录（供模块 D 评分 / 报告）。

响应 `data`（**数据键已冻结**）：

```json
{
  "session_id": "sess_20261023_ab12cd",
  "transcript": [
    {"turn": 1, "question": "请先做个自我介绍", "answer": "……", "rule_id": "self_intro", "direction": "self_intro", "follow_up_depth": 0}
  ],
  "state": {"turn_index": 5, "context_usage_pct": 30},
  "report_input": {"resume_text": "……", "jd_text": "……", "scene": "intern", "turn_count": 5}
}
```

### POST /api/report/generate

结束面试后生成评分报告（4 维度 + 交叉复核）。

请求（二选一）：

```json
{"session_id": "sess_20261023_ab12cd"}
```

或直接传对话记录：

```json
{"session_id": "sess_20261023_ab12cd", "turns": [{"question": "……", "answer": "……"}]}
```

响应 `data`：

```json
{
  "report_id": "rep_20261023_x1y2z3",
  "evaluation": {
    "dimensions": [{"dimension": "相关性", "score": 8, "evidence": ["……"]}],
    "strengths": ["……"],
    "weaknesses": ["……"],
    "suggestions": ["……"]
  },
  "cross_review": {"agreed": true, "adjustments": [], "deviation_note": "……"},
  "markdown": "# 面试报告\n……"
}
```

`evaluation` 严格使用 `backend/schemas/interview_evaluation.py` 的 `InterviewEvaluation`。

### GET /api/report/{report_id}

按报告 ID 取回完整报告（结构同 `/generate` 的 `data`）。

### GET /api/report/{report_id}/markdown

返回 `text/markdown` 文件下载（`Content-Disposition: attachment; filename="report_{report_id}.md"`）。不存在 → `3001`。

## 六、Python 内部契约（后端模块间，供 T2–T5 对齐）

### 6.1 `backend/services/model_router.py`

```python
from backend.services.model_router import get_router, ChatResult, StreamChunk, JsonChatResult

router = get_router()                       # 进程内单例；首次调用加载 .env 与 models.yaml
cfg = router.get_route_config("resume_diagnosis")   # RouteConfig(model, temperature, timeout, max_retries, context_window_tokens, ...)
result: ChatResult = router.chat("resume_diagnosis", messages, temperature=None, max_tokens=None, response_format=None, extra=None)
for chunk in router.chat_stream("interview_agent", messages): ...   # StreamChunk(delta, index, finish_reason)
parsed: JsonChatResult = router.chat_json("jd_matching", messages, JDStructured, max_repair=1)
parsed.value        # 已通过 Pydantic 校验的对象
parsed.repair_attempts
```

- 五条固定路由：`resume_diagnosis` / `jd_matching` / `interview_agent` / `evaluation` / `cross_review`；配置唯一事实来源 `backend/config/models.yaml`。
- 异常层级：`ModelRouterError` → `RouterConfigError` / `UnknownRouteError` / `MissingAPIKeyError`（报环境变量名，不打印 Key）/ `ModelCallError` / `ModelJSONError`。
- 重试：超时 / 连接 / 429 / 5xx 按 `max_retries` 指数退避；401/403/400 不重试。
- `chat_json`：优先 `response_format={"type":"json_object"}`；供应商不支持时自动去参重试；校验失败把错误回填给模型修复，仍失败抛 `ModelJSONError`（调用方降级纯文本）。
- **同步阻塞**：FastAPI 端点用 `def`（线程池）或 `run_in_threadpool`；流式用生成器 + `StreamingResponse`。
- 测试注入：`ModelRouter(config_path=..., load_env=False, client_factory=lambda cfg, key, base_url: fake_client, sleep_fn=lambda s: None)`。

### 6.2 `backend/services/context_manager.py`

```python
from backend.services.context_manager import ContextManager

ctx = ContextManager.from_route("interview_agent", system_prompt=sys_prompt)  # 窗口取自 models.yaml
ctx.add_user("……"); ctx.add_assistant("……")
ctx.build_messages()          # system（含摘要/关键事实）+ 窗口内对话
ctx.should_compress()         # 默认 90% 阈值
ctx.compress(force=True)      # 滑动窗口 + 摘要（摘要器可注入 LLM，失败自动回退规则式）
ctx.add_pinned_fact("目标岗位：……")
```

### 6.3 `backend/services/parser/`

```python
from backend.services.parser import parse_resume, ParserError

doc = parse_resume("resume.pdf", data)   # 或 parse_resume(path)；也支持 .docx / .txt
doc.text; doc.segments; doc.degraded; doc.warnings; doc.char_count; doc.to_dict()
```

- PDF：pdfplumber 主力（`extract_text` + `extract_tables`），缺依赖时 pypdf 兜底；扫描件 / 乱码 → `degraded=true`。
- DOCX：python-docx 段落 + 表格；纯图片文档 → `ParserError`。
- 异常：`ParserError` / `UnsupportedResumeFormatError` → API 层映射 `1002`。

## 七、前端对接约定（T5）

- 统一拦截：HTTP 非 2xx 或 `code != 0` 均走错误提示，展示 `message`；`code=2003` 显示“该模块开发中”。
- 简历上传：`FormData` 字段名 `file`；`degraded=true` 时提示“解析质量有限，建议粘贴文本”。
- 面试流式：用 `fetch` + `ReadableStream` 读取 SSE 行；按 `event` 字段分发 `delta` / `done` / `error`；`retryable=false` 时不自动重试。
- 报告页：`evaluation.dimensions[].evidence` 用于原文证据高亮；`markdown` 可直接预览 / 下载。

## 八、前端静态托管（阶段 3，可选）

> 用途：答辩 / 演示时由 FastAPI **单进程**同时提供 API 与前端构建产物，无需另起静态服务器。
> **本节不改变接口契约**：契约端点共 15 个（`/health` 1 个 + `/api` 14 个）保持冻结；静态托管路由
> `include_in_schema=False`，不进入 OpenAPI，也不会劫持 `/api`。

启用条件：

- `frontend/dist/index.html` 存在时自动启用（按请求检测，构建产物出现后无需重启后端）；
- `frontend/dist` 不存在时行为与阶段 1 一致：未知路径 404，不影响开发与测试。

路径映射（仅 GET）：

| 请求路径 | 行为 |
| --- | --- |
| `/assets/*` | 返回 `frontend/dist/assets/*`；文件不存在或路径越界 → 404 |
| 其他非保留路径（`/`、`/interview`、`/report/<rid>` 等） | SPA 回退：存在同名文件则返回该文件，否则返回 `frontend/dist/index.html`（`text/html`） |
| `/api/*`、`/health`、`/docs`、`/redoc`、`/openapi.json` | 不接管，保持原有 API / 文档行为 |

演示方式：

```bash
# 1) 构建前端（需 Node/pnpm 环境）
cd frontend && pnpm build
# 2) 单进程启动：http://127.0.0.1:8000/ 即前端页面，/api 为后端接口
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

注意事项：

- 前端真实模式需 `VITE_USE_MOCK=false`，且 `VITE_API_BASE_URL` 指向同一后端（默认 8000）；
- CORS 仍按「一、通用约定」放行 5173 / 3000，可用环境变量 `CORS_ORIGINS` 覆盖；
- 测试使用临时伪造 dist（`tests/test_static_frontend.py`），不依赖真实构建产物。

## 九、变更记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1 | 2026-10-23 | 契约冻结：统一响应壳 / 错误码 / 四组路由 / SSE `event` 信封 / Python 内部契约；阶段 1 除 `/health`、`/api/meta/routes` 外均为 501 占位 |
