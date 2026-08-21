# Podsum Email Topic Map

This file is the editable EmailTopicMap for Podsum Email Summary.

It defines the topics the Hermes user is actively tracking. EmailIntelBrief
uses this object to organize the summary by tracked topics instead of producing
a broad inbox digest.

Topic v0.2 is distilled from the user's Hermes MEMORY.md. It keeps durable
tracking themes and deliberately avoids copying private memory prose verbatim.
The Markdown text is for humans; Podsum reads the fenced JSON block below.

```json
{
  "object_type": "email_topic_map",
  "version": 2,
  "source": "hermes_memory_sanitized",
  "default_behavior": "未命中 topic.md 的邮件只做低优先级补充；除非存在明确行动、安全、账单、交付或关系风险，不要把它写成主线。",
  "topics": [
    {
      "id": "ai_industry_agent_strategy",
      "name": "AI 行业 / Agent 战略",
      "priority": "high",
      "description": "用于收集会影响长期 AI 行业判断和个人 Agent 能力路线的邮件。重点不是普通 AI 新闻，而是会改变平台格局、工具链选择、端云分工或个人/小团队杠杆方式的内容。",
      "examples": [
        "OpenAI、Anthropic、Codex、Claude Code、Cursor、开源 Agent 框架发布影响工作流的新能力。",
        "AIPC、端侧模型、云端慢智能、AI Office、AI OS、AI DOS 这类平台级趋势文章。",
        "垂直行业 Codex、Skill、培训、Agent 工作台的真实案例或商业化信号。"
      ],
      "non_examples": [
        "只是在标题里提到 AI 的营销邮件。",
        "没有产品变化、架构变化或行业判断价值的模型榜单转发。"
      ],
      "keywords": ["ai", "agent", "智能体", "openai", "anthropic", "codex", "skill", "skills", "aipc", "ai office", "ai os", "ai dos", "ai windows", "开源", "垂直codex", "行业ai", "超级个体", "小团队", "外骨骼"],
      "aliases": ["AI行业推演", "AIPC", "AI时代里程碑", "垂直 Codex"],
      "summary_focus": "关注会影响 AI 产业判断、Agent 工作方式、端云分工、Codex/Skill 生态和个人 AI 杠杆路线的线索。"
    },
    {
      "id": "cema_topic_credit_system",
      "name": "CEMA / Topic / Credit",
      "priority": "high",
      "description": "用于收集 CEMA 的核心机制线索：Topic 如何沉淀需求和读者，Credit 如何衡量贡献，Service Project Community 如何从成熟 Topic 里长出来。",
      "examples": [
        "社区、知识库、Topic、贡献信用、服务项目共同体相关产品或案例。",
        "Agent 如何使用外部记忆、引用来源、追踪读者需求、形成服务闭环。",
        "GitHub、Notion、Discord、社区平台中可借鉴到 CEMA 的协作、信用或项目机制。"
      ],
      "non_examples": [
        "普通社区运营文章，但没有 Topic、信用、服务项目或 Agent 协作机制。",
        "只讲 GitHub 代码托管功能、不能迁移到 CEMA 关系/信用模型的更新。"
      ],
      "keywords": ["cema", "topic", "credit", "service project community", "ai-github", "github", "community", "agent 外部记忆", "外部记忆", "贡献信用", "服务闭环", "读者", "关系", "credit bank", "project community"],
      "aliases": ["CEMA", "Topic/Credit", "Service Project Community", "AI-GitHub framing"],
      "summary_focus": "关注 CEMA 的 Topic、关系、信用、服务项目共同体、Agent 外部记忆和贡献机制相关线索。"
    },
    {
      "id": "vis_nb_work_objects",
      "name": "VIS / NB / 可视化工作对象",
      "priority": "high",
      "description": "用于收集人和 AI 通过可视化工作对象协作的设计线索。重点是对象边界、对象之间关系、GUI 如何让用户审核/控制对象，而不是普通界面美化。",
      "examples": [
        "Workbench、dashboard、visual object、agent workspace、AI-native IDE 的对象模型和交互模式。",
        "能帮助定义 EvidencePack、TopicMap、Brief、Policy 这类核心对象边界的设计原则。",
        "NB、神笔马良、领域软件原语、可视化工作对象体系相关材料。"
      ],
      "non_examples": [
        "只有配色、动效或营销落地页的 UI 设计文章。",
        "只讲 workflow 自动化，但没有可视化工作对象或人机协作界面。"
      ],
      "keywords": ["vis", "vwoi", "visual work object", "work object", "可视化工作对象", "workbench", "gui", "nb", "神笔马良", "设计领域软件", "visual interaction", "workflow", "工作流", "对象体系", "dashboard"],
      "aliases": ["Visual Work Object Interaction System", "NB", "神笔马良", "Workbench"],
      "summary_focus": "关注人和 AI 通过可视化工作对象协作的对象边界、GUI 设计、领域软件原语和 Workbench 审核流。"
    },
    {
      "id": "education_pbl_ai_product",
      "name": "教育 / PBL / AI 美育产品",
      "priority": "high",
      "description": "用于收集教师侧 AI 产品、PBL 情境设计和 AI 美育系统的线索。重点是能进入产品设计、课程设计或教师工作台的内容。",
      "examples": [
        "PBL 情境导演台、情境创设、提问链、作品评价、复盘记忆相关案例。",
        "义务教育艺术课程标准、美育、项目式学习、教师备课/课堂 AI 工具。",
        "可以转化为一节“神笔马良 AI 课”的情境、任务、评价或复盘材料。"
      ],
      "non_examples": [
        "普通教育新闻，无法转化为课程/产品设计。",
        "只讲 AI 教育概念，但没有教师侧场景、课堂任务或作品评价。"
      ],
      "keywords": ["pbl", "情境创设", "情境导演台", "pbl 情境导演台", "教师", "美育", "艺术课程标准", "课程标准", "课堂", "提问链", "作品评价", "复盘记忆", "学习情境", "神笔马良 ai 课"],
      "aliases": ["PBL 情境导演台", "AI 美育", "情境创设"],
      "summary_focus": "关注教师侧 AI 产品、PBL 情境设计、课程标准术语、课堂任务链和 AI 美育系统化机会。"
    },
    {
      "id": "power_credit_ai_transition",
      "name": "Power 信贷 / AI 转型",
      "priority": "medium",
      "description": "用于收集 Power 信贷理论和 AI 转型框架相关线索。重点是能力、授信、外部权力、组织资源如何通过 AI 变成新的能力结构。",
      "examples": [
        "一人企业 OS、AI 转型、个人能力资产化、差异化能力授信相关内容。",
        "外部资源、渠道、组织权力如何被借入并转化为新的能力结构。",
        "能补充 Power A/B/C 概念、案例或反例的文章和邮件。"
      ],
      "non_examples": [
        "普通个人成长鸡汤，没有可授信能力、外部权力或 AI 转型结构。",
        "只讲融资、借贷或金融产品，但不能映射到 Power 信贷模型。"
      ],
      "keywords": ["power", "power a", "power b", "power c", "信贷", "授信", "差异化能力", "核心能力", "ai转型", "ai 转型", "一人企业", "一人企业os", "合作机器", "能力结构", "cema power"],
      "aliases": ["Power信贷理论", "一人企业 OS", "AI转型框架"],
      "summary_focus": "关注可被授信的差异化能力、外部权力借入、AI 转型路径、一人企业 OS 和 CEMA Power 信贷模型。"
    },
    {
      "id": "podsum_hermes_local_infra",
      "name": "Podsum / Hermes / 本地基础设施",
      "priority": "medium",
      "description": "用于收集会影响本地 AI 工具链稳定性和自动化产线的内容。重点是 Podsum、Hermes、OpenClaw、邮箱摘要、EPUB/Feishu 投递、macmini 运维。",
      "examples": [
        "Hermes、Podsum、OpenClaw、Codex、macmini、launchd、Feishu、EPUB 相关报错或更新。",
        "IMAP/Gmail/Exmail、邮件摘要、自动投递、fixture、Workbench 的运行问题。",
        "Tailscale、Headscale、服务器、代理、搜索/抓取 provider 的稳定性变化。"
      ],
      "non_examples": [
        "和本地自动化无关的通用云服务营销邮件。",
        "只讲工具功能但不会影响当前 Podsum/Hermes/OpenClaw 工作流。"
      ],
      "keywords": ["hermes", "podsum", "openclaw", "codex", "macmini", "imap", "gmail", "exmail", "qq", "feishu", "epub", "launchd", "tailscale", "headscale", "server", "ops", "运维", "自动化", "workflow", "email summary"],
      "aliases": ["Hermes", "Podsum", "OpenClaw", "macmini"],
      "summary_focus": "关注会影响本地 AI 工具链、邮件摘要、EPUB/Feishu 投递、自动任务、网络和部署稳定性的内容。"
    },
    {
      "id": "writing_delivery_quality",
      "name": "写作 / PRD / 交付质量",
      "priority": "medium",
      "description": "用于收集会影响交付物质量的写作、术语、PPT、PRD、EPUB 和格式兼容信息。重点是能改变具体输出标准或检查清单的内容。",
      "examples": [
        "PRD 术语、官方课标措辞、PPT/演讲稿审查、Winston 方法相关材料。",
        "Markdown/EPUB/微信读书兼容、文件名特殊字符、交付格式问题。",
        "需要直接改稿、统一术语或避免特定句式的写作规范。"
      ],
      "non_examples": [
        "泛泛的写作技巧文章，没有能落到当前交付标准的规则。",
        "只宣传模板或工具，但没有术语、结构、格式或质量门禁价值。"
      ],
      "keywords": ["prd", "ppt", "演讲稿", "winston", "改文", "重写", "术语", "epub", "微信读书", "markdown", "文档", "remove negation", "不在于", "而在于", "情景创设", "情境创设"],
      "aliases": ["Winston方法", "PRD术语", "改文", "EPUB兼容"],
      "summary_focus": "关注文档、PPT、PRD、EPUB、术语一致性和写作质量规则；特别留意需要直接改稿或避免禁用句式的内容。"
    },
    {
      "id": "personal_choice_org_strategy",
      "name": "个人选择 / 组织策略",
      "priority": "medium",
      "description": "用于收集需要个人判断、关键选择、组织关系处理或低冲突迁移的邮件。它也兜底真实待办、安全、账单、回复等必须处理事项。",
      "examples": [
        "涉及合作、分工、确权、退出、迁移、低冲突切割的组织/关系邮件。",
        "需要回复、确认、付款、验证、安全处理或排期的邮件。",
        "能补充“选择大于努力”方法论或关键选择判断的案例。"
      ],
      "non_examples": [
        "普通日程提醒，已经有明确安排且没有决策风险。",
        "泛泛个人成长内容，没有具体选择、关系、组织或行动压力。"
      ],
      "keywords": ["选择大于努力", "选择", "关键选择", "决策", "decision", "follow-up", "meeting", "组织", "权力", "博弈", "分手", "迁移", "低冲突", "资产化", "账单", "invoice", "receipt", "security", "验证", "回复"],
      "aliases": ["选择大于努力", "组织/权力博弈", "个人待办"],
      "summary_focus": "关注需要回复、确认、付款、安全处理、关系处理、组织策略或关键选择判断的邮件。"
    }
  ]
}
```
