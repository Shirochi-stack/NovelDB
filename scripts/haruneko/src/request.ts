// Every `new Request(...)` in the bundle builds HaruNeko's FetchConcealedRequest (esbuild `inject`),
// as HaruNeko's own app does by replacing the global, so headers a page may not set (Referer,
// Origin, Cookie) reach the app's transport instead of being dropped. The page's own global stays
// untouched.
export { FetchConcealedRequest as Request } from '@hn/engine/platform/FetchConcealedRequest';
