# AI 增强二开方案与进度（CRMEB v6.0.0 → crmeb-ai-mall）

> **状态**：✅ 六个窗口全部完成（2026-10-26），v1.0.0 已发布
> **定位**：主打 **AI 系统工程能力**——在成熟电商系统上落地 AI 能力的异步架构、降级容错与可观测性实践
> 最后更新：2026-10-26（窗口 6 完成，项目收官）

## 一、项目定位

基于 CRMEB-KY v6.0.0（Apache-2.0，西安众邦网络科技）二次开发，开源至 GitHub `yu788587-dot/crmeb-ai-mall` 作为求职作品集项目。

展品不是"加了两个 AI 功能"，而是**这条 AI 链路本身经不起推敲的地方都被做实了**：跨进程异步、超时分层、幂等、熔断降级、背压、trace 贯穿、指标量化、故障演练。

**合规要求（发布前逐项核对）**：保留全部 CRMEB 版权头 / LICENSE / LICENSE.txt；README 首部声明「基于 CRMEB-KY v6.0.0（Apache-2.0）二次开发」；不删品牌标识与官方链接、不宣称原创、不售卖；修改文件以 git 历史 + 文档体现（Apache-2.0 §4b）。

## 二、功能设计

### ① AI 智能客服（RAG）— 主线展品
- 触发：人工客服在线 → 人工优先；离线 + 关键词未命中 → AI 接管（后台开关可控）
- 知识库：在售商品（名称/价格/库存/卖点/详情）+ 客服话术 + 关键词回复条目
- 上下文：最近 N 条聊天；可选携带用户最近订单摘要
- 答不好 → 引导转人工；AI 故障 → 静默降级，原聊天功能零影响
- 回复以客服身份落库并 ws 推送，用户端聊天页零改动；v1 单条消息不流式

### ② 后台 AI 内容生成 — 第二用例（轻量）
证明 AI 服务是通用能力平台而非客服专用：复用同一 client、同一观测链路。商品编辑页「AI 生成」标题/卖点/详情/SEO，预览可编辑后应用。

## 三、可靠性工程标准（窗口 3 验收线）

1. **trace_id 贯穿**：PHP 生成 → 随任务传递 → ai-service 以 `X-Trace-Id` 接收 → 双端日志同 id，一次问答可完整串联
2. **超时分层**：连接 1s 快速失败 / HTTP 15s / 整体 SLA 20s（超时放弃 AI，保持原逻辑）
3. **重试与幂等**：仅连接失败与 5xx 重试一次，超时不重试；按 `user_msg_id` 幂等，一条消息只回一次
4. **背压与限流**：Worker 并发固定 + 队列上限，超限直接降级；同一会话串行（防回复乱序）
5. **熔断降级**：连续失败 K 次开熔断 X 秒，期间走原逻辑，半开试探自动恢复
6. **健康检查**：`/api/health` 含向量库与 LLM 连通性探针；Worker 崩溃由 Workerman 自动拉起
7. **优雅关闭**：进程重启不丢在处理任务、不产生半条回复

## 四、可观测性（双轨，窗口 4）

指标维度：调用量、成功率、P50/P95/P99 延迟、耗时分解（检索 vs LLM）、token 消耗、RAG 命中率、降级次数、熔断触发次数、队列积压。

- **轨道一 · 业务内可观测**：双端结构化 JSON 日志（level/ts/trace_id/uid/duration/error）+ 自建 `ai_call_log` 埋点表 + CRMEB 后台「AI 运行监控」页（图表 + 明细 + trace 查询）
- **轨道二 · 标准栈**：ai-service `/metrics`（prometheus_client）；PHP 侧事件（降级/熔断/积压）由小 exporter 读 `ai_call_log` 暴露 `/metrics`；compose 内加 Prometheus + Grafana，dashboard 截图进 README

## 五、技术架构

```
用户(H5/小程序/APP) ──ws:40002──▶ CRMEB 聊天进程(Workerman)
                                     │ 关键词未命中 & 无人工在线 & AI开关开
                                     ▼ ChannelService:40003（跨进程投递任务）
                              AI Worker 独立进程 ──HTTP(X-Trace-Id)──▶ FastAPI AI 服务(:8000)
                                │  超时/重试/幂等/熔断                 ├─ RAG：Chroma + bge-m3
                                ▼                                    └─ LLM：SiliconFlow DeepSeek-V3
                    写库(store_service_log/record)+ai_call_log
                                │
                                ▼ ChannelService 回推 → chat 进程 ws 推送 reply

后台 admin ──adminapi──▶ /api/copywriting（第二用例）｜ /api/kb/sync（只读MySQL同步）
Grafana ◀── Prometheus ◀── ai-service:/metrics ＋ PHP指标exporter ◀── ai_call_log
```

选型：Python 3.12 + FastAPI + openai SDK；Chroma 本地持久化（可升级 Milvus）；SiliconFlow DeepSeek-V3 + bge-m3（真实 key 仅本地 .env，gitignored）；Prometheus + Grafana。

## 六、执行窗口（6 个，每窗口：独立边界 + 验收 + commit）

| 窗口 | 内容 | 验收 |
|---|---|---|
| 1 环境与基线 | git init 两段提交 → 启动 Docker → compose 全栈（nginx:8011/mysql:33061/redis:63791）→ 安装 CRMEB → admin/H5 登录 → 基线聊天可用 → **预验证 admin 前端构建链（便携 Node 20）** → 集成机制细节确认 → AI 关闭时原版行为基线 | ✅ 完成，见第十节（含 9p 性能悬崖修复 60s→10ms、E2E 聊天与自动回复基线、admin 构建链与产物 96.6% 字节一致，见 10.7） |
| 2 AI 微服务 | FastAPI + RAG + 知识库同步 + **trace_id 契约 / 结构化日志 / /metrics / health** | ✅ 代码已提交 f665fcb7，pytest 28 passed，fake 冒烟通过；真实 API 冒烟移至窗口 3（需用户填 key） |
| 3 集成与可靠性 | ChatHandle 挂接 + AI Worker + 七项可靠性标准 + 后台开关 | ✅ 完成，见第十二节（E2E 五场景全绿 + 故障注入矩阵 + trace_id 双端实证） |
| 4 可观测与量化 | ai_call_log + 后台监控页 + Prometheus/Grafana + exporter + locust 压测 + 故障演练 | ✅ 完成，见第十三节（量化数据 + 5 张截图入库 `docs/screenshots/`） |
| 5 后台 AI 文案 | adminapi 接口 + admin 前端按钮 + 构建（轻量，工期紧可砍） | ✅ 完成，见第十四节（真实环境生成并应用，trace + 落库双证据） |
| 6 文档与开源 | README 重写（架构/时序/性能表/演练GIF）+ docs + compose 一键部署（含 ai-service/Prometheus/Grafana）+ push + Release v1.0.0 | ✅ 完成，见第十五节（README + 截图嵌入 + 容器化 + 密钥核查 + 发布） |

## 七、关键技术坐标（已核实）

- AI 挂接点：`crmeb/crmeb/services/workerman/chat/ChatHandle.php` → `chat()`（313–339 行现有关键词自动回复逻辑）；落库经 `StoreServiceLogServices`（store_service_log / store_service_record），ws 端口 40002 推 `reply`
- 跨进程：`crmeb/crmeb/services/workerman/ChannelService.php`（send / trigger；channel 40003），ChatService 已在用
- 端口配置：`crmeb/config/workerman.php`（admin 40001 / chat 40002 / channel 40003）
- 用户端聊天页已在预构建产物 `crmeb/public/static`（customer_list/chat），演示入口 public/index.html，mobile.html 为 375px 壳；admin 产物在 `crmeb/public/admin`
- admin 前端构建要求 **Node >14 <23**（本机 Node 24 不兼容）→ 便携 Node 20 装 `D:\AIcode\Tool`；因需新增监控页与文案按钮，前端构建为**必做项**
- 环境：本机无 PHP/MySQL；Docker CLI 29.7.2 已装；ccr.ccs.tencentyun.com 实测可达
- 仓库体积约 306MB（vendor 123MB）；.env / .constant 被上游 gitignore（安装时生成）

## 八、风险与对策

| 风险 | 对策 |
|---|---|
| Docker Desktop / WSL2 未就绪 | 窗口 1 首要动作，需配合 GUI 时用系统弹窗辅助启动 |
| Prometheus/Grafana 镜像在 Docker Hub 拉取慢 | 预配 registry mirror 或国内可用源 |
| admin 构建链走不通 | 窗口 1 提前预验；兜底先用 dev server 验证，构建问题单独修 |
| H5 ws 地址若预构建写死 | 兜底：自建 uni-app H5 构建链 |
| 异步机制调研受阻 | 兜底：同步短超时调用（保留 TODO，链路可靠性打折需如实说明） |
| LLM key 失效 | 密钥属凭据，不能从工作区外读取、也不入库；由使用者把 key 放进 `ai-service/.env`（已 gitignore），窗口 2 首步做 `/api/health` + 一次真实 embeddings/chat 冒烟验活 |

## 九、量化产出（进 README 与简历）

压测吞吐与 P50/P95/P99、降级与熔断触发次数、RAG 命中率、故障演练录屏/截图、代码规模（新增 x 行 / 修改 y 行 / 涉及 N 个文件）。

## 十、窗口 1 执行记录（已完成）

### 10.1 环境与安装

Docker Desktop（WSL2 后端）跑通上游 compose：nginx 8011、mysql 33061、redis 63791、phpfpm 9000/40001/40002。安装向导 5 步走完，勾选「保留演示数据」——`public/install/index.php:330` 在 `!$_POST['demo']` 时会清空测试数据，演示与 RAG 知识库都依赖这几件商品，必须勾选。安装后 `public/install.lock` 生成，DB 内 `eb_store_product=4`、`eb_user=2`、`eb_system_config=248`、`eb_system_menus=1096`。

### 10.2 Windows + Docker 性能悬崖（本窗口最大的工程问题，已量化解决）

现象：装完之后所有框架接口 60s 超时 504，静态文件正常。

定位数据：容器内遍历 `/var/www/vendor/topthink` 461 个条目耗时 **99.9s**（≈216ms/次元数据操作）；MySQL 查询 2ms、Redis 连接 0ms。`/proc/<pid>/wchan` 显示 `p9_client_rpc` —— 上游 compose 把 Windows 目录直接绑定挂载进容器，Docker Desktop WSL2 下走 9p，每次 `stat/realpath` 都是跨系统 RPC。叠加镜像默认 `open_basedir`，PHP 对每个 include 文件都要做一次 realpath 校验，ThinkPHP 单请求要碰上千个文件，直接放大成 60s。

修复（三处，已进仓库）：

1. `help/docker/docker-compose.yml`：`/var/www` 从 Windows 绑定挂载改为命名卷 `crmeb_app`（位于 WSL2 ext4）。
2. `help/docker/php/conf.d/zz-dev.ini`：清空 `open_basedir`，开启 OPcache（`validate_timestamps=0`）与 realpath 缓存。
3. `help/docker/sync.sh`：本机 NTFS 顺序读 + `tar` 流式经 `docker exec` stdin 写入 ext4，绕开 9p 随机元数据访问；**12592 个文件 / 247MB 全量同步 32.6s**，脚本随后修正属主、同步 `.env` 并重启容器。

修复后实测：H5 首页 **4–11ms**，`adminapi/login/info` **15–47ms**（此前 >60s 504）。

副作用与约定：`validate_timestamps=0` 后改 PHP 代码必须走 `sync.sh`（内含 `docker restart`）才生效，已在脚本注释写明。

### 10.3 Workerman 与聊天链路基线（真实 E2E，非 mock）

`/etc/supervisor/supervisord.conf` 显示容器内由 supervisord 拉起 php-fpm、`think queue:listen`、`think timer start`、`think workerman start --d`。Workerman 必须在安装完成后启动（安装前 `SystemConfigServices::getSslFilePath()` 读库会卡死在 9p 上），`restart: always` + 容器重启即可自愈。现 40001/40002/40003 全部 LISTEN，nginx 已配 `/notice→40001`、`/msg→40002`，`GET /adminapi/get_workerman_url` 返回 `ws://localhost:8011/notice|/msg`。

新增 `help/dev/chat-e2e.mjs`（Node ≥21 内置 WebSocket，零 npm 依赖）模拟客户 + 客服两条长连接，实测结果全部通过：

| 检查项 | 结果 |
|---|---|
| 客户 → 客服 实时 `reply` | ✅ msn 原文到达 |
| 客服 → 客户 实时 `reply` | ✅ |
| `user_online` 广播、`mssage_num` 未读数 | ✅ |
| 落库 `eb_store_service_log` | ✅ 3 行 |
| 关键词自动回复（基线） | ✅ 客户发「发货」→ 收到 `reply`，`uid=客服`，msn 为配置文案 |

协议坑（已写进脚本注释）：`to_chat` 的参数名是 `data.id`（不是 `to_uid`），`chat` 才是 `data.to_uid`；`/api/login` 只返回 token，uid 要从 JWT 载荷 `jti.id` 解出。

**结论：AI 回复可以直接沿用「关键词自动回复」这条已验证的通路（落库 + ws 事件 `reply`），用户端前端零改动。**

### 10.4 集成相关机制确认

- 后台「开发配置」是低代码：`Route::resource('config_class','v1.setting.SystemConfigTab')` + `Route::resource('config','v1.setting.SystemConfig')`，表单由 FormBuilder 依 `system_config` 行渲染 → **AI 设置分组（开关、超时、并发、降级文案等）纯数据落地，不需要写 Vue 页面**。读取用 `sys_config()`（`app/common.php:130`）。
- 客服自动回复管理接口：`POST /adminapi/app/kefu/auto_reply/save/:id`（字段 `keys/type/data/status`），底层即 `eb_wechat_key` + `eb_wechat_reply`，与 `ChatHandle::chat()` 读取的表一致。注意路由挂在 `app` 分组下，完整路径带 `/app/` 前缀。
- adminapi 鉴权头名是 **`Authori-zation`**（`config('cookie.token_name')`），不是 `Token`；也接受 `?token=`。
- `.env` 坑：上游模板 `public/install/.env` 是 **CR 换行**，`think\Env::load()` 用 `parse_ini_file` 解析不了；已把模板改为 LF 并新增 `.gitattributes` 锁住换行符（见 10.8），这是我们对上游的一个小修复，README 会单独列出。
- 安全观察（暂不改，考虑提 issue 给上游）：`adminapi` 登录在 `captchaVerification` 为空时直接跳过滑块校验（`app/adminapi/controller/Login.php:101`）。

### 10.5 本地演示账号（README 会公开，仅本地演示数据）

后台 `admin / crmeb.com`；客户 `13800000002 / crmeb123456`（uid=2）；客服 `13000000000 / kefu123456`（`eb_store_service` id=1，uid=1）。注意 `app/services/user/LoginServices.php:58` 显式拒绝初始密码 `md5('123456')`（提示「请修改您的初始密码」），所以演示用户必须改密码后才能登录。

### 10.6 待办移交窗口 3

滑块验证码在本机 Qoder 内置浏览器里因画布尺寸为 0 无法拖动，后台 UI 登录改用 API 取证；窗口 3 若要截图，需要在本机 Chrome 里人工过一次滑块，或用 API 会话取数据。

### 10.7 admin 前端构建链预验证（窗口 5 的前置风险已消除）

上游 `template/admin` 是 Vue2 + `@vue/cli-service 4.x`，`package.json` 要求 `node >14 <23`（本机系统 Node 24 不兼容），改用便携 Node **v20.19.0**（`D:\AIcode\Tool\node-v20.19.0-win-x64`）。走通的命令序列，窗口 5 直接复用：

```bash
export PATH="/d/AIcode/Tool/node-v20.19.0-win-x64:$PATH"
cd template/admin
npm install --legacy-peer-deps --registry=https://registry.npmmirror.com   # 658MB node_modules
NODE_OPTIONS=--openssl-legacy-provider npx vue-cli-service build --mode=production
```

`--openssl-legacy-provider` 是必须的：webpack4 用 md4 做哈希，Node 17+ 的 OpenSSL 3 禁用了它，否则报 `error:0308010C:digital envelope routines::unsupported`。

构建结果与验收（不只看「编译成功」，而是与上游随包产物 `crmeb/public/admin` 做逐文件内容比对）：

| 指标 | 数值 |
|---|---|
| 构建 | exit 0，无 error |
| 产物 | 598 个文件 / 41MB（上游 596 个 / 41MB） |
| **内容逐字节相同** | **576 / 596 = 96.6%** |
| 仅样式 chunk 文件名（chunk-id）不同、内容哈希相同 | `app.86b55c86.css`、`chunk-vendors.0a536a1c.css` 等，说明源码一致 |
| 内容不同的文件 | 6 个 monaco worker（`ts/html/css/json/editor`，依赖在 semver 范围内漂到新版）+ `app.js`/`chunk-vendors.js` + 9 个懒加载 chunk + `index.html` |

部署校验：把 `dist/` 灌进容器 `/var/www/public/admin`（`docker cp` 3.2s），经真实 nginx 访问 `/admin/` → 200，入口引用的 4 个资源 `app.css`/`chunk-vendors.css`/`app.js`/`chunk-vendors.js` 全部 200 且体积正确（1.4MB / 4.2MB / 259KB / 782KB），随后已还原成上游产物，保持基线干净（宿主目录未动，容器内文件数回到 596）。

**结论：新增「AI 运行监控」页与文案生成按钮需要改 admin 前端，这条链路可行**；构建产物比对说明本机源码与上游发布物同源，窗口 5 的改动不会被工具链差异淹没。

### 10.8 本窗口新增进仓库的文件

| 文件 | 作用 |
|---|---|
| `help/docker/docker-compose.yml`（改） | `/var/www` 换 ext4 命名卷 `crmeb_app` |
| `help/docker/php/conf.d/zz-dev.ini`（新） | 清 `open_basedir` + OPcache/realpath 调优 |
| `help/docker/sync.sh`（新） | 宿主 → 容器代码同步（tar 流式，绕开 9p） |
| `help/dev/chat-e2e.mjs`（新） | 双端 WebSocket 聊天 E2E 脚本，窗口 3/4 复用 |
| `.gitattributes`（新） | 强制 `*.sh/*.mjs/*.ini/*.yml` 与安装模板为 LF，防止 Windows 检出又变回 CRLF |
| `crmeb/public/install/.env`（改） | CR 换行 → LF，`parse_ini_file` 才能解析；容器内实测替换后 7 个 section 正常读出 |

## 十一、窗口 2 执行记录（代码已提交 f665fcb7）

### 11.1 ai-service 目录结构与职责

```
ai-service/
├── .env.example          # 配置模板（复制为 .env 后填 key）
├── requirements.txt      # fastapi/httpx/chromadb/prometheus-client/pymysql/numpy/pytest
├── pytest.ini            # asyncio_mode=auto
├── app/
│   ├── config.py         # pydantic-settings，含 fake_fail_mode/fake_latency_ms 故障注入
│   ├── trace.py          # X-Trace-Id contextvar + sanitize（防日志注入）
│   ├── logging_setup.py  # JSON 结构化日志（ts/level/logger/trace_id/event + extra 字段）
│   ├── metrics.py        # Prometheus 指标（HTTP/LLM/RAG/Breaker/Backpressure/Idempotency/KB）
│   ├── resilience.py     # CircuitBreaker / RetryPolicy / Backpressure / ConversationSerializer / AIServiceError
│   ├── llm.py            # ResilientLLM 包装器 + make_llm() 工厂
│   ├── schemas.py        # ChatRequest/CopywritingRequest/Envelope/HealthResponse
│   ├── routers.py        # /api/chat /copywriting /kb/sync /kb/stats /health
│   ├── main.py           # FastAPI app + lifespan + trace 中间件 + 异常处理 + /metrics
│   ├── _chroma_noop.py   # chromadb 遥测空实现（解决 posthog 7.x 签名不兼容 bug）
│   ├── providers/
│   │   ├── base.py       # LLMProvider Protocol + ChatMessage/ChatResult
│   │   ├── fake.py       # 特征哈希 embedding + 故障注入（timeout/connect/5xx/4xx）
│   │   └── siliconflow.py # httpx AsyncClient + 分层 Timeout + RetryPolicy
│   ├── rag/
│   │   ├── documents.py  # KBDocument 模型 + product/speechcraft/keyword_reply 构造器
│   │   └── store.py      # Chroma PersistentClient（cosine 空间，asyncio.to_thread）
│   ├── kb/
│   │   ├── mysql_source.py # 只读 pymysql + SQL 白名单校验
│   │   └── service.py    # KBSyncService：MySQL → embed → Chroma 批量同步
│   └── services/
│       ├── chat.py       # 完整流水线：幂等→串行→背压→deadline→RAG→LLM→置信度→降级
│       └── copywriting.py # 后台文案生成（走同一套 breaker/backpressure/trace）
└── tests/                # 28 passed
    ├── conftest.py       # fake_settings + fake_llm + tmp chroma fixtures
    ├── test_health.py    # /api/health + trace 回传 + /metrics
    ├── test_chat.py      # KB 命中 / 无 KB / 幂等 / LLM 故障降级
    ├── test_breaker.py   # closed→open→half_open→closed 状态机
    ├── test_retry.py     # 只对 connect+5xx 重试，超时/4xx 绝不重试
    ├── test_backpressure.py # 队列满拒绝 + 容量内放行 + 同会话串行
    └── test_rag.py       # 特征哈希确定性 + store upsert/search
```

### 11.2 可靠性原语实现要点

| 原语 | 关键设计 | 对应七项标准 |
|---|---|---|
| CircuitBreaker | asyncio.Lock 线程安全；half_open_in_flight 限制并发探测数；record_failure 区分「上游故障」和「业务降级」（后者不触发熔断） | 熔断 |
| RetryPolicy | 只对 ConnectError/RemoteProtocolError 和 5xx 重试一次；ReadTimeout 和 4xx 直接上报 | 重试 |
| Backpressure | Semaphore(max_inflight) + 等待计数(max_queue)；超出直接 OverloadedError(503)，不让请求堆积超时 | 背压 |
| ConversationSerializer | OrderedDict + LRU 淘汰；同 uid 消息串行，避免并发读未落库上下文 | 同会话串行 |
| Idempotency | dict[user_msg_id, (timestamp, ChatData)] + TTL + max_entries LRU | 幂等 |
| TraceMiddleware | X-Trace-Id 头读取/生成 → contextvar → JSON 日志 + 响应头回传；sanitize 正则过滤非法字符 | trace_id 贯穿 |

### 11.3 FakeProvider 的离线 RAG 为什么有意义

fake provider 的 embedding 不是随机向量，而是**特征哈希**（feature hashing）：把文本按词/字切分后 hash 到固定维度，相同词面映射到相同维度并累加符号值，最后 L2 归一化。这意味着：

- 「测试手机多少钱」和「测试手机 1999 元」的余弦相似度 > 「蓝牙耳机 59 元」
- 离线状态下 RAG 检索仍然返回语义相关的 top-k，而不是噪声
- 窗口 4 故障演练可以直接用 fake provider 模拟各种失败模式，不需要真实 API

### 11.4 chromadb 遥测坑（已解决）

chromadb 0.5.23 + posthog 7.x 有签名不兼容 bug：即使设 `anonymized_telemetry=False`，仍会触发 `capture() takes 1 positional argument but 3 were given` 警告。解决方案：自定义 `NoopProductTelemetry`（继承 ProductTelemetryClient + @override capture），通过 `chroma_product_telemetry_impl` 设置注入。详见 `app/_chroma_noop.py` 和 `app/rag/store.py` 注释。

### 11.5 测试结果

```
28 passed, 1 warning in 4.65s
```

覆盖：health/trace/metrics/breaker/retry/backpressure/conversation-serializer/RAG-documents/RAG-store/chat-with-kb/chat-without-kb/chat-idempotent/chat-degraded/copywriting（间接通过 health）。

### 11.6 冒烟验证（fake 模式）

启动命令：`cd ai-service && .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`

| 端点 | 结果 |
|---|---|
| GET /api/health | 200，status=degraded（kb 为空），breaker=closed，config 完整 |
| GET /metrics | 200，Prometheus text format，ai_http_requests_total/ai_llm_requests_total 等指标齐全 |
| POST /api/chat | fake 模式下正常返回 reply（注：curl 需正确 JSON body，TestClient 已验证） |

### 11.7 窗口 3 待办（下次会话起点）

1. **PHP ChatHandle 挂接**：在 `chat()` 关键词自动回复之后插入 AI 调用（httpx → ai-service `/api/chat`）
2. **AI Worker**：think-queue job 异步调用，结果经 ChannelService::send 推回 ws 40002
3. **后台开关**：利用 `system_config_tab` + `system_config` 低代码新增 AI 配置分组（开关/超时/降级话术）
4. **真实 API 冒烟**：**需用户把 SiliconFlow key 填入 `ai-service/.env`**（`SILICONFLOW_API_KEY=sk-xxx`，`LLM_PROVIDER=siliconflow`），然后 `POST /api/kb/sync` 灌入知识库，再跑 chat-e2e.mjs 验证三场景
5. **E2E 三场景**：AI 正常回复 / 人工在线时 AI 不接管 / LLM 故障时降级兜底

### 11.8 本窗口新增进仓库的文件

共 35 个文件（不含 .env 和 __pycache__），详见 11.1 目录树。核心依赖：fastapi 0.115.6 / httpx 0.28.1 / chromadb 0.5.23 / prometheus-client 0.23.1 / PyMySQL 1.1.1 / numpy 2.2.1 / pytest 8.3.4。

## 十二、窗口 3 执行记录（集成与可靠性，2026-10-25 完成）

### 12.1 集成链路（全部真实进程，非 mock）

```
用户 ws(:40002 chat进程) ──chat() 关键词未命中 && 客服离线 && 开关开
  └─ AiChatService::tryDispatch（chat 进程内，微秒级）
       ├─ 幂等锁 SETNX ai:reply_lock:{msg_id}（原生 Redis 直连，TTL 1 天）
       ├─ 背压检查 LLEN 队列 > ai_service_queue_limit → 放弃
       ├─ trace_id 生成 ai-{16hex} → 作为任务参数入队
       └─ think-queue 推入 redis 队列 CRMEB6ad6fadaa57bf
            └─ queue:listen 进程消费 AiChatJob::doJob
                 ├─ SLA 剩余 = ai_service_timeout - (now - 发消息时刻)，<2s 直接放弃
                 ├─ 取最近 N 条双向文本聊天 → history[{role,text}]
                 ├─ AiClient POST /api/chat（X-Trace-Id 头；连接 1s / 单次 15s / 预算受 SLA 约束）
                 │    └─ ai-service(:8000)：幂等→串行→背压→RAG→LLM→置信度→降级
                 ├─ 落库 store_service_log（客服身份, type=1）+ saveRecord —— 与关键词自动回复同通路
                 └─ ChannelService(trigger=crmeb_chat) → chat 进程 ws 推 reply 给用户
```

挂接点：`ChatHandle::chat()` 关键词自动回复块的 `elseif (!isset(kefuUser()[$to_uid]))` 分支（`ChatHandle.php:313-343`）。**人工优先**：目标客服以客服身份在线（kefuUser 连接表）时绝不接管；AI 任何异常在 tryDispatch/doJob 两层 try/catch 内吞掉，原聊天功能零影响。

### 12.2 七项可靠性标准的落地与验证

| 标准 | PHP 侧实现 | 验证证据 |
|---|---|---|
| 1 trace_id 贯穿 | chat 进程生成 → 队列参数 → X-Trace-Id 头 → 双端日志 | 12.5 成对日志样例 |
| 2 超时分层 | curl CONNECTTIMEOUT=1 / 单次 15s / 整体 SLA 按发消息时刻计算 | 注入挂起 16s → curl(28) 于 15001ms 精确放弃 |
| 3 重试与幂等 | 仅 errno 6/7 与 5xx 重试一次；28/4xx 不重试；SETNX + user_msg_id 双层幂等 | 500→attempts=2；连接拒绝→attempts=2；超时→attempts=1 |
| 4 背压 | 入队前 LLEN > queue_limit(50) 直接放弃 | 代码路径 + 配置项（窗口 4 压测量化） |
| 5 熔断降级 | ai-service 熔断开启时秒级返回 degraded(circuit_open)，PHP 静默 | 5 次连续失败→open，PHP 收到 circuit_open 仅 16.4ms |
| 6 健康检查 | `/api/health`（含 kb/breaker/backpressure 探针）已有；ai_service_url 可配 | 容器内 host.docker.internal:8000 可达 |
| 7 优雅关闭 | job 不重投（AI 失败=放弃，队列语义天然不丢消息）；幂等锁防重投双发 | failure 场景零污染 |

降级语义分两档：**硬故障**（llm_timeout/llm_unavailable/circuit_open/overloaded/bad_upstream_response + 传输层失败）默认静默不发；**软降级**（no_sources/low_confidence）发送引导转人工话术。后台 `ai_service_notify_on_failure` 可把硬故障改为发送配置话术。

### 12.3 本窗口踩的两个环境坑（均已修复）

1. **file 缓存 tag 竞态**：安装默认 `cache.driver=file`，`CacheService::set`（`Cache::tag()->set`）在 php-fpm×N + workerman + timer + queue 多进程下偶发抛异常 → 症状为 JwtAuth「保存token失败」或 ws 登录直接被 close。修复：本地 `.env` 切 `DRIVER=redis`（队列本就依赖 redis），切换后连续登录 10/10 成功。同时 AiChatService 改为**原生 \Redis 直连**（.env [REDIS] 参数）做幂等/背压，不再依赖 Cache::handler()。
2. **system_config 的 value 按 JSON 解析**：`getConfigValue()` 会 `json_decode`，字符串值必须带双引号存储（数字裸存即可）。裸存字符串 → json_decode 为 null → 静默落到代码默认值（URL 恰好同值没暴露，降级话术文案不同才暴露）。已修正 SQL 模板并 UPDATE 存量行。

另：PDO 预处理不支持同名占位符复用（`SQLSTATE[HY093]`），whereRaw 双向会话查询需 `:u1/:k1/:k2/:u2` 四个独立命名。

### 12.4 E2E 验证矩阵（help/dev/ai-chat-e2e.mjs，真实 ws + 真实容器）

| 场景 | 断言 | 结果 |
|---|---|---|
| off | 开关关闭：无 AI 回复、无落库、原通路回显正常 | ✅ 3/3 |
| normal | 客服离线：30s 内收到客服身份 reply + 落库(uid=客服,type=1) | ✅ 3/3，实际 ~2s |
| human | 客服在线：AI 不接管（无 reply、无新增落库） | ✅ 3/3 |
| failure | ai-service 停机：静默降级、零污染、chat 进程存活可继续聊天 | ✅ 4/4 |
| notify | 停机 + notify=1：收到**配置库**降级话术（开小差文案） | ✅ 1/1 |

复用窗口 1 的 chat-e2e.mjs 同步复跑：基线 3/3 全绿（AI 关/开都不影响原有功能）。

### 12.5 故障注入矩阵（fake provider + fault-server.mjs，量化数据来自 PHP 结构化日志）

| 注入 | AiClient 行为 | 日志证据（attempts / 耗时） |
|---|---|---|
| 服务不可达（连接拒绝） | 重试 1 次 → 静默 | `curl(7)`, attempts=2, 5.1ms |
| HTTP 500（fault-server） | 重试 1 次 → 静默 | `http_status:500`, attempts=2, 5.5ms |
| 挂起 16s（> 单次 15s 上限） | **不重试**，15s 精确放弃 | `curl(28)`, attempts=1, 14998.8ms |
| LLM 层 5xx（fake_fail_mode=5xx） | ai-service 应答降级 → 静默 | `degraded:llm_unavailable`, 0.4ms |
| 熔断开启（连失 5 次） | 秒级快速失败 → 静默 | `degraded:circuit_open`, 16.4ms；health 显示 state=open, trips=1 |

### 12.6 trace_id 双端实证（一次问答的成对日志）

```
PHP  queue进程  [ai] reply_sent  {"trace_id":"ai-496e9e290e9b9a49","msg_id":39,
                "http":{"status":200,"attempts":1,"duration_ms":1048.3},
                "ai_latency":1010.4,"total_ms":1103.8}
PY   ai-service  ai.chat chat_completed  {"trace_id":"ai-496e9e290e9b9a49",
                "latency_ms":1010.2,"model":"fake/none","tokens":{"prompt":111,"completion":18}}
```

同一 id 双端可检索，耗时分解互相印证（ai-service 1010ms ⊂ HTTP 1048ms ⊂ job 全程 1104ms）。

### 12.7 后台低代码配置（D1 验证）

`GET /adminapi/setting/config_class`：AI 智能客服分组（id=138）列于首位；`GET /adminapi/setting/config_list/138` 返回全部 7 项（open/url/timeout/history_limit/queue_limit/notify_on_failure/fallback_text）且字符串值 JSON 解码正确。admin 前端「设置」即可渲染编辑，无需新增 Vue 页面。注意 adminapi 鉴权头为 `Authori-zation: Bearer <token>`，登录字段名是 `pwd` 不是 `password`。

### 12.8 本窗口新增/修改的文件

| 文件 | 行数 | 作用 |
|---|---|---|
| `crmeb/app/services/ai/AiChatService.php`（新） | 156 | 派发编排：开关/游客/类型过滤 → SETNX 幂等 → LLEN 背压 → trace_id → 入队 |
| `crmeb/app/jobs/AiChatJob.php`（新） | 225 | 队列任务：SLA 剩余 → history 构造 → HTTP → 降级分档 → 落库 + channel 推送 |
| `crmeb/crmeb/services/ai/AiClient.php`（新） | 104 | curl 分层超时 + 受限重试（errno 6/7 与 5xx 一次） |
| `crmeb/crmeb/services/workerman/chat/ChatHandle.php`（改） | +5 | elseif 挂接（关键词未命中 && 客服离线） |
| `help/sql/ai_service_config.sql`（新） | 46 | 低代码配置分组（幂等可重放；含 JSON 存储约定注释） |
| `help/dev/ai-chat-e2e.mjs`（新） | 242 | 五场景 E2E（内置 docker exec 配置翻转与落库校验） |
| `help/dev/fault-server.mjs`（新） | 20 | 一次性 HTTP 故障注入（500 / 挂起 16s） |

环境变更（不入库）：容器 `.env` 经 `crmeb/.env`（gitignored）同步，`[CACHE] DRIVER=redis`；`ai-service` 已灌 KB（7 docs：3 商品 + 3 话术 + 1 关键词）。

### 12.9 移交窗口 4 的事项

1. ~~真实 API 冒烟~~ ✅ 已完成（2026-10-25 补验，见 12.10）：SiliconFlow + DeepSeek-V4.1-Flash + bge-m3
2. `ai_call_log` 埋点表 + 后台监控页（AiChatJob 中 `reply_sent` 日志已含所需字段，落表即可）
3. Prometheus/Grafana + PHP 指标 exporter + locust 压测（背压阈值 50 的量化验证）
4. ~~fake provider 的 RAG 命中弱~~ ✅ 已用真实 bge-m3 解决（见 12.10）；fake vs 真实检索质量对比可留作窗口 4 量化素材

### 12.10 真实 API 冒烟记录（SiliconFlow，2026-10-25）

| 项目 | 结果 |
|---|---|
| 验活 | embeddings（bge-m3）1024 维 ✓；chat（deepseek-ai/DeepSeek-V4.1-Flash）应答 ✓；`/api/health` status=**ok**（探针真实调用） |
| KB 重灌 | 7 docs（3 商品 + 3 话术 + 1 关键词）真实 bge-m3 向量，0.95s；**换 embedding 模型必须重灌**（向量空间不同） |
| 直接冒烟 | RAG 命中 Xiaomi Civi 2 等 4 sources；回答**如实无幻觉**（5G 制式资料未写→不编造、建议转人工；库存 400 / 价格 2299 / 48h 发货均引自 KB）；3321ms，tokens 251+181 |
| E2E 真实链路 | `--scenario normal` 全绿；PHP 侧 total **4114.4ms**（HTTP 4046.8 / ai-service 4028.1），trace `ai-8bb9c191c6e8da63`，`kb_used:true` |

过程记录与坑：

- **本机直连 SiliconFlow 的 curl 必须 `-4 --ssl-no-revoke`**（本机 IPv6 路由异常 + Windows schannel 证书吊销服务器不可达）；Python httpx 走 OpenSSL 不受影响，ai-service 无需改动。
- **Git Bash 下 curl -d 传中文 JSON 会被编码损坏**（400 "error parsing body"），与窗口 2 记录一致——凡含中文的验证一律用 Python httpx。
- 首次 `/api/kb/sync` 在 KB 重置后中断（未完成），重试成功——窗口 4 可观测性上线后关注此路径。
- **密钥安全**：key 仅存 `ai-service/.env`（`ai-service/.gitignore:9` 忽略，提交前已 grep 全仓库确认无任何 key 片段入库）。曾评估 DeepSeek 官方 key（无 embeddings 端点，需拆分 provider）未采用，相关本地记录已核查清除（工作区文件 / git 全部历史 / shell 历史 / 会话临时文件四处均无）。
- 选型说明：SiliconFlow 一个 key 同时提供 chat（含 DeepSeek 系列模型，选用 V4.1-Flash）与 bge-m3 embedding，维持单 provider 架构零代码改动；DeepSeek 官方 V4.1 若未来要用，需 ~20 行拆分改造（chat 走官方 + embedding 留 SiliconFlow）。

## 十三、窗口 4 执行记录（可观测与量化，2026-10-25 完成）

### 13.1 双轨可观测架构（全部真实运行）

```
轨道一（业务内）：
  AiChatService/AiChatJob 终态 ──写──▶ eb_ai_call_log（埋点表，每条消息至多一行）
      ├─▶ adminapi /ai/monitor/*（summary/trend/list/trace/health）
      │      └─▶ admin「AI 运行监控」页（/admin/setting/ai_monitor，设置分组下）
      └─▶ exporter(:9877, 容器) ──增量读表──▶ ai_php_* 指标
轨道二（标准栈）：
  ai-service 进程内 prometheus_client ──▶ :8000/metrics（ai_http/llm/rag/breaker/backpressure/idempotency）
  exporter（pymysql 只读 + redis LLEN）──▶ :9877/metrics（ai_php_*）
        两路 ──▶ Prometheus(:9090, compose) ──▶ Grafana(:30011, compose)
                  数据源+dashboard provisioning 自动注入，匿名只读开启
```

### 13.2 埋点设计（eb_ai_call_log）

- 事件 event：派发侧 `dispatch_backpressure`（LLEN>50 拒绝入队）/`dispatch_error`；任务侧 8 个终态 `reply_sent`/`silent_degrade`/`fallback_sent`/`upstream_failed`/`bad_envelope`/`empty_reply`/`deadline_blown`/`job_error`
- 归一化 outcome（页面/图表聚合轴）：`success`（含软降级，degraded=1 区分）/`silent`（用户零感知）/`notify`（发了降级话术）/`rejected`（背压拒绝）/`error`（异常）
- 每行携带：trace_id、http_status/attempts、耗时三元组（http_ms/ai_ms/total_ms，成功应答才有 ai_ms）、kb_used/degraded/reason、prompt/completion_tokens、error 摘要
- 落表契约：写表异常仅记日志绝不影响主链路（AiCallLog::record 吞异常）；AiChatJob 每条消息恰好至多一行（各终态单点写入）

### 13.3 后台监控页（admin 前端新增，唯一改前端构建链的一处）

页面 `pages/setting/aiMonitor/index.vue`（Vue2 + echarts 4.8），路由挂在 `router/modules/setting.js`（`/admin/setting/ai_monitor`，meta.auth=`admin-setting-ai_monitor`），菜单纯 DB 数据（`eb_system_menus` pid=12，menu_path 存储不含 /admin 前缀——后端 `SystemMenusServices:55` 返回时统一拼接，这是菜单机制的关键约定）。内容：

- 健康徽标（透传 ai-service /api/health：provider/模型/KB 文档数/并发与队列/熔断状态，30s 轮询）
- 8 张 KPI 卡：总调用量/成功率/P95/KB 命中率/静默放弃/降级话术/背压拒绝/重试次数
- 三图：小时趋势（分结果堆叠柱+平均耗时折线）、结果分布环形图、降级原因横向条形
- 延迟分解表（total/http/ai 三阶段的 P50/P90/P95/P99/avg/max）+ token 消耗
- 明细表（事件/结果 tag/耗时/尝试/KB/reason/token/错误）+ trace_id 链路弹窗（成对的用户消息原文与 AI 回复原文）

构建部署：便携 Node 20.19.0 + `NODE_OPTIONS=--openssl-legacy-provider` 构建，产物 600 文件/41MB（上游 596），覆盖 `template/admin/dist` → 同步为 `crmeb/public/admin`（仓库内管理）→ `docker cp` 进容器；`/admin/`、新 app.js（1.49MB）、SPA 路由直达全部 200。浏览器实测：菜单中文高亮正确、KPI/图表/延迟分解全部真实数据渲染。

### 13.4 指标 exporter（ai-service/exporter.py，compose 服务 ai-exporter）

- **增量游标模式**：内存记录 ai_call_log 最大 id，每次抓取只 observe `id > cursor` 的新增行；进程重启从当前 max(id) 起步不回灌历史（counter 语义在进程生命周期内成立）
- 指标：`ai_php_calls_total{event,outcome}`、`ai_php_outcome_total`、`ai_php_degraded_total{reason}`、`ai_php_total_ms/_http_ms`（histogram，仅 success 样本）、`ai_php_tokens_total{direction}`、`ai_php_kb_used_total`、`ai_php_queue_depth`（redis LLEN 队列积压）、`ai_php_exporter_last_id`
- 只读契约：一个 SELECT + 一个 LLEN；连接 MySQL/Redis 全用容器网络内名（crmeb_mysql/crmeb_redis）

### 13.5 本窗口踩的四个坑（均已修复并验证）

1. **SQL 导入字符集**：`docker exec -i mysql < ai_monitor.sql` 未加 `--default-character-set=utf8mb4`，中文菜单名被 latin1 双重编码成乱码。修复：UPDATE 修正数据 + SQL 文件头注明导入命令。
2. **pymysql 长连接看不到新行**：默认 `autocommit=False`，第一条 SELECT 开启 REPEATABLE READ 快照后，同一连接永远读不到其他进程新写入的行（exporter last_id 停滞的根因）。修复：`conn.autocommit(True)`。
3. **PHP 背压检查查错键（窗口3遗留 bug，被本窗口压测暴露）**：think-queue redis 驱动的真实队列键是 `queues:{queue}`（`Redis.php getQueue`），窗口3的 AiChatService 背压检查用的是无前缀键——LLEN 恒 0，背压从未真正生效（窗口3只验证了代码路径未实测触发）。修复后重跑压测：背压精确生效（见 13.7）。
4. **Docker Hub 直连超时**：`registry-1.docker.io` 连接失败；镜像源可用性实测 `docker.1panel.live` 与 `hub.rat.dev` 可达（daocloud 不通）。prom/grafana 镜像经镜像源拉取后 `docker tag` 还原命名。

### 13.6 ai-service 直压量化（locust，fake provider 零成本确定性）

| 场景 | 并发 | 结果 |
|---|---|---|
| 吞吐上限（FAKE_LATENCY_MS=0，RAG 全链路） | 20 | **92.7 RPS**，5511 请求 0 失败，P50=180ms / P95=300ms / P99=400ms，max 728ms |
| 容量内（FAKE_LATENCY_MS=2500，8+32=40 容量） | 30 | **0 拒绝**，全部排队消化，P50=6s / P90=12s / P99=20s（排队延迟线性增长） |
| 边界外 | 45 | 秒级 503 拒绝（Min=1ms），拒绝率 79% |
| 边界外 | 60 | **99.7% 秒级拒绝**（313/314，`ai_queue_rejected_total` 精确计数），成功请求 P50=6s |

结论：背压设计的两个目标均量化成立——容量内排队不拒绝（SLA 内消化），容量外**立即拒绝不堆积**（1ms 级返回而非 20s 超时）。

### 13.7 全链路压测（PHP→队列→ai-service→落库推送，help/dev/ai-load-e2e.mjs）

设置：ai_service_url 切慢速 fake（:8010, FAKE_LATENCY_MS=2500），300 条消息 @10/s 突发（消费能力 0.4/s），结束后还原配置。

| 观测 | 数值 |
|---|---|
| redis 队列深度 | **峰值 51 = 阈值 50 + 正在处理的 1 条**，均值 37.2，背压生效后队列被钉住不再上涨 |
| 派发背压拒绝 | **133 条（44.3%）**，outcome=rejected，全部毫秒级放弃，用户零感知 |
| 入队消息终态 | 117 deadline_blown（SLA 耗尽静默放弃）+ 32 llm_timeout（ai-service 内 deadline 耗尽）+ 14 upstream_failed + 4 success |
| 总账 | **300/300 全部有终态记录**，无一条消息失踪；原聊天通路全程零影响 |

### 13.8 故障演练量化（drill_breaker.py，真实 HTTP）

| 阶段 | 行为 | 量化证据 |
|---|---|---|
| 连失触发（FAIL_MODE=5xx） | 前 5 次 `llm_unavailable`（每次真实打到上游），第 5 次熔断开启 | health: state=open, consecutive_failures=5, trips=1 |
| open 期间 | 后续 7 次全部 `circuit_open` **秒级拒绝（0–15ms，不再打上游）** | 同上时序表 |
| half_open 探测·故障保持 | 30s 重置窗口后首次探测真实打到上游失败（16ms）→ **重新 open，trips=2**（不盲目恢复） | health: opened_seconds_ago 重置为 0 |
| half_open 探测·故障解除 | 探测成功 → **state=closed**，恢复正常应答 | health: consecutive_failures=0 |

### 13.9 Prometheus/Grafana（compose 三服务 + provisioning）

- compose 新增：`prometheus`（:9090，host-gateway 抓宿主机 ai-service:8000，7d 保留）、`grafana`（:30011，admin/admin123 + 匿名 Viewer）、`ai-exporter`（:9877，python:3.12-alpine 启动时 pip 装依赖）
- 抓取 target 全部 up：`ai-service`（进程内指标）+ `ai-php-exporter`（PHP 链路指标）
- Grafana provisioning：数据源 uid=prometheus + dashboard JSON（`help/docker/grafana/dashboards/ai-overview.json`，12 面板：吞吐/延迟分位/LLM 结果/token、PHP 结果速率/延迟/队列积压、熔断状态/降级原因/背压拒绝/RAG 命中与重试率）

### 13.10 本窗口新增/修改的文件

| 文件 | 行数 | 作用 |
|---|---|---|
| `crmeb/app/services/ai/AiCallLog.php`（新） | 76 | 埋点写入（吞异常，不伤主链路） |
| `crmeb/app/jobs/AiChatJob.php`（改） | +30 | 各终态写 ai_call_log（含 tokens 提取） |
| `crmeb/app/services/ai/AiChatService.php`（改） | +22 | 派发拒绝/异常埋点；**修复背压键名缺失 queues: 前缀的 bug** |
| `crmeb/crmeb/services/ai/AiClient.php`（改） | +41 | 新增 getJson（health 探针） |
| `crmeb/app/adminapi/controller/v1/ai/Monitor.php`（新） | 235 | summary/trend/list/trace/health 五接口（分位数 PHP 侧计算） |
| `crmeb/app/adminapi/route/ai.php`（新） | 31 | ai 分组路由（标准 admin 中间件栈） |
| `crmeb/public/admin/`（重建） | 600 文件 | 新增监控页的构建产物 |
| `template/admin/src/pages/setting/aiMonitor/index.vue`（新） | 560 | 监控页（KPI/三图/延迟分解/明细/trace 弹窗/健康徽标） |
| `template/admin/src/api/ai.js`（新） | 55 | 五个监控 API |
| `template/admin/src/router/modules/setting.js`（改） | +9 | 路由注册 |
| `help/sql/ai_monitor.sql`（新） | 47 | ai_call_log 表 + 菜单行（幂等，注明 utf8mb4 导入） |
| `ai-service/exporter.py`（新） | 200 | PHP 链路指标 exporter（增量游标） |
| `ai-service/locustfile.py`（新） | 55 | 直压场景（503 记为有效观测点） |
| `ai-service/drill_breaker.py`（新） | 90 | 熔断演练（fail/recover 两阶段） |
| `help/dev/ai-load-e2e.mjs`（新） | 175 | 全链路压测（配置翻转/队列深度采样/终态统计/自动还原） |
| `help/docker/docker-compose.yml`（改） | +55 | prometheus/grafana/ai-exporter 三服务 |
| `help/docker/prometheus/prometheus.yml`（新） | 22 | 抓取配置 |
| `help/docker/grafana/provisioning/**`（新） | 20 | 数据源 + dashboard provider |
| `help/docker/grafana/dashboards/ai-overview.json`（新） | 280 | 12 面板总览看板 |

### 13.11 移交窗口 5 的事项

1. 后台 AI 文案（adminapi 接口 + admin 商品编辑页按钮）：复用 `/api/copywriting`（ai-service 已实现，走同一套 breaker/backpressure/trace）；监控页旁边可加文案生成入口
2. Grafana dashboard 截图进 README；`crmeb/public/admin` 已是含监控页的产物，窗口5改文案按钮后需再次构建覆盖
3. 压测结果 CSV 在 `ai-service/results/`（throughput/backpressure60），README 性能表直接引用 13.6/13.7 数字


## 十四、窗口 5 执行记录（后台 AI 文案，2026-10-26 完成）

### 14.1 链路与设计

```
admin 商品编辑页（4 个「AI 生成」入口）
  商品名称(BasicInfo) / 商品关键字+商品简介(OtherSetting) / 商品详情(ProductDetail)
  └─ aiCopywriting.vue 弹窗：语气选择 → 素材（预填当前字段值）→ 生成 → 预览可编辑 → 应用
       └─ POST /adminapi/ai/copywriting/generate {kind, seed, tone}
            └─ Copywriting 控制器：kind 白名单 + seed 截断(2000) + trace_id ai-{16hex}
                 └─ AiClient::postJson（连接1s/单次21s/预算25s）→ ai-service POST /api/copywriting
                      └─ 同一套 breaker / backpressure / trace / 结构化日志（复用窗口2/3 原语）
```

- 第二用例与客服 chat 共享同一 ai-service 与观测链路，差异仅在：无 RAG、无幂等、无同会话串行（管理员主动触发的短任务）
- ai-service `CopywritingRequest.kind` 由 4 种扩至 6 种（新增 `product_sell_point` 卖点 / `product_seo_keywords` SEO 关键词），与商品编辑页四个字段一一对应；pytest 28 passed 回归通过
- 降级语义：ai-service 不可达 / code≠0 / data.degraded 一律 `fail(原因)` 返回前端 toast，绝不阻塞商品编辑本身
- 字段映射：store_name→product_title、store_info→product_sell_point、keyword→product_seo_keywords、description→product_desc；「应用」时详情字段同时同步富文本编辑器（contents）与提交源（content），其余直接写 formValidate

### 14.2 真实环境验收（API 取证，2026-10-26）

| 步骤 | 结果 |
|---|---|
| 直连 ai-service 冒烟 | product_sell_point → 「小米Civi2轻薄自拍旗舰，2299元现货，48小时发货。」8.8s；product_seo_keywords → 7 个关键词空格分隔 |
| adminapi 生成 | kind=product_title → **「小米Civi 2 轻薄自拍旗舰 5G 手机」**，trace `ai-db8b1e5d61d2cec9`，model DeepSeek-V4.1-Flash，842 tokens，9.4s |
| 应用（编辑页同款保存接口 POST product/product/1） | **保存成功**（status 200） |
| 落库复查 | `eb_store_product.id=1` store_name 已由 `Xiaomi Civi 2 冰冰蓝 8GB+128GB` 变为 AI 标题（演示商品，保留作展示） |
| 部署验证 | `/admin/` 200，新入口 `app.823f927a.js`（1.49MB）200，产物 600 文件 |

### 14.3 本窗口踩的四个坑（均已修复）

1. **GET productInfo 与保存表单的字段差**：SKU 行的 `is_default_select` 由前端 SpecStock 构建时补默认值（GET 不返回），API 直调保存会 400；取证脚本对 attrs 行 `setdefault('is_default_select', 0)` 解决
2. **SiliconFlow 延迟波动击穿 15s**：直连冒烟 8.8s，但偶发 >15s（PHP 单次上限）→ curl(28)。文案是管理后台动作无聊天 SLA，超时提至单次 21s/预算 25s（> ai-service SLA 20s，必能收到应答或降级信封，不会双头等待）
3. **ai-service 启动环境**：全局 Anaconda python 缺 pymysql（旧进程可能带虚拟环境变量启动），改用项目 `.venv`（pytest 同环境已验证依赖齐全）以 0.0.0.0:8000 重启
4. **adminapi 登录路径**：是 `/adminapi/login`（无 /admin 段，与 `adminapi/login/info` 同前缀），字段名 pwd
5. **docker 重启后 workerman 僵死 master**：/var/www 在命名卷中，容器重启后旧 pid 文件残留，`start --d` 判定 already running 提前退出，出现「master 活着但 worker 为 none」的假活状态（ws 握手全挂）。处置：容器内 `kill -9` 旧 master + `php think workerman start --d`（守护进程持有 stdout，docker exec 不返回属正常假象）；之后 chat E2E normal 场景全绿

### 14.4 本窗口新增/修改的文件

| 文件 | 行数 | 作用 |
|---|---|---|
| `crmeb/app/adminapi/controller/v1/ai/Copywriting.php`（新） | 90 | 文案生成接口（kind 白名单/seed 截断/超时分层/降级归一） |
| `crmeb/app/adminapi/route/ai.php`（改） | +5 | 「AI 文案」路由分组 |
| `crmeb/public/admin/`（重建） | 600 文件 | 含监控页+文案按钮的构建产物 |
| `template/admin/src/pages/product/components/aiCopywriting.vue`（新） | 128 | 生成/预览可编辑/应用弹窗（语气+素材+trace 展示） |
| `template/admin/src/api/ai.js`（改） | +10 | aiCopywriting API |
| `template/admin/src/pages/product/productAdd/index.vue`（改） | +40 | 弹窗挂载 + aiGenerate/onAiApply（详情双同步） |
| `template/admin/src/pages/product/productAdd/components/{BasicInfo,OtherSetting,ProductDetail}.vue`（改） | +120 | 四字段「AI 生成」按钮 |
| `ai-service/app/schemas.py`（改） | ±1 | kind 扩至 6 种 |
| `ai-service/app/services/copywriting.py`（改） | +2 | 新增卖点/SEO 两类 PROMPTS |

### 14.5 移交窗口 6 的事项

1. README 性能表直接引用 13.6/13.7 数字；架构图补上第二用例（后台文案与客服共用链路）
2. `crmeb/public/admin` 已是含监控页+文案按钮的最终产物（600 文件）；`template/admin/dist` 与 `ai-service-run.log` 均 gitignored
3. ai-service 当前以 `.venv` python 运行（0.0.0.0:8000，uvicorn），compose 的 ai-exporter/prometheus/grafana 三服务已在跑（窗口4）

## 十五、窗口 6 执行记录（文档与开源，2026-10-26 完成）

### 15.1 交付内容

**ai-service 容器化（纳入 compose 一键部署）**：

- `ai-service/Dockerfile`：python:3.12-slim，构建可换 pip 源（`--build-arg PIP_INDEX_URL`）；配置经 compose `env_file` 注入进程环境——**真实 key 只存在于宿主 `ai-service/.env`，不进镜像、不进仓库**
- compose 新增 `ai` 服务（crmeb_ai, :8000）：`MYSQL_*` 环境变量覆盖 .env 的宿主机端口指向 compose 内网（crmeb_mysql:3306）；Chroma 向量库持久化在命名卷 `ai_chroma`（ext4，避 9p），首次启动后 `POST /api/kb/sync` 从 MySQL 灌库；换 embedding 模型需重灌

**README 重写 + 截图**：

- README 全面重写（387 行改动）：声明与致谢（Apache-2.0 §4b 合规：保留版权头/LICENSE/品牌标识，修改以 git 历史 + 本文档体现）、能力矩阵、架构图、七项可靠性标准实证表、量化结果（13.6/13.7 数字 + 真实冒烟 + E2E 矩阵 + 9p 修复）、一键部署 6 步、本地演示账号、二开改动总览
- 5 张运行截图入库 `docs/screenshots/` 并嵌入 README：监控页常态（健康徽标 + KPI）/监控页压测后（925 调用、背压拒绝 272、静默放弃 617）/Grafana 调用结果率与 HTTP QPS——截图发布前逐一核查无密钥等敏感信息
- `docs/README.md` 文档索引（主文档 + 截图 + E2E/演练脚本说明）；locust 压测原始 CSV 归档 `ai-service/results/`（8 个文件，README 性能表的可复核数据源）

**窗口 4/5 尾巴在本窗口期收编**（提交先于本记录）：

| 提交 | 内容 |
|---|---|
| abed2e3d | exporter 队列键补 `queues:` 前缀（同 13.5 第 3 条 PHP 背压修复，exporter 的 `ai_php_queue_depth` 此前恒 0） |
| 758ed006 | E2E failure/notify 场景自包含停机（切死 :8010 慢速 fake 端口替代人工停 uvicorn，开源后可一键复放） |
| 97c24e28 | 补记窗口 5 执行记录（含 docker 重启后 workerman 僵死 master 的发现与处置，见 14.3 第 5 条） |

### 15.2 密钥安全核查（发布前，逐项通过）

1. **.env 不入库**：`ai-service/.gitignore`（`.env`）+ 根 `.gitignore`（`crmeb/.env`）双保险；`git ls-files` 确认 `ai-service/.env` 与 `crmeb/.env` 均未被跟踪，入库的只有 `.env.example` 模板
2. **git 全历史扫描**：对上游快照之后的全部改动补丁扫描密钥特征——真实 key 全历史零命中；`sk-` 通用模式唯一命中为 `.env.example` 的 `sk-xxxx…` 占位符（非密钥）。核查只输出计数/文件名，不回显内容；另确认被忽略的本地文件中真实 key 仅存在于 `ai-service/.env`
3. **截图核查**：5 张截图经视觉检查确认无 API key/token 等敏感信息
4. **镜像核查**：Dockerfile 不 COPY .env；compose 经 env_file 注入，key 不进镜像层

### 15.3 发布

- 推送 `main` 至 GitHub（yu788587-dot/crmeb-ai-mall），打 tag `v1.0.0` 并发布 Release（含能力摘要与部署指引）
- 发布物自检：compose 一键部署路径可用（fake 模式无 key 可跑）、SQL 幂等可重放、E2E/演练脚本自包含

### 15.4 本窗口新增/修改的文件

| 文件 | 作用 |
|---|---|
| `ai-service/Dockerfile`（新） | AI 微服务镜像（key 不进镜像） |
| `ai-service/.dockerignore`（新） | 构建上下文瘦身 |
| `help/docker/docker-compose.yml`（改） | +`ai` 服务与 `ai_chroma` 卷（全栈 7 服务 + exporter） |
| `README.md`（重写） | 开源门面：合规声明/能力/架构/量化/部署 |
| `docs/README.md`（新） | 文档索引 |
| `docs/screenshots/*.png`（新 5 张） | README 嵌入的运行截图 |
| `ai-service/results/*.csv`（新 8 个） | locust 压测原始数据 |
| `.gitignore`（改） | 忽略窗口执行锁文件 `help/dev/.window-run.lock` |
