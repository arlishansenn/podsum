# Podsum Email Link Policy

This file is the editable EmailPolicy spec for Podsum Email Summary.

It defines how an EmailEvidencePack classifies email items and whether links
inside those items should be enriched. In the VIS model it is edited through
the core EmailPolicyPanel visual work object. The Markdown text is for humans;
Podsum reads the fenced JSON block below.

```json
{
  "object_type": "email_policy",
  "version": 1,
  "limits": {
    "max_links_per_email": 2,
    "max_links_total": 10,
    "timeout_seconds": 8,
    "excerpt_chars": 1200
  },
  "skip_url_patterns": [
    "unsubscribe",
    "optout",
    "tracking",
    "track",
    "pixel",
    "login",
    "signin",
    "calendar",
    ".ics",
    "attachment",
    "download"
  ],
  "email_types": [
    {
      "name": "google_alert",
      "fetch_links": true,
      "match": {
        "subject_contains": ["google快讯", "google alert", "alert"],
        "from_contains": ["alerts"]
      },
      "summary_focus": "提炼 alert 中真正值得知道的新线索。"
    },
    {
      "name": "newsletter_article",
      "fetch_links": true,
      "match": {
        "subject_contains": ["newsletter", "digest", "weekly", "日报", "周报"],
        "snippet_contains": ["read more", "source:", "https://"]
      },
      "summary_focus": "优先读取公开文章链接，判断是否值得行动或记录。"
    },
    {
      "name": "digest",
      "fetch_links": true,
      "match": {
        "subject_contains": ["digest", "roundup", "汇总", "精选"]
      },
      "summary_focus": "从多条链接里识别最高价值条目。"
    },
    {
      "name": "personal",
      "fetch_links": false,
      "match": {
        "subject_contains": ["follow-up", "re:", "回复"],
        "snippet_contains": ["follow-up", "decision", "meeting"]
      },
      "summary_focus": "关注是否需要回复或做决定。"
    },
    {
      "name": "transactional",
      "fetch_links": false,
      "match": {
        "subject_contains": ["receipt", "invoice", "security", "验证", "账单", "登录"]
      },
      "summary_focus": "只提炼账号、账单、安全和到期风险。"
    },
    {
      "name": "marketing_low_signal",
      "fetch_links": false,
      "match": {
        "subject_contains": ["sale", "discount", "promo", "优惠", "促销"]
      },
      "summary_focus": "默认低信号，除非 snippet 显示明确行动价值。"
    }
  ]
}
```
