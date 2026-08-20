// frontend/src/config/devAuth.js
// Non-secret runtime flag: skips the login gate in local/dev only. Read from
// Vite's import.meta.env (VITE_DEV_AUTH_BYPASS), set via docker-compose.yml
// for containerized dev or frontend/.env.local for host-mode `npm run dev`.
// Never true in a production build.
export function isDevAuthBypassEnabled() {
  return import.meta.env.VITE_DEV_AUTH_BYPASS === "true";
}
