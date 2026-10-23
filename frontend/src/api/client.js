/**
 * 交互层 API 客户端（契约冻结 v1 · 2026-10-23）。
 *
 * 契约唯一来源：docs/api.md（backend-core 维护、Lead 冻结，含统一响应壳 / 错误码 / SSE 事件格式）。
 * 本文件是页面组件访问后端的唯一出口：页面不直接拼接 URL、不硬编码错误处理。
 *
 * 默认运行在 Mock 模式（VITE_USE_MOCK=true）：
 * - 后端 Key 暂不可用（DeepSeek 401 / GLM 无 Key）、且阶段 1 四组业务路由为 501 薄壳；
 * - 因此默认由 src/api/mock.js 按同一契约返回同构 mock 数据，保证页面可交互、可演示；
 * - 后端就绪后把环境变量设为 VITE_USE_MOCK=false，即无缝切到真实 HTTP 调用。
 *
 * 契约要点（详见 docs/api.md）：
 * - 统一响应壳 {code, message, data}；code=0 成功，其余为业务错误；
 * - 业务接口 Base 前缀 /api；GET /health 保持原样（{"status":"ok"}）；
 * - 面试流式接口使用 SSE，每行 data: {"event":"delta"|"done"|"error", ...}。
 */

const DEFAULT_BASE_URL = 'http://127.0.0.1:8000'

/** 后端基地址（不含 /api 前缀）：VITE_API_BASE_URL，默认本地 uvicorn */
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || DEFAULT_BASE_URL).replace(/\/+$/, '')

/** 是否使用 Mock 数据层：默认 true，后端联调时设 VITE_USE_MOCK=false */
export const isMockMode = String(import.meta.env.VITE_USE_MOCK ?? 'true').toLowerCase() !== 'false'

/** 统一错误类型：页面据此展示 message；code 对应 docs/api.md 错误码表 */
export class ApiError extends Error {
  constructor(message, { code = 0, status = 0, payload = null, retryable = false } = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.payload = payload
    this.retryable = retryable
  }
}

function buildUrl(path) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${normalizedPath}`
}

function unwrapPayload(payload) {
  // 统一响应壳：{code, message, data}；/health 不是壳，直接返回
  if (payload && typeof payload === 'object' && !Array.isArray(payload) && 'code' in payload) {
    const code = Number(payload.code)
    if (code !== 0) {
      throw new ApiError(payload.message || `请求失败（code=${code}）`, { code, payload })
    }
    return payload.data ?? null
  }
  return payload
}

/**
 * 通用 JSON / FormData 请求，返回响应壳中的 data。
 * @param {string} path 以 / 开头的接口路径
 * @param {{ method?: string, body?: unknown, headers?: Record<string, string>, signal?: AbortSignal }} options
 */
export async function request(path, { method = 'GET', body, headers = {}, signal } = {}) {
  const requestInit = {
    method,
    headers: { Accept: 'application/json', ...headers },
    signal,
  }
  if (body instanceof FormData) {
    requestInit.body = body
  } else if (body !== undefined) {
    requestInit.headers['Content-Type'] = 'application/json'
    requestInit.body = JSON.stringify(body)
  }

  let response
  try {
    response = await fetch(buildUrl(path), requestInit)
  } catch (error) {
    throw new ApiError(`无法连接后端（${API_BASE_URL}）：${error.message}`)
  }

  const text = await response.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    const code = payload && typeof payload === 'object' && 'code' in payload ? Number(payload.code) : 0
    const message = payload && typeof payload === 'object' ? payload.message : null
    throw new ApiError(message || `请求失败（HTTP ${response.status}）`, {
      code,
      status: response.status,
      payload,
    })
  }

  return unwrapPayload(payload)
}

/** 健康检查：GET /health（不属于 /api，契约不变）→ {"status":"ok"} */
export function health() {
  return request('/health')
}

/**
 * SSE 流式请求：解析 docs/api.md 约定的 data: {"event": ...} 行并逐条回调。
 * @param {string} path
 * @param {{ body?: unknown, onEvent?: (event: object) => void, signal?: AbortSignal }} options
 * @returns {Promise<object|null>} 收到 done 事件时返回该事件，否则返回最后一个事件
 */
export async function streamRequest(path, { body, onEvent, signal } = {}) {
  let response
  try {
    response = await fetch(buildUrl(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body ?? {}),
      signal,
    })
  } catch (error) {
    throw new ApiError(`无法连接后端（${API_BASE_URL}）：${error.message}`)
  }

  if (!response.ok) {
    const text = await response.text()
    let payload = null
    try {
      payload = JSON.parse(text)
    } catch {
      payload = null
    }
    throw new ApiError(payload?.message || `请求失败（HTTP ${response.status}）`, {
      code: Number(payload?.code ?? 0),
      status: response.status,
      payload,
    })
  }
  if (!response.body) {
    throw new ApiError('当前浏览器不支持流式响应（ReadableStream 不可用）')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let lastEvent = null

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r/g, '')
      let separatorIndex = buffer.indexOf('\n\n')
      while (separatorIndex >= 0) {
        const rawEvent = buffer.slice(0, separatorIndex)
        buffer = buffer.slice(separatorIndex + 2)
        const dataText = rawEvent
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n')
        if (dataText) {
          let event = null
          try {
            event = JSON.parse(dataText)
          } catch {
            event = null
          }
          if (event) {
            lastEvent = event
            onEvent?.(event)
            if (event.event === 'error') {
              throw new ApiError(event.message || '模型调用失败，请重试', {
                code: Number(event.code ?? 0),
                retryable: Boolean(event.retryable),
                payload: event,
              })
            }
            if (event.event === 'done') return event
          }
        }
        separatorIndex = buffer.indexOf('\n\n')
      }
    }
  } finally {
    reader.cancel().catch(() => {})
  }

  return lastEvent
}

/** 真实 HTTP 实现（契约冻结 v1） */
export const http = {
  /** GET /api/meta/routes：5 条模型路由非敏感摘要（决策④自查） */
  metaRoutes: () => request('/api/meta/routes'),

  /** POST /api/resume/parse：multipart 字段 file（PDF/DOCX） */
  resumeParse: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/resume/parse', { method: 'POST', body: form })
  },

  /** POST /api/resume/structure：{resume_text} → ResumeStructured */
  resumeStructure: (resumeText) =>
    request('/api/resume/structure', { method: 'POST', body: { resume_text: resumeText } }),

  /** POST /api/resume/diagnose：{resume_text, target_role?} → 诊断报告（数据键已冻结） */
  resumeDiagnose: (payload) => request('/api/resume/diagnose', { method: 'POST', body: payload }),

  /** POST /api/jd/structure：{jd_text} → JDStructured */
  jdStructure: (jdText) => request('/api/jd/structure', { method: 'POST', body: { jd_text: jdText } }),

  /** POST /api/jd/match：{resume?, jd?} 或 {resume_text, jd_text} → 匹配结果（数据键已冻结） */
  jdMatch: (payload) => request('/api/jd/match', { method: 'POST', body: payload }),

  /** POST /api/interview/sessions：创建会话（scene 三选一，默认 intern） */
  createInterviewSession: (payload) =>
    request('/api/interview/sessions', { method: 'POST', body: payload }),

  /** POST /api/interview/sessions/{sid}/turns：提交回答，返回下一题（非流式） */
  submitAnswer: (sessionId, answer) =>
    request(`/api/interview/sessions/${encodeURIComponent(sessionId)}/turns`, {
      method: 'POST',
      body: { answer },
    }),

  /** POST /api/interview/sessions/{sid}/turns/stream：SSE 流式返回下一题 */
  streamAnswer: (sessionId, answer, { onEvent, signal } = {}) =>
    streamRequest(`/api/interview/sessions/${encodeURIComponent(sessionId)}/turns/stream`, {
      body: { answer },
      onEvent,
      signal,
    }),

  /** GET /api/interview/sessions/{sid}：查询会话状态（刷新 / 历史记录） */
  getSession: (sessionId) =>
    request(`/api/interview/sessions/${encodeURIComponent(sessionId)}`),

  /** POST /api/interview/sessions/{sid}/end：结束面试，返回对话记录 */
  endSession: (sessionId) =>
    request(`/api/interview/sessions/${encodeURIComponent(sessionId)}/end`, {
      method: 'POST',
      body: {},
    }),

  /** POST /api/report/generate：{session_id} 或 {session_id, turns} → 评分报告 */
  generateReport: (payload) => request('/api/report/generate', { method: 'POST', body: payload }),

  /** GET /api/report/{rid}：按报告 ID 取回完整报告 */
  getReport: (reportId) => request(`/api/report/${encodeURIComponent(reportId)}`),

  /** GET /api/report/{rid}/markdown：返回 Markdown 文本（text/markdown 下载） */
  async getReportMarkdown(reportId) {
    const response = await fetch(buildUrl(`/api/report/${encodeURIComponent(reportId)}/markdown`), {
      headers: { Accept: 'text/markdown' },
    })
    if (!response.ok) {
      throw new ApiError(`导出失败（HTTP ${response.status}）`, { status: response.status })
    }
    return response.text()
  },
}

/**
 * Mock 数据层：与 http 同构的接口集合，按 docs/api.md 契约返回虚构示例数据。
 * 默认导出给页面使用；切换真实后端只需 VITE_USE_MOCK=false。
 */
import { mock } from './mock'

export { mock }

/** 页面统一入口：当前模式由 VITE_USE_MOCK 决定 */
export const api = isMockMode ? mock : http
