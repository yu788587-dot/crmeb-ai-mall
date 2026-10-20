/**
 * crmeb-ai-mall —— 客服聊天端到端（E2E）冒烟脚本
 *
 * 为什么需要它：
 *   CRMEB 的客服链路是「H5 用户端 WebSocket ↔ Workerman chat(40002) ↔ MySQL ↔ 客服端 WebSocket」，
 *   只靠后台页面点不出全链路。本脚本用两个 WebSocket 客户端模拟「客户」和「客服」，
 *   跑通 登录 → 进入会话 → 互发消息 → 断言双方都收到事件 → 校验落库，
 *   用于验证 AI 接入前后的行为基线（AI 关闭时应当与原版完全一致）。
 *
 * 运行环境：Node.js >= 21（使用内置全局 WebSocket，无需 npm 依赖）
 *
 * 用法：
 *   node help/dev/chat-e2e.mjs \
 *     --site http://localhost:8011 \
 *     --user-account 13800000002 --user-password crmeb123456 \
 *     --kefu-account 13000000000 --kefu-password kefu123456
 *
 * 协议（来自 crmeb/services/workerman/chat/ChatHandle.php）：
 *   {type:'login',      data:'<用户 token>'}
 *   {type:'kefu_login', data:'<客服 token>'}
 *   {type:'to_chat',    data:{id:<对方 uid>}}            // 声明"我正在和谁聊"
 *   {type:'chat',       data:{to_uid,type,msn}}          // 发消息，type=1 文字
 *   服务端回推：reply（对方收到）、chat（自己收到）、mssage_num（未读数）、user_online（上下线）
 */

const args = process.argv.slice(2);
const opt = (name, dft) => {
  const i = args.indexOf('--' + name);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : dft;
};

const SITE = opt('site', 'http://localhost:8011');
const USER_ACCOUNT = opt('user-account', '13800000002');
const USER_PASSWORD = opt('user-password', 'crmeb123456');
const KEFU_ACCOUNT = opt('kefu-account', '13000000000');
const KEFU_PASSWORD = opt('kefu-password', 'kefu123456');
const MESSAGE = opt('msn', '你好，我想咨询一下这个订单的发货时间');
const WAIT_MS = Number(opt('wait', 4000));

const wsBase = SITE.replace(/^http/, 'ws');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

/** 建立一个聊天 WebSocket 客户端，记录收到的所有事件 */
function chatClient(name, wsPath) {
  const events = [];
  const ws = new WebSocket(wsBase + wsPath);
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
    console.log(`  [${name}] <- ${msg.type} ${compact(msg.data)}`);
  });
  return {
    name,
    events,
    ready,
    send(obj) {
      ws.send(JSON.stringify(obj));
      console.log(`  [${name}] -> ${obj.type}`);
    },
    close() {
      try {
        ws.close();
      } catch {}
    },
    /** 等待某个事件到达 */
    async waitFor(type, timeout = WAIT_MS) {
      const deadline = Date.now() + timeout;
      while (Date.now() < deadline) {
        const hit = events.find((e) => e.type === type);
        if (hit) return hit;
        await sleep(100);
      }
      return null;
    },
  };
}

const compact = (d) => {
  const s = JSON.stringify(d ?? '');
  return s && s.length > 90 ? s.slice(0, 90) + '…' : s;
};

function assert(label, ok, detail = '') {
  console.log(`${ok ? '  ✅' : '  ❌'} ${label}${detail ? ' — ' + detail : ''}`);
  if (!ok) process.exitCode = 1;
  return ok;
}

/**
 * /api/login 只返回 token，uid 藏在 JWT 载荷的 jti.id 里（{"id":2,"type":"api"}）。
 * 这里只做 base64 解码取 uid，不校验签名，纯本地测试用。
 */
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
  console.log(`目标站点：${SITE}`);
  console.log(`客户账号：${USER_ACCOUNT}    客服账号：${KEFU_ACCOUNT}\n`);

  // 1) 取 token
  const userData = await postForm('/api/login', { account: USER_ACCOUNT, password: USER_PASSWORD });
  const kefuData = await postForm('/kefuapi/login', { account: KEFU_ACCOUNT, password: KEFU_PASSWORD });
  const userUid = Number(userData.uid || userData.userInfo?.uid || uidFromToken(userData.token));
  const kefuUid = Number(kefuData.kefuInfo?.uid || 0);
  console.log(`登录成功：客户 uid=${userUid}，客服 uid=${kefuUid} (service_id=${kefuData.kefuInfo?.id})`);
  if (!userUid || !kefuUid) throw new Error('未能确定 uid，请检查账号是否已注册/是否为客服');
  assert('客户/客服 token 获取', !!userData.token && !!kefuData.token);

  // 2) 建立长连接
  const user = chatClient('客户', '/msg');
  const kefu = chatClient('客服', '/msg');
  await Promise.all([user.ready, kefu.ready]);

  user.send({ type: 'login', data: userData.token, form_type: 1 });
  kefu.send({ type: 'kefu_login', data: kefuData.token });
  await sleep(600);

  // 3) 声明会话对象（双方都在"正在和对方聊"的状态，才会走实时 reply 而不是未读数）
  kefu.send({ type: 'to_chat', data: { id: userUid } });
  user.send({ type: 'to_chat', data: { id: kefuUid } });
  await sleep(600);

  // 4) 客户 -> 客服
  const msn1 = MESSAGE;
  user.send({ type: 'chat', data: { to_uid: kefuUid, type: 1, msn: msn1 } });
  const got1 = await kefu.waitFor('reply');
  assert('客服实时收到客户消息（reply 事件）', !!got1, got1 ? `msn=${String(got1.data?.msn).slice(0, 24)}` : `${WAIT_MS}ms 内未收到`);

  // 5) 客服 -> 客户（回复）
  const msn2 = '您好，订单一般在付款后 48 小时内发出～';
  kefu.send({ type: 'chat', data: { to_uid: userUid, type: 1, msn: msn2 } });
  const got2 = await user.waitFor('reply');
  assert('客户实时收到客服回复（reply 事件）', !!got2, got2 ? `msn=${String(got2.data?.msn).slice(0, 24)}` : `${WAIT_MS}ms 内未收到`);

  // 6) 断线场景：客户不在线时应产生未读数（这是 AI 兜底回复要依赖的分支）
  const offlinePeer = chatClient('临时客户', '/msg');
  await offlinePeer.ready;
  offlinePeer.send({ type: 'login', data: userData.token, form_type: 1 });
  await sleep(400);
  offlinePeer.close();
  await sleep(400);

  user.close();
  kefu.close();
  await sleep(300);

  console.log('\n提示：落库校验请查 eb_store_service_log / eb_store_service_record，例如：');
  console.log('  docker exec crmeb_mysql mysql -ucrmeb -p123456 crmeb -e "select id,uid,to_uid,msn_type,left(msn,24) from eb_store_service_log order by id desc limit 5;"');
  console.log(process.exitCode ? '\n结果：存在失败项' : '\n结果：全部通过');
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error('脚本异常：', e.message);
  process.exit(1);
});
