# Prompt 设计方法论与迭代记录

> 本文件为项目报告素材，基于仓库真实文件、git 提交与测试结果撰写，不虚构数据。来源：`prompts/README.md`、`prompts/*.md`、`evaluation/README.md`、`docs/decision_log.md`。

## 一、设计方法论：按任务异质选择 Prompt 形态

**A 简历诊断：检查清单 + few-shot 改写。** `resume_diagnosis.md` 固定 5 项检查清单（量化表达 / 动词弱化 / 关键词对齐 / 结构问题 / 错别字与格式），内置 4 组“原句→改写”few-shot 统一风格；输出 JSON（`summary` / `issues` / `strengths`），每条问题强制 `original_quote` 逐字引用原文并必须给出 `rewrite`，禁止编造数字（缺失用“待补”占位提示用户）。

**B JD 匹配：两段式调用。** `jd_structured.md` 以低温 0.2 把 JD 抽成 `JDStructured`，能力项含 level（了解 / 熟悉 / 精通 / 硬性 / 加分 / 不限）和逐字原文 `evidence`；`jd_matching.md` 只接收两个结构化 JSON，逐项输出 status（matched / partial / missing）与简历证据，按显式权重算 0–100 分（硬性 / 精通权重 3、熟悉 2、其余 1；matched 1 / partial 0.5 / missing 0，硬性未覆盖封顶 60，加分至多 +6），并给差距与补法。拆分使遗漏可见、每步可独立评测。

**C 模拟面试：角色卡 + 显式追问决策树。** `interviewer/system_base.md` 用 `{{company}}`/`{{role}}`/`{{style}}`/`{{scene}}` 注入人设，追问轮由代码注入 `{{rule_id}}`/`{{rule_instruction}}`；决策树 D1–D5 显式定义触发条件、判定信号、追问动作与目的（含糊→追数据来源；技术名词→深挖原理；与 JD 冲突→温和质疑；回答优秀→换维度；同方向追问≥2 次→强制换向），规定判定顺序（先 D5，再 D1→D3→D2→D4）。硬约束“一次一问、锚定简历/JD、不评分”，配 3 轮 few-shot 示范追问节奏；三场景变体只改语气与侧重。

**D 即时反馈：rubric + 锚定 + 强制 grounding + 交叉复核。** `scoring.md` 定义 4 维 rubric（相关性 / 结构性 / 具体性 / 岗位匹配，各 1–10）与差 / 中 / 优三档锚定示例（分带冻结 1–4 / 5–7 / 8–10）；硬规则为“先证据后分数、每维证据少于 2 条该维不得高于 6 分、无信息≤4 分、四维禁止同分”。`cross_review.md` 由不同模型逐维复核 agree / too_high / too_low，单维调整幅度≤2 分并标注 `risk_flags`。

**JSON Schema 校验与修复重试。** 统一封装在 `model_router.chat_json()`：`response_format=json_object`（400 自动去参重试）→ Pydantic 校验 → 失败把错误与 Schema 回填修复重试 → 耗尽抛 `ModelJSONError`；模块层再降级（A 诊断 `degraded=True` 纯文本、B 匹配 `score=null`、简历结构化降级规则分段）。

## 二、版本管理与评测铁律

- **版本注释**：每个 Prompt 首行必须为 `<!-- prompt_version: vX | changed: YYYY-MM-DD | summary -->`；v1 骨架→v2 定稿见 `feat(prompts)` 提交 0d8f6da（10 个 Prompt，含 A/B/D v2、C 三场景 v2 与简历结构化 v1）。
- **金标准**：`evaluation/golden_resumes` 8 份三场景虚构简历、`golden_qa` 20 条三场景 QA（三档锚定、分带与中心分冻结），格式唯一事实来源为 `evaluation/README.md`（`schema_version=1`）。
- **强制回归**：`run_eval.py` 对比 v_old（`prompt_baselines/scoring_v1.md`）与 v_new，指标为 band_hit_rate / MAE / mean_score，退出码 0 通过、1 分数下降拦截、2 数据或配置错误；mock 无 Key 可演示“改 Prompt→评测→对比→拦截”闭环（全仓 301 passed + 1 skipped + 80 subtests）。
- **Prompt v2 临时提交口径**：无可用 Key 时只完成 mock 回归；v2 先随阶段 1 提交，真实回归前不得继续叠加效果优化；Key 到位须跑 `--real`，不降保留、下降回滚。
- **临时全 DeepSeek 模式**：按用户指示新增 `models.deepseek.yaml`，经 `MODELS_CONFIG_PATH` 临时切到 5 路由全 DeepSeek（pro / flash），决策④配置不动，评分/复核仍异模型但同供应商。真实链路 `smoke_deepseek.py` exit=0、`e2e_real.py --with-report` 13/13；但真实回归因 v1 基线按 GLM 调优、在 DeepSeek-V4-Pro 下无法产出合法 JSON 而中止；v2 维持临时口径，待 GLM Key 恢复决策④后重跑全量真实回归再固化。

## 三、各模块要点与证据

| 模块 | 目标模型（决策④） | Prompt（v2） | 关键机制 |
| --- | --- | --- | --- |
| A 简历诊断 | `deepseek-chat` | `resume_diagnosis.md`、`resume_structured.md` | 5 项清单、few-shot 改写、强制引原文、校验→降级 |
| B JD 匹配 | `glm-4-air` | `jd_structured.md`、`jd_matching.md` | 低温抽取 + 逐项 evidence 对齐 + 显式权重评分 |
| C 模拟面试 | `glm-4-flash` | `interviewer/system_base.md` + 三场景 | 角色卡、D1–D5 决策树、一次一问 |
| D 即时反馈 | 评分 `glm-4-plus` / 复核 `deepseek-chat` | `scoring.md`、`cross_review.md` | 4 维 rubric、三档锚定、grounding、跨模型复核 |

模型分配唯一事实来源 `backend/config/models.yaml`（决策④），业务层只按路由名调用。证据：301 passed + 1 skipped + 80 subtests；真实 E2E 13/13；`evaluation` pytest 与 `run_eval.py` mock 回归全绿。

## 四、答辩亮点（4 条）

1. **按任务异质选形态**：清单化保证可评测、两段式降遗漏、决策树让追问可维护、rubric+锚定降漂移。
2. **结构化输出工程化**：JSON mode + Pydantic + 修复重试 + 降级兜底，模型抖动不直接变成 500。
3. **可解释可追溯**：强制引用原文/证据、证据不足降分、跨模型复核且调整≤2 分，每分都能对应到原文。
4. **评测即关卡、数据诚实**：分数下降非零退出，mock 无 Key 也能演示闭环；真实回归未通过就不宣称 v2 已固化。

## 五、局限与改进（3 条）

1. **真实 Prompt 回归尚未完成**：临时模式 v1 基线模型不匹配导致 `--real` 中止；改进：补 GLM Key、恢复决策④后全量回归 v1 vs v2，再按铁律固化/回滚。
2. **评测集与标注规模有限**：当前 8 份简历 / 20 条 QA、人工标注样本偏少；改进：扩到 20 份 / 30 条，补一致性抽样（偏差≤15%）与模型 A/B 对照。
3. **Prompt 与模型强绑定**：换模型或供应商必须重跑评测；改进：按目标模型做差异化措辞，并把“换模型必回归”固化进评测关卡。
