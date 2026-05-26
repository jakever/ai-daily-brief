# AI Daily Brief

每天 09:00 (Asia/Shanghai) 自动聚合 14 个 AI 信息源，调用 Claude Sonnet 4.6 翻译、分类、摘要，以飞书 interactive 卡片推送到指定群。

## 架构

```
GitHub Actions (cron 0 1 * * * UTC)
  → fetch_sources.py  抓 RSS/HTML/HN 14 源 → raw.json
  → analyze.py        Claude Sonnet 4.6 → digest.json
  → send_lark.py      飞书 webhook
```

## 快速开始

### 1. Secrets 配置

仓库 Settings → Secrets and variables → Actions：

- `ANTHROPIC_API_KEY` — Claude API Key
- `LARK_WEBHOOK_URL` — 飞书群机器人 webhook URL

### 2. 本地调试

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 抓取（不调 LLM）
python scripts/fetch_sources.py
jq '.ok_sources, .total_sources, .errors' raw.json

# 分析
ANTHROPIC_API_KEY=sk-... python scripts/analyze.py
jq '.sections | map_values(length)' digest.json

# 推送
LARK_WEBHOOK_URL=https://... python scripts/send_lark.py
```

### 3. 手动触发

GitHub Actions UI → "AI Daily Brief" → Run workflow。可选 `skip_send` 跳过推送。

## 迭代

- 调整源：编辑 `sources.yaml`
- 调整 prompt：编辑 `prompts/analyze.md`
- 调整版块/筛选规则：同上

每次跑完会上传 `raw.json` / `digest.json` 到 artifact，保留 7 天，便于回看。
