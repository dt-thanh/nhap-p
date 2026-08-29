export const onRequest: PagesFunction = async (context) => {
  const url = new URL(context.request.url);
  const backendUrl = `https://minicrm-backend-production.up.railway.app${url.pathname.replace(/^\/api/, "")}${url.search}`;

  const requestHeaders = new Headers(context.request.headers);

  const fetchOptions: RequestInit = {
    method: context.request.method,
    headers: requestHeaders,
    redirect: "manual",
  };

  if (context.request.method !== "GET" && context.request.method !== "HEAD") {
    fetchOptions.body = context.request.body;
  }

  try {
    const response = await fetch(backendUrl, fetchOptions);
    return response;
  } catch (err: any) {
    return new Response(JSON.stringify({ error: err.message }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
};
