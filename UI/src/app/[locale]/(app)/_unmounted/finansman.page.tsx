import { setRequestLocale } from "next-intl/server";

import { TopicPage } from "@/components/layout/TopicPage";

/**
 * Finansman.
 *
 * The RAG zone only, for now. The live comparator — the deterministic software
 * that prices this family across seven banks — arrives as a second zone above
 * it in a later slice; the two are kept apart on purpose, because a figure read
 * off a bank's website and a figure returned by its calculator are different
 * kinds of fact and must never share a table.
 */
export default async function FinansmanPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return <TopicPage category="finansman" />;
}
