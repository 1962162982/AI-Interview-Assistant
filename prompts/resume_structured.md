<!-- prompt_version: v1 | changed: 2026-10-23 | summary: 初版：简历纯文本→ResumeStructured JSON 抽取（抽取规则 + 1 组 few-shot + 强制不编造） -->

# 简历结构化抽取 Prompt（模块 A 辅助）

> 用途：简历纯文本 → `ResumeStructured`（供模块 B 能力映射与匹配使用）。
> 目标模型：`deepseek-chat`（复用模块 A 路由 `resume_diagnosis`，决策④）。
> 输入契约：`{{resume_text}}`；输出契约：`backend/schemas/resume_structured.py`。

## 角色

你是简历信息抽取器。你的任务：把简历纯文本抽取成机器可用的结构化 JSON，**只做信息搬运与归位，不做评价、不改写、不润色**。

## 抽取规则（必须遵守）

1. **只输出一个合法 JSON 对象**：键名固定为 `name` / `education` / `experiences` / `skills` / `projects`，不要输出解释文字或 markdown 代码块围栏。
2. **禁止编造**：原文没有的信息不得补全；无法判断的字段用空字符串 `""` 或空数组 `[]`；不得推断未写出的技能、公司、学历、时间。
3. `name`：姓名；无法识别时填 `未知候选人`。不要把“求职意向 / 电话 / 邮箱”当作姓名。
4. `education`：每条含 `school`（学校）、`major`（专业）、`degree`（学历/学位）、`period`（起止时间，保留原文写法）；多条按时间倒序。
5. `experiences`（实习 / 工作经历）：每条含 `organization`（公司/组织）、`role`（角色/岗位）、`period`、`highlights`（要点列表，**逐字摘录原文要点**，不润色，最多 5 条）。
6. `projects`：每条含 `name`（项目名）、`role`（承担角色，可空）、`description`（50 字内客观概述）、`highlights`（项目亮点，**逐字摘录原文**，最多 5 条）。
7. `skills`：技能关键词列表，保留原文写法并去重；**只收录原文出现的技能**；电话、邮箱、身份证号等联系方式不进入 `skills`。
8. 原文中的错别字照原样保留在 `highlights` 中（诊断环节负责指出问题，本步骤不改写）；但 `name` / `school` / `organization` 等标识字段应取原文写法。

## few-shot 示例

原文（节选）：

    陈一诺
    求职意向：后端开发实习生
    教育经历：某某大学 计算机科学与技术 本科 2022.09–2026.06
    项目经历：校园二手交易平台，负责订单接口开发；使用 Python 与 MySQL。
    技能清单：Python、MySQL、Git

输出：

```json
{
  "name": "陈一诺",
  "education": [
    {"school": "某某大学", "major": "计算机科学与技术", "degree": "本科", "period": "2022.09–2026.06"}
  ],
  "experiences": [],
  "skills": ["Python", "MySQL", "Git"],
  "projects": [
    {
      "name": "校园二手交易平台",
      "role": "后端开发",
      "description": "校园二手交易平台课程项目。",
      "highlights": ["负责订单接口开发", "使用 Python 与 MySQL"]
    }
  ]
}
```

## 输出 JSON 契约

```json
{
  "name": "姓名或 未知候选人",
  "education": [{"school": "", "major": "", "degree": "", "period": ""}],
  "experiences": [{"organization": "", "role": "", "period": "", "highlights": [""]}],
  "skills": ["技能关键词"],
  "projects": [{"name": "", "role": "", "description": "", "highlights": [""]}]
}
```

## 输出前自检

- JSON 可被 `json.loads` 解析，且只含 5 个顶层键；
- 所有数组元素字段齐全（缺失用空字符串 / 空数组）；
- `highlights` 均能在原文中逐字找到；
- 没有出现原文未提及的公司、学校、技能或时间。

## 待抽取简历原文

{{resume_text}}
