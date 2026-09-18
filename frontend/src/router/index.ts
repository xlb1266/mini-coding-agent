import { createRouter, createWebHistory } from 'vue-router'

import AgentsView from '../views/AgentsView.vue'
import ChatView from '../views/ChatView.vue'
import EvaluationView from '../views/EvaluationView.vue'
import McpView from '../views/McpView.vue'
import ModelsView from '../views/ModelsView.vue'
import RunsView from '../views/RunsView.vue'
import RunDetailView from '../views/RunDetailView.vue'
import SkillEditView from '../views/SkillEditView.vue'
import SkillsView from '../views/SkillsView.vue'
import ToolDetailView from '../views/ToolDetailView.vue'
import ToolsView from '../views/ToolsView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/chat', name: 'chat', component: ChatView },
    { path: '/agents', name: 'agents', component: AgentsView },
    { path: '/models', name: 'models', component: ModelsView },
    { path: '/tools', name: 'tools', component: ToolsView },
    { path: '/tools/:id', name: 'tool-detail', component: ToolDetailView },
    { path: '/mcp', name: 'mcp', component: McpView },
    { path: '/skills', name: 'skills', component: SkillsView },
    { path: '/skills/:dirName/edit', name: 'skill-edit', component: SkillEditView },
    { path: '/runs', name: 'runs', component: RunsView },
    { path: '/runs/:id', name: 'run-detail', component: RunDetailView },
    { path: '/evaluation', name: 'evaluation', component: EvaluationView },
    // 未匹配地址统一回到聊天页，避免刷新后出现空白
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})

export default router
