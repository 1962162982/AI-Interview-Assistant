/**
 * JD 匹配雷达图数据推导（纯函数 · 无第三方图表库）。
 *
 * 输入：`POST /api/jd/match` 响应 `data`（docs/api.md「四」）：
 *   { score, summary, items: [{ capability, status, evidence, comment }], gaps: [...] }
 * 输出：5 维雷达图的 SVG 几何数据（viewBox 360 × 320，中心 (180, 170)，半径 110）。
 * `items` 为空 / 非法时返回 null，由视图渲染「无数据」占位（不画空图）。
 *
 * 维度与推导规则（全部由 items 的 matched / partial / missing 与总分 score 推导，写死可复现）：
 * 1) 技能匹配 = 能力项加权覆盖率 ×10：matched 记 1 分、partial 记 0.5 分、missing 记 0 分，取平均；
 * 2) 项目经验 = matched 能力项占比 ×10：被判定「已覆盖」的能力项比例（有明确项目/经历证据支撑）；
 * 3) 量化表达 = 证据含数字的能力项占比 ×10：evidence 文本中出现数字（/[0-9]/）视为量化证据；
 * 4) 岗位相关 = 总分 score / 100 ×10：直接用整体匹配度（0–100 → 0–10）；
 * 5) 成长潜力 = (1 − missing 占比) ×10：缺口越少、补齐后可达成的上限越高（缺失项为 0 时满分）。
 * 所有维度四舍五入保留 1 位小数并 clamp 到 [0, 10]；数据点按维度顺序从正上方开始顺时针分布。
 *
 * 说明：本模块只做数据推导与几何计算，便于在 Node 中单测（不依赖 DOM / Vue / 网络）；
 * 视图 JdMatchView.vue 负责 hover、文本值列表、aria-label 等交互与无障碍语义。
 */

const VIEW_BOX = '0 0 360 320'
const CENTER_X = 180
const CENTER_Y = 170
const RADIUS = 110
const LABEL_RADIUS = 136
const RING_LEVELS = [0.2, 0.4, 0.6, 0.8, 1]

const STATUS_KEYS = ['matched', 'partial', 'missing']

function clampScore(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 0
  return Math.round(Math.min(10, Math.max(0, numeric)) * 10) / 10
}

function angleAt(index, total) {
  // 从正上方（-90°）开始顺时针均匀分布
  return ((-90 + (360 / total) * index) * Math.PI) / 180
}

function pointAt(index, total, ratio) {
  const angle = angleAt(index, total)
  return {
    x: CENTER_X + RADIUS * ratio * Math.cos(angle),
    y: CENTER_Y + RADIUS * ratio * Math.sin(angle),
  }
}

function pathPoints(total, ratio) {
  return Array.from({ length: total }, (_, index) => pointAt(index, total, ratio))
    .map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`)
    .join(' ')
}

/**
 * 推导 5 维雷达图数据。
 * @param {{ score?: number, items?: Array<{ status?: string, evidence?: string }> } | null} result
 * @returns {null | { dimensions: Array<{key,label,value,rule}>, points, polygon, axes, rings, labels, center, viewBox }}
 */
export function buildRadarData(result) {
  const items = Array.isArray(result?.items) ? result.items.filter((item) => item && typeof item === 'object') : []
  if (items.length === 0) return null

  const total = items.length
  const counts = { matched: 0, partial: 0, missing: 0 }
  let numericEvidence = 0
  for (const item of items) {
    const status = String(item.status ?? '').toLowerCase()
    if (STATUS_KEYS.includes(status)) counts[status] += 1
    if (/[0-9]/.test(String(item.evidence ?? ''))) numericEvidence += 1
  }

  const matchedRatio = counts.matched / total
  const partialRatio = counts.partial / total
  const missingRatio = counts.missing / total
  const weightedCoverage = (counts.matched + 0.5 * counts.partial) / total
  const overallScore = Number(result?.score)
  const scoreRatio = Number.isFinite(overallScore) ? Math.min(100, Math.max(0, overallScore)) / 100 : weightedCoverage

  const dimensions = [
    {
      key: 'skill',
      label: '技能匹配',
      value: clampScore(weightedCoverage * 10),
      rule: `能力项加权覆盖：matched ${counts.matched} / partial ${counts.partial} / missing ${counts.missing}（权重 1 / 0.5 / 0）`,
    },
    {
      key: 'project',
      label: '项目经验',
      value: clampScore(matchedRatio * 10),
      rule: `被判定「已覆盖」的能力项占比（${counts.matched}/${total}）`,
    },
    {
      key: 'quantification',
      label: '量化表达',
      value: clampScore((numericEvidence / total) * 10),
      rule: `能力项证据中含数字的比例（${numericEvidence}/${total}）`,
    },
    {
      key: 'role',
      label: '岗位相关',
      value: clampScore(scoreRatio * 10),
      rule: `整体匹配度 score=${Number.isFinite(overallScore) ? overallScore : '-'} / 100`,
    },
    {
      key: 'growth',
      label: '成长潜力',
      value: clampScore((1 - missingRatio) * 10),
      rule: `缺口（missing）越少、补齐后可达上限越高（missing ${counts.missing}/${total}）`,
    },
  ]

  const dimensionCount = dimensions.length
  const points = dimensions.map((dimension, index) => {
    const point = pointAt(index, dimensionCount, dimension.value / 10)
    return { x: Number(point.x.toFixed(1)), y: Number(point.y.toFixed(1)) }
  })
  const axes = dimensions.map((_, index) => {
    const point = pointAt(index, dimensionCount, 1)
    return { x: Number(point.x.toFixed(1)), y: Number(point.y.toFixed(1)) }
  })
  const labels = dimensions.map((dimension, index) => {
    const angle = angleAt(index, dimensionCount)
    const x = CENTER_X + LABEL_RADIUS * Math.cos(angle)
    const y = CENTER_Y + LABEL_RADIUS * Math.sin(angle)
    const cosine = Math.cos(angle)
    const anchor = Math.abs(cosine) < 0.2 ? 'middle' : cosine > 0 ? 'start' : 'end'
    return { x: Number(x.toFixed(1)), y: Number((y + 4).toFixed(1)), anchor }
  })

  return {
    dimensions,
    points,
    polygon: points.map((point) => `${point.x},${point.y}`).join(' '),
    axes,
    rings: RING_LEVELS.map((level) => ({ level, points: pathPoints(dimensionCount, level) })),
    labels,
    center: { x: CENTER_X, y: CENTER_Y },
    viewBox: VIEW_BOX,
    meta: {
      total,
      matched: counts.matched,
      partial: counts.partial,
      missing: counts.missing,
      numericEvidence,
      score: Number.isFinite(overallScore) ? overallScore : null,
    },
  }
}
