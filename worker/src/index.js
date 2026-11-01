const API_BASE = "https://api.marketapp.org/v1";

const ROUTES = {
  "/gifts": { target: `${API_BASE}/rent/gifts/`, ttl: 8 },
  "/collections": { target: `${API_BASE}/collections/gifts/`, ttl: 300 },
};

const ALLOWED_PARAMS = ["limit", "cursor", "collection_address", "sort", "order"];

function corsHeaders(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

export default {
  async fetch(request, env, ctx) {
    const cors = corsHeaders(env);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    if (request.method !== "GET") {
      return new Response("Method not allowed", { status: 405, headers: cors });
    }

    const url = new URL(request.url);
    const route = ROUTES[url.pathname];

    if (!route) {
      return new Response("Not found", { status: 404, headers: cors });
    }

    if (!env.MARKETAPP_API_TOKEN) {
      return new Response(
        JSON.stringify({ error: "MARKETAPP_API_TOKEN is not set" }),
        { status: 500, headers: { ...cors, "Content-Type": "application/json" } }
      );
    }

    // فقط پارامترهای مجاز به API اصلی ارسال می‌شوند
    const upstream = new URL(route.target);
    for (const key of ALLOWED_PARAMS) {
      const value = url.searchParams.get(key);
      if (value) upstream.searchParams.set(key, value);
    }

    const cache = caches.default;
    const cacheKey = new Request(upstream.toString(), { method: "GET" });

    let response = await cache.match(cacheKey);

    if (!response) {
      const apiResponse = await fetch(upstream.toString(), {
        headers: {
          Authorization: env.MARKETAPP_API_TOKEN,
          Accept: "application/json",
          "User-Agent": "Marketapp-Gift-Proxy/1.0",
        },
      });

      const body = await apiResponse.text();

      response = new Response(body, {
        status: apiResponse.status,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": `public, max-age=${route.ttl}`,
        },
      });

      // فقط پاسخ موفق کش می‌شود
      if (apiResponse.ok) {
        ctx.waitUntil(cache.put(cacheKey, response.clone()));
      }
    }

    const finalResponse = new Response(response.body, response);
    Object.entries(cors).forEach(([k, v]) => finalResponse.headers.set(k, v));
    finalResponse.headers.set("Cache-Control", "no-store"); // مرورگر کش نکند

    return finalResponse;
  },
};
