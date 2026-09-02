const VERSION_PATTERN = /^\s*(\d+)\.(\d+)(?:\.(\d+))?(?:[-+][0-9A-Za-z.-]+)?\s*$/
const VERSION_IN_TEXT = /(?:^|[^0-9])((?:1|26)\.\d+(?:\.\d+)?(?:-(?:pre\d+|rc-?\d+))?)(?=$|[^0-9])/g

export function requiredJavaMajor(mcVersion: string): number | null {
  const match = VERSION_PATTERN.exec(mcVersion)
  if (!match) return null
  const major = Number(match[1])
  const minor = Number(match[2])
  const patch = Number(match[3] ?? 0)
  if (major === 1) {
    if ((minor === 7 && patch >= 10) || (minor >= 8 && minor <= 11)) return 8
    if ((minor >= 12 && minor <= 15) || (minor === 16 && patch <= 4)) return 11
    if (minor === 16 && patch >= 5) return 16
    if (minor >= 17 && minor <= 19) return 17
    if (minor === 20 || (minor === 21 && patch <= 11)) return 21
  }
  if (major === 26 && minor >= 1) return 25
  return null
}

export function extractMinecraftVersion(value: string): string | null {
  for (const match of value.replace(/\.zip$/i, '').matchAll(VERSION_IN_TEXT)) {
    const version = match[1]
    if (version && requiredJavaMajor(version) !== null) return version
  }
  return null
}