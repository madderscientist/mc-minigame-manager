import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, tokenSession } from '../api/client'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(tokenSession.get())
  const checking = ref(false)
  const error = ref('')
  const unlocked = computed(() => Boolean(token.value))

  async function unlock(value: string) {
    checking.value = true
    error.value = ''
    tokenSession.set(value.trim())
    token.value = value.trim()
    try {
      await api.maps()
    } catch (reason) {
      lock()
      error.value = reason instanceof Error ? reason.message : 'Token 验证失败'
      throw reason
    } finally {
      checking.value = false
    }
  }

  function lock() {
    tokenSession.clear()
    token.value = ''
  }

  return { token, checking, error, unlocked, unlock, lock }
})
