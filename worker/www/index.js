// www.decomp.fun -> decomp.fun, same path and query, permanent.
export default {
  fetch(request) {
    const url = new URL(request.url);
    url.hostname = "decomp.fun";
    return Response.redirect(url.toString(), 301);
  },
};
