import DefaultTheme from 'vitepress/theme'
import { h } from 'vue'
import SidebarToggle from './SidebarToggle.vue'
import './custom.css'

export default {
  extends: DefaultTheme,
  Layout() {
    return h(DefaultTheme.Layout, null, {
      // Inject the sidebar toggle button outside the title <a> tag
      'nav-bar-content-before': () => h(SidebarToggle)
    })
  }
}
