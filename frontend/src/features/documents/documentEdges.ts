import type { DocumentRecord } from '../../api/types'

export type DocumentEdge = { source: number; target: number; kind: 'link' | 'text' }
export type GraphDepth = 1 | 2 | 3

const filler = new Set(
  'about after also before could from have into only should that their them then there these this those when which will with would более будет были если есть или которого которой можно могут нужно после перед также того через чтобы этот этом'.split(' '),
)

function key(value: string) {
  return value.trim().normalize('NFC').toLowerCase().replaceAll('ё', 'е')
}

function fileName(value: string) {
  return value.replaceAll('\\', '/').split('/').at(-1) ?? ''
}

function withoutExtension(value: string) {
  return value.replace(/\.(md|txt|pdf|docx)$/i, '')
}

function decoded(value: string) {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

function markdownSource(source: string, target: string) {
  const base = 'https://catalog.invalid/'
  try {
    const url = new URL(target, new URL(source.replaceAll('\\', '/'), base))
    url.hash = ''
    return url.href.startsWith(base) ? decoded(url.pathname.slice(1)) : url.href
  } catch {
    return target
  }
}

function words(value: string) {
  return (key(value).match(/[\p{L}]{4,}/gu) ?? []).filter(word => !filler.has(word)).map(word =>
    word.length < 6 ? word : word.replace(/(?:иями|ами|ями|ого|его|ыми|ими|иях|ах|ях|ий|ый|ая|ое|ые|ую|ам|ям|ов|ев|ы|и|а|я|у|е)$/u, ''),
  )
}

export function documentEdges(documents: DocumentRecord[]): DocumentEdge[] {
  const aliases = new Map<string, number | null>()
  const sources = new Map<string, number | null>()
  function remember(map: typeof aliases, alias: string, index: number) {
    const identifier = key(alias)
    if (!identifier) return
    if (!map.has(identifier)) map.set(identifier, index)
    else if (map.get(identifier) !== index) map.set(identifier, null)
  }
  documents.forEach(({ id, source, title }, index) => {
    remember(sources, source, index)
    const name = fileName(source)
    for (const alias of [id, title, name, withoutExtension(name)]) remember(aliases, alias, index)
  })

  const edges = new Map<string, DocumentEdge>()
  function connect(from: number, to: number | null | undefined, kind: DocumentEdge['kind']) {
    if (to == null || from === to) return
    const source = Math.min(from, to)
    const target = Math.max(from, to)
    const pair = `${source}:${target}`
    if (!edges.has(pair)) edges.set(pair, { source, target, kind })
  }

  documents.forEach(({ source, text }, from) => {
    for (const [, title] of text.matchAll(/\[\[([^\]\n#|]+)(?:[#|][^\]\n]*)?\]\]/g)) {
      const target = decoded(title)
      connect(from, sources.get(key(target)) ?? aliases.get(key(target)) ?? aliases.get(key(fileName(target))), 'link')
    }
    for (const [, bracketed, plain] of text.matchAll(/\[[^\]\n]+\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+"[^"\n]*")?\s*\)/g)) {
      const target = decoded(bracketed ?? plain)
      if (target.startsWith('#documents?')) {
        const id = new URLSearchParams((bracketed ?? plain).slice('#documents?'.length)).get('id')
        if (id) connect(from, aliases.get(key(id)), 'link')
      } else if (/^(?:[\w+.-]+:|\/\/)/.test(target)) {
        connect(from, sources.get(key(target)), 'link')
      } else {
        const path = target.split(/[?#]/, 1)[0].replace(/^\.\//, '')
        if (!path) continue
        const fallback = path.includes('/') ? undefined : aliases.get(key(path))
        connect(from, sources.get(key(markdownSource(source, bracketed ?? plain))) ?? fallback, 'link')
      }
    }
  })
  documents.forEach(({ text }, from) => {
    for (const [, title] of text.matchAll(/«([^»\n]+)»/g)) {
      connect(from, aliases.get(key(title)), 'text')
    }
  })

  const frequencies = new Map<string, number>()
  const counts = documents.map(({ title, text }) => {
    const terms = new Map<string, number>()
    // shortcut: use the first 24,000 characters for similarity, use indexed terms if longer files need more coverage.
    for (const word of words(text.slice(0, 24_000))) terms.set(word, Math.min(4, (terms.get(word) ?? 0) + 1))
    for (const word of new Set(words(title))) terms.set(word, (terms.get(word) ?? 0) + 3)
    for (const word of terms.keys()) frequencies.set(word, (frequencies.get(word) ?? 0) + 1)
    return terms
  })
  const postings = new Map<string, { index: number; weight: number }[]>()
  const vectors = counts.map((terms, index) => {
    const top = Array.from(terms, ([word, count]) => ({
      word, weight: count * (1 + Math.log((documents.length + 1) / (frequencies.get(word)! + 1))),
    }))
      // Terms that occur across most of the catalog do not distinguish a topic.
      .filter(({ word }) => frequencies.get(word)! <= Math.max(2, Math.min(100, documents.length * .5)))
      .sort((a, b) => b.weight - a.weight)
      .slice(0, 48)
    const norm = Math.hypot(...top.map(({ weight }) => weight))
    for (const term of top) {
      term.weight /= norm
      const list = postings.get(term.word) ?? []
      if (!list.length) postings.set(term.word, list)
      list.push({ index, weight: term.weight })
    }
    return top
  })
  vectors.forEach((terms, from) => {
    const candidates = new Map<number, { similarity: number; matches: number }>()
    for (const { word, weight } of terms) {
      for (const { index, weight: other } of postings.get(word) ?? []) {
        if (from === index) continue
        const score = candidates.get(index) ?? { similarity: 0, matches: 0 }
        score.similarity += weight * other
        score.matches += 1
        candidates.set(index, score)
      }
    }
    const strongest = [...candidates].filter(([, score]) => score.matches >= 2 && score.similarity >= .19)
      .sort((a, b) => b[1].similarity - a[1].similarity || a[0] - b[0])
      .slice(0, 2)
    for (const [target] of strongest) connect(from, target, 'text')
  })
  return [...edges.values()]
}
