'use strict';

/**
 * rcon.js — 纯 Node.js 实现的 RCON (Source/Minecraft 远程控制台) 协议客户端
 * 零第三方依赖，仅使用 Node 内置的 net 与 events 模块。
 *
 * 支持的协议操作（即 RCON 协议定义的全部操作类型）：
 *   - SERVERDATA_AUTH (3)         —— 认证
 *   - SERVERDATA_AUTH_RESPONSE (2)—— 认证响应（服务端返回）
 *   - SERVERDATA_EXECCOMMAND (2)  —— 执行命令
 *   - SERVERDATA_RESPONSE_VALUE (0)—— 命令响应（可跨多个数据包分片）
 *
 * 适配：Minecraft、CS:GO / CS2 / GMOD / TF2（Source 引擎）、Rust、ARK、Palworld 等
 * 几乎所有使用 RCON 协议的游戏服务器。
 */

const net = require('net');
const { EventEmitter } = require('events');

const PACKET_TYPE = Object.freeze({
  RESPONSE_VALUE: 0,
  AUTH_RESPONSE: 2,
  EXECCOMMAND: 2,
  AUTH: 3,
});

class RconClient extends EventEmitter {
  /**
   * @param {object} opts
   * @param {string} opts.host      服务器 IP / 域名
   * @param {number} opts.port      服务器 RCON 端口
   * @param {string} opts.password  RCON 密码
   * @param {number} [opts.timeout] 连接与命令超时（毫秒），默认 5000
   */
  constructor({ host, port, password, timeout = 5000 } = {}) {
    super();
    this.host = host;
    this.port = Number(port);
    this.password = String(password ?? '');
    this.timeout = timeout;

    this.socket = null;
    this.authenticated = false;
    this._requestId = 0;
    this._buffer = Buffer.alloc(0);
  }

  /** 连接并认证 */
  connect() {
    return new Promise((resolve, reject) => {
      if (this.socket && !this.socket.destroyed) {
        return reject(new Error('已处于连接状态'));
      }

      this.socket = new net.Socket();
      this.socket.setNoDelay(true);
      this.socket.setTimeout(this.timeout);

      const onConnect = () => {
        this._authenticate()
          .then(() => resolve())
          .catch((err) => {
            this.disconnect();
            reject(err);
          });
      };
      const onError = (err) => reject(err);
      const onTimeout = () => {
        this.socket.destroy();
        reject(new Error('连接超时'));
      };

      this.socket.once('connect', onConnect);
      this.socket.once('error', onError);
      this.socket.once('timeout', onTimeout);
      this.socket.on('data', (d) => this._onData(d));
      this.socket.on('close', () => {
        this.authenticated = false;
        this.emit('close');
      });

      this.socket.connect(this.port, this.host);
    });
  }

  /** 断开连接 */
  disconnect() {
    if (this.socket) {
      this.socket.destroy();
      this.socket = null;
    }
    this.authenticated = false;
  }

  get connected() {
    return !!(this.socket && !this.socket.destroyed && this.authenticated);
  }

  /**
   * 执行一条命令，返回服务端响应。
   * 大响应会被拆成多个 RESPONSE_VALUE 数据包，本方法会将其拼接完整。
   * @param {string} cmd
   * @param {number} [timeout]
   * @returns {Promise<{response:string, partial:boolean}>}
   */
  sendCommand(cmd, timeout) {
    return new Promise((resolve, reject) => {
      if (!this.connected) {
        return reject(new Error('未连接或未认证'));
      }

      const id = this._nextId();
      const flushId = this._nextId(); // 用于探测响应结束的空包
      const chunks = [];
      let settled = false;
      const t = timeout || this.timeout;

      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        this.off('packet', onPacket);
        // 超时但已有部分响应时，尽量返回已收到的内容
        resolve({ response: chunks.join(''), partial: true });
      }, t);

      const onPacket = (pkt) => {
        if (pkt.type !== PACKET_TYPE.RESPONSE_VALUE) return;
        if (pkt.id === id) {
          chunks.push(pkt.body);
        } else if (pkt.id === flushId) {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          this.off('packet', onPacket);
          resolve({ response: chunks.join(''), partial: false });
        }
      };

      this.on('packet', onPacket);

      this.socket.write(this._encode(id, PACKET_TYPE.EXECCOMMAND, cmd));
      this.socket.write(this._encode(flushId, PACKET_TYPE.RESPONSE_VALUE, ''));
    });
  }

  // ---- 内部实现 ----

  _nextId() {
    this._requestId = (this._requestId + 1) & 0x7fffffff;
    return this._requestId;
  }

  _authenticate() {
    return new Promise((resolve, reject) => {
      const id = this._nextId();
      let settled = false;

      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        this.off('packet', onPacket);
        reject(new Error('认证超时'));
      }, this.timeout);

      const onPacket = (pkt) => {
        if (pkt.type !== PACKET_TYPE.AUTH_RESPONSE) return;
        if (pkt.id === -1) {
          settled = true;
          clearTimeout(timer);
          this.off('packet', onPacket);
          return reject(new Error('认证失败：密码错误'));
        }
        if (pkt.id === id) {
          settled = true;
          clearTimeout(timer);
          this.off('packet', onPacket);
          this.authenticated = true;
          resolve();
        }
      };

      this.on('packet', onPacket);
      this.socket.write(this._encode(id, PACKET_TYPE.AUTH, this.password));
    });
  }

  /** 编码单个 RCON 数据包 */
  _encode(id, type, body) {
    const bodyBuf = Buffer.from(body, 'utf8');
    const size = 4 + 4 + bodyBuf.length + 2; // id + type + body + 2 字节空终止
    const buf = Buffer.allocUnsafe(4 + size);
    buf.writeInt32LE(size, 0);
    buf.writeInt32LE(id, 4);
    buf.writeInt32LE(type, 8);
    bodyBuf.copy(buf, 12);
    buf.writeInt16LE(0, 12 + bodyBuf.length);
    return buf;
  }

  /** 解析接收到的字节流，可能包含多个完整数据包 */
  _onData(data) {
    this._buffer = Buffer.concat([this._buffer, data]);
    while (this._buffer.length >= 4) {
      const size = this._buffer.readInt32LE(0);
      if (size < 10 || size > 0x100000) {
        // 非法长度，丢弃 1 字节后继续尝试对齐
        this._buffer = this._buffer.slice(1);
        continue;
      }
      if (this._buffer.length < 4 + size) break;

      const id = this._buffer.readInt32LE(4);
      const type = this._buffer.readInt32LE(8);
      const bodyBuf = this._buffer.slice(12, 4 + size - 2); // 去掉末尾 2 字节空终止
      const body = bodyBuf.toString('utf8');

      this._buffer = this._buffer.slice(4 + size);
      this.emit('packet', { id, type, body });
    }
  }
}

module.exports = { RconClient, PACKET_TYPE };
