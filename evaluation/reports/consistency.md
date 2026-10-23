# 评分一致性抽样报告（阶段 4 收尾）

- 生成时间：2026-10-23T12:22:00+08:00
- 模式：live（真实模型调用）
- 抽样：10 条 golden_qa，档位 `medium`
- 评分 Prompt 版本：v2
- 评分路由：`evaluation` → `deepseek-v4-pro`（temperature 0.1，配置 backend\config\models.deepseek.yaml）

## 逐条偏差

| 样本 ID | 场景 | 模型综合分 | 人工分带中心 | 绝对偏差 | 相对偏差 |
| --- | --- | --- | --- | --- | --- |
| qa-autumn-002 | autumn | 4.50 | 6.00 | 1.50 | 25.0% |
| qa-autumn-003 | autumn | 5.00 | 6.00 | 1.00 | 16.7% |
| qa-autumn-009 | autumn | 5.00 | 6.00 | 1.00 | 16.7% |
| qa-autumn-010 | autumn | 6.00 | 6.00 | 0.00 | 0.0% |
| qa-grad-005 | grad | 4.25 | 6.00 | 1.75 | 29.2% |
| qa-grad-006 | grad | 5.00 | 6.00 | 1.00 | 16.7% |
| qa-grad-009 | grad | 6.00 | 6.00 | 0.00 | 0.0% |
| qa-intern-001 | intern | 5.75 | 6.00 | 0.25 | 4.2% |
| qa-intern-008 | intern | 5.25 | 6.00 | 0.75 | 12.5% |
| qa-intern-010 | intern | 6.25 | 6.00 | 0.25 | 4.2% |

## 汇总与结论

- 平均相对偏差：**12.50%**（阈值 ≤ 15%）
- 最大相对偏差：29.17%
- 人工与 AI 评分偏差 ≤ 15%：**通过**（PASS）

## 说明与局限

- 真实调用：当前为临时全 DeepSeek 模式（evaluation 路由为质量档模型，非同供应商/跨供应商混用），结论仅代表当前临时配置。
- 调用次数 = 10（每条样本 1 次评分）；temperature 由 models 配置固定 0.1。
