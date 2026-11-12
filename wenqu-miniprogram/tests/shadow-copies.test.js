import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)))

// `src/` is the CLI-managed source of truth (see README). Before the consolidation, copies of
// these files were left at the repo root and stayed git-tracked, so a fix could land in one
// copy only. This test makes that drift class impossible rather than relying on review.
const ALLOWED_PREFIXES = ['src/', 'scripts/', 'static/', 'minitest/']

function trackedFiles() {
  return execFileSync('git', ['ls-files'], { cwd: repoRoot, encoding: 'utf8' })
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

function shaOf(relativePath) {
  return createHash('sha256').update(readFileSync(join(repoRoot, relativePath))).digest('hex')
}

describe('no tracked shadow copies of src/ files', () => {
  it('finds no duplicate content hashes outside the allowed source directories', () => {
    const files = trackedFiles()
    const srcHashes = new Map()
    for (const file of files) {
      if (file.startsWith('src/')) {
        srcHashes.set(shaOf(file), file)
      }
    }

    const duplicates = []
    for (const file of files) {
      if (ALLOWED_PREFIXES.some((prefix) => file.startsWith(prefix))) {
        continue
      }
      const hash = shaOf(file)
      if (srcHashes.has(hash)) {
        duplicates.push(`${file} duplicates ${srcHashes.get(hash)}`)
      }
    }

    expect(duplicates).toEqual([])
  })
})
