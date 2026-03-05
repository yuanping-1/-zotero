# Zotero 文献邮件自动筛选器

一个用于自动处理“文献推送邮件”的 Python 脚本：

1. 从 IMAP 邮箱读取未读邮件；
2. 使用 Qwen 提取邮件中的论文信息（标题、摘要、DOI、URL）；
3. 按自定义筛选标准给论文打分；
4. 达到阈值后自动写入 Zotero；
5. 使用本地 SQLite 记录状态，避免重复处理邮件或重复入库论文。

## 功能概览

- 自动抓取未读邮件（可按发件人过滤）
- 自动从 HTML / 文本邮件正文抽取内容
- 调用 Qwen 模型进行论文提取与评分
- 按 `min-score` 阈值决定是否同步到 Zotero
- 基于 `state.db` 做去重与幂等

## 环境要求

- Python 3.9+
- 可访问 IMAP 邮箱
- 可用的 Qwen API Key（DashScope 兼容接口）
- 可用的 Zotero 用户 ID 与 API Key

## 快速开始

### 1) 安装依赖

本项目仅使用 Python 标准库，无需额外 `pip install`。

### 2) 设置环境变量

```bash
export IMAP_HOST="imap.163.com"
export EMAIL_USER="your_email@example.com"
export EMAIL_PASSWORD="your_imap_password_or_auth_code"
export SENDER_FILTER="newsletter@example.com"

export QWEN_API_KEY="your_qwen_api_key"

export ZOTERO_USER_ID="your_zotero_user_id"
export ZOTERO_API_KEY="your_zotero_api_key"

export SCREENING_CRITERIA="与我的研究方向相关，方法清晰且有实验验证"
export MIN_SCORE="75"
```

### 3) 运行脚本

```bash
python literature_pipeline.py
```

## 命令行参数

```bash
python literature_pipeline.py \
  --imap-host imap.163.com \
  --email-user your_email@example.com \
  --email-password your_password \
  --sender-filter newsletter@example.com \
  --qwen-api-key your_qwen_api_key \
  --zotero-user-id your_zotero_user_id \
  --zotero-api-key your_zotero_api_key \
  --screening-criteria "你的筛选标准" \
  --min-score 75
```

> 说明：命令行参数优先级高于环境变量。

## 输出与状态文件

- `state.db`
  - `processed_messages`: 已处理邮件 ID
  - `synced_papers`: 已同步论文 key（DOI 优先，否则标题归一化）

重复运行脚本时，会自动跳过已处理项目。

## 常见问题

- **报错“缺少必填参数”**
  - 请检查以下字段是否已提供：`email-user`、`email-password`、`qwen-api-key`、`zotero-user-id`、`zotero-api-key`。

- **Qwen 提取失败 / 打分失败**
  - 检查 API Key、网络连通性和接口配额。

- **Zotero 写入失败**
  - 检查用户 ID、API Key 和权限范围是否正确。

## 安全建议

- 不要将真实 API Key、邮箱密码提交到代码仓库。
- 建议使用邮箱授权码而非登录密码。
- 建议通过 CI/CD Secret 或本地 `.env` 管理敏感信息。
