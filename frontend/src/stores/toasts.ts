import { defineStore } from 'pinia'
import { ref } from 'vue'

interface Toast {
  id: string
  title: string
  message?: string
  tone: 'success' | 'danger' | 'neutral'
}

export const useToastStore = defineStore('toasts', () => {
  const items = ref<Toast[]>([])

  function push(title: string, message = '', tone: Toast['tone'] = 'neutral') {
    const id = crypto.randomUUID()
    items.value.push({ id, title, message, tone })
    window.setTimeout(() => remove(id), 5000)
  }

  function remove(id: string) {
    items.value = items.value.filter((item) => item.id !== id)
  }

  return { items, push, remove }
})
