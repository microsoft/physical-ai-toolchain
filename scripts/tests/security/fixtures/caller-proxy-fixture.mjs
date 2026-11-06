// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

// cspell:ignore addext keyout nosniff fcaller ealth

// Test-only stand-ins for the Data Viewer backend and the caller-proof helper.
// The helper stand-in exercises nginx transport behavior only; token issuance and
// JWT verification are covered by the backend and caller helper unit tests.
//
// Usage: node caller-proxy-fixture.mjs backend|helper
// Receipts stay in memory and are read through a container-loopback control endpoint.
// /fixture/helper-mode selects the helper response (ok, empty, malformed, unavailable, slow).

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { generateKeyPairSync, sign } from 'node:crypto'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer as createHttpServer } from 'node:http'
import { createServer as createTlsServer } from 'node:https'
import { request as httpRequest } from 'node:http'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const fixture = '/fixture'
const role = process.argv[2]
const { privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
const unsigned = [
  { alg: 'RS256', typ: 'JWT' },
  { sub: 'fixture', exp: Math.floor(Date.now() / 1000) + 300 },
].map((value) => Buffer.from(JSON.stringify(value)).toString('base64url')).join('.')
const token = `${unsigned}.${sign('RSA-SHA256', Buffer.from(unsigned), privateKey).toString('base64url')}`
const mediaBytes = Buffer.from('0123456789abcdef')
const requestReceipts = []

function record(entry) {
  requestReceipts.push(entry)
}

function readBody(request) {
  return new Promise((resolve) => {
    const chunks = []
    request.on('data', (chunk) => chunks.push(chunk))
    request.on('end', () => resolve(Buffer.concat(chunks)))
  })
}

function backendHandler(request, response) {
  readBody(request).then(async (body) => {
    const url = new URL(request.url, 'https://fixture')
    record({
      method: request.method,
      url: request.url,
      headers: request.headers,
      body: body.toString('base64'),
    })

    if (url.pathname === '/health') {
      if (helperMode() === 'storage-slow') {
        setTimeout(() => response.writeHead(200).end(), 20000)
        return
      }
      response.writeHead(200, { 'content-type': 'text/plain' })
      response.end('healthy')
      return
    }

    if (process.env.CALLER_FIXTURE_ENDPOINT && request.headers['x-dataviewer-caller-token']) {
      const verified = await fetch(`${process.env.CALLER_FIXTURE_ENDPOINT}/verify`, {
        method: 'POST', body: JSON.stringify(request.headers),
      })
      if (!verified.ok) {
        response.writeHead(verified.status).end()
        return
      }
    }

    if (url.pathname === '/api/media') {
      const match = request.headers['if-range'] && request.headers['if-range'] !== '"fixture"'
        ? null : /^bytes=(\d+)-(\d+)$/.exec(request.headers.range ?? '')
      const headers = { 'accept-ranges': 'bytes', 'content-type': 'application/octet-stream', etag: '"fixture"' }
      if (match) {
        const start = Number(match[1])
        const end = Math.min(Number(match[2]), mediaBytes.length - 1)
        if (start >= mediaBytes.length) {
          response.writeHead(416, { 'content-range': `bytes */${mediaBytes.length}` }).end()
          return
        }
        response.writeHead(206, {
          ...headers,
          'content-range': `bytes ${start}-${end}/${mediaBytes.length}`,
          'content-length': end - start + 1,
        })
        response.end(mediaBytes.subarray(start, end + 1))
        return
      }
      response.writeHead(200, { ...headers, 'content-length': mediaBytes.length })
      response.end(mediaBytes)
      return
    }

    if (url.pathname === '/api/stream') {
      response.writeHead(200, { 'content-type': 'application/octet-stream' })
      let chunks = 0
      const timer = setInterval(() => {
        response.write(Buffer.alloc(65536))
        if (++chunks === 128) response.end()
      }, 20)
      response.on('close', () => {
        clearInterval(timer)
        writeFileSync(`${fixture}/stream-closed`, String(chunks))
      })
      return
    }

    const headers = { ...request.headers }
    delete headers['x-dataviewer-caller-token']
    response.writeHead(200, { 'content-type': 'application/json', 'x-dataviewer-caller-token': token })
    response.end(
      JSON.stringify({ method: request.method, url: request.url, headers, bodyBytes: body.length }),
    )
  })
}

function helperMode() {
  try {
    return readFileSync(`${fixture}/helper-mode`, 'utf8').trim() || 'ok'
  } catch {
    return 'ok'
  }
}

function helperHandler(request, response) {
  readBody(request).then(async (body) => {
    record({
      method: request.method,
      url: request.url,
      headers: request.headers,
      bodyBytes: body.length,
    })

    const mode = helperMode()
    const path = new URL(request.url, 'http://helper').pathname
    if (path !== '/token' && path !== '/ready') {
      response.writeHead(404).end()
      return
    }
    if (mode === 'slow') {
      setTimeout(() => response.writeHead(204).end(), 20000)
      return
    }
    if (mode === 'unavailable') {
      response.writeHead(503, { 'cache-control': 'no-store' }).end()
      return
    }

    const headers = { 'cache-control': 'no-store' }
    if (path === '/token' && ['ok', 'storage-slow', 'expired', 'wrong-audience', 'unauthorized', 'forged'].includes(mode)) {
      if (process.env.CALLER_FIXTURE_ENDPOINT) {
        const issued = await fetch(`${process.env.CALLER_FIXTURE_ENDPOINT}/token?case=${mode}`)
        headers['x-dataviewer-caller-token'] = issued.headers.get('x-dataviewer-caller-token')
      } else {
        headers['x-dataviewer-caller-token'] = token
      }
    }
    if (path === '/token' && mode === 'malformed') headers['x-dataviewer-caller-token'] = 'not a token'
    if (path === '/token' && mode === 'oversized') headers['x-dataviewer-caller-token'] = `${'a'.repeat(8200)}.b.c`
    response.writeHead(204, headers).end()
  })
}

async function runTransport() {
  const repository = resolve(process.argv[3])
  const frontend = join(repository, 'data-management/viewer/frontend')
  const dockerfile = readFileSync(join(frontend, 'Dockerfile'), 'utf8')
  const nginxImage = /^FROM (\S+) AS serve$/m.exec(dockerfile)[1]
  const nodeImage = /^FROM (\S+) AS build$/m.exec(dockerfile)[1]
  const directory = mkdtempSync(join(tmpdir(), 'caller-proxy-'))
  const suffix = directory.split('-').at(-1).toLowerCase()
  const network = `caller-${suffix}`
  const containers = []
  let checks = 0
  const docker = (...args) => execFileSync('docker', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  const receipts = (kind) => {
    const container = containers.find((name) => name.startsWith(`caller-${kind}-`))
    if (!container) return []
    return JSON.parse(docker('exec', container, 'node', '--input-type=module', '-e',
      'const response = await fetch("http://127.0.0.1:8091/receipts"); if (!response.ok) throw new Error("Receipt lookup failed"); process.stdout.write(await response.text())'))
  }
  const check = (condition, message) => { assert.ok(condition, message); checks += 1 }
  const principal = Buffer.from(JSON.stringify({ claims: [{ typ: 'sub', val: 'user-1' }] })).toString('base64')
  const headers = {
    'X-MS-CLIENT-PRINCIPAL': principal, Cookie: 'session=fixture', Authorization: 'Bearer user-fixture',
    'X-CSRF-Token': 'csrf-fixture', 'X-Dataviewer-Caller-Token': ['incoming-one', 'incoming-two'],
  }
  const request = (port, path, method = 'GET', extra = {}, body = '') => new Promise((resolveRequest, reject) => {
    const pending = httpRequest({ hostname: '127.0.0.1', port, path, method, headers: { ...headers, ...extra } }, (response) => {
      const chunks = []
      response.on('data', (chunk) => chunks.push(chunk))
      response.on('end', () => resolveRequest({ status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks) }))
    })
    pending.setTimeout(10000, () => pending.destroy(new Error('fixture request deadline exceeded')))
    pending.on('error', reject)
    pending.end(body)
  })
  const waitReady = async (probe) => {
    const deadline = Date.now() + 10000
    while (Date.now() < deadline) {
      try { if (await probe()) return } catch {}
      await new Promise((resolveWait) => setTimeout(resolveWait, 50))
    }
    throw new Error('fixture startup deadline exceeded')
  }
  const fixtureArgs = ['--volume', `${directory}:/fixture`, '--volume', `${resolve(process.argv[1])}:/fixture/server.mjs:ro`]
  if (process.env.CALLER_FIXTURE_ENDPOINT) fixtureArgs.push('--env', `CALLER_FIXTURE_ENDPOINT=${process.env.CALLER_FIXTURE_ENDPOINT}`)
  try {
    execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=tls-valid',
      '-addext', 'subjectAltName=DNS:tls-valid', '-keyout', join(directory, 'server.key'), '-out', join(directory, 'server.crt')], { stdio: 'ignore' })
    docker('network', 'create', network)
    const backend = `caller-backend-${suffix}`
    containers.push(backend)
    docker('run', '-d', '--name', backend, '--network', network, '--network-alias', 'tls-valid',
      '--add-host', 'host.docker.internal:host-gateway', ...fixtureArgs, nodeImage, 'node', '/fixture/server.mjs', 'backend')
    await waitReady(() => readFileSync(join(directory, 'backend-ready')) !== null)
    for (const mode of ['off', 'pending', 'managed_identity']) {
      writeFileSync(join(directory, 'helper-mode'), 'ok')
      const nginx = `caller-nginx-${mode}-${suffix}`
      containers.push(nginx)
      docker('run', '-d', '--name', nginx, '--network', network, '-p', '127.0.0.1::8080',
        '--add-host', 'host.docker.internal:host-gateway',
        '--entrypoint', '/bin/sh', '--env', `DATAVIEWER_CALLER_MODE=${mode}`, '--env', 'DATAVIEWER_PROXY_KEY=',
        '--env', 'NGINX_BACKEND_HOST=tls-valid', '--env', 'NGINX_BACKEND_SCHEME=https', '--env', 'NGINX_RESOLVER=127.0.0.11',
        '--volume', `${frontend}/nginx.conf.template:/etc/nginx/templates/default.conf.template:ro`,
        '--volume', `${frontend}/start-nginx.sh:/start.sh:ro`, '--volume', `${directory}/server.crt:/etc/ssl/certs/ca-certificates.crt:ro`,
        nginxImage, '/start.sh')
      const port = Number(docker('port', nginx, '8080/tcp').split(':').at(-1))
      await waitReady(async () => (await request(port, '/')).status === 200)
      if (mode === 'managed_identity') {
        const helper = `caller-helper-${suffix}`
        containers.push(helper)
        docker('run', '-d', '--name', helper, '--network', `container:${nginx}`, ...fixtureArgs,
          nodeImage, 'node', '/fixture/server.mjs', 'helper')
        await waitReady(() => readFileSync(join(directory, 'helper-ready')) !== null)
      }
      const before = receipts('backend').length
      const helperBefore = receipts('helper').length
      const response = await request(port, '/api/echo?value=a%2Fb', 'POST', { 'Content-Type': 'application/octet-stream' }, 'body-fixture')
      if (mode === 'pending') {
        check(response.status === 503 && receipts('backend').length === before, 'pending must not contact backend')
      } else {
        check(response.status === 200, `${mode}: normal request succeeds`)
        const receipt = receipts('backend').at(-1)
        check(receipt.method === 'POST' && receipt.url === '/api/echo?value=a%2Fb' && Buffer.from(receipt.body, 'base64').toString() === 'body-fixture', 'preserve method/path/query/body')
        check(receipt.headers.authorization === headers.Authorization && receipt.headers.cookie === headers.Cookie && receipt.headers['x-csrf-token'] === headers['X-CSRF-Token'] && receipt.headers['x-ms-client-principal'] === principal, 'preserve user credentials and CSRF')
        check(!response.headers['x-dataviewer-caller-token'] && !response.body.includes(Buffer.from('incoming-one')), 'proof response header remains hidden')
        if (mode === 'off') check(!receipt.headers['x-dataviewer-caller-token'], 'off strips incoming proofs')
        else {
          check(receipt.headers['x-dataviewer-caller-token']?.split('.').length === 3 && !receipt.headers['x-dataviewer-caller-token'].includes('incoming'), 'replace duplicate incoming proofs')
          check(!response.body.includes(Buffer.from(receipt.headers['x-dataviewer-caller-token'])), 'proof never enters response body')
          const helperReceipt = receipts('helper').at(-1)
          check(helperReceipt.method === 'GET' && helperReceipt.bodyBytes === 0 && !helperReceipt.headers.cookie && !helperReceipt.headers.authorization && !helperReceipt.headers['x-ms-client-principal'], 'helper is body/header isolated')
          for (const failure of ['empty', 'malformed', 'oversized', 'unavailable', 'slow']) {
            writeFileSync(join(directory, 'helper-mode'), failure)
            const count = receipts('backend').length
            const denied = await request(port, '/api/echo')
            check(denied.status >= 500 && denied.body.length < 4096 && receipts('backend').length === count, `${failure}: fail before backend receipt`)
            check(denied.headers['x-content-type-options'] === 'nosniff' && !denied.headers['x-dataviewer-caller-token'], 'denials retain security without proof')
          }
          writeFileSync(join(directory, 'helper-mode'), 'ok')
          for (const path of ['/_caller_proof', '/_caller_ready', '/%5fcaller_proof', '/_caller_proof/']) {
            const count = receipts('helper').length
            await request(port, path)
            check(receipts('helper').length === count, 'private helper locations cannot be acquired externally')
          }
        }
      }
      if (mode !== 'managed_identity') check(receipts('helper').length === helperBefore, 'inactive modes never acquire proof')
      for (const method of ['GET', 'HEAD']) {
        const healthy = await request(port, '/health', method)
        check(healthy.status === 200 && !healthy.headers['x-dataviewer-caller-token'], 'exact health works without proof disclosure')
        check(!receipts('backend').at(-1).headers['x-dataviewer-caller-token'], 'health strips incoming caller proof')
      }
      for (const path of ['/health/', '/health/private', '/%68ealth']) {
        const count = receipts('backend').length
        await request(port, path)
        check(receipts('backend').length === count, 'health aliases do not reach backend health')
      }
      const count = receipts('backend').length
      await request(port, '/health', 'POST')
      check(receipts('backend').length === count, 'POST health is not forwarded')
      if (mode === 'managed_identity') {
        const paddedPrincipal = Buffer.from(Buffer.from(principal, 'base64').toString().padEnd(6144, ' ')).toString('base64')
        const budget = await request(port, '/api/echo', 'GET', { 'X-MS-CLIENT-PRINCIPAL': paddedPrincipal, Cookie: `session=${'c'.repeat(8000)}` })
        check(budget.status === 200 && receipts('backend').at(-1).headers['x-ms-client-principal'] === paddedPrincipal, 'principal and cookie header budgets preserve bytes')
        const budgetCount = receipts('backend').length
        check((await request(port, '/api/echo', 'GET', { 'X-MS-CLIENT-PRINCIPAL': 'p'.repeat(20000) })).status === 400 && receipts('backend').length === budgetCount, 'oversized ingress header fails without truncation or backend work')
        writeFileSync(join(directory, 'helper-mode'), 'unavailable')
        const healthCount = receipts('backend').length
        check((await request(port, '/health')).status >= 500 && receipts('backend').length === healthCount, 'unready caller prevents storage-health receipt')
        check((await request(port, '/')).status === 200, 'static liveness survives identity outage')
        writeFileSync(join(directory, 'helper-mode'), 'ok')
        for (const method of ['GET', 'HEAD']) {
          const media = await request(port, '/api/media', method, { Range: 'bytes=2-5', 'If-Range': '"fixture"' })
          check(media.status === 206 && media.headers['content-range'] === 'bytes 2-5/16' && media.headers['content-length'] === '4', 'native Range/HEAD content metadata')
          check(media.body.toString() === (method === 'HEAD' ? '' : '2345'), 'native Range/HEAD bytes')
        }
        check((await request(port, '/api/media', 'GET', { Range: 'bytes=99-100' })).status === 416, 'unsatisfied range retained')
        check((await request(port, '/api/media', 'GET', { Range: 'bytes=2-5', 'If-Range': '"stale"' })).status === 200, 'stale If-Range returns full media')
        const streamStart = Date.now()
        await new Promise((resolveStream, reject) => {
          const streamRequest = httpRequest({ hostname: '127.0.0.1', port, path: '/api/stream', headers }, (response) => {
            response.once('data', () => {
              response.destroy()
              resolveStream()
            })
            response.on('error', reject)
          })
          streamRequest.setTimeout(5000, () => streamRequest.destroy(new Error('stream deadline exceeded')))
          streamRequest.on('error', reject)
          streamRequest.end()
        })
        check(Date.now() - streamStart < 1500, 'large media begins before complete upstream buffering')
        await waitReady(() => readFileSync(join(directory, 'stream-closed')) !== null)
        check(Number(readFileSync(join(directory, 'stream-closed'))) < 128, 'browser cancellation closes upstream media')
        if (process.env.CALLER_FIXTURE_ENDPOINT) {
          for (const [proofCase, status] of [['expired', 401], ['wrong-audience', 401], ['forged', 401], ['unauthorized', 403]]) {
            writeFileSync(join(directory, 'helper-mode'), proofCase)
            check((await request(port, '/api/echo')).status === status, `production verifier rejects ${proofCase}`)
          }
        }
        writeFileSync(join(directory, 'helper-mode'), 'storage-slow')
        const start = Date.now()
        const unhealthy = await request(port, '/health')
        check(unhealthy.status >= 500 && Date.now() - start < 9000, 'storage health timeout is bounded')
      }
    }
    check(!existsSync(join(directory, 'backend-receipts.jsonl')) && !existsSync(join(directory, 'helper-receipts.jsonl')), 'request receipts never persist to disk')
    for (const container of containers) {
      const logs = docker('logs', container)
      check(!logs.includes(token) && !logs.includes('incoming-one'), 'logs never contain caller proof')
    }
    console.log(`caller transport: ${checks} assertions passed`)
  } finally {
    for (const container of containers.reverse()) {
      try { docker('rm', '-f', container) } catch {}
    }
    try { docker('network', 'rm', network) } catch {}
    rmSync(directory, { recursive: true, force: true })
  }
}

if (role === 'backend' || role === 'helper') {
  createHttpServer((request, response) => {
    if (request.method !== 'GET' || request.url !== '/receipts') {
      response.writeHead(404).end()
      return
    }
    response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' })
    response.end(JSON.stringify(requestReceipts))
  }).listen(8091, '127.0.0.1')
}

if (role === 'run') {
  await runTransport()
} else if (role === 'backend') {
  createTlsServer(
    { cert: readFileSync(`${fixture}/server.crt`), key: readFileSync(`${fixture}/server.key`), maxHeaderSize: 65536 },
    backendHandler,
  ).listen(443, '0.0.0.0', () => writeFileSync(`${fixture}/backend-ready`, ''))
} else if (role === 'helper') {
  createHttpServer(helperHandler).listen(8090, '127.0.0.1', () => writeFileSync(`${fixture}/helper-ready`, ''))
} else {
  throw new Error('Usage: caller-proxy-fixture.mjs backend|helper')
}
