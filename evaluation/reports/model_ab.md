# 模型分配 A/B 报告（阶段 4 收尾 · 临时同供应商）

- 生成时间：2026-10-23T12:24:41+08:00
- 模式：live（真实模型调用）
- 样本：2 条 × 三档 = 6 条目（qa-intern-001, qa-autumn-001）
- 真实调用次数：12

## 配置

| 配置 | 说明 | evaluation 模型 | temperature | 配置文件 |
| --- | --- | --- | --- | --- |
| mixed | A 现临时混合配置 | `deepseek-v4-pro` | 0.1 | `backend\config\models.deepseek.yaml` |
| flash | B 全 flash 基线 | `deepseek-flash` | 0.1 | `evaluation\configs\models_all_flash.yaml` |

## 指标对比（evaluation 路由评分 vs 人工分带）

| 指标 | A 现临时混合配置 | B 全 flash 基线 |
| --- | --- | --- |
| band_hit_rate（越高越好） | 1.000 | 1.000 |
| MAE（越低越好） | 0.208 | 0.292 |
| mean_score（观察项） | 5.792 | 5.708 |

## 结论

- band_hit_rate 差值（A - B）：+0.000；MAE 改善（B - A）：+0.083。
- band_hit_rate 相同；MAE 更低的是 A（质量档）——说明档位差异体现在分数偏离度上，但样本量很小，尚不足以支撑切换决策。
- 本报告不做自动模型切换决策：按决策④，换模型属于效果变更，须在 GLM Key 到位后跑全量评测回归再定。

## 说明与局限

- 临时同供应商局限：A/B 两套配置均走 DeepSeek 端点（临时全 DeepSeek 联调模式），不是决策④跨供应商混用；结论仅用于当前临时配置的模型档位取舍参考。
- A/B 仅使用 evaluation 路由；models_all_flash.yaml 中 cross_review 设为 deepseek-v4-pro 仅用于通过决策④「评分与复核必须异模型」强校验，A/B 未调用该路由。
- 样本量很小（2–3 条 × 三档），单次运行存在方差；不做自动切换，需人工结合全量回归判断。
