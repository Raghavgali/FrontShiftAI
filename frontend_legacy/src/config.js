// Single source of truth for the backend and voice service URLs.
//
// These are injected at build time by Vite. In CI they come from repository
// variables (not secrets) because anything referenced here is readable in the
// shipped client bundle:
//   VITE_API_URL        <- vars.BACKEND_URL
//   VITE_VOICE_API_URL  <- vars.VOICE_API_URL
//
// When a variable is unset Vite substitutes an empty string, which is falsy,
// so the localhost defaults below take over and local development keeps
// working with no .env file.

// A trailing slash would produce request paths like "https://host//api/foo",
// so normalise it away once here instead of at every call site.
const stripTrailingSlash = (url) => (url ? url.replace(/\/+$/, '') : url);

export const API_BASE_URL = stripTrailingSlash(
  import.meta.env.VITE_API_URL || 'http://localhost:8000'
);

// VITE_MODAL_VOICE_AGENT_URL is the older name for the same value and is kept
// as a fallback so existing local .env files do not break.
export const VOICE_API_URL = stripTrailingSlash(
  import.meta.env.VITE_VOICE_API_URL ||
    import.meta.env.VITE_MODAL_VOICE_AGENT_URL ||
    'http://localhost:8001'
);
