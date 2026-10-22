'use strict';

/**
 * mock-server.js — 模拟 RCON 服务器，用于在没有真实游戏服务器时测试本工具。
 *
 * 启动：  node test/mock-server.js
 * 默认：  监听 127.0.0.1:25575，密码 test123
 *
 * 它会模拟认证、命令执行、大响应分片、以及响应结束空包，
 * 可以用来验证 rcon.js 的协议实现是否正确。
 */

const net = require('net');
const os = require('os');

const HOST = process.env.MOCK_HOST || '127.0.0.1';
const PORT = parseInt(process.env.MOCK_PORT || '25575', 10);
const PASSWORD = process.env.MOCK_PASSWORD || 'test123';

const TYPE = { RESPONSE_VALUE: 0, AUTH_RESPONSE: 2, EXECCOMMAND: 2, AUTH: 3 };

// 内置几个可交互的命令响应
function handleCommand(cmd) {
  const c = cmd.trim();
  if (c === 'list') {
    return 'There are 2/20 players online:\nPlayerA, PlayerB';
  }
  if (c === 'status') {
    return [
      'hostname: Mock RCON Server',
      'version : 1.20.4 (mock)',
      'players : 2/20',
      'map     : world',
    ].join('\n');
  }
  if (c === 'seed') {
    return 'Seed: [1234567890123456789]';
  }
  if (c === 'help') {
    return '可用命令: list, status, seed, echo <text>, help, stop';
  }
  if (c.startsWith('echo ')) {
    return c.slice(5);
  }
  if (c === 'stop') {
    return 'Stopping server... (mock 不会真正关闭)';
  }
  if (c === 'big') {
    // 返回一个很长的响应，用于测试多包分片
    return 'BIG_RESPONSE_' + 'x'.repeat(5000);
  }
  return 'Unknown command: ' + c;
}

function encode(id, type, body) {
  const bodyBuf = Buffer.from(body, 'utf8');
  const size = 4 + 4 + bodyBuf.length + 2;
  const buf = Buffer.alloc(4 + size);
  buf.writeInt32LE(size, 0);
  buf.writeInt32LE(id, 4);
  buf.writeInt32LE(type, 8);
  bodyBuf.copy(buf, 12);
  buf.writeInt16LE(0, 12 + bodyBuf.length);
  return buf;
}

const server = net.createServer((socket) => {
  socket.setNoDelay(true);
  let authenticated = false;
  let buffer = Buffer.alloc(0);

  socket.on('data', (data) => {
    buffer = Buffer.concat([buffer, data]);
    while (buffer.length >= 4) {
      const size = buffer.readInt32LE(0);
      if (size < 10) { buffer = buffer.slice(1); continue; }
      if (buffer.length < 4 + size) break;

      const id = buffer.readInt32LE(4);
      const type = buffer.readInt32LE(8);
      const body = buffer.slice(12, 4 + size - 2).toString('utf8');
      buffer = buffer.slice(4 + size);

      if (type === TYPE.AUTH) {
        // 认证
        if (body === PASSWORD) {
          authenticated = true;
          socket.write(encode(id, TYPE.AUTH_RESPONSE, ''));
        } else {
          socket.write(encode(-1, TYPE.AUTH_RESPONSE, ''));
        }
      } else if (type === TYPE.EXECCOMMAND && authenticated) {
        // 执行命令
        const response = handleCommand(body);
        const respBuf = Buffer.from(response, 'utf8');
        // 模拟分片：每片最多 200 字节
        const CHUNK = 200;
        for (let i = 0; i < respBuf.length; i += CHUNK) {
          socket.write(encode(id, TYPE.RESPONSE_VALUE, respBuf.slice(i, i + CHUNK).toString('utf8')));
        }
      } else if (type === TYPE.RESPONSE_VALUE && authenticated) {
        // 客户端发送的“探测结束”空包 -> 原样回空包，表示响应结束
        socket.write(encode(id, TYPE.RESPONSE_VALUE, ''));
      }
    }
  });

  socket.on('error', () => {});
});

server.listen(PORT, HOST, () => {
  console.log(`Mock RCON 服务器已启动: ${HOST}:${PORT}`);
  console.log(`密码: ${PASSWORD}`);
  console.log('现在可以用 GeLi RCON 连接测试。');
});
