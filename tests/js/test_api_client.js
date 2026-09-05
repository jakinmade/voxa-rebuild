/**
 * tests/js/test_api_client.js — real, executed coverage for the two
 * refresh-handling fixes made to shared/api_client.js:
 *
 *   1. A lost refresh race (backend error_code "token_expired") must
 *      not clear stored credentials — only "token_revoked" may.
 *   2. Two callers racing to refresh (the proactive alarm and the
 *      reactive retry-once path) must share one in-flight network
 *      call, not fire two.
 *
 * Run with: node --test tests/js
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadClassicScript } = require("./load_classic_script");

function makeStorageMock({ installation = null, accessToken = null } = {}) {
  const state = { installation, accessToken, clearAllCalls: 0 };
  return {
    state,
    getInstallation: async () => state.installation,
    setInstallation: async (v) => {
      state.installation = v;
    },
    getAccessToken: async () => state.accessToken,
    setAccessToken: async (v) => {
      state.accessToken = v;
    },
    clearAll: async () => {
      state.clearAllCalls += 1;
      state.installation = null;
      state.accessToken = null;
    },
  };
}

function makeFetchMock(responses) {
  // `responses` is an array; each call to fetch() shifts the next one
  // off, so a test can script exactly what each successive call sees
  // (e.g. two concurrent calls should only ever consume one entry if
  // the in-flight-sharing fix works).
  const calls = [];
  const fn = async (url, opts) => {
    calls.push({ url, opts });
    if (responses.length === 0) {
      throw new Error("fetch mock called more times than scripted");
    }
    const next = responses.shift();
    if (next.throws) throw next.throws;
    return {
      ok: next.ok,
      json: async () => next.body,
    };
  };
  fn.calls = calls;
  return fn;
}

function loadApiClient({ storage, fetchMock }) {
  return loadClassicScript("shared/api_client.js", {
    VoicovaStorage: storage,
    fetch: fetchMock,
  });
}

test("successful refresh stores the new access token and refresh handle", async () => {
  const storage = makeStorageMock({
    installation: { installationId: "inst-1", refreshHandle: "old-handle" },
    accessToken: "old-token",
  });
  const fetchMock = makeFetchMock([
    { ok: true, body: { access_token: "new-token", refresh_handle: "new-handle" } },
  ]);
  const sandbox = loadApiClient({ storage, fetchMock });

  const result = await sandbox.VoicovaApiClient.refreshAccessToken();

  assert.equal(result, true);
  assert.equal(storage.state.accessToken, "new-token");
  assert.equal(storage.state.installation.refreshHandle, "new-handle");
  assert.equal(storage.state.installation.installationId, "inst-1"); // unchanged
  assert.equal(storage.state.clearAllCalls, 0);
});

test("token_expired (lost race) does not clear credentials", async () => {
  const storage = makeStorageMock({
    installation: { installationId: "inst-1", refreshHandle: "old-handle" },
    accessToken: "old-token",
  });
  const fetchMock = makeFetchMock([
    { ok: false, body: { detail: { error_code: "token_expired" } } },
  ]);
  const sandbox = loadApiClient({ storage, fetchMock });

  const result = await sandbox.VoicovaApiClient.refreshAccessToken();

  assert.equal(result, true, "a lost race should be treated as recoverable, not fatal");
  assert.equal(storage.state.clearAllCalls, 0);
  assert.equal(storage.state.accessToken, "old-token", "storage untouched by the loser");
});

test("token_revoked clears credentials", async () => {
  const storage = makeStorageMock({
    installation: { installationId: "inst-1", refreshHandle: "old-handle" },
    accessToken: "old-token",
  });
  const fetchMock = makeFetchMock([
    { ok: false, body: { detail: { error_code: "token_revoked" } } },
  ]);
  const sandbox = loadApiClient({ storage, fetchMock });

  const result = await sandbox.VoicovaApiClient.refreshAccessToken();

  assert.equal(result, false);
  assert.equal(storage.state.clearAllCalls, 1);
  assert.equal(storage.state.installation, null);
});

test("an unrecognised or malformed error body fails without destroying credentials", async () => {
  const storage = makeStorageMock({
    installation: { installationId: "inst-1", refreshHandle: "old-handle" },
    accessToken: "old-token",
  });
  // Body has no `detail.error_code` at all — e.g. a proxy 502 page,
  // or an API contract change this client doesn't know about yet.
  const fetchMock = makeFetchMock([{ ok: false, body: {} }]);
  const sandbox = loadApiClient({ storage, fetchMock });

  const result = await sandbox.VoicovaApiClient.refreshAccessToken();

  assert.equal(result, false);
  assert.equal(storage.state.clearAllCalls, 0, "an unrecognised failure must not be treated as revocation");
  assert.equal(storage.state.accessToken, "old-token");
});

test("a network failure fails without destroying credentials", async () => {
  const storage = makeStorageMock({
    installation: { installationId: "inst-1", refreshHandle: "old-handle" },
    accessToken: "old-token",
  });
  const fetchMock = makeFetchMock([{ throws: new Error("network down") }]);
  const sandbox = loadApiClient({ storage, fetchMock });

  const result = await sandbox.VoicovaApiClient.refreshAccessToken();

  assert.equal(result, false);
  assert.equal(storage.state.clearAllCalls, 0);
});

test("no stored installation short-circuits without calling fetch", async () => {
  const storage = makeStorageMock({ installation: null, accessToken: null });
  const fetchMock = makeFetchMock([]); // any call here is a bug — throws immediately
  const sandbox = loadApiClient({ storage, fetchMock });

  const result = await sandbox.VoicovaApiClient.refreshAccessToken();

  assert.equal(result, false);
  assert.equal(fetchMock.calls.length, 0);
});

test("two concurrent refreshAccessToken calls share one in-flight network request", async () => {
  const storage = makeStorageMock({
    installation: { installationId: "inst-1", refreshHandle: "old-handle" },
    accessToken: "old-token",
  });
  // Only ONE response is scripted. If the fix regressed and each
  // caller fired its own fetch, the second call would find the
  // responses array empty and throw — failing this test.
  const fetchMock = makeFetchMock([
    { ok: true, body: { access_token: "new-token", refresh_handle: "new-handle" } },
  ]);
  const sandbox = loadApiClient({ storage, fetchMock });

  const [first, second] = await Promise.all([
    sandbox.VoicovaApiClient.refreshAccessToken(),
    sandbox.VoicovaApiClient.refreshAccessToken(),
  ]);

  assert.equal(first, true);
  assert.equal(second, true);
  assert.equal(fetchMock.calls.length, 1, "both concurrent callers must share one network call");
  assert.equal(storage.state.accessToken, "new-token");
});

test("a later refresh call after one completes fires a new network request", async () => {
  // Guards against the in-flight cache accidentally never clearing —
  // a real second refresh (e.g. the next scheduled alarm) must still
  // reach the network, not silently reuse a stale finished promise.
  const storage = makeStorageMock({
    installation: { installationId: "inst-1", refreshHandle: "handle-1" },
    accessToken: "token-1",
  });
  const fetchMock = makeFetchMock([
    { ok: true, body: { access_token: "token-2", refresh_handle: "handle-2" } },
    { ok: true, body: { access_token: "token-3", refresh_handle: "handle-3" } },
  ]);
  const sandbox = loadApiClient({ storage, fetchMock });

  await sandbox.VoicovaApiClient.refreshAccessToken();
  await sandbox.VoicovaApiClient.refreshAccessToken();

  assert.equal(fetchMock.calls.length, 2);
  assert.equal(storage.state.accessToken, "token-3");
});
