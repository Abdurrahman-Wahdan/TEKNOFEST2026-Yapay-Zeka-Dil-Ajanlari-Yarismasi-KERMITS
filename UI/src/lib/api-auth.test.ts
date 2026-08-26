import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import {
  api,
  ApiError,
  setAccessToken,
  setAuthSessionHooks,
  type TokenPair,
} from "./api.ts";

const originalFetch = globalThis.fetch;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function tokenPair(suffix: string): TokenPair {
  return {
    access_token: `access-${suffix}`,
    refresh_token: `refresh-${suffix}`,
    token_type: "bearer",
    expires_in: 1800,
  };
}

function wireSession(onExpired: () => void = () => undefined) {
  setAuthSessionHooks({
    getRefreshToken: () => ({ token: "refresh-old", remember: true }),
    applyTokens: (tokens) =>
      setAccessToken(tokens.access_token, tokens.expires_in),
    onSessionExpired: onExpired,
  });
}

afterEach(() => {
  globalThis.fetch = originalFetch;
  setAccessToken(null);
  setAuthSessionHooks(null);
});

describe("authenticated API transport", () => {
  it("refreshes an expired access token and replays the request once", async () => {
    let refreshCalls = 0;
    let resourceCalls = 0;
    setAccessToken("access-old", 1);
    wireSession();

    globalThis.fetch = (async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/refresh")) {
        refreshCalls += 1;
        return json(tokenPair("new"));
      }
      resourceCalls += 1;
      const authorization = new Headers(init?.headers).get("Authorization");
      if (authorization === "Bearer access-old") {
        return json({ detail: "Not authenticated." }, 401);
      }
      assert.equal(authorization, "Bearer access-new");
      return json({ id: "user-1", email: "user@example.com" });
    }) as typeof fetch;

    const user = await api.me();
    assert.equal(user.id, "user-1");
    assert.equal(refreshCalls, 1);
    assert.equal(resourceCalls, 2);
  });

  it("shares one refresh across concurrent 401 responses", async () => {
    let refreshCalls = 0;
    setAccessToken("access-old", 1);
    wireSession();

    globalThis.fetch = (async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/refresh")) {
        refreshCalls += 1;
        await new Promise((resolve) => setTimeout(resolve, 10));
        return json(tokenPair("new"));
      }
      const authorization = new Headers(init?.headers).get("Authorization");
      return authorization === "Bearer access-new"
        ? json({ id: "user-1", email: "user@example.com" })
        : json({ detail: "Not authenticated." }, 401);
    }) as typeof fetch;

    await Promise.all([api.me(), api.me(), api.me(), api.me()]);
    assert.equal(refreshCalls, 1);
  });

  it("expires the visible session when the refresh token is rejected", async () => {
    let expired = 0;
    setAccessToken("access-old", 1);
    wireSession(() => {
      expired += 1;
      setAccessToken(null);
    });

    globalThis.fetch = (async (input) =>
      String(input).endsWith("/auth/refresh")
        ? json({ detail: "Incorrect email or password." }, 401)
        : json({ detail: "Not authenticated." }, 401)) as typeof fetch;

    await assert.rejects(
      api.me(),
      (error: unknown) => error instanceof ApiError && error.status === 401,
    );
    assert.equal(expired, 1);
  });

  it("expires the session if the freshly issued access token is also rejected", async () => {
    let expired = 0;
    setAccessToken("access-old", 1);
    wireSession(() => {
      expired += 1;
      setAccessToken(null);
    });

    globalThis.fetch = (async (input) =>
      String(input).endsWith("/auth/refresh")
        ? json(tokenPair("new"))
        : json({ detail: "Not authenticated." }, 401)) as typeof fetch;

    await assert.rejects(
      api.me(),
      (error: unknown) => error instanceof ApiError && error.status === 401,
    );
    assert.equal(expired, 1);
  });

  it("does not log out for a temporary refresh-service failure", async () => {
    let expired = 0;
    setAccessToken("access-old", 1);
    wireSession(() => {
      expired += 1;
    });

    globalThis.fetch = (async (input) =>
      String(input).endsWith("/auth/refresh")
        ? json({ detail: "Temporarily unavailable." }, 503)
        : json({ detail: "Not authenticated." }, 401)) as typeof fetch;

    await assert.rejects(
      api.me(),
      (error: unknown) => error instanceof ApiError && error.status === 503,
    );
    assert.equal(expired, 0);
  });

  it("never refreshes a rejected login request", async () => {
    let refreshCalls = 0;
    setAccessToken("access-old", 1);
    wireSession();

    globalThis.fetch = (async (input) => {
      if (String(input).endsWith("/auth/refresh")) refreshCalls += 1;
      return json({ detail: "Incorrect email or password." }, 401);
    }) as typeof fetch;

    await assert.rejects(
      api.login({ email: "x@example.com", password: "wrong" }),
      (error: unknown) => error instanceof ApiError && error.status === 401,
    );
    assert.equal(refreshCalls, 0);
  });
});
