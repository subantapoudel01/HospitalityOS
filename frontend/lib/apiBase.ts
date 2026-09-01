// Where the browser should send API requests.
//
// One definition, imported everywhere, because the previous arrangement -
// four files each writing
//
//     const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
//
// - shipped a production build that called localhost. The production image
// sets NEXT_PUBLIC_API_URL="" so the browser uses relative paths (Traefik
// serves the API from the same origin), but "" is FALSY, so `||` fell
// through to the development fallback. Next.js inlines these at build
// time, so the string was baked into the bundle: nine occurrences across
// every page, and every visitor's browser would have tried to reach its
// own machine. Caught by building the image and grepping .next/static;
// CI now does that on every push.
//
// The three cases, spelled out because the distinction is the whole point:
//
//   unset  -> http://localhost:8000   running `next dev` outside Docker
//   ""     -> "" (relative)           production, API on the same origin
//   a URL  -> that URL                API on a different origin

const RAW = process.env.NEXT_PUBLIC_API_URL;

/**
 * Prefix for API paths. Either empty (same origin) or an absolute origin
 * with no trailing slash, so `API_BASE + "/api/..."` is always well-formed.
 */
export const API_BASE =
  RAW === undefined || RAW === null
    ? "http://localhost:8000"
    : RAW.replace(/\/+$/, "");
