/**
 * 交互层 API 客户端（契约冻结 v1 · 阶段 3 真实联调增强）
 *
 * 契约唯一来源：docs/api.md（backend-core 维护、Lead 冻结）。
 * 本文件是页面组件访问后端的唯一出口：页面不直接拼接 URL、不重复实现错误处理。
 *
 * 契约要点：
 * - 统一响应壳 {code, message, data}；业务接口 Base 前缀 /api；GET /health 保持原样；
 * - 错误码：1001 参数 / 1002 解析 / 2001 模型调用 / 2002 JSON 校验 / 2003 未实现 /
 *   3001 不存在 / 5000 内部错误；HTTP 非 2xx 与 code != 0 统一抛 ApiError；
 * - 面试流式：POST /api/interview/sessions/{sid}/turns/stream，SSE data: {"event": "delta"|"done"|"error", ...}。
 *
 * 模式：默认 Mock（VITE_USE_MOCK=true）；真实联调在 frontend/.env 设 VITE_USE_MOCK=false。
 * 体验：所有请求支持超时（AbortController）与外部取消（signal）；SSE 支持中途停止。
 */

// 后端基地址（不含 /api 前缀）：
// - 本地开发（`pnpm dev`）：默认 http://127.0.0.1:8000；
// - 生产构建（Docker/云端部署）：默认同源（空串），即请求相对路径 `/api/...`，
//   由 FastAPI 单进程同时托管页面与 API；也可用 VITE_API_BASE_URL 显式覆盖。
const DEFAULT_BASE_URL = import.meta.env.DEV ? 'http://127.0.0.1:8000' : ''
const DEFAULT_TIMEOUT_MS = 90_000

/** 后端基地址（不含 /api 前缀）：VITE_API_BASE_URL，默认本地 uvicorn */
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || DEFAULT_BASE_URL).replace(/\/+$/, '')

/** 是否使用 Mock 数据层：默认 true；真实联调设 VITE_USE_MOCK=false */
export const isMockMode = String(import.meta.env.VITE_USE_MOCK ?? 'true').toLowerCase() !== 'false'

/** 错误码 → 用户可读提示（docs/api.md 错误码表） */
const CODE_MESSAGES = {
  1001: '参数校验失败，请检查输入',
  1002: '文件解析失败，请改用粘贴文本',
  2001: '模型调用失败（超时 / 限流 / 鉴权），请稍后重试',
  2002: '模型输出校验失败，请重试',
  2003: '该模块开发中（后端暂未实现）',
  3001: '会话或报告不存在（可能已过期）',
  5000: '服务内部错误，请稍后重试',
}

/** 统一错误类型：页面据此展示 message；code 对应 docs/api.md 错误码表 */
export class ApiError extends Error {
  constructor(message, { code = 0, status = 0, payload = null, retryable = false, cancelled = false, timeout = false } = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.payload = payload
    this.retryable = retryable
    this.cancelled = cancelled
    this.timeout = timeout
  }
}

function buildUrl(path) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${normalizedPath}`
}

function describeError(message, code, status) {
  if (message && String(message).trim()) return String(message)
  if (CODE_MESSAGES[code]) return CODE_MESSAGES[code]
  if (status) return `请求失败（HTTP ${status}）`
  return '请求失败，请稍后重试'
}

/**
 * 合并「外部取消 signal」与「超时」：返回受控 signal 与状态查询/清理方法。
 * 超时与用户主动取消在页面层需要区分展示（超时可重试、取消不报错）。
 */
function createCancelScope(externalSignal, timeoutMs) {
  const controller = new AbortController()
  let timedOut = false
  let timer = null
  const onExternalAbort = () => controller.abort(externalSignal?.reason)

  if (externalSignal) {
    if (externalSignal.aborted) controller.abort(externalSignal.reason)
    else externalSignal.addEventListener('abort', onExternalAbort, { once: true })
  }
  if (Number.isFinite(timeoutMs) && timeoutMs > 0) {
    timer = setTimeout(() => {
      timedOut = true
      controller.abort(new Error('timeout'))
    }, timeoutMs)
  }

  return {
    signal: controller.signal,
    isTimeout: () => timedOut,
    isExternalAbort: () => Boolean(externalSignal?.aborted),
    cleanup() {
      if (timer) clearTimeout(timer)
      timer = null
      externalSignal?.removeEventListener?.('abort', onExternalAbort)
    },
  }
}

function abortToApiError(scope, timeoutMs, fallbackMessage) {
  if (scope.isTimeout()) {
    const seconds = Number.isFinite(timeoutMs) ? Math.round(timeoutMs / 1000) : 0
    return new ApiError(`请求超时${seconds ? `（>${seconds}s）` : ''}，请检查后端服务是否在线`, {
      retryable: true,
      timeout: true,
    })
  }
  if (scope.isExternalAbort()) return new ApiError('请求已取消', { cancelled: true })
  return new ApiError(fallbackMessage || '请求中断', { retryable: true })
}

function unwrapPayload(payload) {
  // 统一响应壳：{code, message, data}；/health 不是壳，直接返回
  if (payload && typeof payload === 'object' && !Array.isArray(payload) && 'code' in payload) {
    const code = Number(payload.code)
    if (code !== 0) {
      throw new ApiError(describeError(payload.message, code, 0), {
        code,
        payload,
        retryable: code === 2001 || code === 2002,
      })
    }
    return payload.data ?? null
  }
  return payload
}

/**
 * 通用 JSON / FormData 请求，返回响应壳中的 data。
 * @param {string} path 以 / 开头的接口路径
 * @param {{ method?: string, body?: unknown, headers?: Record<string, string>, signal?: AbortSignal, timeoutMs?: number }} options
 */
export async function request(path, { method = 'GET', body, headers = {}, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const scope = createCancelScope(signal, timeoutMs)
  try {
    const requestInit = {
      method,
      headers: { Accept: 'application/json', ...headers },
      signal: scope.signal,
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
      if (scope.signal.aborted || error?.name === 'AbortError') {
        throw abortToApiError(scope, timeoutMs, `无法连接后端（${API_BASE_URL}）`)
      }
      throw new ApiError(`无法连接后端（${API_BASE_URL}）：${error.message}`, { retryable: true })
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
      throw new ApiError(describeError(message, code, response.status), {
        code,
        status: response.status,
        payload,
        retryable: code === 2001 || code === 2002,
      })
    }

    return unwrapPayload(payload)
  } finally {
    scope.cleanup()
  }
}

/** 健康检查：GET /health（不属于 /api，契约不变）→ {"status":"ok"} */
export function health({ signal, timeoutMs = 10_000 } = {}) {
  return request('/health', { signal, timeoutMs })
}

/**
 * SSE 流式请求：解析 docs/api.md 约定的 data: {"event": ...} 行并逐条回调。
 * - delta：增量文本；done：完整 question/state；error：流内错误（抛 ApiError，含 retryable）。
 * - 支持外部 AbortSignal 中途停止；超时覆盖连接 + 整个流（默认 180s，后端 interview 路由 timeout 120s）。
 *
 * @returns {Promise<object|null>} 收到 done 事件时返回该事件
 */
export async function streamRequest(path, { body, onEvent, signal, timeoutMs = 180_000 } = {}) {
  const scope = createCancelScope(signal, timeoutMs)
  let reader = null
  try {
    let response
    try {
      response = await fetch(buildUrl(path), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify(body ?? {}),
        signal: scope.signal,
      })
    } catch (error) {
      if (scope.signal.aborted || error?.name === 'AbortError') {
        throw abortToApiError(scope, timeoutMs, `无法连接后端（${API_BASE_URL}）`)
      }
      throw new ApiError(`无法连接后端（${API_BASE_URL}）：${error.message}`, { retryable: true })
    }

    if (!response.ok) {
      const text = await response.text()
      let payload = null
      try {
        payload = JSON.parse(text)
      } catch {
        payload = null
      }
      const code = Number(payload?.code ?? 0)
      throw new ApiError(describeError(payload?.message, code, response.status), {
        code,
        status: response.status,
        payload,
        retryable: code === 2001 || code === 2002,
      })
    }
    if (!response.body) {
      throw new ApiError('当前浏览器不支持流式响应（ReadableStream 不可用）')
    }

    reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    for (;;) {
      let chunk
      try {
        chunk = await reader.read()
      } catch (error) {
        if (scope.signal.aborted || error?.name === 'AbortError') {
          throw abortToApiError(scope, timeoutMs, '流式响应中断')
        }
        throw new ApiError(`流式响应中断：${error.message}`, { retryable: true })
      }
      if (chunk.done) break

      buffer = (buffer + decoder.decode(chunk.value, { stream: true })).replace(/\r/g, '')
      let separatorIndex = buffer.indexOf('\n\n')
      while (separatorIndex >= 0) {
        const rawEvent = buffer.slice(0, separatorIndex)
        buffer = buffer.slice(separatorIndex + 2)
        separatorIndex = buffer.indexOf('\n\n')

        const dataText = rawEvent
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n')
        if (!dataText) continue

        let event = null
        try {
          event = JSON.parse(dataText)
        } catch {
          continue
        }
        onEvent?.(event)

        if (event.event === 'error') {
          const code = Number(event.code ?? 0)
          throw new ApiError(describeError(event.message, code, 0), {
            code,
            retryable: Boolean(event.retryable),
            payload: event,
          })
        }
        if (event.event === 'done') return event
      }
    }
    return null
  } finally {
    try {
      await reader?.cancel()
    } catch {
      // 忽略取消异常
    }
    scope.cleanup()
  }
}

/** 真实 HTTP 实现（契约冻结 v1；支持可选 signal / timeoutMs） */
export const http = {
  /** GET /api/meta/routes：5 条模型路由非敏感摘要（决策④自查） */
  metaRoutes: (options = {}) =>
    request('/api/meta/routes', { ...options, timeoutMs: options.timeoutMs ?? 15_000 }),

  /** POST /api/resume/parse：multipart 字段 file（PDF/DOCX，本地解析不调模型） */
  resumeParse: (file, options = {}) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/resume/parse', {
      ...options,
      method: 'POST',
      body: form,
      timeoutMs: options.timeoutMs ?? 60_000,
    })
  },

  /** POST /api/resume/structure：{resume_text} → ResumeStructured */
  resumeStructure: (resumeText, options = {}) =>
    request('/api/resume/structure', {
      ...options,
      method: 'POST',
      body: { resume_text: resumeText },
      timeoutMs: options.timeoutMs ?? 120_000,
    }),

  /** POST /api/resume/diagnose：{resume_text, target_role?, jd_text?} → 诊断报告（数据键已冻结） */
  resumeDiagnose: (payload, options = {}) =>
    request('/api/resume/diagnose', {
      ...options,
      method: 'POST',
      body: payload,
      timeoutMs: options.timeoutMs ?? 180_000,
    }),

  /** POST /api/jd/structure：{jd_text} → JDStructured */
  jdStructure: (jdText, options = {}) =>
    request('/api/jd/structure', {
      ...options,
      method: 'POST',
      body: { jd_text: jdText },
      timeoutMs: options.timeoutMs ?? 120_000,
    }),

  /** POST /api/jd/match：{resume?, jd?} 或 {resume_text, jd_text} → 匹配结果（数据键已冻结） */
  jdMatch: (payload, options = {}) =>
    request('/api/jd/match', {
      ...options,
      method: 'POST',
      body: payload,
      timeoutMs: options.timeoutMs ?? 240_000,
    }),

  /** POST /api/interview/sessions：创建会话（scene 三选一，默认 intern） */
  createInterviewSession: (payload, options = {}) =>
    request('/api/interview/sessions', {
      ...options,
      method: 'POST',
      body: payload,
      timeoutMs: options.timeoutMs ?? 180_000,
    }),

  /** POST /api/interview/sessions/{sid}/turns：提交回答，返回下一题（非流式，备用） */
  submitAnswer: (sessionId, answer, options = {}) =>
    request(`/api/interview/sessions/${encodeURIComponent(sessionId)}/turns`, {
      ...options,
      method: 'POST',
      body: { answer },
      timeoutMs: options.timeoutMs ?? 180_000,
    }),

  /** POST /api/interview/sessions/{sid}/turns/stream：SSE 流式返回下一题 */
  streamAnswer: (sessionId, answer, { onEvent, signal, timeoutMs = 180_000 } = {}) =>
    streamRequest(`/api/interview/sessions/${encodeURIComponent(sessionId)}/turns/stream`, {
      body: { answer },
      onEvent,
      signal,
      timeoutMs,
    }),

  /** GET /api/interview/sessions/{sid}：查询会话状态（刷新 / 历史记录） */
  getSession: (sessionId, options = {}) =>
    request(`/api/interview/sessions/${encodeURIComponent(sessionId)}`, {
      ...options,
      timeoutMs: options.timeoutMs ?? 15_000,
    }),

  /** POST /api/interview/sessions/{sid}/end：结束面试，返回对话记录 */
  endSession: (sessionId, options = {}) =>
    request(`/api/interview/sessions/${encodeURIComponent(sessionId)}/end`, {
      ...options,
      method: 'POST',
      body: {},
      timeoutMs: options.timeoutMs ?? 60_000,
    }),

  /** POST /api/report/generate：{session_id} 或 {session_id, turns} → 评分报告（评分 + 交叉复核，耗时较长） */
  generateReport: (payload, options = {}) =>
    request('/api/report/generate', {
      ...options,
      method: 'POST',
      body: payload,
      timeoutMs: options.timeoutMs ?? 300_000,
    }),

  /** GET /api/report/{rid}：按报告 ID 取回完整报告 */
  getReport: (reportId, options = {}) =>
    request(`/api/report/${encodeURIComponent(reportId)}`, {
      ...options,
      timeoutMs: options.timeoutMs ?? 15_000,
    }),

  /** GET /api/report/{rid}/markdown：返回 Markdown 文本（text/markdown 下载） */
  async getReportMarkdown(reportId, { signal, timeoutMs = 30_000 } = {}) {
    const scope = createCancelScope(signal, timeoutMs)
    try {
      let response
      try {
        response = await fetch(buildUrl(`/api/report/${encodeURIComponent(reportId)}/markdown`), {
          headers: { Accept: 'text/markdown' },
          signal: scope.signal,
        })
      } catch (error) {
        if (scope.signal.aborted || error?.name === 'AbortError') {
          throw abortToApiError(scope, timeoutMs, '导出请求中断')
        }
        throw new ApiError(`无法连接后端（${API_BASE_URL}）：${error.message}`, { retryable: true })
      }
      const text = await response.text()
      if (!response.ok) {
        let payload = null
        try {
          payload = JSON.parse(text)
        } catch {
          payload = null
        }
        const code = Number(payload?.code ?? 0)
        throw new ApiError(describeError(payload?.message, code, response.status), {
          code,
          status: response.status,
          payload,
        })
      }
      return text
    } finally {
      scope.cleanup()
    }
  },
}

/**
 * Mock 数据层：与 http 同构的接口集合，按 docs/api.md 契约返回虚构示例数据。
 * 仅在 VITE_USE_MOCK=true 时启用。
 */
import { mock } from './mock'

export { mock }

/** 页面统一入口：当前模式由 VITE_USE_MOCK 决定 */
export const api = isMockMode ? mock : http
