/**
 * crmeb-ai-mall —— AI 智能客服 E2E 三场景验证（窗口3）
 *
 * 与 chat-e2e.mjs（AI 接入前基线）互补，本脚本验证 AI 链路的关键行为：
 *   off        AI 开关关闭 → 发消息后无 AI 回复，原聊天功能不受影响（基线保持）
 *   normal     客服离线 + 关键词未命中 → AI 经 queue→ai-service→channel 异步回复 reply 事件
 *   human      客服在线（已连接）→ AI 绝不接管，客服正常收到消息
 *   failure    ai-service 不可达 → 静默降级：无 AI 回复、无报错、原消息正常落库
 *   notify     同 failure，但 ai_service_notify_on_failure=1 → 收到配置的降级话术
 *
 * 配置翻转/落库校验直接 docker exec（本机开发环境专用，勿用于 CI）。
 *
 * 运行环境：Node.js >= 21（内置 WebSocket / fetch）；Docker CLI 可用
 * 用法：node help/dev/ai-chat-e2e.mjs --scenario normal [--site http://localhost:8011]
 */

import { execSync } from 'node:child_process';

const args = process.argv.slice(2);
const opt = (name, dft) => {
  const i = args.indexOf('--' + name);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : dft;
};

const SITE = opt('site', 'http://localhost:8011');
const SCENARIO = opt('scenario', 'normal');
const USER_ACCOUNT = opt('user-account', '13800000002');
const USER_PASSWORD = opt('user-password', 'crmeb123456');
const KEFU_ACCOUNT = opt('kefu-account', '13000000000');
const KEFU_PASSWORD = opt('kefu-password', 'kefu123456');
const WAIT_AI_MS = Number(opt('wait-ai', 30000)); // AI SLA 20s + 队列调度，留 30s
const WAIT_NO_AI_MS = Number(opt('wait-no-ai', 8000));

const wsBase = SITE.replace(/^http/, 'ws');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const stamp = () => new Date().toISOString().slice(11, 19);

// 消息必须避开 eb_wechat_key 关键词（如「发货」），否则走关键词自动回复而非 AI
const AI_MSG = opt('msn', '请问这款测试手机是5G的吗？现在有现货吗？');

const sh = (cmd) => execSync(cmd, { shell: 'bash', encoding: 'utf8' }).toString();
const mysql = (sql) => sh(`docker exec crmeb_mysql mysql -uroot -p123456 --default-character-set=utf8mb4 -N -B crmeb -e ${JSON.stringify(sql)} 2>/dev/null`);
const clearConfigCache = () => {
  // 本部署 cache.driver=file：配置缓存在 runtime/cache 文件里，redis 清理只是兜底
  sh('docker exec crmeb_php sh -c "rm -rf /var/www/runtime/cache/*"');
  sh(`docker exec crmeb_redis sh -c "redis-cli -a 123456 --scan --pattern '*system_config*' 2>/dev/null | xargs -r redis-cli -a 123456 DEL 2>/dev/null"`);
};
const setConfig = (name, value) => {
  mysql(`UPDATE eb_system_config SET value='${value.replace(/'/g, "''")}' WHERE menu_name='${name}'`);
  clearConfigCache();
};

function assert(label, ok, detail = '') {
  console.log(`${ok ? '  ✅' : '  ❌'} ${label}${detail ? ' — ' + detail : ''}`);
  if (!ok) process.exitCode = 1;
  return ok;
}

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

function chatClient(name) {
  const events = [];
  const ws = new WebSocket(wsBase + '/msg');
  const ready = new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve);
    ws.addEventListener('error', (e) => reject(new Error(`${name} 连接失败：${e.message}`)));
  });
  ws.addEventListener('message', (ev) => {
    let msg;
    try {
      msg = JSON.parse(String(ev.data));
    } catch {
      msg = { type: 'raw', data: String(ev.data).slice(0, 120) };
    }
    events.push(msg);
    if (msg.type !== 'ping') console.log(`  [${name}] <- ${msg.type} ${JSON.stringify(msg.data ?? '').slice(0, 120)}`);
  });
  return {
    name, events, ready,
    send(obj) {
      ws.send(JSON.stringify(obj));
      console.log(`  [${name}] -> ${obj.type}`);
    },
    close() { try { ws.close(); } catch {} },
    /** 等待首个满足谓词的事件 */
    async waitFor(pred, timeout) {
      const deadline = Date.now() + timeout;
      while (Date.now() < deadline) {
        const hit = events.find((e) => e.type !== 'ping' && pred(e));
        if (hit) return hit;
        await sleep(150);
      }
      return null;
    },
  };
}

/** AI 回复的判定：reply 事件，落库身份是客服（data.uid=kefuUid），文本非用户原文 */
const isAiReply = (e, kefuUid) => e.type === 'reply' && Number(e.data?.uid) === kefuUid && Number(e.data?.to_uid) !== kefuUid;

async function login() {
  const userData = await postForm('/api/login', { account: USER_ACCOUNT, password: USER_PASSWORD });
  const kefuData = await postForm('/kefuapi/login', { account: KEFU_ACCOUNT, password: KEFU_PASSWORD });
  const userUid = Number(userData.uid || userData.userInfo?.uid || uidFromToken(userData.token));
  const kefuUid = Number(kefuData.kefuInfo?.uid || 0);
  if (!userUid || !kefuUid) throw new Error('登录失败：uid 解析不到');
  return { userToken: userData.token, kefuToken: kefuData.token, userUid, kefuUid };
}

/** 最近一条客服身份的 AI 落库行（uid=客服, to_uid=用户, msn_type=1） */
const lastKefuReplyRow = (userUid, kefuUid) =>
  mysql(`SELECT id,uid,to_uid,msn_type,type,left(msn,60) FROM eb_store_service_log WHERE uid=${kefuUid} AND to_uid=${userUid} AND msn_type=1 ORDER BY id DESC LIMIT 1`);

(async () => {
  console.log(`[${stamp()}] 场景：${SCENARIO}   站点：${SITE}   消息：「${AI_MSG}」`);
  const { userToken, kefuToken, userUid, kefuUid } = await login();
  console.log(`客户 uid=${userUid}  客服 uid=${kefuUid}`);

  // 每个场景独立建连接：客服连接与否本身是场景变量
  if (SCENARIO === 'off') {
    setConfig('ai_service_open', '0');
    const user = chatClient('客户');
    await user.ready;
    user.send({ type: 'login', data: userToken, form_type: 1 });
    user.send({ type: 'to_chat', data: { id: kefuUid } });
    await sleep(600);
    const before = lastKefuReplyRow(userUid, kefuUid);
    user.send({ type: 'chat', data: { to_uid: kefuUid, type: 1, msn: AI_MSG } });
    const echo = await user.waitFor((e) => e.type === 'chat', 5000);
    assert('原聊天通路正常（chat 回显）', !!echo);
    const ai = await user.waitFor((e) => isAiReply(e, kefuUid), WAIT_NO_AI_MS);
    assert('AI 关闭时不产生 AI 回复', !ai, ai ? `意外收到：${String(ai.data?.msn).slice(0, 40)}` : `${WAIT_NO_AI_MS}ms 无回复`);
    const after = lastKefuReplyRow(userUid, kefuUid);
    assert('客服侧无新增落库行', before === after);
    user.close();

  } else if (SCENARIO === 'normal') {
    setConfig('ai_service_open', '1');
    const user = chatClient('客户');
    await user.ready;
    user.send({ type: 'login', data: userToken, form_type: 1 });
    user.send({ type: 'to_chat', data: { id: kefuUid } });
    await sleep(600);
    const before = lastKefuReplyRow(userUid, kefuUid);
    user.send({ type: 'chat', data: { to_uid: kefuUid, type: 1, msn: AI_MSG } });
    const ai = await user.waitFor((e) => isAiReply(e, kefuUid), WAIT_AI_MS);
    assert('收到 AI 异步回复（reply 事件, 客服身份）', !!ai, ai ? `msn=${String(ai.data?.msn).slice(0, 50)}` : `${WAIT_AI_MS}ms 未收到`);
    if (ai) {
      assert('AI 回复非空且非原文', String(ai.data?.msn || '').trim().length > 0 && ai.data.msn !== AI_MSG);
      const after = lastKefuReplyRow(userUid, kefuUid);
      assert('AI 回复以客服身份落库', before !== after, after);
    }
    user.close();

  } else if (SCENARIO === 'human') {
    setConfig('ai_service_open', '1');
    const user = chatClient('客户');
    const kefu = chatClient('客服');
    await Promise.all([user.ready, kefu.ready]);
    user.send({ type: 'login', data: userToken, form_type: 1 });
    kefu.send({ type: 'kefu_login', data: kefuToken });
    await sleep(600);
    user.send({ type: 'to_chat', data: { id: kefuUid } });
    kefu.send({ type: 'to_chat', data: { id: userUid } });
    await sleep(600);
    const before = lastKefuReplyRow(userUid, kefuUid);
    user.send({ type: 'chat', data: { to_uid: kefuUid, type: 1, msn: AI_MSG } });
    const delivered = await kefu.waitFor((e) => e.type === 'reply' && Number(e.data?.uid) === userUid, 5000);
    assert('客服正常收到用户消息（人工优先）', !!delivered);
    const ai = await user.waitFor((e) => isAiReply(e, kefuUid), WAIT_NO_AI_MS);
    assert('客服在线时 AI 不接管', !ai, ai ? `意外收到：${String(ai.data?.msn).slice(0, 40)}` : `${WAIT_NO_AI_MS}ms 无 AI 回复`);
    const after = lastKefuReplyRow(userUid, kefuUid);
    assert('客服侧无新增 AI 落库行', before === after);
    user.close();
    kefu.close();

  } else if (SCENARIO === 'failure') {
    setConfig('ai_service_open', '1');
    setConfig('ai_service_notify_on_failure', '0');
    //自包含停机：切到死端口（传输层不可达，与停 uvicorn 等价），场景结束还原——
    //不依赖人工停宿主机进程，开源后可一键复放
    const originalUrl = mysql(`SELECT value FROM eb_system_config WHERE menu_name='ai_service_url'`).trim();
    setConfig('ai_service_url', '"http://host.docker.internal:9999"');
    const user = chatClient('客户');
    await user.ready;
    user.send({ type: 'login', data: userToken, form_type: 1 });
    user.send({ type: 'to_chat', data: { id: kefuUid } });
    await sleep(600);
    const before = lastKefuReplyRow(userUid, kefuUid);
    user.send({ type: 'chat', data: { to_uid: kefuUid, type: 1, msn: AI_MSG } });
    const echo = await user.waitFor((e) => e.type === 'chat', 5000);
    assert('AI 故障时原聊天通路不受影响（chat 回显）', !!echo);
    const ai = await user.waitFor((e) => isAiReply(e, kefuUid), WAIT_NO_AI_MS);
    assert('静默降级：无 AI 回复、用户无感知', !ai, ai ? `意外收到：${String(ai.data?.msn).slice(0, 40)}` : `${WAIT_NO_AI_MS}ms 无回复`);
    const after = lastKefuReplyRow(userUid, kefuUid);
    assert('客服侧无新增落库行（零污染）', before === after);
    // 故障后聊天进程仍活着：再发一条消息仍有回显
    user.send({ type: 'chat', data: { to_uid: kefuUid, type: 1, msn: '故障注入后的第二条消息' } });
    const echo2 = await user.waitFor((e) => e.type === 'chat' && e.data?.msn === '故障注入后的第二条消息', 5000);
    assert('故障后 chat 进程存活（可继续聊天）', !!echo2);
    user.close();
    setConfig('ai_service_url', originalUrl); // 还原真实 ai-service 地址

  } else if (SCENARIO === 'notify') {
    setConfig('ai_service_open', '1');
    setConfig('ai_service_notify_on_failure', '1');
    //同 failure：自包含切死端口制造故障
    const originalUrl = mysql(`SELECT value FROM eb_system_config WHERE menu_name='ai_service_url'`).trim();
    setConfig('ai_service_url', '"http://host.docker.internal:9999"');
    const user = chatClient('客户');
    await user.ready;
    user.send({ type: 'login', data: userToken, form_type: 1 });
    user.send({ type: 'to_chat', data: { id: kefuUid } });
    await sleep(600);
    user.send({ type: 'chat', data: { to_uid: kefuUid, type: 1, msn: AI_MSG } });
    const ai = await user.waitFor((e) => isAiReply(e, kefuUid), WAIT_AI_MS);
    assert('收到配置的降级话术（notify 模式）', !!ai, ai ? `msn=${String(ai.data?.msn).slice(0, 50)}` : `${WAIT_AI_MS}ms 未收到`);
    setConfig('ai_service_notify_on_failure', '0'); // 还原默认
    setConfig('ai_service_url', originalUrl); // 还原真实 ai-service 地址
    user.close();

  } else {
    console.error(`未知场景：${SCENARIO}（可用：off / normal / human / failure / notify）`);
    process.exit(2);
  }

  console.log(process.exitCode ? `\n[${stamp()}] 结果：存在失败项` : `\n[${stamp()}] 结果：全部通过`);
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error('脚本异常：', e.message);
  process.exit(1);
});
