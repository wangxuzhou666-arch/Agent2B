import { render } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { requestLanguage, setRequestLocale } from "@/lib/api";

import { IntlProviders } from "../../../test/render";
import { RequestLocale } from "./request-locale";

afterEach(() => setRequestLocale("en"));

/** Stands in for a view that fetches on mount: records the language its request would ask for. */
function Fetcher({ seen }: { seen: string[] }) {
  useEffect(() => {
    seen.push(requestLanguage());
  }, [seen]);
  return null;
}

describe("RequestLocale", () => {
  it("keeps the API's Accept-Language on the active locale, and follows a switch", () => {
    const view = render(
      <IntlProviders locale="zh">
        <RequestLocale />
      </IntlProviders>,
    );
    expect(requestLanguage()).toBe("zh-CN");
    view.rerender(
      <IntlProviders locale="en">
        <RequestLocale />
      </IntlProviders>,
    );
    expect(requestLanguage()).toBe("en");
  });

  it("is in place before any view's first fetch, wherever that view sits", () => {
    const seen: string[] = [];
    render(
      <IntlProviders locale="zh">
        <Fetcher seen={seen} />
        <RequestLocale />
        <div>
          <Fetcher seen={seen} />
        </div>
      </IntlProviders>,
    );
    expect(seen).toEqual(["zh-CN", "zh-CN"]);
  });
});
