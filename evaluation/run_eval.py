"""Prompt 回归评测脚本（v1 骨架，阶段 4 实现）

铁律（见实行清单「Prompt 迭代纪律」）：
任何 Prompt 修改必须先跑本脚本，v_old vs v_new 分数对比合格后才允许 commit；变差则回滚。

样本集：
- evaluation/golden_resumes/：20 份简历诊断黄金样本（输入 + 人工标注的标准问题清单）
- evaluation/golden_qa/：30 条面试 Q&A（每题带 差/中/优 三档锚定答案）

TODO（阶段 4 实现）：
1. 加载黄金样本
2. 用被测 Prompt 版本批量推理
3. 与人工标注对比，输出分数报告（v_old vs v_new）
4. 退出码设计：分数下降则非零退出（供 git hook / CI 强制关卡）
"""
