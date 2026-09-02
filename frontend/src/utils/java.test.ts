import { describe, expect, it } from 'vitest'

import { extractMinecraftVersion, requiredJavaMajor } from './java'

describe('requiredJavaMajor', () => {
  it.each([
    ['1.7.10', 8],
    ['1.11.2', 8],
    ['1.12.2', 11],
    ['1.16.4', 11],
    ['1.16.5', 16],
    ['1.17.1', 17],
    ['1.19.4', 17],
    ['1.20.4', 21],
    ['1.21.11-rc3', 21],
    ['26.1', 25],
    ['26.2-rc-2', 25],
  ])('maps Minecraft %s to Java %s', (mcVersion, javaMajor) => {
    expect(requiredJavaMajor(mcVersion)).toBe(javaMajor)
  })

  it.each(['', '1.7.9', '1.21.12', '25.1', 'latest'])(
    'rejects unsupported version %s',
    (mcVersion) => {
      expect(requiredJavaMajor(mcVersion)).toBeNull()
    },
  )
})

describe('extractMinecraftVersion', () => {
  it.each([
    ['SkyWars-1.20.4.zip', '1.20.4'],
    ['竞技场 [1.21.11]', '1.21.11'],
    ['paper-26.2-rc-2-map.zip', '26.2-rc-2'],
  ])('extracts a supported version from %s', (value, version) => {
    expect(extractMinecraftVersion(value)).toBe(version)
  })

  it.each(['SkyWars.zip', 'map-1.7.9.zip', 'backup-25.1.zip'])(
    'does not infer an unsupported version from %s',
    (value) => {
      expect(extractMinecraftVersion(value)).toBeNull()
    },
  )
})