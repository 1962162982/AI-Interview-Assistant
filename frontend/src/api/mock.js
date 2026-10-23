/**
 * 交互层 Mock 数据层（VITE_USE_MOCK=true 时启用，默认开启）。
 *
 * 用途：DeepSeek Key 暂不可用（401）、GLM 暂无 Key，且阶段 1 四组业务路由为 501 薄壳占位；
 * 本模块按 docs/api.md 冻结 v1 契约返回**同构虚构数据**，保证 5 个页面可交互、可演示。
 * 所有内容均为虚构示例，不含真实用户信息与任何 API Key；接入真实后端后设 VITE_USE_MOCK=false 即可停用。
 */

const LATENCY_MS = 220

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/** 虚构示例简历（仅用于演示与素材占位，勿使用真人简历） */
export const SAMPLE_RESUME_TEXT = `张同学
求职意向：后端开发实习生

教育经历
2022.09–2026.06  某大学  计算机科学与技术  本科

实习经历
2025.07–2025.09  某科技公司  后端开发实习生
- 参与了学院迎新系统的开发，负责后端接口。

项目经历
选课系统（课程项目）
- 使用 FastAPI 完成 3 个接口，数据库使用 MySQL。
- 优化了接口性能，QPS 提升 3 倍。

专业技能
- 熟悉 Python、FastAPI、MySQL；了解 Linux 基本命令。`

/** 虚构示例 JD（后端开发实习生方向） */
export const SAMPLE_JD_TEXT = `后端开发实习生（Python 方向）

岗位职责：
1. 负责业务系统后端接口的设计、开发与优化；
2. 参与高并发场景下的性能优化与稳定性建设；
3. 与产品、前端协作完成需求交付。

任职要求：
1. 熟悉 Python，了解 FastAPI / Flask 等 Web 框架；
2. 熟悉 MySQL，了解索引与事务；
3. 熟悉 Redis 缓存，了解常见缓存设计；
4. 有良好的编码习惯与问题排查能力。`

const SCENE_LABELS = { intern: '实习', autumn: '秋招', grad: '保研' }

const QUESTION_BANK = {
  intern: [
    { text: '请先做个自我介绍，并说明你为什么想找后端开发方向的实习？', direction: 'self_intro', follow_up_depth: 0 },
    {
      text: '你简历里提到「优化了接口性能，QPS 提升 3 倍」，能具体说说优化前的情况、你做了什么、结果怎么测出来的吗？',
      direction: 'data_source',
      follow_up_depth: 1,
    },
    { text: '如果让你设计一个选课系统的高并发方案，你会从哪些方面入手？', direction: 'design_probe', follow_up_depth: 2 },
  ],
  autumn: [
    { text: '请先自我介绍一下，并挑一个你最熟悉的项目讲讲你的核心贡献。', direction: 'self_intro', follow_up_depth: 0 },
    {
      text: '你提到做过缓存优化，为什么选择 Redis 而不是本地缓存？缓存一致性怎么处理？',
      direction: 'tech_probe',
      follow_up_depth: 1,
    },
    { text: '如果线上接口 P99 响应时间突然升高，你会怎么排查？', direction: 'troubleshooting', follow_up_depth: 2 },
  ],
  grad: [
    { text: '请介绍一下你的科研 / 项目经历，以及你的研究兴趣。', direction: 'self_intro', follow_up_depth: 0 },
    { text: '你在这个研究 / 项目中遇到的最大技术难点是什么？你是如何解决的？', direction: 'depth_probe', follow_up_depth: 1 },
    { text: '你未来两年的研究规划是什么？为什么选择这个方向？', direction: 'planning', follow_up_depth: 2 },
  ],
}

const CLOSING_QUESTION = {
  question_id: 'q_closing',
  text: '今天的面试到这里，谢谢你的时间。你还有什么想问我的吗？',
  direction: 'closing',
  follow_up_depth: 0,
}

const ROUTES_DATA = [
  { route: 'resume_diagnosis', model: 'deepseek-chat', temperature: 0.3, context_window_tokens: 65536, stream: false, timeout: 60, max_retries: 2 },
  { route: 'jd_matching', model: 'glm-4-air', temperature: 0.2, context_window_tokens: 131072, stream: false, timeout: 60, max_retries: 2 },
  { route: 'interview_agent', model: 'glm-4-flash', temperature: 0.8, context_window_tokens: 131072, stream: true, timeout: 120, max_retries: 3 },
  { route: 'evaluation', model: 'glm-4-plus', temperature: 0.1, context_window_tokens: 131072, stream: false, timeout: 60, max_retries: 2 },
  { route: 'cross_review', model: 'deepseek-chat', temperature: 0.1, context_window_tokens: 131072, stream: false, timeout: 60, max_retries: 2 },
]

const DIAGNOSE_DATA = {
  summary:
    '经历方向与后端开发岗位匹配，但成果量化明显不足（全文只有 1 处数字），且缓存 / 高并发关键词缺失。建议优先补齐指标与个人贡献边界，再处理格式细节。',
  issues: [
    {
      check_item: '量化表达',
      priority: 'high',
      original_quote: '参与了学院迎新系统的开发，负责后端接口。',
      problem: '缺少可量化结果，也未说明个人贡献边界，面试官无法判断真实参与度。',
      rewrite:
        '独立负责学院迎新系统 3 个核心接口（登录 / 选课 / 通知）的设计与实现，上线支撑 1200+ 新生并发选课，接口平均响应 < 200ms。',
    },
    {
      check_item: '动词弱化',
      priority: 'medium',
      original_quote: '参与了……负责……',
      problem: '「参与 / 负责」是弱动词，无法体现主导性与技术决策。',
      rewrite: '主导接口设计与实现 / 独立完成……并用具体动作替代泛化描述。',
    },
    {
      check_item: '关键词对齐',
      priority: 'high',
      original_quote: '熟悉 Python、FastAPI、MySQL；了解 Linux 基本命令。',
      problem: '目标 JD 要求「Redis 缓存 / 高并发」，简历中完全未出现相关关键词。',
      rewrite:
        '补充缓存实践：使用 Redis 缓存热点选课数据并设置过期策略，接口 P99 从 800ms 降至 120ms（写入项目描述）。',
    },
    {
      check_item: '结构问题',
      priority: 'medium',
      original_quote: '选课系统（课程项目）：使用 FastAPI 完成 3 个接口，数据库使用 MySQL。',
      problem: '经历按流水账罗列，未按「背景→目标→动作→结果」组织，重点不突出。',
      rewrite: '按 STAR 结构重写：背景（课程项目 200 人使用）→ 目标（支撑选课峰值）→ 动作（接口 / 缓存）→ 结果（P99 与 QPS）。',
    },
    {
      check_item: '错别字格式',
      priority: 'low',
      original_quote: '2022.09–2026.06 某大学 计算机科学与技术 本科',
      problem: '其余经历时间线缺月份，中英文标点混用，格式不一致。',
      rewrite: '统一时间格式为 YYYY.MM–YYYY.MM；中英文标点分别使用对应全 / 半角。',
    },
  ],
  strengths: ['项目经历与目标岗位方向一致', '有真实的后端接口开发经验', '技术栈覆盖 Python / MySQL，具备继续补齐缓存的基础'],
}

const JD_STRUCTURED_DATA = {
  title: '后端开发实习生（Python 方向）',
  responsibilities: ['负责业务系统后端接口的设计、开发与优化', '参与高并发场景下的性能优化与稳定性建设', '与产品、前端协作完成需求交付'],
  capabilities: [
    { capability: 'Python / Web 框架', level: '熟悉', evidence: '熟悉 Python，了解 FastAPI / Flask 等 Web 框架' },
    { capability: 'MySQL', level: '熟悉', evidence: '熟悉 MySQL，了解索引与事务' },
    { capability: 'Redis 缓存', level: '熟悉', evidence: '熟悉 Redis 缓存，了解常见缓存设计' },
    { capability: '问题排查能力', level: '了解', evidence: '有良好的编码习惯与问题排查能力' },
  ],
}

const JD_MATCH_DATA = {
  score: 78,
  summary:
    '整体匹配度较高：Python 后端开发与项目实践可对应岗位要求；主要差距在 Redis 缓存与高并发场景的量化证据，建议补充一个可讲清楚的优化案例。',
  items: [
    {
      capability: 'Python 后端开发',
      status: 'matched',
      evidence: '项目中「使用 FastAPI 完成 3 个接口」',
      comment: '有真实接口开发经验，建议补充接口复杂度与用户量。',
    },
    {
      capability: 'MySQL 数据库',
      status: 'partial',
      evidence: '「数据库使用 MySQL」',
      comment: '只提到使用，未体现索引 / 事务 / 慢查询优化能力。',
    },
    {
      capability: 'Redis 缓存',
      status: 'missing',
      evidence: '简历未出现 Redis 相关经历',
      comment: 'JD 明确要求熟悉 Redis，是当前最大缺口。',
    },
    {
      capability: '高并发 / 性能优化',
      status: 'partial',
      evidence: '「优化了接口性能，QPS 提升 3 倍」',
      comment: '有结果但缺少优化前指标、具体手段与测量方法。',
    },
  ],
  gaps: [
    {
      capability: 'Redis 缓存',
      gap: '简历未体现缓存设计、使用场景与效果数据',
      suggestion: '补一个热点数据缓存小实践（含过期 / 一致性策略），并在简历写明优化前后指标。',
    },
    {
      capability: '高并发经验',
      gap: '未体现 QPS、压测方法与瓶颈定位过程',
      suggestion: '准备 1 个可讲 3 分钟的压测与优化案例：如何定位瓶颈、做了什么、结果如何验证。',
    },
  ],
}

const REPORT_DATA = {
  evaluation: {
    dimensions: [
      {
        dimension: '相关性',
        score: 8,
        evidence: ['回答围绕岗位要求展开', '项目经验与 JD 的 Python 后端方向一致'],
      },
      {
        dimension: '结构性',
        score: 7,
        evidence: ['项目介绍按背景→动作→结果组织', '个别回答缺少总结句，收尾略仓促'],
      },
      {
        dimension: '具体性',
        score: 6,
        evidence: ['提到「优化了接口性能」，但未给出优化前 QPS 与测量方法', '缓存选型回答停留在概念层'],
      },
      {
        dimension: '岗位匹配',
        score: 7,
        evidence: ['后端开发实践与 JD 职责匹配', '分布式 / 高并发相关经历不足'],
      },
    ],
    strengths: ['能围绕项目讲清技术选型与个人贡献', '对性能优化有基本认知，方向正确'],
    weaknesses: ['量化数据不足，成果难以验证', '追问时需要更深入的原理解释'],
    suggestions: [
      '用 STAR 结构回答行为类问题，先给结论再展开细节',
      '为每个项目准备 1–2 个可量化指标（QPS / 时延 / 用户量）',
      '提前准备 Redis 缓存、缓存一致性、高并发排查等高频追问',
    ],
  },
  cross_review: {
    agreed: true,
    adjustments: [],
    deviation_note: '复核模型（DeepSeek-V3，与评分模型不同）认可 4 个维度评分，仅建议「具体性」维持 6 分。',
  },
}

function buildReportMarkdown(reportId, evaluation, crossReview) {
  const dimensionLines = evaluation.dimensions
    .map(
      (item) =>
        `### ${item.dimension}：${item.score} / 10\n\n` +
        item.evidence.map((evidence) => `- 证据：${evidence}`).join('\n'),
    )
    .join('\n\n')
  return [
    '# 面试反馈报告',
    '',
    `> 报告 ID：${reportId}（Mock 示例数据）`,
    '',
    '## 维度评分',
    '',
    dimensionLines,
    '',
    '## 亮点',
    '',
    ...evaluation.strengths.map((item) => `- ${item}`),
    '',
    '## 短板',
    '',
    ...evaluation.weaknesses.map((item) => `- ${item}`),
    '',
    '## 改进建议',
    '',
    ...evaluation.suggestions.map((item, index) => `${index + 1}. ${item}`),
    '',
    '## 交叉复核',
    '',
    `- 结论：${crossReview.agreed ? '认可首评' : '存在调整'}`,
    `- 说明：${crossReview.deviation_note}`,
    '',
  ].join('\n')
}

// ------------------------------------------------------------------ //
// 会话 / 报告状态（仅内存，刷新页面即重置；属 Mock 层内部状态）
// ------------------------------------------------------------------ //
const sessions = new Map()
const reports = new Map()
let sessionSeq = 0
let reportSeq = 0

function createSessionState(payload) {
  const scene = ['intern', 'autumn', 'grad'].includes(payload?.scene) ? payload.scene : 'intern'
  sessionSeq += 1
  const bank = QUESTION_BANK[scene].map((question, index) => ({
    question_id: `q${index + 1}`,
    ...question,
  }))
  const session = {
    session_id: `sess_mock_${String(sessionSeq).padStart(2, '0')}`,
    scene,
    company: payload?.company || '某科技公司',
    role: payload?.role || '后端开发实习生',
    style: payload?.style || 'friendly',
    bank,
    turns: [],
    currentQuestion: bank[0],
    ended: false,
  }
  sessions.set(session.session_id, session)
  return session
}

function sessionState(session) {
  return {
    turn_index: session.turns.length,
    scene: session.scene,
    asked_questions: session.turns.map((turn) => turn.question.question_id),
    context_usage_pct: Math.min(95, 2 + session.turns.length * 6),
  }
}

function buildNextQuestion(session) {
  const nextIndex = session.turns.length
  return session.bank[nextIndex] || CLOSING_QUESTION
}

function questionPayload(question) {
  return {
    question_id: question.question_id,
    text: question.text,
    direction: question.direction,
    follow_up_depth: question.follow_up_depth,
  }
}

// ------------------------------------------------------------------ //
// 对外 Mock 接口（与 client.js 的 http 实现同构）
// ------------------------------------------------------------------ //
export const mock = {
  async metaRoutes() {
    await sleep(LATENCY_MS)
    return { routes: ROUTES_DATA.map((route) => ({ ...route })) }
  },

  async resumeParse(file) {
    await sleep(LATENCY_MS)
    const filename = file?.name || 'demo_resume.docx'
    const kind = filename.toLowerCase().endsWith('.pdf') ? 'pdf' : 'docx'
    return {
      filename,
      kind,
      text: SAMPLE_RESUME_TEXT,
      segments: [
        { heading: '教育经历', text: '2022.09–2026.06 某大学 计算机科学与技术 本科' },
        { heading: '实习经历', text: '某科技公司 后端开发实习生：参与了学院迎新系统的开发，负责后端接口。' },
        { heading: '项目经历', text: '选课系统：使用 FastAPI 完成 3 个接口，数据库使用 MySQL；优化了接口性能，QPS 提升 3 倍。' },
      ],
      page_count: kind === 'pdf' ? 1 : null,
      char_count: SAMPLE_RESUME_TEXT.length,
      degraded: false,
      warnings: [],
    }
  },

  async resumeStructure() {
    await sleep(LATENCY_MS)
    return {
      name: '张同学',
      education: [{ school: '某大学', major: '计算机科学与技术', degree: '本科', period: '2022.09–2026.06' }],
      experiences: [
        {
          organization: '某科技公司',
          role: '后端开发实习生',
          period: '2025.07–2025.09',
          highlights: ['参与了学院迎新系统的开发，负责后端接口。'],
        },
      ],
      skills: ['Python', 'FastAPI', 'MySQL', 'Linux'],
      projects: [
        {
          name: '选课系统（课程项目）',
          role: '后端开发',
          description: '使用 FastAPI 完成 3 个接口，数据库使用 MySQL。',
          highlights: ['优化了接口性能，QPS 提升 3 倍。'],
        },
      ],
    }
  },

  async resumeDiagnose() {
    await sleep(LATENCY_MS * 2)
    return { ...DIAGNOSE_DATA, issues: DIAGNOSE_DATA.issues.map((issue) => ({ ...issue })) }
  },

  async jdStructure() {
    await sleep(LATENCY_MS)
    return { ...JD_STRUCTURED_DATA, capabilities: JD_STRUCTURED_DATA.capabilities.map((item) => ({ ...item })) }
  },

  async jdMatch() {
    await sleep(LATENCY_MS * 2)
    return {
      ...JD_MATCH_DATA,
      items: JD_MATCH_DATA.items.map((item) => ({ ...item })),
      gaps: JD_MATCH_DATA.gaps.map((item) => ({ ...item })),
    }
  },

  async createInterviewSession(payload) {
    await sleep(LATENCY_MS * 2)
    const session = createSessionState(payload)
    return {
      session_id: session.session_id,
      question: questionPayload(session.currentQuestion),
      state: sessionState(session),
    }
  },

  async submitAnswer(sessionId, answer) {
    await sleep(LATENCY_MS * 2)
    const session = sessions.get(sessionId)
    if (!session) throw new Error(`会话不存在：${sessionId}`)
    session.turns.push({ question: session.currentQuestion, answer: String(answer ?? '') })
    const nextQuestion = buildNextQuestion(session)
    session.currentQuestion = nextQuestion
    return {
      session_id: sessionId,
      turn_index: session.turns.length,
      question: questionPayload(nextQuestion),
      state: { ...sessionState(session), rule_id: nextQuestion.direction },
    }
  },

  async streamAnswer(sessionId, answer, { onEvent, signal } = {}) {
    const session = sessions.get(sessionId)
    if (!session) throw new Error(`会话不存在：${sessionId}`)
    session.turns.push({ question: session.currentQuestion, answer: String(answer ?? '') })
    const nextQuestion = buildNextQuestion(session)
    session.currentQuestion = nextQuestion

    // 模拟 SSE：逐字 delta → done（契约：docs/api.md 五、turns/stream）
    const text = nextQuestion.text
    for (const char of text) {
      if (signal?.aborted) return null
      await sleep(12)
      onEvent?.({ event: 'delta', text: char })
    }
    const doneEvent = {
      event: 'done',
      question: questionPayload(nextQuestion),
      state: { ...sessionState(session), turn_index: session.turns.length, rule_id: nextQuestion.direction },
      usage: { prompt_tokens: 120, completion_tokens: text.length, total_tokens: 120 + text.length },
    }
    onEvent?.(doneEvent)
    return doneEvent
  },

  async getSession(sessionId) {
    await sleep(LATENCY_MS)
    const session = sessions.get(sessionId)
    if (!session) throw new Error(`会话不存在：${sessionId}`)
    return {
      session_id: session.session_id,
      scene: session.scene,
      turn_index: session.turns.length,
      questions_asked: session.turns.map((turn) => questionPayload(turn.question)),
      summary: '',
      context_usage_pct: sessionState(session).context_usage_pct,
      ended: session.ended,
    }
  },

  async endSession(sessionId) {
    await sleep(LATENCY_MS)
    const session = sessions.get(sessionId)
    if (!session) throw new Error(`会话不存在：${sessionId}`)
    session.ended = true
    return {
      session_id: session.session_id,
      transcript: session.turns.map((turn, index) => ({
        turn: index + 1,
        question: turn.question.text,
        answer: turn.answer,
        rule_id: turn.question.direction,
        direction: turn.question.direction,
        follow_up_depth: turn.question.follow_up_depth,
      })),
      state: { turn_index: session.turns.length, context_usage_pct: sessionState(session).context_usage_pct },
      report_input: {
        resume_text: SAMPLE_RESUME_TEXT,
        jd_text: SAMPLE_JD_TEXT,
        scene: session.scene,
        turn_count: session.turns.length,
      },
    }
  },

  async generateReport(payload) {
    await sleep(LATENCY_MS * 2)
    reportSeq += 1
    const reportId = `rep_mock_${String(reportSeq).padStart(2, '0')}`
    const evaluation = JSON.parse(JSON.stringify(REPORT_DATA.evaluation))
    const crossReview = JSON.parse(JSON.stringify(REPORT_DATA.cross_review))
    const report = {
      report_id: reportId,
      evaluation,
      cross_review: crossReview,
      markdown: buildReportMarkdown(reportId, evaluation, crossReview),
      session_id: payload?.session_id || null,
    }
    reports.set(reportId, report)
    return report
  },

  async getReport(reportId) {
    await sleep(LATENCY_MS)
    const report = reports.get(reportId)
    if (!report) throw new Error(`报告不存在：${reportId}`)
    return report
  },

  async getReportMarkdown(reportId) {
    await sleep(LATENCY_MS)
    const report = reports.get(reportId)
    if (!report) throw new Error(`报告不存在：${reportId}`)
    return report.markdown
  },
}
