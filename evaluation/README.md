# evaluation/ — 核心资产二：Prompt 评测集与强制回归关卡

> 铁律（见 `prompts/README.md`、`说明书.md` §8.3/§9）：
> **任何 Prompt 修改 → 先跑本目录 `run_eval.py` → 分数不降才允许 commit；分数下降 → 回滚。**
>
> **本文件是金标准样本格式的唯一事实来源。格式版本：`schema_version: 1`（2026-10-23 冻结）。**
> 冻结后字段/语义变更须经 Lead 评审，并同步修改 `run_eval.py` 与全部样本。

---

## 1. 目录结构

```
evaluation/
├── README.md                     # 本文件：样本格式 + 回归口径 + 接口契约（唯一事实来源）
├── run_eval.py                   # 回归评测脚本（mock 可无 Key 跑；real 模式对接 model_router）
├── prompt_baselines/             # Prompt 冻结基线快照（v_old）
│   └── scoring_v1.md             # v1 评分 Prompt 冻结副本（勿改）
├── golden_qa/                    # 模块 D：面试 Q&A 黄金样本（JSONL，一行一个样本）
│   ├── qa_intern.jsonl           # 实习场景
│   ├── qa_autumn.jsonl           # 秋招场景
│   └── qa_grad.jsonl             # 保研场景
├── golden_resumes/               # 模块 A：简历诊断黄金样本（JSONL，一行一个样本）
│   ├── resume_intern.jsonl
│   ├── resume_autumn.jsonl
│   └── resume_grad.jsonl
├── conftest.py                   # pytest 配置（sys.path + 工作区内 tmp_dir）
└── test_*.py                     # 本目录 pytest（样本格式 / 回归阈值 / Prompt 契约 / 模块 D 服务）
```

**命名规则**：`golden_qa/qa_<scene>.jsonl`、`golden_resumes/resume_<scene>.jsonl`；
`scene ∈ {intern, autumn, grad}`（实习 / 秋招 / 保研）；一个文件可包含多行样本，
`run_eval.py` 自动加载 `evaluation/golden_*/**/*.jsonl`，新增样本无需改脚本。

---

## 2. 文件格式（JSONL）

- 一行一个 JSON 对象（JSONL），**UTF-8、无 BOM、LF 换行**；空行忽略；JSON 内不允许注释。
- 样本 `id` 全局唯一，命名：`qa-<scene>-<3 位序号>` / `resume-<scene>-<3 位序号>`。
- 样本全部为 **LLM 生成的虚构素材，禁止真人简历/真实个人信息**（脱敏要求同 `data/`）。
- `enabled: false` 为**开关项**：样本保留在库中但不参与评测（用于问题样本下线、A/B 临时剔除），
  默认 `true`；`run_eval.py --include-disabled` 可强制纳入。

### 2.1 通用信封字段（QA 与简历共用）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | int | ✅ | 格式版本，当前固定 `1`；不匹配时 `run_eval.py` 报数据错误（退出码 2） |
| `id` | str | ✅ | 全局唯一样本 ID（见命名规则） |
| `module` | str | ✅ | `evaluation`（模块 D 评分）/ `resume_diagnosis`（模块 A 诊断） |
| `scene` | str | ✅ | `intern` / `autumn` / `grad` |
| `enabled` | bool | ✅ | 开关项；默认语义为 `true` |
| `tags` | str[] | ❌ | 检索/分组标签（题型、技术方向、考察点） |
| `prompt_input` | object | ✅ | 喂给被测 Prompt 的输入上下文（见 2.2 / 2.3） |
| `human_note` | str | ❌ | 人工标注说明（为什么给这个分带；供人工校验对齐） |

### 2.2 QA 样本（`golden_qa/`，模块 D）

在通用信封上增加：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `prompt_input.question` | str | ✅ | 面试题（评分 Prompt 的题干） |
| `prompt_input.jd` | str | ❌ | 目标岗位 JD 摘要（岗位匹配维度依据）；可为 `null`/缺省 |
| `prompt_input.resume_excerpt` | str | ❌ | 候选人简历片段（回答背景）；可为 `null`/缺省 |
| `anchors` | object | ✅ | 三档锚定答案，键固定为 `poor` / `medium` / `good`，缺一不可 |
| `anchors.<tier>.answer` | str | ✅ | 该档候选回答原文（评分 Prompt 的被评回答） |
| `anchors.<tier>.expected_band` | [int,int] | ✅ | 人工标注分带（含端点）：`poor=[1,4]`、`medium=[5,7]`、`good=[8,10]` |
| `anchors.<tier>.expected_center` | float | ✅ | 分带中心分（回归指标参照值）：`2.5` / `6.0` / `9.0` |

**分带映射（冻结，不得自定义）**：差 `1–4`（中心 2.5）/ 中 `5–7`（中心 6.0）/ 优 `8–10`（中心 9.0）。
`expected_center` 必须落在 `expected_band` 内，且三档 `answer` 长度/质量应有可辨识差异，
否则该样本判为无效（`run_eval.py` 校验失败）。

示例（一行）：

```json
{"schema_version":1,"id":"qa-intern-001","module":"evaluation","scene":"intern","enabled":true,"tags":["项目经历","性能优化"],"prompt_input":{"question":"你的简历里写了“优化接口性能，QPS 提升 3 倍”，能具体说说优化前后的情况和你做了什么吗？","jd":"岗位：后端开发实习生；要求熟悉 MySQL、Redis 与性能优化。","resume_excerpt":"校园二手交易平台 后端开发：优化接口性能，QPS 提升 3 倍。"},"anchors":{"poor":{"answer":"就是把数据库查询优化了一下，加了个索引，然后 QPS 就提升了。","expected_band":[1,4],"expected_center":2.5},"medium":{"answer":"当时列表接口比较慢，我先看日志找耗时，发现主要花在数据库查询上。后来给常用字段加了索引，还加了本地缓存，接口快了很多，QPS 大概提升了 3 倍。","expected_band":[5,7],"expected_center":6.0},"good":{"answer":"优化前列表接口 P95 约 850ms、压测 QPS 约 120；我用 pprof 定位到 70% 时间在模糊查询和 N+1 查询上。随后改覆盖索引、去掉 SELECT *，首屏数据放 Redis 缓存 60 秒，并复用连接池。优化后 P95 降到 180ms、QPS 到 360、缓存命中率约 85%；也遇到缓存一致性，用“先更库再删缓存 + 短过期”兜底。","expected_band":[8,10],"expected_center":9.0}},"human_note":"差：只有结论无过程；中：有排查与手段，但缺优化前基线与一致性处理；优：STAR + 数据 + 权衡。"}
```

### 2.3 简历样本（`golden_resumes/`，模块 A）

在通用信封上增加：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `prompt_input.resume_text` | str | ✅ | 完整虚构简历纯文本（可含 `\n` 分段） |
| `prompt_input.target_role` | str | ❌ | 目标岗位名称 |
| `prompt_input.jd` | str | ❌ | 对应 JD 摘要（与 T2 `data/jds/` 同源时可直接引用其文本） |
| `annotations.expected_issues` | object[] | ✅ | 人工标注的**标准问题清单**（诊断回归的 Ground Truth） |
| `annotations.expected_issues[].category` | str | ✅ | 固定枚举：`量化表达` / `动词弱化` / `关键词对齐` / `结构问题` / `错别字格式` |
| `annotations.expected_issues[].quote` | str | ✅ | 问题对应的简历原文片段 |
| `annotations.expected_issues[].severity` | str | ✅ | `high` / `medium` / `low`（影响优先级排序） |
| `annotations.expected_issues[].suggestion` | str | ❌ | 人工改写建议（供命中率/改写质量评估参考） |

简历样本**不含三档锚定答案**（三档锚定只用于 QA 评分）；改写参考可选放
`annotations.reference_rewrites: {原句, 改写}`，格式另行补充。

### 2.4 与 T2（resume-jd）素材的衔接

- `golden_resumes/` 与 `data/resumes/` 可**同源**：当前本目录已自建 8 份虚构标注样本
  （实习 3 / 秋招 3 / 保研 2），T2 产出 `data/resumes/` + `data/manifest.json` 后，把标注过的
  样本复制进本目录并补齐 `annotations.expected_issues` 即可；**未标注意图的数据不能进入本目录**。
- 若 T2 `data/manifest.json` 需要索引本目录，建议字段：
  `{id, file, scene, module, enabled, sha256}`；样本本体仍以本 README 的 JSONL 为准，
  manifest 只做索引，不复制字段语义。

---

## 3. 回归口径与 `run_eval.py`

### 3.1 一次评测做什么

1. 加载 `enabled: true` 的 QA 黄金样本；
2. 对每个样本的 `poor/medium/good` 三档 answer，分别用被测 Prompt 版本评分
   （mock 模式用确定性桩，真实模式调 `evaluation` 路由模型，固定温度 0.1）；
3. 把模型 4 维分数的均分作为该档综合分，与人工标注 `expected_band/expected_center` 对比；
4. 统计指标并对比 `v_old`（基线，如 `evaluation/prompt_baselines/scoring_v1.md`）与
   `v_new`（当前 `prompts/scoring.md`）；
5. 分数下降 → **退出码 1**（供 git hook / CI 强制拦截）。

### 3.2 指标（v_old / v_new 各自计算）

| 指标 | 定义 | 方向 |
| --- | --- | --- |
| `band_hit_rate` | 综合分落在人工分带内的样本比例 | 越高越好（主指标） |
| `mae` | 综合分与 `expected_center` 的平均绝对误差 | 越低越好（主指标） |
| `mean_score` | 全部样本综合分均值（观察漂移，不单独作为门槛） | — |
| `per_tier_mean` | `poor/medium/good` 三档均值（检查分数是否拉开档次） | 观察项 |
| `n_items` | 参评条目数（= 样本数 × 3 档） | — |

综合分 = 该次评分 `dimensions` 的 4 维均分（保留 2 位小数）；维度分 1–10。

### 3.3 判定与退出码（冻结）

设容差 `tolerance`（默认 `0.0`，严格不降；可临时放宽用于降低采样噪声）：

```
passed = (new.band_hit_rate + tolerance >= old.band_hit_rate)
     and (new.mae <= old.mae + tolerance)
     and (new.band_hit_rate >= min_hit_rate)
```

- **退出码 0**：通过（分数未下降）→ 允许 commit；
- **退出码 1**：回归（分数下降）→ 禁止 commit，回滚 Prompt；
- **退出码 2**：数据/配置错误（样本非法、Prompt 路径不存在、真实模式缺 Key 等）。

### 3.4 用法

```bash
# 默认：mock 模式（无 Key、无 pydantic 依赖即可跑），v_old vs v_new
python evaluation/run_eval.py

# 演示“分数下降 → 拦截”：模拟 v_new 变差，退出码应为 1
python evaluation/run_eval.py --mock-scenario regress

# mock 快速验证阈值/报告（放宽容差到 3 分）
python evaluation/run_eval.py --tolerance 3.0 --json-out eval_report.json

# 真实模式（需要 Key；当前阶段由 Lead 统一执行，GLM 无 Key 时请勿使用）
python evaluation/run_eval.py --real \
  --old-prompt evaluation/prompt_baselines/scoring_v1.md \
  --new-prompt prompts/scoring.md --json-out eval_report.json

# 本目录 pytest（样本格式 / 回归阈值 / Prompt v2 契约 / 模块 D 服务，全部 mock、无 Key）
# Windows 沙箱建议先把临时目录指到仓库内 .tmp，避免生成不可读临时目录：
#   $env:TMP="$PWD\.tmp"; $env:TEMP=$env:TMP; New-Item -ItemType Directory -Force $env:TMP | Out-Null
.venv\Scripts\python.exe -m pytest evaluation -q -p no:cacheprovider
```

**Windows GBK 控制台兼容**：入口（`main()`）自动把 `stdout/stderr` 的错误处理设为
`errors="replace"`（保留原编码，GBK 控制台下中文仍正常显示），且控制台判定标记统一为
ASCII 的 `[PASS]` / `[FAIL]` / `[WARN]`——直接运行不会因 emoji / 生僻符号抛
`UnicodeEncodeError`，退出码语义（0/1/2）不受影响。

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--mock` / `--real` | `--mock` | mock 为确定性桩评分，可无 Key 跑；real 调真实模型 |
| `--mock-scenario` | `pass` | `pass` 演示“新版本更好”；`regress` 演示“分数下降被拦截” |
| `--old-prompt` | `evaluation/prompt_baselines/scoring_v1.md` | v_old Prompt 文件（real 模式必填有效） |
| `--new-prompt` | `prompts/scoring.md` | v_new Prompt 文件 |
| `--old-label` / `--new-label` | `v1` / `v2` | 报告中的版本名 |
| `--tolerance` | `0.0` | 容差，同时作用于 `band_hit_rate` 与 `mae` |
| `--min-hit-rate` | `0.0` | `band_hit_rate` 下限（绝对门槛） |
| `--data-dir` | `evaluation/` | 样本根目录（读取 `golden_qa/*.jsonl`） |
| `--include-disabled` | 关 | 纳入 `enabled: false` 的样本 |
| `--json-out` / `--md-out` | 无 | 输出机器可读 JSON / Markdown 报告（结题素材） |

`--json-out` 报告结构（供 CI/报告引用）：

```json
{"generated_at":"...","mode":"mock","decision":{"passed":true,"reasons":[],"tolerance":0.0,"min_hit_rate":0.0},
 "old":{"label":"v1","n_items":30,"band_hit_rate":0.233,"mae":2.1,"mean_score":5.7,"per_tier_mean":{}},
 "new":{"label":"v2","n_items":30,"band_hit_rate":0.9,"mae":0.4,"mean_score":6.1,"per_tier_mean":{}}}
```

---

## 4. 真实模式接口契约（供 T1 / backend-core 对接）

`run_eval.py --real` 只依赖两个后端接口（与 T1 已实现版本对齐），不关心其内部实现：

1. **模型路由（T1 提供，`backend/services/model_router.py`）**：
   ```python
   router = get_router()                       # 进程内单例；加载 .env 与 models.yaml
   result = router.chat_json(                  # route="evaluation"（评分）
       "evaluation", messages, InterviewEvaluation, max_repair=1
   )
   result.value                                # 已通过 Pydantic 校验的 InterviewEvaluation
   router.get_route_config("evaluation")       # RouteConfig(model, temperature, ...)
   ```
   `temperature` 由 `models.yaml` 固定 `0.1`，脚本不改温度；无 Key 时 `chat_json` 抛
   `MissingAPIKeyError`，`run_eval.py` 统一按退出码 2 处理（不会被当作回归）。
2. **评分服务（本模块提供，`backend/services/evaluation.py`）**：
   ```python
   score_answer(prompt_input: dict, answer: str, *,
                router=None, prompt_path=None) -> ScoreResult
   # ScoreResult.evaluation 为 interview_evaluation.InterviewEvaluation（Pydantic 实例）
   # 已执行「证据 <2 条 → 该维度不得 >6 分」代码兜底；ScoreResult.adjustments 记录钳制明细
   ```

对接要点：

- `chat_json` 的解析 / 修复重试由 T1 路由统一负责；`evaluation.py` 再做严格契约校验
  （恰好 4 维、名称固定、分数 1–10），失败抛 `EvaluationValidationError`，不静默降级；
- 无 Key 时必须停留在 mock 模式：mock 路径**不 import** `pydantic` / `model_router`，
  当前环境（仅 DeepSeek Key 且 401 无效、GLM 无 Key）可完整演示
  「改 Prompt → 跑评测 → 对比 → 拦截」闭环，真实调用由 Lead 换 Key 后统一执行；
- 温度：评分与复核均为 `0.1`，由 `backend/config/models.yaml` 唯一决定，脚本不改温度；
- 模型隔离：`evaluation` 与 `cross_review` 必须不同模型，由 `models.yaml` 校验
  （`evaluation.py` 中 `check_model_separation()` / `validate_model_config()`），违反时冒烟测试失败。

### 4.1 模块 D 服务面（`backend/services/evaluation.py`，供 T1 接线 `backend/api/report.py`）

> HTTP 契约以 `docs/api.md`「五、模块 C / D」v1（2026-10-23 冻结）为准；**`report.py` 路由由 T1 接线**，
> 服务函数签名已定稿如下（模块内逐题评分 / 复核 / 报告链路均已实现并有 pytest 覆盖）。

```python
from backend.services import evaluation

# 1) 生成报告：POST /api/report/generate
#    路由拿到请求后：
#    - 若请求带 turns（或 T1 从 T3 会话服务取出 turns）→ generate_report(...)
#    - 若已有 InterviewSession / snapshot dict（含 report_input）→ generate_report_for_session(...)
report: dict = evaluation.generate_report(
    session_id: str,
    turns: Sequence[Mapping],              # [{"turn": 1, "question": "…", "answer": "…"}, …]
    *,
    scene: str = "", company: str = "", role: str = "",
    jd: Any = None, resume: Any = None,    # 结构化 dict 或纯文本均可
    router=None,                           # None → backend.services.model_router.get_router()
    check_config: bool = True,             # 决策④异模型 + 温度 0.1 校验
)                                          # → {"report_id", "evaluation", "cross_review", "markdown"}

report = evaluation.generate_report_for_session(session, router=None, check_config=True)
#    session 支持：InterviewSession 对象 / snapshot dict / {"session_id","turns","report_input":{…}}

# 2) 查询：GET /api/report/{report_id}
stored: dict = evaluation.get_report(report_id)      # 不存在抛 ReportNotFoundError（→ 3001）
data = evaluation.to_get_response(stored)            # 结构同 POST /generate 的 data（含 markdown）

# 3) 下载：GET /api/report/{report_id}/markdown
md: str = evaluation.get_report_markdown(report_id)
#    路由层返回 text/markdown + Content-Disposition: attachment; filename="report_{report_id}.md"

# 4) 导出到文件（服务内部 / 演示脚本用）
evaluation.export_markdown(report, "path/to/report.md")
```

**`data` 结构（docs/api.md 冻结）**：

- `report_id`：`rep_YYYYMMDD_<6 位 hex>`；
- `evaluation`：严格为 `backend/schemas/interview_evaluation.py` 的 `InterviewEvaluation`
  （`dimensions[{dimension, score, evidence}]` / `strengths` / `weaknesses` / `suggestions`）；
- `cross_review`：**数据键冻结为** `{agreed: bool, adjustments: [{dimension, verdict, suggested_score, deviation, reason}], deviation_note: str}`；
- `markdown`：可直接预览 / 下载的报告正文。

服务端存储额外保留 `cross_review_detail`（逐维复核明细）、`turn_evaluations`（逐题评分）、
`metadata`/`created_at`（审计与报告生成用），**不进入 API 的 `data`**。

**异常 → HTTP 错误码（T1 在路由层映射）**：

| 服务异常 | code | HTTP |
| --- | --- | --- |
| `ReportNotFoundError` | 3001 | 404 |
| `model_router.MissingAPIKeyError` / `ModelCallError` | 2001 | 502 |
| `model_router.ModelJSONError` / `EvaluationValidationError` / `CrossReviewValidationError` | 2002 | 502 |
| 空 `session_id` / 空 `turns` 等参数问题（`EvaluationError` 子集，路由层先做 1001 校验） | 1001 | 400 |
| 其他 `EvaluationError` / 未捕获异常 | 5000 | 500 |

> 说明：早前口头汇报中 `GET /api/report/{rid}` 曾写为“不含 markdown”，**以 `docs/api.md` v1 为准**：
> 该接口“结构同 /generate 的 data”，即包含 `markdown`（前端报告页可直接预览）。

其他对外函数：`score_answer` / `score_turn`（实时评分）、`cross_review_answer`（交叉复核）、
`evaluate_session`（逐轮评分→聚合→复核→Markdown）、`cross_review_to_api`（内部复核→冻结数据键）、
`validate_model_config`（决策④校验）。
无 Key 时 `generate_report` 会抛 `MissingAPIKeyError`（不自动 mock）；mock 演示统一走 `run_eval.py --mock`。

---

## 5. 基线快照与 Prompt 版本纪律

- `prompt_baselines/scoring_v1.md`：v1 评分 Prompt 的**冻结副本**（勿改），作为 v_old。
- 每次修改 `prompts/scoring.md`（或 `cross_review.md`）前：
  1. 把**修改前**的文件复制为 `prompt_baselines/scoring_<version>.md`；
  2. 改 Prompt → v2 头部版本注释 + 变更摘要；
  3. 跑 `run_eval.py`（v_old=快照, v_new=当前）→ 分数不降才提交；下降即回滚。
- 版本注释格式与铁律见 `prompts/README.md`。

---

## 6. 扩量计划与进度

| 资产 | 当前（2026-10-23 · 阶段 4 第一批） | 阶段 4 目标 |
| --- | --- | --- |
| `golden_qa/` | **20 条**（实习 6 / 秋招 8 / 保研 6；每题三档锚定） | 30 条（实习/秋招/保研各约 10 条） |
| `golden_resumes/` | **8 份**（实习 3 / 秋招 3 / 保研 2；每份 4–6 条标注问题） | 20 份（含输入 + 人工标注问题清单） |

- 剩余扩量：QA +10（→30）、简历 +12（→20）；按 §2 格式追加 JSONL 即可，`run_eval.py` 自动发现，无需改代码。
- 新增样本必须全虚构脱敏、人工标注分带/问题清单可核验；`pytest evaluation -q` 中的
  `test_golden_samples.py` 会校验计数、场景分布、分带冻结与 quote 逐字可核验。
