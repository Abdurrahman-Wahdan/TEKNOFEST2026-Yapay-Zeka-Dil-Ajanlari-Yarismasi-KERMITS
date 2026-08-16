"use client";

import { AppPage } from "@/components/layout/AppPage";
import { CategoryComponents } from "@/components/widgets/CategoryComponents";

/**
 * The shell every topic page uses.
 *
 * A new topic page is a route file and one line: the category. The producer's
 * content, the table, the filters and the failure states all come from
 * `CategoryComponents`; the frame comes from `AppPage`.
 */
export function TopicPage({ category }: { category: string }) {
  return (
    <AppPage>
      <CategoryComponents category={category} />
    </AppPage>
  );
}
