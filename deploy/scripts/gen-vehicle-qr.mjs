#!/usr/bin/env node
/**
 * Generate vehicle QR payload URL for cabin sticker.
 * Usage: node deploy/scripts/gen-vehicle-qr.mjs --host 192.168.1.20 --port 19000 --id fork-01
 * Prints URL; pipe to a QR encoder of your choice.
 */
const args = process.argv.slice(2)
function get(flag, def) {
  const i = args.indexOf(flag)
  return i >= 0 ? args[i + 1] : def
}
const host = get('--host', '127.0.0.1')
const port = get('--port', '19000')
const id = get('--id', 'fork-01')
const site = get('--site', '')
const q = new URLSearchParams({ v: id })
if (site) q.set('site', site)
const url = `http://${host}:${port}/?${q.toString()}`
console.log(url)
console.log('# Paste into QR generator. site nonce should be current unlock nonce from GET /api/site')
