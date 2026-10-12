import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '../api/client'
import type { MapUploadResult } from '../api/types'
import AddMapDialog from './AddMapDialog.vue'

let wrapper: VueWrapper | undefined

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/maps',
        component: defineComponent({
          setup: () => () => h(AddMapDialog, {
            open: true,
            onAdded: (id: number) => { void router.push(`/maps/${id}`) },
          }),
        }),
      },
      { path: '/maps/:mapId', component: { template: '<div>Map detail</div>' } },
    ],
  })
  await router.push('/maps')
  await router.isReady()
  wrapper = mount(RouterView, {
    global: { plugins: [router], stubs: { teleport: true } },
  })
  return { dialog: wrapper.findComponent(AddMapDialog), router }
}

describe('AddMapDialog completion', () => {
  it('allows detail navigation and removes unload protection after uploading', async () => {
    const alert = vi.fn()
    vi.stubGlobal('alert', alert)
    vi.spyOn(api, 'uploadMap').mockImplementation(async (_input, progress) => {
      progress(1)
      return { map_id: 7, name: 'Uploaded', mc_version: '1.20.4' }
    })
    const { dialog, router } = await render()
    const fileInput = dialog.find('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', {
      value: [new File(['world'], 'map.zip', { type: 'application/zip' })],
    })
    await fileInput.trigger('change')
    await dialog.find('.dialog-actions .primary').trigger('click')
    await flushPromises()
    expect(api.uploadMap).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.path).toBe('/maps/7')
    expect(alert).not.toHaveBeenCalled()
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(false)
  })

  it('still blocks leaving during server validation and releases protection on success', async () => {
    const alert = vi.fn()
    vi.stubGlobal('alert', alert)
    let finish!: (result: MapUploadResult) => void
    vi.spyOn(api, 'uploadMap').mockImplementation((_input, progress) => {
      progress(1)
      return new Promise((resolve) => { finish = resolve })
    })
    const { dialog, router } = await render()
    const fileInput = dialog.find('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', {
      value: [new File(['world'], 'map.zip', { type: 'application/zip' })],
    })
    await fileInput.trigger('change')
    await dialog.find('.dialog-actions .primary').trigger('click')
    await router.push('/maps/99')
    expect(router.currentRoute.value.path).toBe('/maps')
    expect(alert).toHaveBeenCalledWith('服务器正在创建地图模板，请等待操作完成后再离开。')
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    finish({ map_id: 7, name: 'Uploaded', mc_version: '1.20.4' })
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/maps/7')
  })

  it('allows detail navigation after generating a template', async () => {
    const alert = vi.fn()
    vi.stubGlobal('alert', alert)
    vi.spyOn(api, 'generateMap').mockResolvedValue({
      map_id: 8, name: 'Generated', mc_version: '1.20.4',
    })
    const { dialog, router } = await render()
    await dialog.findAll('[role="tab"]')[1]!.trigger('click')
    await dialog.find('.generated-fields input[maxlength="255"]').setValue('Generated')
    await dialog.find('.generated-fields input[placeholder="例如 1.21.11"]').setValue('1.20.4')
    await dialog.find('.dialog-actions .primary').trigger('click')
    await flushPromises()
    expect(api.generateMap).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.path).toBe('/maps/8')
    expect(alert).not.toHaveBeenCalled()
  })
})