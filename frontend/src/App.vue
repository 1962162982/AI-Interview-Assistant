<script setup>
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

const route = useRoute()

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
