# -*- coding: utf-8 -*-
"""
Zotero 文献邮件自动筛选器
自动读取文献鸟推送邮件 -> Qwen 提取论文信息 -> 打分 -> 写入 Zotero
"""

import argparse
import email
import imaplib
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class Paper:
    title: str
    abstract: str = ""
    doi: str = ""
    url: str = ""
    score: float = 0.0


# ── HTML 转纯文本 ─────────────────────────────────────────────────────────────

class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        if tag in ("p", "br", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def get_text(self):
        return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()


def html_to_text(html: str) -> str:
    p = HTMLTextExtractor()
    p.feed(html)
    return p.get_text()


# ── 邮件读取 ──────────────────────────────────────────────────────────────────

def decode_header_value(value: str) -> str:
    parts = email.header.decode_header(value)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def get_body(msg: email.message.Message) -> str:
    html_body = ""
    text_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ct == "text/html":
                html_body = text
            elif ct == "text/plain":
                text_body = text
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = text
            else:
                text_body = text
    if html_body:
        return html_to_text(html_body)
    return text_body


def fetch_unseen(host: str, user: str, password: str,
                 sender_filter: Optional[str] = None) -> list:
    client = imaplib.IMAP4_SSL(host)
    client.login(user, password)
    client.list()
    client.select("INBOX")

    if sender_filter:
        criteria = f'(UNSEEN FROM "{sender_filter}")'
    else:
        criteria = "(UNSEEN)"

    status, data = client.search(None, criteria)
    if status != "OK":
        client.logout()
        raise RuntimeError("IMAP 搜索失败")

    messages = []
    ids = data[0].split()
    print(f"收到未读邮件: {len(ids)}")
    for msg_id in ids:
        status, msg_data = client.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        messages.append((msg_id.decode(), msg))

    client.logout()
    return messages


# ── 状态数据库（去重） ─────────────────────────────────────────────────────────

class StateDB:
    def __init__(self, path: str = "state.db"):
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                msg_id TEXT PRIMARY KEY,
                ts INTEGER
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS synced_papers (
                key TEXT PRIMARY KEY,
                ts INTEGER
            )
        """)
        self.conn.commit()

    def seen_message(self, msg_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM processed_messages WHERE msg_id=?", (msg_id,)
        ).fetchone()
        return row is not None

    def mark_message(self, msg_id: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO processed_messages VALUES (?,?)",
            (msg_id, int(time.time())),
        )
        self.conn.commit()

    def seen_paper(self, key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM synced_papers WHERE key=?", (key,)
        ).fetchone()
        return row is not None

    def mark_paper(self, key: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO synced_papers VALUES (?,?)",
            (key, int(time.time())),
        )
        self.conn.commit()


def stable_paper_key(paper: Paper) -> str:
    if paper.doi:
        return "doi:" + paper.doi.strip().lower()
    return "title:" + re.sub(r"\s+", " ", paper.title.strip().lower())


# ── Qwen API ──────────────────────────────────────────────────────────────────

def qwen_chat(api_key: str, system: str, user: str, max_tokens: int = 2000) -> str:
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    payload = json.dumps({
        "model": "qwen-plus",
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


def extract_candidates(api_key: str, body: str) -> list[Paper]:
    system = (
        "你是一个学术助手。从邮件正文中提取所有论文，"
        "输出 JSON 数组，每项字段：title, abstract, doi, url。"
        "如果某字段缺失则填空字符串。只输出 JSON，不要其他内容。"
    )
    user = f"邮件正文：\n{body[:6000]}"
    try:
        raw = qwen_chat(api_key, system, user)
        raw = re.sub(r"```json|```", "", raw).strip()
        items = json.loads(raw)
        papers = []
        for item in items:
            if not item.get("title"):
                continue
            papers.append(Paper(
                title=item.get("title", ""),
                abstract=item.get("abstract", ""),
                doi=item.get("doi", ""),
                url=item.get("url", ""),
            ))
        return papers
    except Exception as e:
        print(f"  [提取失败] {e}")
        return []


def score_paper(api_key: str, paper: Paper, criteria: str) -> float:
    system = (
        "你是一个学术论文筛选助手。根据筛选标准给论文打分（0-100整数）。"
        "只输出一个数字，不要其他内容。"
    )
    criteria_text = criteria if criteria else "综合学术价值、创新性和实用性"
    user = (
        f"筛选标准：{criteria_text}\n\n"
        f"标题：{paper.title}\n"
        f"摘要：{paper.abstract[:1000]}"
    )
    try:
        raw = qwen_chat(api_key, system, user, max_tokens=10).strip()
        return float(re.search(r"\d+(\.\d+)?", raw).group())
    except Exception as e:
        print(f"  [打分失败] {e}")
        return 0.0


# ── Zotero API ────────────────────────────────────────────────────────────────

def zotero_add(user_id: str, api_key: str, paper: Paper):
    url = f"https://api.zotero.org/users/{user_id}/items"
    item = {
        "itemType": "journalArticle",
        "title": paper.title,
        "abstractNote": paper.abstract,
        "DOI": paper.doi,
        "url": paper.url,
        "tags": [{"tag": "auto-imported"}],
    }
    payload = json.dumps([item]).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Zotero-API-Version": "3",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"Zotero 返回 {resp.status}")


# ── 主流程 ────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="文献邮件自动筛选并同步到 Zotero")
    parser.add_argument("--imap-host", default=os.getenv("IMAP_HOST", "imap.163.com"))
    parser.add_argument("--email-user", default=os.getenv("EMAIL_USER", ""))
    parser.add_argument("--email-password", default=os.getenv("EMAIL_PASSWORD", ""))
    parser.add_argument("--sender-filter", default=os.getenv("SENDER_FILTER", ""))
    parser.add_argument("--qwen-api-key", default=os.getenv("QWEN_API_KEY", ""))
    parser.add_argument("--zotero-user-id", default=os.getenv("ZOTERO_USER_ID", ""))
    parser.add_argument("--zotero-api-key", default=os.getenv("ZOTERO_API_KEY", ""))
    parser.add_argument("--screening-criteria", default=os.getenv("SCREENING_CRITERIA", ""))
    parser.add_argument("--min-score", type=float, default=float(os.getenv("MIN_SCORE", "75")))
    return parser.parse_args()


def run_once(ns):
    # 参数校验
    for field, val in [
        ("--email-user", ns.email_user),
        ("--email-password", ns.email_password),
        ("--qwen-api-key", ns.qwen_api_key),
        ("--zotero-user-id", ns.zotero_user_id),
        ("--zotero-api-key", ns.zotero_api_key),
    ]:
        if not val:
            raise SystemExit(f"缺少必填参数：{field}（或对应环境变量）")

    db = StateDB()

    messages = fetch_unseen(
        ns.imap_host, ns.email_user, ns.email_password,
        ns.sender_filter or None,
    )

    for msg_id, msg in messages:
        if db.seen_message(msg_id):
            print(f"  [跳过] 已处理邮件 {msg_id}")
            continue

        subject = decode_header_value(msg.get("Subject", "(无主题)"))
        print(f"\n📧 {subject}")
        body = get_body(msg)

        papers = extract_candidates(ns.qwen_api_key, body)
        print(f"  提取到论文: {len(papers)} 篇")

        for paper in papers:
            key = stable_paper_key(paper)
            if db.seen_paper(key):
                print(f"  [跳过] 已同步: {paper.title[:50]}")
                continue

            paper.score = score_paper(ns.qwen_api_key, paper, ns.screening_criteria)
            flag = "✅" if paper.score >= ns.min_score else "  "
            print(f"  {flag} {paper.score:.0f} | {paper.title[:60]}")

            if paper.score >= ns.min_score:
                try:
                    zotero_add(ns.zotero_user_id, ns.zotero_api_key, paper)
                    db.mark_paper(key)
                    print(f"       → 已写入 Zotero")
                except Exception as e:
                    print(f"       → Zotero 写入失败: {e}")

        db.mark_message(msg_id)

    print("\n完成。")


if __name__ == "__main__":
    ns = parse_args()
    run_once(ns)
