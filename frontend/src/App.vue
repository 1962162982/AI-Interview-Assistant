<script setup>
import { computed, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { getThinkingMode, setThinkingMode } from './api/client'

const route = useRoute()
const thinkingEnabled = ref(getThinkingMode())

function onThinkingChange(event) {
  thinkingEnabled.value = event.target.checked
  setThinkingMode(thinkingEnabled.value)
}

const navItems = [
  { name: 'home', path: '/', label: '首页' },
  { name: 'resume', path: '/resume', label: '简历诊断' },
  { name: 'jd', path: '/jd', label: 'JD 匹配' },
  { name: 'interview', path: '/interview', label: '模拟面试' },
  { name: 'report', path: '/report', label: '反馈报告' },
]

const currentTitle = computed(() => route.meta.title || '首页')
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="brand">
        <span class="brand-mark">AI</span>
        <div>
          <h1 class="brand-title">AI 模拟面试与简历诊断助手</h1>
          <p class="brand-subtitle">简历诊断 · 岗位匹配 · 模拟面试 · 反馈报告</p>
        </div>
      </div>
      <div class="header-actions">
        <label
          class="thinking-toggle"
          :class="{ 'is-on': thinkingEnabled }"
          title="开启后模型会先进行深度思考：复杂任务可能更准，但响应更慢（单次可达数十秒到数分钟）、消耗更多 token"
        >
          <input
            type="checkbox"
            :checked="thinkingEnabled"
            aria-label="深度思考开关"
            @change="onThinkingChange"
          />
          <span class="thinking-switch" aria-hidden="true"></span>
          <span>深度思考{{ thinkingEnabled ? '：开' : '：关' }}</span>
        </label>
        <nav class="app-nav" aria-label="主导航">
          <RouterLink
            v-for="item in navItems"
            :key="item.name"
            :to="item.path"
            class="nav-link"
            active-class="is-active"
          >
            {{ item.label }}
          </RouterLink>
        </nav>
      </div>
    </header>

    <main class="app-main">
      <RouterView />
    </main>

    <footer class="app-footer">
      <span>AI 模拟面试与简历诊断助手 · 仅供学习演示</span>
      <span>当前页面：{{ currentTitle }}</span>
    </footer>
  </div>
</template>
