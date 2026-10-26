# docs 索引

| 文档 | 内容 |
|---|---|
| [AI增强二开方案与进度.md](AI增强二开方案与进度.md) | **主文档**：项目定位、功能设计、可靠性/可观测标准、执行架构，以及窗口 1–6 的完整执行记录（每个窗口含设计决策、踩坑、量化证据与文件清单）——也是本仓库相对上游 CRMEB 的全部修改档案（Apache-2.0 §4b） |
| `screenshots/` | README 引用的运行截图：后台「AI 运行监控」页×3（常态/压测后）+ Grafana 面板×2（调用结果率/HTTP QPS） |

## 快速定位（按主题）

- **架构与挂接点**：主文档第五节（技术架构）、12.1（集成链路）、14.1（文案链路）
- **可靠性七项标准的实现与实证**：第三节（标准）、12.2（落地矩阵）、12.5（故障注入矩阵）、13.8（熔断演练）
- **可观测双轨**：第四节（设计）、13.1–13.4（埋点表/监控页/exporter/Prometheus/Grafana）
- **量化数据**（README 性能表出处）：13.6（ai-service 直压）、13.7（全链路背压压测）、12.10（真实 API 冒烟）、10.2（9p 性能悬崖修复）
- **部署与运维**：主文档 10.1/10.2（compose 与 sync.sh）、仓库根 README「一键部署」、`help/docker/docker.md`
- **原始压测数据**：`ai-service/results/*.csv`（locust 产物，对应 13.6 两场景）

## E2E / 演练脚本

| 脚本 | 用途 |
|---|---|
| `help/dev/chat-e2e.mjs` | 基线聊天 E2E（客户+客服双 ws 连接） |
| `help/dev/ai-chat-e2e.mjs` | AI 客服五场景 E2E（off/normal/human/failure/notify） |
| `help/dev/ai-load-e2e.mjs` | 全链路压测（配置翻转/队列深度采样/终态统计/自动还原） |
| `help/dev/fault-server.mjs` | 一次性 HTTP 故障注入（500 / 挂起） |
| `ai-service/drill_breaker.py` | 熔断状态机演练（fail/recover 两阶段，读 /api/health 时序） |
| `ai-service/locustfile.py` | ai-service 直压场景 |
