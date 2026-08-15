#!/usr/bin/env node
/**
 * Verify that every translation key the code asks for actually exists.
 *
 * Parity between tr.json and en.json is not enough: a key missing from *both*
 * files passes a parity check and then throws MISSING_MESSAGE in the browser —
 * which is exactly how `comparator.banksOffering` reached a running page. This
 * walks the source instead, resolves each `t("…")` call against the namespace
 * its variable was bound to, and checks the key is present in every locale.
 *
 * Dynamic keys (`t(\`category.${key}\`)`) cannot be resolved statically. They
 * are reported separately rather than ignored, so a namespace built by
 * interpolation is at least visible.
 *
 *   npm run i18n:check
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const SRC = join(ROOT, "src");
const MESSAGES = join(ROOT, "messages");

const locales = readdirSync(MESSAGES)
  .filter((f) => f.endsWith(".json"))
  .map((f) => [f.replace(/\.json$/, ""), JSON.parse(readFileSync(join(MESSAGES, f), "utf8"))]);

const has = (messages, dotted) => {
  let node = messages;
  for (const part of dotted.split(".")) {
    if (node === undefined || node === null || typeof node !== "object") return false;
    node = node[part];
  }
  return typeof node === "string";
};

function* sources(dir) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) yield* sources(path);
    else if (/\.(tsx?|jsx?)$/.test(path) && !path.endsWith(".test.ts")) yield path;
  }
}

const missing = [];
const dynamic = [];

for (const file of sources(SRC)) {
  const code = readFileSync(file, "utf8");

  // `const t = useTranslations("comparator")` -> t maps to "comparator".
  const namespaces = new Map();
  const bind = /(?:const|let)\s+(\w+)\s*=\s*(?:await\s+)?(?:useTranslations|getTranslations)\s*\(\s*["'`]([^"'`]*)["'`]\s*\)/g;
  for (const m of code.matchAll(bind)) namespaces.set(m[1], m[2]);
  if (namespaces.size === 0) continue;

  for (const [variable, ns] of namespaces) {
    const call = new RegExp(`\\b${variable}\\(\\s*(["'\`])([^"'\`]*)\\1`, "g");
    for (const m of code.matchAll(call)) {
      const key = m[2];
      const dotted = ns ? `${ns}.${key}` : key;
      const absent = locales.filter(([, messages]) => !has(messages, dotted)).map(([name]) => name);
      if (absent.length > 0) {
        missing.push({ file: relative(ROOT, file), key: dotted, locales: absent });
      }
    }

    // t(`category.${key}`) — the prefix is checkable even if the leaf is not.
    const templated = new RegExp(`\\b${variable}\\(\\s*\`([^\`]*\\$\\{[^\`]*)\``, "g");
    for (const m of code.matchAll(templated)) {
      dynamic.push({ file: relative(ROOT, file), pattern: `${ns}.${m[1]}` });
    }
  }
}

if (dynamic.length > 0) {
  console.log(`\n${dynamic.length} dynamic key(s), not statically checkable:`);
  for (const d of dynamic) console.log(`   ${d.file}  ${d.pattern}`);
}

// `call` matches backtick strings too, so `t(\`why.${entry.why}\`)` is caught
// once here with its literal `${…}` still inside the key, and again by
// `templated` above. It is the same call, reported twice, never a real static
// key -- exclude anything the interpolation marker survived into.
const realMissing = missing.filter((m) => !m.key.includes("${"));

if (realMissing.length > 0) {
  console.error(`\n${missing.length} missing translation key(s):`);
  for (const m of missing) {
    console.error(`   ${m.file}\n      ${m.key}  — absent in: ${m.locales.join(", ")}`);
  }
  console.error("");
  process.exit(1);
}

console.log(`\nAll translation keys resolve in: ${locales.map(([n]) => n).join(", ")}.`);
