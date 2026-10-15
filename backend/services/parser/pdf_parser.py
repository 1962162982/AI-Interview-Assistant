"""PDF 简历解析（v1 骨架，阶段 2 实现）

- 主力：pdfplumber（含 page.extract_tables() 处理表格）
- 兜底：检测到扫描件 / 乱码时，降级为“用户粘贴文本”输入
"""
