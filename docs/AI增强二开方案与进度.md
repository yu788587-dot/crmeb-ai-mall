# AI 增强二开方案与进度（CRMEB v6.0.0 → crmeb-ai-mall）

> **状态**：✅ 方案已批准（2026-10-01），执行中
> **定位**：主打 **AI 系统工程能力**——在成熟电商系统上落地 AI 能力的异步架构、降级容错与可观测性实践
> 最后更新：2026-10-01

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
| 1 环境与基线 | git init 两段提交 → 启动 Docker → compose 全栈（nginx:8011/mysql:33061/redis:63791）→ 安装 CRMEB → admin/H5 登录 → 基线聊天可用 → **预验证 admin 前端构建链（便携 Node 20）** → 集成机制细节确认 → AI 关闭时原版行为基线 | 系统可跑、构建链可行、git 就绪 |
| 2 AI 微服务 | FastAPI + RAG + 知识库同步 + **trace_id 契约 / 结构化日志 / /metrics / health** | pytest 全绿 + 真实 API 冒烟 |
| 3 集成与可靠性 | ChatHandle 挂接 + AI Worker + 七项可靠性标准 + 后台开关 | 链路跑通 + 初步故障注入 |
| 4 可观测与量化 | ai_call_log + 后台监控页 + Prometheus/Grafana + exporter + locust 压测 + 故障演练 | 数字、图表与截图产出 |
| 5 后台 AI 文案 | adminapi 接口 + admin 前端按钮 + 构建（轻量，工期紧可砍） | 生成并应用文案 |
| 6 文档与开源 | README 重写（架构/时序/性能表/演练GIF）+ docs + compose 一键部署（含 ai-service/Prometheus/Grafana）+ push + Release v1.0.0 | 开源发布 |

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
| LLM key 失效 | 窗口 1 顺带 curl 验活 |

## 九、量化产出（进 README 与简历）

压测吞吐与 P50/P95/P99、降级与熔断触发次数、RAG 命中率、故障演练录屏/截图、代码规模（新增 x 行 / 修改 y 行 / 涉及 N 个文件）。
