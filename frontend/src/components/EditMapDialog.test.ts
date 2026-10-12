import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '../api/client'
import type { MapRecord } from '../api/types'
import EditMapDialog from './EditMapDialog.vue'
import ServerSettingsFields from './ServerSettingsFields.vue'

const map: MapRecord = {
  map_id: 7, state: 'ready', name: 'Original', source_type: 'uploaded',
  mc_version: '1.20.4', paper_build: '497', java_major: 21, data_version: 3700,
  created_at: '2026-10-12T00:00:00Z', server_settings: { pvp: false, custom: {} },
  resource_pack: {
    filename: 'pack.zip', sha1: 'abc', sha256: 'def', size_bytes: 100,
    pack_format: 22, required: false, prompt: 'Old prompt', url: '/pack.zip',
  },
}

afterEach(() => vi.restoreAllMocks())

function render(record = map) {
  return mount(EditMapDialog, {
    props: { open: true, map: record },
    global: { stubs: { teleport: true } },
  })
}

describe('EditMapDialog', () => {
  it('saves editable fields and clears the resource pack prompt', async () => {
    const update = vi.spyOn(api, 'updateMap').mockResolvedValue({ ...map, name: 'Renamed' })
    const wrapper = render()
    await wrapper.find('input[maxlength="255"]').setValue(' Renamed ')
    await wrapper.find('input[type="checkbox"]').setValue(true)
    await wrapper.find('input[maxlength="256"]').setValue('')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenCalledWith(7, {
      name: 'Renamed',
      resource_pack_required: true, resource_pack_prompt: null,
    })
    expect(wrapper.emitted('saved')?.[0]?.[0]).toMatchObject({ name: 'Renamed' })
    expect(map.name).toBe('Original')
    expect(map.resource_pack?.prompt).toBe('Old prompt')
    wrapper.unmount()
  })

  it('keeps the draft after failure and allows retry without resource pack fields', async () => {
    const update = vi.spyOn(api, 'updateMap')
      .mockRejectedValueOnce(new ApiError(409, 'map_busy', '地图正在创建游戏'))
      .mockResolvedValueOnce({ ...map, resource_pack: null })
    const wrapper = render({ ...map, resource_pack: null, source_type: 'generated' })
    expect(wrapper.text()).toContain('世界种子')
    await wrapper.find('input[maxlength="255"]').setValue('Draft')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toBe('地图正在创建游戏')
    expect(wrapper.emitted('saved')).toBeUndefined()
    expect((wrapper.find('input[maxlength="255"]').element as HTMLInputElement).value).toBe('Draft')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenLastCalledWith(7, { name: 'Draft' })
    expect(wrapper.emitted('saved')).toHaveLength(1)
    wrapper.unmount()
  })

  it('sends only the name when settings and resource pack are unchanged', async () => {
    const update = vi.spyOn(api, 'updateMap').mockResolvedValue({ ...map, name: 'Renamed' })
    const wrapper = render()
    await wrapper.find('input[maxlength="255"]').setValue('Renamed')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenCalledWith(7, { name: 'Renamed' })
    wrapper.unmount()
  })

  it('submits changed server settings without mutating the original custom properties', async () => {
    const update = vi.spyOn(api, 'updateMap').mockResolvedValue(map)
    const wrapper = render()
    const fields = wrapper.findComponent(ServerSettingsFields)
    fields.props('modelValue').custom.motd = 'New message'
    fields.vm.$emit('update:modelValue', { ...fields.props('modelValue'), pvp: true })
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenCalledWith(7, {
      name: 'Original', server_settings: { pvp: true, custom: { motd: 'New message' } },
    })
    expect(map.server_settings).toEqual({ pvp: false, custom: {} })
    wrapper.unmount()
  })

  it('cancels without saving and resets drafts when reopened', async () => {
    const update = vi.spyOn(api, 'updateMap')
    const wrapper = render()
    await wrapper.find('input[maxlength="255"]').setValue('Unsaved')
    await wrapper.find('.dialog-actions button[type="button"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(update).not.toHaveBeenCalled()
    await wrapper.setProps({ open: false })
    await wrapper.setProps({ open: true })
    expect((wrapper.find('input[maxlength="255"]').element as HTMLInputElement).value).toBe('Original')
    wrapper.unmount()
  })

  it('blocks duplicate submissions while saving', async () => {
    let finish!: (value: MapRecord) => void
    const update = vi.spyOn(api, 'updateMap').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const wrapper = render()
    await wrapper.find('form').trigger('submit')
    await wrapper.find('form').trigger('submit')
    expect(update).toHaveBeenCalledTimes(1)
    expect(wrapper.find('fieldset').attributes('disabled')).toBeDefined()
    finish(map)
    await flushPromises()
    expect(wrapper.emitted('saved')).toHaveLength(1)
    wrapper.unmount()
  })
})