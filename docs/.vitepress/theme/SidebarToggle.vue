<script setup>
import { ref, onMounted } from 'vue'
import { useSidebar } from 'vitepress/theme'

const { hasSidebar } = useSidebar()
const isCollapsed = ref(false)

function toggleSidebar() {
  isCollapsed.value = !isCollapsed.value
  const html = document.documentElement
  if (isCollapsed.value) {
    html.classList.add('sidebar-collapsed')
  } else {
    html.classList.remove('sidebar-collapsed')
  }
}

// Clean up class on mount just in case
onMounted(() => {
  document.documentElement.classList.remove('sidebar-collapsed')
})
</script>

<template>
  <div v-if="hasSidebar" class="sidebar-toggle" @click.stop.prevent="toggleSidebar" @keydown.enter.space.prevent="toggleSidebar" role="button" tabindex="0" :aria-label="isCollapsed ? 'Expand Sidebar' : 'Collapse Sidebar'">
    <!-- Menu/Collapse toggle icons -->
    <svg v-if="isCollapsed" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <line x1="3" y1="12" x2="21" y2="12"></line>
      <line x1="3" y1="6" x2="21" y2="6"></line>
      <line x1="3" y1="18" x2="21" y2="18"></line>
    </svg>
    <svg v-else xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
      <line x1="9" y1="3" x2="9" y2="21"></line>
    </svg>
  </div>
</template>

<style scoped>
.sidebar-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 5px;
  margin-left: 12px;
  color: var(--vp-c-text-2);
  border: 1px solid var(--vp-c-divider);
  border-radius: 4px;
  background-color: var(--vp-c-bg-elv);
  cursor: pointer;
  transition: border-color 0.25s, color 0.25s;
}
.sidebar-toggle:hover {
  color: var(--vp-c-text-1);
  border-color: var(--vp-c-text-2);
}
@media (max-width: 960px) {
  /* Hide custom desktop collapse button on mobile/tablet screens */
  .sidebar-toggle {
    display: none;
  }
}
</style>
