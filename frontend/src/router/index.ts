import { createRouter, createWebHistory } from 'vue-router'

import AppLayout from '../layouts/AppLayout.vue'
import DashboardPage from '../pages/DashboardPage.vue'
import GameDetailPage from '../pages/GameDetailPage.vue'
import GamesPage from '../pages/GamesPage.vue'
import HelpPage from '../pages/HelpPage.vue'
import MapDetailPage from '../pages/MapDetailPage.vue'
import MapsPage from '../pages/MapsPage.vue'
import TasksPage from '../pages/TasksPage.vue'
import UnlockPage from '../pages/UnlockPage.vue'
import { tokenSession } from '../api/client'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/unlock', component: UnlockPage, meta: { public: true } },
    {
      path: '/',
      component: AppLayout,
      children: [
        { path: '', component: DashboardPage },
        { path: 'games', component: GamesPage },
        { path: 'games/:gameId', component: GameDetailPage },
        { path: 'maps', component: MapsPage },
        { path: 'maps/:mapId', component: MapDetailPage },
        { path: 'tasks', component: TasksPage },
        { path: 'help', component: HelpPage },
      ],
    },
  ],
})

router.beforeEach((to) => {
  if (!to.meta.public && !tokenSession.get()) return '/unlock'
  if (to.path === '/unlock' && tokenSession.get()) return '/'
})

window.addEventListener('mc-manager:unauthorized', () => {
  void router.replace('/unlock')
})
