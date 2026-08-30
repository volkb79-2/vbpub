import { JSDOM } from 'jsdom'
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import path from 'node:path'

const dir = path.resolve('dist')
const html = readFileSync(path.join(dir, 'index.html'), 'utf-8')

const dom = new JSDOM(html, {
  url: pathToFileURL(path.join(dir, 'index.html')).href,
  runScripts: 'dangerously',
  resources: 'usable',
})

// Give the module script (and any fetch-driven chunk loading) time to run.
await new Promise((resolve) => setTimeout(resolve, 500))

const coverage = dom.window.__coverage__
if (!coverage) {
  console.error('NO COVERAGE CAPTURED')
  process.exit(1)
}
console.log(JSON.stringify(coverage))
