import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ServerSettingsFields from './ServerSettingsFields.vue'

describe('ServerSettingsFields', () => {
  it('emits structured numeric and boolean settings', async () => {
    const wrapper = mount(ServerSettingsFields, {
      props: { modelValue: { custom: {} } },
    })

    await wrapper.find('input[type="number"]').setValue('0')
    const numeric = wrapper.emitted('update:modelValue')?.at(-1)?.[0]
    expect(numeric).toMatchObject({ spawn_protection: 0, custom: {} })

    const pvp = wrapper.findAll('select')[2]
    await pvp?.setValue('false')
    const boolean = wrapper.emitted('update:modelValue')?.at(-1)?.[0]
    expect(boolean).toMatchObject({ pvp: false, custom: {} })
  })

  it('only shows world generation fields in that context', () => {
    const hidden = mount(ServerSettingsFields, {
      props: { modelValue: { custom: {} } },
    })
    const visible = mount(ServerSettingsFields, {
      props: { modelValue: { custom: {} }, worldGeneration: true },
    })

    expect(hidden.text()).not.toContain('世界种子')
    expect(visible.text()).toContain('世界种子')
    expect(visible.text()).toContain('生成结构')
  })

  it('does not overwrite an existing custom property when renaming', async () => {
    const wrapper = mount(ServerSettingsFields, {
      props: { modelValue: { custom: { first: '1', second: '2' } } },
    })
    const nameInput = wrapper.findAll('input[aria-label="属性名"]')[0]

    await nameInput?.setValue('second')
    await nameInput?.trigger('change')

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect((nameInput?.element as HTMLInputElement).value).toBe('first')
  })
})