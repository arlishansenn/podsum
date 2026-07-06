#!/usr/bin/env node

const { simpleParser } = require("mailparser");
const { JSDOM } = require("jsdom");

const SNIPPET_CHARS = 900;

const genericTextPatterns = [
  /\bview in browser\b/i,
  /\bgo to my news\b/i,
  /\bmanage keywords\b/i,
  /\bmanage email preferences\b/i,
  /\bunsubscribe\b/i,
  /\bsponsor(?:ing)? our media\b/i,
  /\bplease do not reply\b/i,
  /\bno reproduction without permission\b/i,
  /\bdiscover the nikkei asia app\b/i,
  /\bupdate your email setting\b/i,
  /\bmarked as irrelevant\b/i,
  /标记为不相关/,
  /查看更多结果/,
  /修改此快讯/,
  /退订/,
  /查看您所有的快讯/,
  /以RSS Feed的形式接收/,
  /发送反馈/,
];

const disposablePhrases = [
  "Here are new articles that match your following keywords.",
  "标记为不相关",
  "Marked as irrelevant",
  "Share this result",
];

const leadingNoisePatterns = [
  /^\s*Read Online\s*\|\s*Sign Up\s*\|\s*Advertise\s*/i,
  /^\s*View it in your browser\.\s*-\s*Is this email not displaying correctly\?\s*/i,
  /^\s*Is this email not displaying correctly\?\s*/i,
  /^\s*Update your preferences\s*[-|:]?\s*/i,
  /^\s*Manage preferences\s*[-|:]?\s*/i,
  /^\s*Manage your preferences\s*[-|:]?\s*/i,
];

const lowSignalTailPatterns = [
  /\bSee more notes in the Substack app\b/i,
  /©\s*\d{4}\s+Substack Inc\./i,
  /\b548 Market Street\b/i,
  /\bUnsubscribe\b/i,
  /\bManage preferences\b/i,
  /\bManage your preferences\b/i,
  /\bUpdate your preferences\b/i,
  /\bPrivacy Policy\b/i,
];

function normalizeText(value) {
  return String(value || "")
    .replace(/[\u200B-\u200D\uFEFF\u00AD\u034F]/g, "")
    .replace(/[\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069]/g, "")
    .replace(/\u00A0/g, " ")
    .replace(/[ \t\f\v]+/g, " ")
    .replace(/\s*\n\s*/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function cleanText(value, limit = 1000) {
  let text = normalizeText(value);
  for (const phrase of disposablePhrases) {
    text = text.split(phrase).join(" ");
  }
  for (const pattern of leadingNoisePatterns) {
    text = text.replace(pattern, " ");
  }
  for (const pattern of lowSignalTailPatterns) {
    const match = pattern.exec(text);
    if (match && match.index >= 0) {
      text = match.index === 0 ? text.slice(match.index + match[0].length) : text.slice(0, match.index);
    }
  }
  text = normalizeText(text);
  if (text.length <= limit) return text;
  return `${text.slice(0, limit).trimEnd()}...`;
}

function isGenericText(text) {
  const cleaned = cleanText(text, 500);
  if (!cleaned) return true;
  if (cleaned.length <= 3) return true;
  if (/^here$/i.test(cleaned)) return true;
  if (/^[|.\-_\s]+$/.test(cleaned)) return true;
  return genericTextPatterns.some((pattern) => pattern.test(cleaned));
}

function isHiddenElement(element) {
  if (!element || element.nodeType !== 1) return false;
  if (element.hasAttribute("hidden")) return true;
  if ((element.getAttribute("aria-hidden") || "").toLowerCase() === "true") return true;
  const style = (element.getAttribute("style") || "").toLowerCase();
  return (
    /display\s*:\s*none/.test(style) ||
    /visibility\s*:\s*hidden/.test(style) ||
    /opacity\s*:\s*0(?:[;\s]|$)/.test(style) ||
    /mso-hide\s*:\s*all/.test(style) ||
    /max-height\s*:\s*0/.test(style) ||
    /font-size\s*:\s*0/.test(style) ||
    /line-height\s*:\s*0/.test(style)
  );
}

function pruneDocument(document) {
  document.querySelectorAll("script, style, noscript, template, meta, link").forEach((node) => node.remove());
  [...document.querySelectorAll("*")].forEach((element) => {
    if (isHiddenElement(element)) element.remove();
  });
}

function canonicalHref(href) {
  if (!href) return "";
  try {
    const url = new URL(href);
    if (!/^https?:$/.test(url.protocol)) return "";
    if (url.hostname.includes("google.") && url.pathname === "/url" && url.searchParams.get("url")) {
      return url.searchParams.get("url");
    }
    return url.toString();
  } catch {
    return "";
  }
}

function isLowSignalHref(href) {
  if (!href) return true;
  let url;
  try {
    url = new URL(href);
  } catch {
    return true;
  }
  const full = url.toString().toLowerCase();
  const pathName = url.pathname.toLowerCase();
  if (url.hostname === "help.asia.nikkei.com") return true;
  if (full.includes("help.asia.nikkei.com")) return true;
  if (full.includes("unsubscribe")) return true;
  if (full.includes("email-preference")) return true;
  if (full.includes("email_setting")) return true;
  if (full.includes("view-in-browser")) return true;
  if (url.hostname.includes("google.") && /\/alerts\/(share|feedback|remove|edit)\b/.test(pathName)) return true;
  if (url.hostname.includes("google.") && /\/alerts\/feeds\b/.test(pathName)) return true;
  return false;
}

function clickHost(href) {
  try {
    return new URL(href).hostname.toLowerCase();
  } catch {
    return "";
  }
}

function nearestUsefulBlock(anchor) {
  let element = anchor;
  let best = anchor;
  while (element && element.parentElement && element.tagName !== "BODY") {
    const text = cleanText(element.textContent, 1200);
    const links = element.querySelectorAll ? element.querySelectorAll("a[href]").length : 0;
    if (text.length >= 30 && text.length <= 900 && links <= 8) {
      best = element;
    }
    if (text.length > 900 || links > 8) break;
    element = element.parentElement;
  }
  return best;
}

function uniquePush(list, value) {
  const text = cleanText(value, 1000);
  if (text && !list.includes(text)) list.push(text);
}

function chooseTitle(anchorTexts, contexts) {
  const usefulAnchor = anchorTexts.find((text) => !isGenericText(text) && text.length >= 8 && text.length <= 220);
  if (usefulAnchor) return usefulAnchor;
  const context = contexts.find((text) => text.length >= 20) || "";
  return context.slice(0, 160);
}

function buildExcerpt(title, anchorTexts, contexts) {
  const byLength = [...contexts].sort((a, b) => b.length - a.length);
  let excerpt = byLength.find((text) => text.includes(title) && text.length > title.length) || byLength[0] || "";
  excerpt = cleanText(excerpt.replace(title, " "), 700);
  for (const text of anchorTexts) {
    if (text !== title && !isGenericText(text) && text.length > 12 && !excerpt.includes(text)) {
      excerpt = cleanText(`${text}. ${excerpt}`, 700);
    }
  }
  for (const pattern of genericTextPatterns) {
    excerpt = cleanText(excerpt.replace(pattern, " "), 700);
  }
  return cleanText(excerpt.replace(/\s+\.\s+/g, ". "), 520);
}

function scoreItem(item) {
  let score = 0;
  if (item.title.length >= 20) score += 3;
  if (item.excerpt.length >= 30) score += 2;
  if (/[\u4E00-\u9FFF]/.test(item.title + item.excerpt)) score += 1;
  if (/\b[A-Z][a-z]+/.test(item.title + item.excerpt)) score += 1;
  if (isGenericText(item.title)) score -= 5;
  if (genericTextPatterns.some((pattern) => pattern.test(`${item.title} ${item.excerpt}`))) score -= 3;
  return score;
}

function isNikkeiClickHost(hostname) {
  return hostname.endsWith(".namail.nikkei.com") || hostname === "namail.nikkei.com";
}

function extractNikkeiLikeItems(records) {
  const items = [];
  const seenTitles = new Set();
  for (let index = 0; index < records.length; index += 1) {
    const record = records[index];
    if (!isNikkeiClickHost(record.host)) continue;
    if (isGenericText(record.text)) continue;
    if (record.text.startsWith("https://")) continue;

    const next = records[index + 1];
    let excerpt = "";
    if (
      next &&
      isNikkeiClickHost(next.host) &&
      !isGenericText(next.text) &&
      next.text !== record.text &&
      !next.text.startsWith("https://")
    ) {
      excerpt = next.text;
      index += 1;
    } else {
      excerpt = buildExcerpt(record.text, [record.text], [record.context]);
    }

    const title = record.text;
    if (seenTitles.has(title)) continue;
    seenTitles.add(title);
    const item = { title, href: record.href, excerpt, order: record.order };
    item.score = scoreItem(item);
    if (item.score >= 2) items.push(item);
  }
  return items.slice(0, 12);
}

function extractHtmlBlocks(html) {
  const dom = new JSDOM(html);
  const document = dom.window.document;
  pruneDocument(document);

  const records = [];
  const groups = new Map();
  let order = 0;
  for (const anchor of document.querySelectorAll("a[href]")) {
    const href = canonicalHref(anchor.href);
    if (isLowSignalHref(href)) continue;

    const anchorText = cleanText(anchor.textContent, 240);
    if (isGenericText(anchorText)) continue;

    const block = nearestUsefulBlock(anchor);
    const context = cleanText(block.textContent, 900);
    if (context.length < 20 && anchorText.length < 8) continue;

    const record = { href, host: clickHost(href), text: anchorText, context, order: order++ };
    records.push(record);

    const group = groups.get(href) || { href, anchorTexts: [], contexts: [], order: groups.size };
    uniquePush(group.anchorTexts, anchorText);
    uniquePush(group.contexts, context);
    groups.set(href, group);
  }

  if (records.some((record) => isNikkeiClickHost(record.host))) {
    const nikkeiItems = extractNikkeiLikeItems(records);
    if (nikkeiItems.length) return nikkeiItems;
  }

  return [...groups.values()]
    .map((group) => {
      const title = chooseTitle(group.anchorTexts, group.contexts);
      const excerpt = buildExcerpt(title, group.anchorTexts, group.contexts);
      const item = { title, href: group.href, excerpt, order: group.order };
      return { ...item, score: scoreItem(item) };
    })
    .filter((item) => item.score >= 2 && !isGenericText(item.title))
    .sort((a, b) => a.order - b.order)
    .slice(0, 12);
}

function cleanPlainLines(text) {
  return [...new Set(
    String(text || "")
      .split(/\r?\n/)
      .map((line) => cleanText(line, 1000))
      .filter(Boolean)
      .filter((line) => !isGenericText(line))
  )].slice(0, 20);
}

function snippetFromBlocks(blocks, plainLines) {
  const lines = blocks.length
    ? blocks.map((block) => cleanText([block.title, block.excerpt].filter(Boolean).join(" - "), 420))
    : plainLines;
  return cleanText(lines.filter(Boolean).join("\n"), SNIPPET_CHARS);
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const raw = Buffer.concat(chunks);
  const parsed = await simpleParser(raw);
  const html = typeof parsed.html === "string" ? parsed.html : "";
  const blocks = html ? extractHtmlBlocks(html) : [];
  const plainLines = blocks.length ? [] : cleanPlainLines(parsed.text || "");
  const links = blocks.map((block, index) => ({
    url: block.href,
    anchor_text: block.title,
    context: block.excerpt || block.title,
    source_content_type: "text/html",
    position: String(index),
  }));

  const bodyPartTypes = [];
  if (parsed.text) bodyPartTypes.push("text/plain");
  if (html) bodyPartTypes.push("text/html");

  const result = {
    snippet: snippetFromBlocks(blocks, plainLines),
    links,
    body_part_count: bodyPartTypes.length,
    body_part_types: bodyPartTypes,
    attachment_count: parsed.attachments.length,
    attachment_shapes: parsed.attachments.map((attachment) => ({
      content_type: attachment.contentType || "application/octet-stream",
      size_bytes: attachment.size || (attachment.content ? attachment.content.length : 0),
    })),
  };
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exit(1);
});
