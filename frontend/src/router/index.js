import { createRouter, createWebHistory } from 'vue-router'

// 交互层 5 个页面路由（决策①：Web 应用）。
// 视图组件按需加载，保持骨架轻量；页面职责见 docs/architecture.md 第 5 节。
const routes = [
  {
    path: '/',
    name: 'home',
    component: () => import('../views/HomeView.vue'),
    meta: { title: '首页' },
  },
  {
    path: '/resume',
    name: 'resume',
    component: () => import('../views/ResumeView.vue'),
    meta: { title: '简历诊断' },
  },
  {
    path: '/jd',
    name: 'jd',
    component: () => import('../views/JdMatchView.vue'),
    meta: { title: 'JD 匹配' },
  },
  {
    path: '/interview',
    name: 'interview',
    component: () => import('../views/InterviewView.vue'),
    meta: { title: '模拟面试' },
  },
  {
    path: '/report',
    name: 'report',
    component: () => import('../views/ReportView.vue'),
    meta: { title: '反馈报告' },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = to.meta.title ? `${to.meta.title} · ` : ''
  document.title = `${title}AI 模拟面试与简历诊断助手`
})

export default router
