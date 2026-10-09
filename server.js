'use strict';

/**
 * server.js — RCON 控制台入口
 *
 * 两种使用方式：
 *   1) Web 图形界面：  node server.js [--web-port 8080]
 *      然后浏览器打开 http://localhost:8080
 *   2) 命令行模式：
 *      单条命令： node server.js --host 127.0.0.1 --port 25575 --password secret --command "list"
 *      交互模式： node server.js --host 127.0.0.1 --port 25575 --password secret --interactive
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const { RconClient } = require('./rcon');

const PUBLIC_DIR = path.join(__dirname, 'public');
const DEFAULT_WEB_PORT = 8080;

// 当前 Web 服务持有的连接（同一时间一个连接）
let client = null;
let connInfo = null;

// ---------------- 命令行参数解析 ----------------
const args = process.argv.slice(2);
function getArg(name, alias) {
  const idx = args.indexOf('--' + name);
  const idxAlias = alias ? args.indexOf('-' + alias) : -1;
  const i = idx !== -1 ? idx : idxAlias;
  if (i === -1) return undefined;
  return args[i + 1];
}
const hasArg = (name) => args.includes('--' + name);

const cliHost = getArg('host', 'H');
const cliPort = getArg('port', 'p');
const cliPassword = getArg('password', 'P');
const cliCommand = getArg('command', 'c');
const cliInteractive = hasArg('interactive');
const cliTimeout = getArg('timeout', 't');

// ---------------- CLI 模式 ----------------
if (cliHost || cliCommand || cliInteractive) {
  if (!cliHost || !cliPort || cliPassword === undefined) {
    console.error(
      [
        '用法：',
        '  单条命令  node server.js --host <IP> --port <端口> --password <密码> --command "命令"',
        '  交互模式  node server.js --host <IP> --port <端口> --password <密码> --interactive',
        '  可选参数  --timeout <毫秒> (默认 5000)',
      ].join('\n'),
    );
    process.exit(1);
  }
  runCli().then(() => process.exit(0)).catch((e) => {
    console.error('[错误] ' + e.message);
    process.exit(1);
  });
} else {
  startServer();
}

// ---------------- CLI 实现 ----------------
async function runCli() {
  const c = new RconClient({
    host: cliHost,
    port: parseInt(cliPort, 10),
    password: cliPassword,
    timeout: cliTimeout ? parseInt(cliTimeout, 10) : 5000,
  });

  process.stdout.write(`正在连接 ${cliHost}:${cliPort} ... `);
  await c.connect();
  console.log('已连接并认证成功 ✓');

  if (cliCommand) {
    const r = await c.sendCommand(cliCommand);
    process.stdout.write(r.response || '(无响应)\n');
    if (r.partial) console.warn('[提示] 响应可能在超时前未完全返回');
    c.disconnect();
    return;
  }

  // 交互模式
  const readline = require('readline');
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: true });

  console.log('输入命令回车执行；输入 exit / quit 退出。');
  const ask = () => {
    rl.question('rcon> ', async (line) => {
      const cmd = (line || '').trim();
      if (!cmd) return ask();
      if (cmd === 'exit' || cmd === 'quit') {
        c.disconnect();
        rl.close();
        return;
      }
      try {
        const r = await c.sendCommand(cmd);
        process.stdout.write(r.response ? r.response + '\n' : '(无响应)\n');
      } catch (e) {
        console.error('[错误] ' + e.message);
      }
      ask();
    });
  };
  ask();

  c.on('close', () => {
    console.log('\n连接已断开。');
    rl.close();
  });
}

// ---------------- Web 服务 ----------------
function startServer() {
  const webPort = parseInt(getArg('web-port') || process.env.PORT || DEFAULT_WEB_PORT, 10);

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const p = url.pathname;

    try {
      // ---- API ----
      if (p === '/api/status') {
        return sendJson(res, 200, {
          connected: !!(client && client.connected),
          info: connInfo,
        });
      }

      if (p === '/api/connect' && req.method === 'POST') {
        const body = await readBody(req);
        const { host, port, password, timeout } = body;
        if (!host || !port) return sendJson(res, 400, { ok: false, error: '缺少 host 或 port' });

        if (client) client.disconnect();
        const c = new RconClient({
          host,
          port: parseInt(port, 10),
          password: String(password ?? ''),
          timeout: timeout || 5000,
        });
        try {
          await c.connect();
          client = c;
          connInfo = { host, port: parseInt(port, 10) };
          return sendJson(res, 200, { ok: true });
        } catch (e) {
          c.disconnect();
          return sendJson(res, 200, { ok: false, error: e.message });
        }
      }

      if (p === '/api/disconnect' && req.method === 'POST') {
        if (client) { client.disconnect(); client = null; connInfo = null; }
        return sendJson(res, 200, { ok: true });
      }

      if (p === '/api/command' && req.method === 'POST') {
        const body = await readBody(req);
        if (!client || !client.connected) {
          return sendJson(res, 200, { ok: false, error: '未连接服务器' });
        }
        const { command, timeout } = body;
        if (!command || !command.trim()) {
          return sendJson(res, 400, { ok: false, error: '命令为空' });
        }
        try {
          const r = await client.sendCommand(command.trim(), timeout);
          return sendJson(res, 200, { ok: true, response: r.response, partial: !!r.partial });
        } catch (e) {
          return sendJson(res, 200, { ok: false, error: e.message });
        }
      }

      // ---- 静态文件 ----
      return serveStatic(req, res, p);
    } catch (e) {
      return sendJson(res, 500, { ok: false, error: e.message });
    }
  });

  server.listen(webPort, () => {
    console.log('==============================================');
    console.log('  RCON 控制台已启动');
    console.log(`  Web 界面: http://localhost:${webPort}`);
    console.log('  按 Ctrl+C 停止服务');
    console.log('==============================================');
  });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (c) => (data += c));
    req.on('end', () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch (e) {
        reject(new Error('请求体不是有效 JSON'));
      }
    });
    req.on('error', reject);
  });
}

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
};

function serveStatic(req, res, pathname) {
  let filePath = pathname === '/' ? '/index.html' : pathname;
  const safe = path.normalize(filePath).replace(/^(\.\.[/\\])+/, '');
  const full = path.join(PUBLIC_DIR, safe);
  if (!full.startsWith(PUBLIC_DIR)) {
    res.writeHead(403);
    return res.end('Forbidden');
  }
  fs.readFile(full, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      return res.end('Not Found');
    }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(full).toLowerCase()] || 'application/octet-stream' });
    res.end(data);
  });
}
