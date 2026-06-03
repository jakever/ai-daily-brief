# AI Daily Brief

每天早晨自动聚合 11 个 AI 信息源，调用 Claude Sonnet 4.6 翻译、过滤、分类、摘要，以飞书 interactive 卡片（schema 2.0，支持版块折叠展开）推送到指定群。

## 架构

```
GitHub Actions (cron 37 22 * * * UTC ≈ 次日 06:37 CST)
  → fetch_sources.py  抓 11 源 → raw.json
  → analyze.py        Claude Sonnet 4.6 → digest.json
  → send_lark.py      飞书 webhook
```

## 版块

| key | 卡片标题 | 内容 |
|---|---|---|
| `frontier` | 🤖 AI Agent / 大模型 | 国外主流模型与 Agent / 编程工具（Claude / Codex / Gemini / Llama 等），始终输出 |
| `breaking` | 🔥 今日重磅 | 国内模型重磅、标志性融资 / 收购 / IPO、重大行业事件 |
| `oversea` | 🌏 海外动态 | 海外公司非模型类日常（研究、合作、人事、政策） |
| `cn` | 🇨🇳 国内动态 | 国内公司日常 + 行业 / 商业分析 |
| `trending` | 🚀 GitHub Trending | GitHub 每日热门仓库，始终输出 |

每版块默认展示前 5 条，超出部分收进卡片折叠面板「展开剩余 N 条」（纯客户端展开，无需回调服务器）。

## 信息源

`sources.yaml` 配置，4 种抓取方式（均为轻量 HTTP，不执行 JS）：

- `rss`（6 源）：量子位、36氪、钛媒体、OpenAI News、Hugging Face、GitHub Blog AI — 最稳，带时间戳
- `html`（3 源）：Anthropic News、Anthropic Engineering、GitHub Trending — `requests` + `BeautifulSoup` + CSS 选择器
- `changelog_md`（1 源）：Claude Code CHANGELOG.md — 解析 markdown 更新日志
- `ai_bot_daily`（1 源）：ai-bot.cn 每日 AI 快讯 — 抓当日逐条新闻

> 时效控制：`window_hours`（默认 24h）只保留近期条目。无时间戳的源默认丢弃，除非标 `assume_fresh`（如 GitHub Trending、Claude Code Changelog 这类天然新鲜或始终展示最新的源）。

## 快速开始

### 1. Secrets 配置

仓库 Settings → Secrets and variables → Actions：

- `ANTHROPIC_API_KEY` — Claude API Key（或中转平台 Key）
- `ANTHROPIC_BASE_URL` — 中转平台 endpoint（用官方 API 则留空）
- `LARK_WEBHOOK_URL` — 飞书群机器人 webhook URL

### 2. 本地调试

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 抓取（不调 LLM）
python scripts/fetch_sources.py
jq '.ok_sources, .total_sources, .errors' raw.json

# 分析
ANTHROPIC_API_KEY=sk-... ANTHROPIC_BASE_URL=https://... python scripts/analyze.py
jq '.sections | map_values(length)' digest.json

# 推送
LARK_WEBHOOK_URL=https://... python scripts/send_lark.py
```

### 3. 手动触发

GitHub Actions UI → "AI Daily Brief" → Run workflow。可选 `skip_send` 跳过推送。

## 实现要点

- **LLM 输出格式**：analyze 用「分隔符纯文本」（`标题 ||| 摘要 ||| URL ||| 来源`）而非 JSON——中转平台会把嵌套对象序列化成字符串并泄漏未转义引号，导致 JSON 解析失败；纯文本格式无需转义，彻底规避。
- **触发时间**：GitHub cron 是 best-effort，整点最拥堵，故用非整点分钟并提前发车，即便延迟 1-3h 仍落在早晨。需准点请改用外部定时器调 `workflow_dispatch`。

## 迭代

- 调整源：编辑 `sources.yaml`
- 调整版块 / 筛选 / 分类规则：编辑 `prompts/analyze.md`
- 调整卡片样式 / 折叠阈值（`VISIBLE`）：编辑 `scripts/lib/lark_card.py`

每次跑完会上传 `raw.json` / `digest.json` 到 artifact，保留 7 天，便于回看。
