/**
 * crmeb-ai-mall —— AI 全链路背压压测（窗口4）
 *
 * 目的：量化「背压阈值 ai_service_queue_limit=50」与「SLA 超时放弃 deadline_blown」：
 *   1. 把 ai_service_url 指到慢速 fake 实例（:8010，FAKE_LATENCY_MS>0，恢复时还原），
 *      queue:listen 消费速度受限于 fake 延迟 → 队列堆积。
 *   2. 以指定速率突发发送 N 条消息，轮询记录 redis 队列深度峰值。
 *   3. 结束后从 eb_ai_call_log 统计：rejected（派发背压拒绝）/ success / silent（deadline_blown 等）。
 *
 * 前置：:8010 已启动 LLM_PROVIDER=fake 的 ai-service（见窗口4文档）。
 * 运行：node help/dev/ai-load-e2e.mjs --count 200 --rate 10
 */

import { execSync } from 'node:child_process';

const args = process.argv.slice(2);
const opt = (name, dft) => {
  const i = args.indexOf('--' + name);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : dft;
};

const SITE = opt('site', 'http://localhost:8011');
const COUNT = Number(opt('count', 200));
const RATE = Number(opt('rate', 10)); // 每秒发送条数
const COUNT_ON_TABLE = Number(opt('count-on-table', 3)); // 目标商品关键词（保证 KB 命中路径）
const WAIT_DRAIN_S = Number(opt('wait-drain', 180)); // 队列清空等待秒数

const wsBase = SITE.replace(/^http/, 'ws');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const stamp = () => new Date().toISOString().slice(11, 19);

const sh = (cmd) => execSync(cmd, { shell: 'bash', encoding: 'utf8' }).toString();
const mysql = (sql) =>
  sh(
    `docker exec crmeb_mysql mysql -uroot -p123456 --default-character-set=utf8mb4 -N -B crmeb -e ${JSON.stringify(sql)} 2>/dev/null`,
  );
const clearConfigCache = () => {
  sh('docker exec crmeb_php sh -c "rm -rf /var/www/runtime/cache/*"');
  sh(
    `docker exec crmeb_redis sh -c "redis-cli -a 123456 --scan --pattern '*system_config*' 2>/dev/null | xargs -r redis-cli -a 123456 DEL 2>/dev/null"`,
  );
};
const setConfig = (name, value) => {
  mysql(`UPDATE eb_system_config SET value='${value.replace(/'/g, "''")}' WHERE menu_name='${name}'`);
  clearConfigCache();
};
const queueLen = () => Number(
  sh(`docker exec crmeb_redis redis-cli -a 123456 --no-auth-warning LLEN queues:CRMEB6ad6fadaa57bf 2>/dev/null`).trim() || '0',
);
const logCounts = (fromTs) =>
  mysql(
    `SELECT outcome, event, COUNT(*) FROM eb_ai_call_log WHERE add_time >= ${fromTs} GROUP BY outcome, event ORDER BY 3 DESC`,
  ).trim();

async function postForm(path, form) {
  const res = await fetch(SITE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(form).toString(),
  });
  const json = await res.json();
  if (json.status !== 200) throw new Error(`${path} 失败：${json.msg}`);
  return json.data;
}

function uidFromToken(token) {
  try {
    const payload = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString('utf8'));
    const jti = typeof payload.jti === 'string' ? JSON.parse(payload.jti) : payload.jti;
    return Number(jti?.id || 0);
  } catch {
    return 0;
  }
}

(async () => {
  console.log(`[${stamp()}] 全链路压测：${COUNT} 条 @ ${RATE}/s   站点：${SITE}`);
  const originalUrl = mysql(`SELECT value FROM eb_system_config WHERE menu_name='ai_service_url'`).trim();
  console.log(`当前 ai_service_url = ${originalUrl}（结束后还原）`);

  const userData = await postForm('/api/login', { account: '13800000002', password: 'crmeb123456' });
  const userUid = Number(userData.uid || uidFromToken(userData.token));
  const kefuUid = Number(mysql(`SELECT uid FROM eb_store_service WHERE id=1`).trim());
  console.log(`客户 uid=${userUid}  客服 uid=${kefuUid}`);

  //切到慢速 fake 实例并开 AI
  setConfig('ai_service_url', '"http://host.docker.internal:8010"');
  setConfig('ai_service_open', '1');
  setConfig('ai_service_notify_on_failure', '0');

  const ws = new WebSocket(wsBase + '/msg');
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve);
    ws.addEventListener('error', (e) => reject(new Error('ws 连接失败')));
  });
  ws.send(JSON.stringify({ type: 'login', data: userData.token, form_type: 1 }));
  ws.send(JSON.stringify({ type: 'to_chat', data: { id: kefuUid } }));
  await sleep(800);

  const fromTs = Math.floor(Date.now() / 1000) - 1;
  const depthSamples = [];
  let maxDepth = 0;
  let polling = true;
  const depthPoller = (async () => {
    while (polling) {
      const d = queueLen();
      depthSamples.push(d);
      if (d > maxDepth) maxDepth = d;
      await sleep(250);
    }
  })();

  //突发发送：fire-and-forget（回复事件不消费，避免海量输出）
  ws.addEventListener('message', () => {});
  const interval = 1000 / RATE;
  const sentStart = Date.now();
  for (let i = 0; i < COUNT; i++) {
    ws.send(
      JSON.stringify({
        type: 'chat',
        data: { to_uid: kefuUid, type: 1, msn: `压测消息 ${i}：这款测试手机多少钱？有货吗？` },
      }),
    );
    if ((i + 1) % 50 === 0) console.log(`[${stamp()}] 已发送 ${i + 1}/${COUNT}  队列深度=${maxDepth}`);
    await sleep(interval);
  }
  const sentMs = Date.now() - sentStart;
  console.log(`[${stamp()}] 发送完毕（${sentMs}ms），等待队列消化（最长 ${WAIT_DRAIN_S}s）...`);

  //等待队列清空
  const drainStart = Date.now();
  let drained = false;
  while (Date.now() - drainStart < WAIT_DRAIN_S * 1000) {
    const d = queueLen();
    if (d === 0) {
      drained = true;
      break;
    }
    await sleep(2000);
  }
  polling = false;
  await depthPoller;
  const avgDepth = depthSamples.length ? (depthSamples.reduce((a, b) => a + b, 0) / depthSamples.length).toFixed(1) : '0';
  console.log(`队列深度：峰值=${maxDepth}  均值=${avgDepth}  样本=${depthSamples.length}  清空=${drained}`);

  //多等几秒让最后的 job 落库
  await sleep(5000);
  console.log(`\n=== eb_ai_call_log 统计（压测窗口内） ===`);
  console.log(logCounts(fromTs));

  const total = Number(mysql(`SELECT COUNT(*) FROM eb_ai_call_log WHERE add_time>=${fromTs}`).trim());
  const rejected = Number(mysql(`SELECT COUNT(*) FROM eb_ai_call_log WHERE add_time>=${fromTs} AND outcome='rejected'`).trim());
  const success = Number(mysql(`SELECT COUNT(*) FROM eb_ai_call_log WHERE add_time>=${fromTs} AND outcome='success'`).trim());
  const silent = Number(mysql(`SELECT COUNT(*) FROM eb_ai_call_log WHERE add_time>=${fromTs} AND outcome='silent'`).trim());
  console.log(`\n=== 结论 ===`);
  console.log(`发送 ${COUNT} 条 @ ${RATE}/s；落库终态 ${total} 行：success=${success} rejected=${rejected} silent=${silent}`);
  console.log(`背压拒绝率 = ${(total ? ((rejected / total) * 100).toFixed(1) : 0)}%   队列峰值 = ${maxDepth}（阈值 50+正在处理的1条）`);

  //还原
  setConfig('ai_service_url', originalUrl);
  console.log(`\nai_service_url 已还原为 ${originalUrl}`);
  ws.close();
  process.exit(0);
})().catch((e) => {
  console.error(`压测失败：${e.message}`);
  try {
    //兜底还原：无论如何把 URL 还原为真实服务
    execSync(
      `docker exec crmeb_mysql mysql -uroot -p123456 -N -B crmeb -e "UPDATE eb_system_config SET value='\\"http://host.docker.internal:8000\\"' WHERE menu_name='ai_service_url'"`,
      { shell: 'bash' },
    );
    console.error('ai_service_url 已兜底还原');
  } catch {}
  process.exit(1);
});
