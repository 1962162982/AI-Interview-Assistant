/**
 * 本地历史记录（localStorage）——阶段 3 体验补齐。
 *
 * 保存最近一次简历文本、最近的面试会话（session_id）与报告（report_id），
 * 供首页展示、报告页按 ID 拉取、刷新后继续演示。纯前端本地数据，不上传后端、不含任何 Key。
 *
 * 存储键：`aia.history.v1`；结构：
 * {
 *   resume: { text, filename, source, savedAt } | null,
 *   sessions: [{ sessionId, scene, role, company, createdAt }],
 *   reports: [{ reportId, sessionId, createdAt }],
 * }
 */
const STORAGE_KEY = 'aia.history.v1'
const DIAGNOSIS_KEY = 'aia.resumeDiagnosis.v1'
const JD_MATCH_KEY = 'aia.jdMatch.v1'
const MAX_ITEMS = 10

function emptyState() {
  return { resume: null, sessions: [], reports: [], resumeDiagnosis: null, jdMatch: null }
}

function readJsonKey(key) {
  const storage = getStorage()
  if (!storage) return null
  try {
    const raw = storage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    return parsed
  } catch {
    return null
  }
}

function readDiagnosis() {
  const parsed = readJsonKey(DIAGNOSIS_KEY)
  return parsed && parsed.report ? parsed : null
}

function readJdMatch() {
  const parsed = readJsonKey(JD_MATCH_KEY)
  return parsed && parsed.result ? parsed : null
}

function getStorage() {
  try {
    if (typeof window !== 'undefined' && window.localStorage) return window.localStorage
  } catch {
    // 隐私模式 / 禁用 localStorage 时静默降级为内存态
  }
  return null
}

/** 读取历史记录（异常/首次访问返回空结构） */
export function loadHistory() {
  const storage = getStorage()
  if (!storage) return emptyState()
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (!raw) return emptyState()
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return emptyState()
    return {
      resume: parsed.resume && typeof parsed.resume === 'object' ? parsed.resume : null,
      sessions: Array.isArray(parsed.sessions) ? parsed.sessions.slice(0, MAX_ITEMS) : [],
      reports: Array.isArray(parsed.reports) ? parsed.reports.slice(0, MAX_ITEMS) : [],
      resumeDiagnosis: readDiagnosis(),
      jdMatch: readJdMatch(),
    }
  } catch {
    return emptyState()
  }
}

function persist(state) {
  const storage = getStorage()
  if (!storage) return
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // 配额不足等异常不影响页面主流程
  }
}

function trim(items) {
  return items.slice(0, MAX_ITEMS)
}

/** 保存最近一次简历文本（上传解析成功 / 粘贴诊断时调用） */
export function saveResume({ text, filename = '', source = 'paste' }) {
  const content = String(text ?? '')
  if (!content.trim()) return loadHistory()
  const state = loadHistory()
  state.resume = {
    text: content,
    filename,
    source,
    savedAt: new Date().toISOString(),
  }
  persist(state)
  return state
}

/** 读取最近一次简历文本（JD 匹配 / 面试建会话时复用） */
export function getResume() {
  return loadHistory().resume
}

/** 记录面试会话（创建成功时调用；同 ID 去重） */
export function addSession({ sessionId, scene = 'intern', role = '', company = '' }) {
  if (!sessionId) return loadHistory()
  const state = loadHistory()
  state.sessions = trim([
    { sessionId, scene, role, company, createdAt: new Date().toISOString() },
    ...state.sessions.filter((item) => item.sessionId !== sessionId),
  ])
  persist(state)
  return state
}

/** 记录报告（生成 / 拉取成功时调用；同 ID 去重） */
export function addReport({ reportId, sessionId = '' }) {
  if (!reportId) return loadHistory()
  const state = loadHistory()
  state.reports = trim([
    { reportId, sessionId, createdAt: new Date().toISOString() },
    ...state.reports.filter((item) => item.reportId !== reportId),
  ])
  persist(state)
  return state
}

/** 最近的会话 / 报告（首页、报告页兜底用） */
export function getLatestSession() {
  return loadHistory().sessions[0] || null
}

export function getLatestReport() {
  return loadHistory().reports[0] || null
}

/** 保存最近一次简历诊断报告（诊断成功后调用，供独立报告页读取） */
export function saveResumeDiagnosis({ report, targetRole = '', resumeName = '' }) {
  const storage = getStorage()
  if (!storage || !report) return getResumeDiagnosis()
  const payload = {
    report,
    targetRole,
    resumeName,
    savedAt: new Date().toISOString(),
  }
  try {
    storage.setItem(DIAGNOSIS_KEY, JSON.stringify(payload))
  } catch {
    // 配额不足等异常不影响主流程
  }
  return payload
}

/** 读取最近一次简历诊断报告（独立报告页 / 首页入口使用） */
export function getResumeDiagnosis() {
  return readDiagnosis()
}

/** 保存最近一次 JD 匹配结果（匹配成功后调用，供独立匹配报告页读取） */
export function saveJdMatch({ result, resumeText = '', jdText = '' }) {
  const storage = getStorage()
  if (!storage || !result) return getJdMatch()
  const payload = {
    result,
    resumeText: String(resumeText || ''),
    jdText: String(jdText || ''),
    savedAt: new Date().toISOString(),
  }
  try {
    storage.setItem(JD_MATCH_KEY, JSON.stringify(payload))
  } catch {
    // 配额不足等异常不影响主流程
  }
  return payload
}

/** 读取最近一次 JD 匹配结果（独立报告页 / 首页入口使用） */
export function getJdMatch() {
  return readJdMatch()
}

/** 清空本地历史（首页「清空历史」按钮） */
export function clearHistory() {
  const storage = getStorage()
  if (!storage) return
  try {
    storage.removeItem(STORAGE_KEY)
    storage.removeItem(DIAGNOSIS_KEY)
    storage.removeItem(JD_MATCH_KEY)
  } catch {
    // ignore
  }
}

/** 时间展示：ISO → 本地 YYYY/MM/DD HH:mm:ss（展示用） */
export function formatTimestamp(isoString) {
  if (!isoString) return '--'
  const date = new Date(isoString)
  if (Number.isNaN(date.getTime())) return '--'
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}
