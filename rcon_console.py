# -*- coding: utf-8 -*-
"""
GeLi RCON —— 单文件桌面程序

运行后弹出一个窗口：
  · 一键启动 —— 启动内置后端（HTTP 服务）并自动打开网页控制台
  · 一键退出 —— 停止服务并退出程序

仅用 Python 标准库实现，无第三方依赖：
  · RCON 协议客户端（认证 / 执行命令 / 多包响应拼接）
  · HTTP 服务（REST API + 内嵌 Web 界面）
  · 弹窗 GUI（tkinter）

支持：Minecraft、CS:GO / CS2 / GMOD / TF2、Rust、ARK、Palworld 等几乎所有
使用 RCON 协议的游戏服务器。

品牌：GeLi —— 白色主调 + 蓝色主色点缀 + 浅绿次色点缀（#96FF8F → #8DFFFA）
"""

import os
import sys
import json
import time
import struct
import socket
import threading
import webbrowser
import http.server

# =============================================================================
# RCON 协议客户端
# =============================================================================

class RconClient:
    RESPONSE_VALUE = 0
    AUTH_RESPONSE = 2
    EXECCOMMAND = 2
    AUTH = 3

    def __init__(self, host, port, password, timeout=5.0):
        self.host = host
        self.port = int(port)
        self.password = str(password or "")
        self.timeout = float(timeout)
        self.sock = None
        self.authenticated = False
        self._req_id = 0
        self._lock = threading.Lock()

    def _next_id(self):
        self._req_id = (self._req_id + 1) & 0x7FFFFFFF
        return self._req_id

    @staticmethod
    def _encode(req_id, pkt_type, body):
        body_b = body.encode("utf-8")
        size = 4 + 4 + len(body_b) + 2
        return struct.pack("<iii", size, req_id, pkt_type) + body_b + b"\x00\x00"

    def _recv_exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("连接已关闭")
            data += chunk
        return data

    def _recv_packet(self):
        header = self._recv_exact(4)
        (size,) = struct.unpack("<i", header)
        if size < 10 or size > 0x100000:
            raise ConnectionError("非法数据包长度")
        rest = self._recv_exact(size)
        req_id, pkt_type = struct.unpack("<ii", rest[:8])
        body = rest[8:size - 2].decode("utf-8", errors="replace")
        return req_id, pkt_type, body

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        req_id = self._next_id()
        self.sock.sendall(self._encode(req_id, self.AUTH, self.password))
        rid, ptype, _ = self._recv_packet()
        if ptype != self.AUTH_RESPONSE:
            self.disconnect()
            raise ConnectionError("认证响应异常")
        if rid == -1:
            self.disconnect()
            raise PermissionError("认证失败：密码错误")
        self.authenticated = True

    def disconnect(self):
        self.authenticated = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def send_command(self, cmd, timeout=None):
        if not self.authenticated:
            raise ConnectionError("未连接或未认证")
        with self._lock:
            t = float(timeout) if timeout else self.timeout
            req_id = self._next_id()
            flush_id = self._next_id()
            self.sock.settimeout(t)
            self.sock.sendall(self._encode(req_id, self.EXECCOMMAND, cmd))
            self.sock.sendall(self._encode(flush_id, self.RESPONSE_VALUE, ""))

            chunks = []
            partial = False
            deadline = time.time() + t
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    partial = True
                    break
                self.sock.settimeout(remaining)
                try:
                    rid, ptype, body = self._recv_packet()
                except socket.timeout:
                    partial = True
                    break
                if ptype != self.RESPONSE_VALUE:
                    continue
                if rid == req_id:
                    chunks.append(body)
                elif rid == flush_id:
                    break
            return {"response": "".join(chunks), "partial": partial}


# =============================================================================
# 全局连接状态
# =============================================================================

STATE = {"client": None, "info": None}
STATE_LOCK = threading.Lock()

APP = {"httpd": None, "thread": None, "port": None, "running": False}


# =============================================================================
# 内嵌 Web 界面（GeLi 品牌配色：白底 + 蓝主色 + 浅绿点缀）
# =============================================================================

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GeLi RCON</title>
<style>
  :root {
    --bg: #f6f8f7;
    --panel: #ffffff;
    --panel-2: #f4f6f5;
    --border: #e4e8e6;
    --text: #16202b;
    --text-dim: #83909c;
    --accent: #2b7fff;
    --accent-hover: #1a66e0;
    --accent-soft: #edf3ff;
    --mint: #96ff8f;
    --cyan: #8dfffa;
    --mint-ink: #0fa968;
    --mint-soft: #f0fff2;
    --green: #00b42a;
    --red: #f53f3f;
    --yellow: #ff7d00;
    --mono: "Cascadia Code", "SFMono-Regular", Consolas, "JetBrains Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body {
    background: var(--bg); color: var(--text);
    font-family: -apple-system, "Segoe UI", "Microsoft YaHei", Roboto, sans-serif;
    font-size: 14px; display: flex; flex-direction: column; overflow: hidden;
  }
  header {
    height: 54px; display: flex; align-items: center; justify-content: space-between;
    padding: 0 20px; background: var(--panel); border-bottom: 1px solid var(--border);
    flex-shrink: 0; position: relative;
  }
  /* 品牌渐变细线：浅绿 → 浅青（取自 GeLi 图标） */
  header::after {
    content: ""; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px;
    background: linear-gradient(90deg, var(--mint), var(--cyan), var(--mint));
    opacity: .9; pointer-events: none;
  }
  .logo { font-size: 16px; font-weight: 700; display: flex; align-items: baseline; gap: 2px; }
  .logo .mark { margin-right: 6px; font-size: 17px; }
  .logo .word { color: var(--accent); }
  .status-badge {
    display: flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 20px;
    background: var(--panel-2); border: 1px solid var(--border); font-size: 13px; color: var(--text-dim);
  }
  .status-dot { width: 9px; height: 9px; border-radius: 50%; background: #c9cdd4; }
  .status-badge.on { color: var(--green); border-color: rgba(0,180,42,.35); background: #f0fff4; }
  .status-badge.on .status-dot { background: var(--green); box-shadow: 0 0 6px rgba(0,180,42,.6); }

  main { flex: 1; display: flex; min-height: 0; }
  .sidebar {
    width: 300px; flex-shrink: 0; background: var(--panel); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; overflow-y: auto;
  }
  .content { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .panel { padding: 16px; border-bottom: 1px solid var(--border); }
  .panel-title {
    font-size: 12px; text-transform: uppercase; letter-spacing: 1px;
    color: var(--text-dim); margin-bottom: 10px; font-weight: 600;
  }

  .server-item {
    padding: 10px 12px; border-radius: 10px; cursor: pointer; background: var(--panel-2);
    border: 1px solid var(--border); margin-bottom: 8px; transition: border-color .15s, background .15s;
  }
  .server-item:hover { border-color: #c9cdd4; }
  .server-item.active { border-color: var(--accent); background: var(--accent-soft); }
  .server-item .name { font-weight: 600; margin-bottom: 2px; }
  .server-item .meta { font-size: 12px; color: var(--text-dim); font-family: var(--mono); }
  .server-item .actions { margin-top: 8px; display: flex; gap: 6px; }
  .server-item .actions button { flex: 1; }

  button {
    background: var(--panel-2); color: var(--text); border: 1px solid var(--border);
    border-radius: 8px; padding: 7px 12px; font-size: 13px; cursor: pointer;
    transition: background .12s, border-color .12s, color .12s;
  }
  button:hover { border-color: #c9cdd4; background: #eceef1; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
  button.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
  button.danger { color: var(--red); }
  button.danger:hover { border-color: var(--red); background: #fff1f0; }
  button.small { padding: 4px 9px; font-size: 12px; }
  button:disabled { opacity: .45; cursor: not-allowed; }

  .preset-grid { display: flex; flex-wrap: wrap; gap: 6px; }
  .preset-btn {
    font-size: 12px; padding: 5px 10px; border-radius: 7px; background: var(--panel-2);
    border: 1px solid var(--border); cursor: pointer; color: var(--text);
  }
  .preset-btn:hover { border-color: var(--mint-ink); color: var(--mint-ink); background: var(--mint-soft); }

  .console {
    flex: 1; overflow-y: auto; padding: 14px 18px; background: var(--panel);
    font-family: var(--mono); font-size: 13px; line-height: 1.6;
  }
  .line { white-space: pre-wrap; word-break: break-word; margin-bottom: 2px; }
  .line.cmd { color: var(--accent); }
  .line.cmd::before { content: "> "; color: var(--text-dim); }
  .line.resp { color: var(--text); }
  .line.sys { color: var(--text-dim); font-style: italic; }
  .line.err { color: var(--red); }
  .line.warn { color: var(--yellow); }

  .inputbar {
    display: flex; gap: 8px; padding: 12px 16px; background: var(--panel); border-top: 1px solid var(--border);
  }
  .inputbar input {
    flex: 1; background: var(--panel-2); color: var(--text); border: 1px solid var(--border);
    border-radius: 8px; padding: 10px 14px; font-family: var(--mono); font-size: 14px; outline: none;
  }
  .inputbar input:focus { border-color: var(--accent); background: #fff; }

  .toolbar {
    display: flex; align-items: center; gap: 12px; padding: 0 16px 12px; background: var(--panel); flex-wrap: wrap;
  }
  .toolbar label { display: flex; align-items: center; gap: 6px; color: var(--text-dim); font-size: 12px; cursor: pointer; }
  .toolbar input[type=checkbox] { accent-color: var(--accent); }
  .toolbar .spacer { flex: 1; }

  .modal-mask {
    position: fixed; inset: 0; background: rgba(0,0,0,.45); display: flex;
    align-items: center; justify-content: center; z-index: 50;
  }
  .modal-mask.hidden { display: none; }
  .modal {
    width: 440px; max-width: 92vw; max-height: 88vh; overflow-y: auto; background: var(--panel);
    border: 1px solid var(--border); border-radius: 12px; padding: 22px;
    box-shadow: 0 12px 40px rgba(0,0,0,.12);
  }
  .modal h3 { margin-bottom: 16px; font-size: 16px; }
  .field { margin-bottom: 12px; }
  .field label { display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 5px; }
  .field input, .field select, .field textarea {
    width: 100%; background: var(--panel-2); color: var(--text); border: 1px solid var(--border);
    border-radius: 8px; padding: 9px 12px; font-size: 14px; outline: none; font-family: inherit;
  }
  .field input:focus, .field select:focus, .field textarea:focus { border-color: var(--accent); background: #fff; }
  .field textarea { font-family: var(--mono); min-height: 120px; resize: vertical; }
  .field .row { display: flex; gap: 10px; }
  .field .row > * { flex: 1; }
  .modal .footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
  .hint { font-size: 12px; color: var(--text-dim); margin-top: 4px; }

  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-thumb { background: #d6dae1; border-radius: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
</style>
</head>
<body>
<header>
  <div class="logo"><span class="mark">🕊️</span>GeLi<span class="word">RCON</span></div>
  <div class="status-badge" id="statusBadge"><span class="status-dot"></span><span id="statusText">未连接</span></div>
</header>

<main>
  <aside class="sidebar">
    <div class="panel">
      <div class="panel-title">服务器</div>
      <div id="serverList"></div>
      <button id="addServerBtn" style="width:100%">+ 添加服务器</button>
    </div>
    <div class="panel">
      <div class="panel-title">快捷命令</div>
      <div class="hint" style="margin-bottom:8px">带 &lt;&gt; 的命令会填入输入框，其余一键执行。</div>
      <div class="preset-grid" id="presetList"></div>
    </div>
  </aside>

  <section class="content">
    <div class="console" id="console"></div>
    <div class="inputbar">
      <input id="cmdInput" placeholder="输入命令，回车发送…" autocomplete="off" spellcheck="false">
      <button class="primary" id="sendBtn">发送</button>
    </div>
    <div class="toolbar">
      <label><input type="checkbox" id="autoScroll" checked>自动滚动</label>
      <button id="clearBtn" class="small">清空屏幕</button>
      <button id="watchBtn" class="small">监控模式</button>
      <button id="batchBtn" class="small">批量执行</button>
      <span class="spacer"></span>
      <button id="discBtn" class="small danger" disabled>断开连接</button>
    </div>
  </section>
</main>

<div class="modal-mask hidden" id="serverModal">
  <div class="modal">
    <h3 id="serverModalTitle">添加服务器</h3>
    <input type="hidden" id="serverId">
    <div class="field"><label>名称</label><input id="fName" placeholder="例如：我的生存服"></div>
    <div class="field"><label>游戏类型</label><select id="fGame"></select></div>
    <div class="field row">
      <div><label>主机地址</label><input id="fHost" placeholder="127.0.0.1"></div>
      <div><label>端口</label><input id="fPort" type="number" placeholder="25575"></div>
    </div>
    <div class="field"><label>RCON 密码</label><input id="fPass" type="password" placeholder="服务器配置里的 rcon.password"></div>
    <div class="field"><label>命令超时（毫秒）</label><input id="fTimeout" type="number" value="5000"></div>
    <div class="footer">
      <button id="serverCancel">取消</button>
      <button class="primary" id="serverSave">保存</button>
    </div>
  </div>
</div>

<div class="modal-mask hidden" id="watchModal">
  <div class="modal">
    <h3>监控模式</h3>
    <p class="hint" style="margin-bottom:14px">每隔一段时间自动重复执行同一条命令（如 list / status），实时刷新服务器状态。</p>
    <div class="field"><label>命令</label><input id="wCmd" placeholder="list"></div>
    <div class="field"><label>间隔（秒）</label><input id="wInterval" type="number" value="5" min="1"></div>
    <div class="footer">
      <button id="watchCancel">取消</button>
      <button class="primary" id="watchStart">开始监控</button>
    </div>
  </div>
</div>

<div class="modal-mask hidden" id="batchModal">
  <div class="modal">
    <h3>批量执行</h3>
    <p class="hint" style="margin-bottom:14px">每行一条命令，将按顺序依次执行。</p>
    <div class="field"><textarea id="bCmds" placeholder="list&#10;save-all&#10;say 服务器即将重启"></textarea></div>
    <div class="footer">
      <button id="batchCancel">取消</button>
      <button class="primary" id="batchRun">执行</button>
    </div>
  </div>
</div>

<script>
(() => {
  const GAMES = {
    minecraft: { name: 'Minecraft', defaultPort: 25575 },
    source:    { name: 'Source (CS:GO/CS2/GMOD/TF2)', defaultPort: 27015 },
    rust:      { name: 'Rust', defaultPort: 28016 },
    ark:       { name: 'ARK', defaultPort: 27020 },
    palworld:  { name: 'Palworld (幻兽帕鲁)', defaultPort: 25575 },
    generic:   { name: '通用', defaultPort: 25575 },
  };
  const PRESETS = {
    minecraft: [
      ['在线玩家', 'list'], ['保存世界', 'save-all'], ['查看种子', 'seed'],
      ['白名单列表', 'whitelist list'], ['添加白名单', 'whitelist add <player>'],
      ['移除白名单', 'whitelist remove <player>'], ['封禁玩家', 'ban <player>'],
      ['解封玩家', 'pardon <player>'], ['踢出玩家', 'kick <player>'],
      ['给予OP', 'op <player>'], ['移除OP', 'deop <player>'],
      ['广播消息', 'say <msg>'], ['给予物品', 'give <player> <item> <count>'],
      ['传送', 'tp <player> <target>'], ['切换模式', 'gamemode <mode> <player>'],
      ['设置时间', 'time set <value>'], ['设置天气', 'weather <type>'],
      ['设置难度', 'difficulty <level>'], ['TPS', 'tps'], ['停止服务器', 'stop'],
    ],
    source: [
      ['状态', 'status'], ['在线用户', 'users'], ['踢出', 'kickid <userid>'],
      ['封禁', 'banid <minutes> <userid>'], ['广播', 'say <msg>'],
      ['换图', 'changelevel <map>'], ['切地图', 'map <map>'],
      ['重启回合', 'mp_restartgame 1'], ['开作弊', 'sv_cheats 1'], ['关作弊', 'sv_cheats 0'],
    ],
    rust: [
      ['状态', 'status'], ['在线玩家', 'players'], ['广播', 'say <msg>'],
      ['踢出', 'kick <player>'], ['封禁', 'ban <player> <reason>'],
      ['重启插件', 'o.reload <plugin>'], ['服务器信息', 'serverinfo'], ['保存', 'save'],
    ],
    ark: [
      ['玩家列表', 'ListPlayers'], ['广播', 'ServerChat <msg>'], ['踢出', 'KickPlayer <steamid>'],
      ['封禁', 'BanPlayer <steamid>'], ['保存世界', 'SaveWorld'], ['关闭服务器', 'DoExit'],
    ],
    palworld: [
      ['在线玩家', 'ShowPlayers'], ['广播', 'Broadcast <msg>'], ['踢出', 'KickPlayer <steamid>'],
      ['封禁', 'BanPlayer <steamid>'], ['保存', 'Save'], ['关闭', 'Shutdown <seconds> <msg>'],
      ['服务器信息', 'Info'],
    ],
    generic: [ ['帮助', 'help'], ['状态', 'status'] ],
  };

  const store = {
    servers: JSON.parse(localStorage.getItem('rcon_servers') || '[]'),
    currentId: localStorage.getItem('rcon_current') || null,
    history: JSON.parse(localStorage.getItem('rcon_history') || '[]'),
    historyIdx: -1,
    connected: false,
    watchTimer: null,
  };

  const $ = (id) => document.getElementById(id);
  const api = async (path, body) => {
    const res = await fetch(path, {
      method: body ? 'POST' : 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    return res.json();
  };

  function log(text, cls) {
    const c = $('console');
    const div = document.createElement('div');
    div.className = 'line ' + (cls || 'resp');
    div.textContent = text;
    c.appendChild(div);
    if ($('autoScroll').checked) c.scrollTop = c.scrollHeight;
  }
  const logCmd = (t) => log(t, 'cmd');
  const logResp = (t, partial) => {
    log(t === '' ? '(无响应)' : t, 'resp');
    if (partial) log('⚠ 响应可能在超时前未完全返回', 'warn');
  };
  const logSys = (t) => log(t, 'sys');
  const logErr = (t) => log(t, 'err');

  function updateStatus(connected, label) {
    store.connected = connected;
    const badge = $('statusBadge');
    badge.className = 'status-badge' + (connected ? ' on' : '');
    $('statusText').textContent = connected ? ('已连接: ' + label) : '未连接';
    $('discBtn').disabled = !connected;
  }

  async function connectTo(id) {
    const s = store.servers.find((x) => x.id === id);
    if (!s) return;
    store.currentId = id;
    localStorage.setItem('rcon_current', id);
    renderServers();
    logSys('正在连接 ' + s.name + ' (' + s.host + ':' + s.port + ') …');
    const r = await api('/api/connect', {
      host: s.host, port: s.port, password: s.password, timeout: s.timeout || 5000,
    });
    if (r.ok) {
      updateStatus(true, s.name);
      logSys('✓ 已连接并认证成功');
      renderPresets(s.game);
    } else {
      updateStatus(false);
      logErr('连接失败: ' + (r.error || '未知错误'));
    }
  }

  async function disconnect() {
    if (stopWatch()) logSys('已停止监控模式');
    await api('/api/disconnect', {});
    updateStatus(false);
    logSys('已断开连接');
  }

  async function sendCommand(cmd) {
    cmd = (cmd || '').trim();
    if (!cmd) return;
    if (!store.connected) { logErr('未连接服务器，请先在左侧选择并连接'); return; }
    logCmd(cmd);
    addHistory(cmd);
    const r = await api('/api/command', { command: cmd });
    if (r.ok) logResp(r.response, r.partial);
    else logErr(r.error || '命令执行失败');
  }

  function addHistory(cmd) {
    store.history = store.history.filter((h) => h !== cmd);
    store.history.push(cmd);
    if (store.history.length > 200) store.history.shift();
    localStorage.setItem('rcon_history', JSON.stringify(store.history));
    store.historyIdx = store.history.length;
  }

  function renderServers() {
    const list = $('serverList');
    list.innerHTML = '';
    if (store.servers.length === 0) {
      list.innerHTML = '<div class="hint" style="padding:6px 2px">暂无服务器，点击下方按钮添加。</div>';
      return;
    }
    store.servers.forEach((s) => {
      const div = document.createElement('div');
      div.className = 'server-item' + (s.id === store.currentId ? ' active' : '');
      const game = (GAMES[s.game] || GAMES.generic).name;
      div.innerHTML = '<div class="name"></div><div class="meta"></div><div class="actions"></div>';
      div.querySelector('.name').textContent = s.name;
      div.querySelector('.meta').textContent = s.host + ':' + s.port + ' · ' + game;
      const actions = div.querySelector('.actions');
      const btnConn = document.createElement('button');
      btnConn.className = 'small primary';
      btnConn.textContent = '连接';
      btnConn.onclick = (e) => { e.stopPropagation(); connectTo(s.id); };
      const btnEdit = document.createElement('button');
      btnEdit.className = 'small';
      btnEdit.textContent = '编辑';
      btnEdit.onclick = (e) => { e.stopPropagation(); openServerModal(s.id); };
      const btnDel = document.createElement('button');
      btnDel.className = 'small danger';
      btnDel.textContent = '删除';
      btnDel.onclick = (e) => {
        e.stopPropagation();
        if (confirm('确定删除服务器「' + s.name + '」？')) {
          store.servers = store.servers.filter((x) => x.id !== s.id);
          if (store.currentId === s.id) store.currentId = null;
          localStorage.setItem('rcon_servers', JSON.stringify(store.servers));
          renderServers();
        }
      };
      actions.appendChild(btnConn);
      actions.appendChild(btnEdit);
      actions.appendChild(btnDel);
      list.appendChild(div);
    });
  }

  function openServerModal(id) {
    const s = id ? store.servers.find((x) => x.id === id) : null;
    $('serverModalTitle').textContent = s ? '编辑服务器' : '添加服务器';
    $('serverId').value = s ? s.id : '';
    $('fName').value = s ? s.name : '';
    $('fGame').value = s ? s.game : 'minecraft';
    $('fHost').value = s ? s.host : '';
    $('fPort').value = s ? s.port : GAMES.minecraft.defaultPort;
    $('fPass').value = s ? s.password : '';
    $('fTimeout').value = s ? (s.timeout || 5000) : 5000;
    $('serverModal').classList.remove('hidden');
    $('fName').focus();
  }

  function saveServer() {
    const name = $('fName').value.trim() || '未命名服务器';
    const game = $('fGame').value;
    const host = $('fHost').value.trim();
    const port = parseInt($('fPort').value, 10);
    const password = $('fPass').value;
    const timeout = parseInt($('fTimeout').value, 10) || 5000;
    if (!host || !port) { alert('请填写主机地址和端口'); return; }
    const id = $('serverId').value;
    if (id) {
      const s = store.servers.find((x) => x.id === id);
      Object.assign(s, { name, game, host, port, password, timeout });
    } else {
      store.servers.push({
        id: 's_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        name, game, host, port, password, timeout,
      });
    }
    localStorage.setItem('rcon_servers', JSON.stringify(store.servers));
    $('serverModal').classList.add('hidden');
    renderServers();
  }

  function renderPresets(game) {
    const box = $('presetList');
    box.innerHTML = '';
    const presets = PRESETS[game] || PRESETS.generic;
    presets.forEach(([label, cmd]) => {
      const btn = document.createElement('button');
      btn.className = 'preset-btn';
      btn.textContent = label;
      btn.onclick = () => {
        if (/<[^>]+>/.test(cmd)) { $('cmdInput').value = cmd; $('cmdInput').focus(); }
        else sendCommand(cmd);
      };
      box.appendChild(btn);
    });
  }

  function startWatch() {
    const cmd = $('wCmd').value.trim();
    const interval = parseInt($('wInterval').value, 10);
    if (!cmd) { alert('请输入监控命令'); return; }
    if (!interval || interval < 1) { alert('间隔至少 1 秒'); return; }
    $('watchModal').classList.add('hidden');
    stopWatch();
    logSys('▶ 开始监控模式: 每 ' + interval + ' 秒执行一次「' + cmd + '」');
    sendCommand(cmd);
    store.watchTimer = setInterval(() => sendCommand(cmd), interval * 1000);
    $('watchBtn').textContent = '停止监控';
  }
  function stopWatch() {
    if (store.watchTimer) {
      clearInterval(store.watchTimer);
      store.watchTimer = null;
      $('watchBtn').textContent = '监控模式';
      return true;
    }
    return false;
  }

  async function runBatch() {
    const cmds = $('bCmds').value.split('\n').map((s) => s.trim()).filter(Boolean);
    if (cmds.length === 0) { alert('请输入命令'); return; }
    $('batchModal').classList.add('hidden');
    logSys('▶ 批量执行 ' + cmds.length + ' 条命令');
    for (const cmd of cmds) {
      await sendCommand(cmd);
      await new Promise((r) => setTimeout(r, 200));
    }
    logSys('✓ 批量执行完成');
  }

  function bindEvents() {
    $('addServerBtn').onclick = () => openServerModal(null);
    $('serverCancel').onclick = () => $('serverModal').classList.add('hidden');
    $('serverSave').onclick = saveServer;

    $('sendBtn').onclick = () => { sendCommand($('cmdInput').value); $('cmdInput').value = ''; $('cmdInput').focus(); };
    $('cmdInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        sendCommand($('cmdInput').value);
        $('cmdInput').value = '';
        store.historyIdx = store.history.length;
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (store.historyIdx > 0) store.historyIdx--;
        $('cmdInput').value = store.history[store.historyIdx] || '';
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (store.historyIdx < store.history.length - 1) store.historyIdx++;
        else store.historyIdx = store.history.length;
        $('cmdInput').value = store.history[store.historyIdx] || '';
      }
    });

    $('clearBtn').onclick = () => { $('console').innerHTML = ''; };
    $('discBtn').onclick = disconnect;

    $('watchBtn').onclick = () => {
      if (store.watchTimer) { if (stopWatch()) logSys('已停止监控模式'); return; }
      $('wCmd').value = 'list';
      $('wInterval').value = 5;
      $('watchModal').classList.remove('hidden');
    };
    $('watchCancel').onclick = () => $('watchModal').classList.add('hidden');
    $('watchStart').onclick = startWatch;

    $('batchBtn').onclick = () => $('batchModal').classList.remove('hidden');
    $('batchCancel').onclick = () => $('batchModal').classList.add('hidden');
    $('batchRun').onclick = runBatch;

    document.querySelectorAll('.modal-mask').forEach((m) => {
      m.addEventListener('click', (e) => { if (e.target === m) m.classList.add('hidden'); });
    });
  }

  function init() {
    const sel = $('fGame');
    sel.innerHTML = '';
    Object.entries(GAMES).forEach(([key, g]) => {
      const o = document.createElement('option');
      o.value = key;
      o.textContent = g.name;
      sel.appendChild(o);
    });

    bindEvents();
    renderServers();
    renderPresets('minecraft');

    sel.addEventListener('change', () => {
      if (!$('serverId').value) $('fPort').value = GAMES[sel.value].defaultPort;
    });

    api('/api/status').then((r) => {
      if (r.connected && r.info) updateStatus(true, r.info.host + ':' + r.info.port);
    });

    logSys('欢迎使用 GeLi RCON。请在左侧添加并连接服务器。');
  }

  init();
})();
</script>
</body>
</html>
"""


# =============================================================================
# HTTP 服务（REST API + 内嵌 Web 界面）
# =============================================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # 静默访问日志

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            with STATE_LOCK:
                c = STATE["client"]
                connected = bool(c and c.authenticated)
                info = STATE["info"]
            self._send_json(200, {"connected": connected, "info": info})
        else:
            self._send_html(INDEX_HTML)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        body = self._read_json()
        try:
            if path == "/api/connect":
                host = body.get("host")
                port = body.get("port")
                password = body.get("password")
                timeout_ms = body.get("timeout") or 5000
                if not host or not port:
                    return self._send_json(400, {"ok": False, "error": "缺少 host 或 port"})
                with STATE_LOCK:
                    old = STATE["client"]
                    if old:
                        old.disconnect()
                    c = RconClient(host, port, password, float(timeout_ms) / 1000.0)
                try:
                    c.connect()
                except Exception as e:
                    c.disconnect()
                    return self._send_json(200, {"ok": False, "error": str(e)})
                with STATE_LOCK:
                    STATE["client"] = c
                    STATE["info"] = {"host": host, "port": int(port)}
                return self._send_json(200, {"ok": True})

            if path == "/api/disconnect":
                with STATE_LOCK:
                    c = STATE["client"]
                    if c:
                        c.disconnect()
                    STATE["client"] = None
                    STATE["info"] = None
                return self._send_json(200, {"ok": True})

            if path == "/api/command":
                with STATE_LOCK:
                    c = STATE["client"]
                    if not c or not c.authenticated:
                        return self._send_json(200, {"ok": False, "error": "未连接服务器"})
                cmd = (body.get("command") or "").strip()
                if not cmd:
                    return self._send_json(400, {"ok": False, "error": "命令为空"})
                timeout_ms = body.get("timeout")
                t = float(timeout_ms) / 1000.0 if timeout_ms else None
                try:
                    r = c.send_command(cmd, t)
                    return self._send_json(200, {"ok": True, "response": r["response"], "partial": r["partial"]})
                except Exception as e:
                    return self._send_json(200, {"ok": False, "error": str(e)})

            return self._send_json(404, {"ok": False, "error": "Not Found"})
        except Exception as e:
            return self._send_json(500, {"ok": False, "error": str(e)})


def start_http_server(preferred_port=8080):
    for port in range(preferred_port, preferred_port + 100):
        try:
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
            return httpd, port
        except OSError:
            continue
    raise RuntimeError("无法找到可用端口")


# =============================================================================
# 弹窗 GUI（tkinter，GeLi 品牌配色）
# =============================================================================

BLUE = "#2B7FFF"        # 主色：品牌蓝
BLUE_DARK = "#1A66E0"
BLUE_LIGHT = "#BBD4FF"
MINT = "#96FF8F"        # 次色：品牌浅绿（图标渐变起点）
CYAN = "#8DFFFA"        # 次色：品牌浅青（图标渐变终点）
MINT_INK = "#0FA968"    # 浅绿的深色版，用于可读文字
MINT_SOFT = "#F0FFF2"
WHITE = "#FFFFFF"
DARK_TEXT = "#16202B"
GRAY_TEXT = "#83909C"
LIGHT_BG = "#F4F6F5"
BORDER = "#E4E8E6"
GREEN = MINT_INK
RED = "#F53F3F"
GRAY_DOT = "#C9CDD4"
FONT = "Microsoft YaHei"


def _hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c1, c2, t):
    a, b = _hex_to_rgb(c1), _hex_to_rgb(c2)
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_brand_gradient(canvas, width, height):
    """在 Canvas 上绘制品牌渐变条：浅绿 → 浅青（对应 GeLi 图标配色）。"""
    canvas.delete("brand_grad")
    if width <= 1 or height <= 0:
        return
    step = 2
    for x in range(0, width, step):
        color = _mix(MINT, CYAN, x / max(width - 1, 1))
        canvas.create_rectangle(x, 0, x + step, height, fill=color,
                                outline="", tags="brand_grad")


def enable_dpi_awareness():
    """启用 Windows DPI 感知，修复高分屏下 tkinter 控件错位/模糊问题。"""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class RconApp:
    def __init__(self):
        import tkinter as tk
        from tkinter import messagebox
        self.tk = tk
        self.messagebox = messagebox

        self.root = tk.Tk()
        self.root.title("GeLi RCON")
        self.root.configure(bg=WHITE)
        self.root.resizable(False, False)
        self._build_ui()
        self._fit_window(440)
        self.root.protocol("WM_DELETE_WINDOW", self.quit_action)

    def _fit_window(self, width):
        """按内容实际需要的高度定尺寸，避免底部控件被裁切。"""
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
        self._center_window(width, height)

    def _center_window(self, w, h):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _build_ui(self):
        tk = self.tk
        # 顶部品牌渐变条（浅绿 → 浅青）
        strip = tk.Canvas(self.root, height=5, bg=MINT, highlightthickness=0, bd=0)
        strip.pack(fill="x")
        strip.bind("<Configure>", lambda e: draw_brand_gradient(strip, e.width, e.height))

        body = tk.Frame(self.root, bg=WHITE)
        body.pack(fill="both", expand=True, padx=30, pady=24)
        body.grid_columnconfigure(0, weight=1)

        # 品牌标题行
        title_row = tk.Frame(body, bg=WHITE)
        title_row.grid(row=0, column=0, sticky="w")

        tk.Label(title_row, text="🕊️", bg=WHITE, fg=DARK_TEXT,
                 font=("Segoe UI Emoji", 17)).pack(side="left", padx=(0, 8))
        tk.Label(title_row, text="GeLi", bg=WHITE, fg=DARK_TEXT,
                 font=(FONT, 20, "bold")).pack(side="left")
        tk.Label(title_row, text="RCON", bg=WHITE, fg=BLUE,
                 font=(FONT, 20, "bold")).pack(side="left", padx=(4, 0))

        tk.Label(body, text="跨游戏 RCON 远程控制台 · 开箱即用", bg=WHITE, fg=GRAY_TEXT,
                 font=(FONT, 10)).grid(row=1, column=0, sticky="w", pady=(3, 0))

        # 状态卡片（浅灰底，圆点 + 文字 + 地址，grid 对齐避免错位）
        card = tk.Frame(body, bg=LIGHT_BG)
        card.grid(row=2, column=0, sticky="ew", pady=(20, 16))
        card.grid_columnconfigure(1, weight=1)

        self.dot_label = tk.Label(card, text="●", bg=LIGHT_BG, fg=GRAY_DOT,
                                  font=(FONT, 11))
        self.dot_label.grid(row=0, column=0, padx=(14, 7), pady=(12, 0))
        self.status_label = tk.Label(card, text="服务未启动", bg=LIGHT_BG, fg=GRAY_TEXT,
                                     font=(FONT, 11), anchor="w")
        self.status_label.grid(row=0, column=1, sticky="w", pady=(12, 0))
        self.url_label = tk.Label(card, text="点击「一键启动」开始", bg=LIGHT_BG, fg=GRAY_TEXT,
                                  font=("Consolas", 9), anchor="w", cursor="hand2")
        self.url_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=14, pady=(2, 12))
        self.url_label.bind("<Button-1>", self._open_url)

        # 按钮
        self.start_btn = tk.Button(body, text="一键启动", command=self.start_action,
                                   bg=BLUE, fg=WHITE, activebackground=BLUE_DARK, activeforeground=WHITE,
                                   font=(FONT, 13, "bold"), relief="flat", bd=0,
                                   cursor="hand2", height=2)
        self.start_btn.grid(row=3, column=0, sticky="ew")

        self.quit_btn = tk.Button(body, text="一键退出", command=self.quit_action,
                                  bg=LIGHT_BG, fg=DARK_TEXT, activebackground="#E9EAEE", activeforeground=DARK_TEXT,
                                  font=(FONT, 13, "bold"), relief="flat", bd=0,
                                  cursor="hand2", height=2)
        self.quit_btn.grid(row=4, column=0, sticky="ew", pady=(10, 0))

        tk.Label(body, text="启动后自动打开网页控制台 · 退出即停止服务并关闭程序",
                 bg=WHITE, fg=GRAY_TEXT, font=(FONT, 9)).grid(row=5, column=0, pady=(14, 0))

    def _open_url(self, _event=None):
        if APP["running"] and APP["port"]:
            webbrowser.open(f"http://localhost:{APP['port']}")

    def _set_status(self, running, text):
        if running:
            self.dot_label.config(fg=GREEN)
            self.status_label.config(text=text, fg=GREEN)
        else:
            self.dot_label.config(fg=GRAY_DOT)
            self.status_label.config(text=text, fg=GRAY_TEXT)

    def start_action(self):
        if APP["running"]:
            return
        try:
            httpd, port = start_http_server(8080)
        except Exception as e:
            self.messagebox.showerror("启动失败", str(e))
            return

        APP["httpd"] = httpd
        APP["port"] = port
        APP["running"] = True
        APP["thread"] = threading.Thread(target=httpd.serve_forever, daemon=True)
        APP["thread"].start()

        url = f"http://localhost:{port}"
        self._set_status(True, f"服务运行中 · 端口 {port}")
        self.url_label.config(text=url, fg=BLUE)
        self.start_btn.config(state="disabled", disabledforeground=WHITE, bg=BLUE_LIGHT)
        webbrowser.open(url)

    def quit_action(self):
        try:
            if APP["httpd"]:
                APP["httpd"].shutdown()
                APP["httpd"].server_close()
        except Exception:
            pass
        APP["running"] = False
        with STATE_LOCK:
            if STATE["client"]:
                STATE["client"].disconnect()
                STATE["client"] = None
        self.root.destroy()
        os._exit(0)

    def run(self):
        self.root.mainloop()


# =============================================================================
# 入口
# =============================================================================

def main():
    enable_dpi_awareness()
    try:
        import tkinter  # noqa: F401
    except Exception:
        print("缺少 tkinter，请使用完整版 Python。")
        sys.exit(1)
    app = RconApp()
    app.run()


if __name__ == "__main__":
    main()
