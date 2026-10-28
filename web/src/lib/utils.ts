import { createCn } from "cn/config";

/**
 * clsx + Tailwind conflict merging, taught the design system's named type sizes (DESIGN 3.3).
 * Without this, `text-caption` and a text colour such as `text-faint` read as two colours and
 * the size is silently dropped.
 */
export const cn = createCn({
  extend: {
    classGroups: {
      "font-size": [{ text: ["caption", "nav", "answer", "page-label"] }],
    },
  },
});
