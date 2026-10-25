/**
 * 一次性故障注入服务器（窗口3 验证 AiClient 的 HTTP 层行为，用完即弃）
 *   node fault-server.mjs 5xx     → 所有请求返回 HTTP 500
 *   node fault-server.mjs timeout → 挂起 16s 后返回（超过 PHP 单次 15s 上限）
 */
import http from 'node:http';

const mode = process.argv[2] || '5xx';
const server = http.createServer((req, res) => {
  if (mode === '5xx') {
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ code: 1, error: 'injected 500' }));
  } else {
    setTimeout(() => {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ code: 0, data: { reply: 'too late' } }));
    }, 16000);
  }
});
server.listen(8000, () => console.log(`[fault-server] mode=${mode} :8000`));
