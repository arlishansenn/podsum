---
name: send-file
description: 发送本地文件到用户当前会话所在平台。自动检测来源（Discord/飞书/Local），路由到对应后端。支持 .epub/.pdf/.docx 等非图片附件，避免"只回文本路径"的假成功。
---

# Send File（来源感知）

## 什么时候用
- 用户说"把这个文件发过来/发给我"
- 需要把 `.epub/.pdf/.docx/.xlsx/.pptx/.zip` 等非图片附件发给用户
- 需要避免仅发送文本路径（看似已发送，实际无附件）

## 核心原则：从哪来，回哪去

**不要硬编码飞书。** 先看当前会话的 `Source:` 字段，决定发送方式：

| 来源 | 发送方式 |
|------|---------|
| Discord | 在回复里包含 `MEDIA:/absolute/path/to/file` |
| 飞书 | 走飞书文件上传 API（`send_feishu_file.py`） |
| Local | 不做额外操作，告知用户文件路径即可 |

## 执行流程

### 1) 校验文件存在，并准备安全文件名
```bash
python3 ~/.hermes/skills/social-media/hermes-feishu-file-send/scripts/safe_file_for_send.py \
  --file '/absolute/path/to/file.ext'
```

脚本会输出 JSON：
- `path_to_send`: 实际要发送的文件路径
- `renamed`: 是否因为文件名含 `&`、冒号等风险字符而复制成安全文件名

**重要：发送时必须使用 `path_to_send`，不要继续用原路径。**
微信读书可能在导入阶段因为 EPUB 文件名包含 `&` 而打不开，即使 EPUB 内部内容完全正常。

### 2) 看当前会话来源，选择发送方式

**如果是 Discord：**
先运行 `safe_file_for_send.py`，然后在回复里写 `MEDIA:{path_to_send}`，Hermes 会自动把文件作为附件发出。不需要调发送脚本。

**如果是飞书：**
```bash
set -a && . ~/.hermes/.env && set +a && \
python3 ~/.hermes/skills/social-media/hermes-feishu-file-send/scripts/send_feishu_file.py \
  --file '{path_to_send}' \
  --chat-id 'oc_xxx'
```

> 如果用户给的是 `ou_xxx`，用 `--user-id 'ou_xxx'` 替代 `--chat-id`。

### 3) 成功判定

| 平台 | 成功标志 |
|------|---------|
| Discord | `MEDIA:` 已包含在回复中，用户能看到附件 |
| 飞书 | 脚本输出 JSON 含 `ok: true`、`file_key`、`message_id` |
| Local | 文件已存在且路径正确 |

## 回复模板

成功后给用户：
- 已发送文件名
- 发送平台
- 飞书：`message_id`；Discord：回复里直接带附件

失败后给用户：
- 失败原因
- 你将采取的下一步

## 注意事项
1. 必须使用**绝对路径**。
2. 不要把"文本路径回显"当成已发送附件。
3. Discord 用 `MEDIA:` 即可，不需要额外脚本。
4. 飞书若 `FEISHU_DOMAIN=lark`，脚本会自动切到 `open.larksuite.com`。
5. 飞书脚本默认禁用环境代理（`trust_env=False`）。
6. 当前 macOS 环境可能缺少 `requests`；若报 `ModuleNotFoundError: requests`，先执行：`python3 -m pip install --user requests`。