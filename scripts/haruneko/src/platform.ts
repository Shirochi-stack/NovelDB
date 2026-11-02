// NovelDB's stand-in for HaruNeko's `platform/FetchProvider` (which picks an Electron or NW.js
// implementation). Requests go to the app through the page binding `__noveldb_engine_transport`
// (the app's HTTP client with the browser's cookies, then the browser itself when a site refuses
// that); FetchWindowScript opens the address in a browser tab of the app and runs the script there
// (`__noveldb_window_script`). Everything else (FetchHTML, FetchJSON, ...) is HaruNeko's own code.
import { FetchProvider } from '@hn/engine/platform/FetchProviderCommon';
import { FetchApiSupportedPrefix } from '@hn/engine/platform/FetchConcealedRequest';
import { Exception } from '@hn/engine/Error';
import { EngineResourceKey as R } from '@hn/i18n/ILocale';

type TransportResult = {
    status?: number;
    statusText?: string;
    finalUrl?: string;
    responseHeaders?: string;
    responseBase64?: string;
    responseText?: string;
    error?: string;
};

declare global {
    interface Window {
        __noveldb_engine_transport?: (request: Record<string, unknown>) => Promise<TransportResult | { pending: number }>;
        __noveldb_window_script?: (request: Record<string, unknown>) => Promise<{ value?: unknown; error?: string } | { pending: number }>;
    }
}

function bytesToBase64(bytes: Uint8Array): string {
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary);
}

function base64ToBytes(text: string): Uint8Array {
    const binary = atob(text || '');
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
}

function revealedHeaders(request: Request): Record<string, string> {
    const out: Record<string, string> = {};
    const prefix = FetchApiSupportedPrefix.toLowerCase();
    request.headers.forEach((value, name) => {
        const lower = name.toLowerCase();
        out[lower.startsWith(prefix) ? lower.slice(prefix.length) : lower] = value;
    });
    if (request.referrer && !out['referer'] && /^https?:/.test(request.referrer)) {
        out['referer'] = request.referrer;
    }
    return out;
}

function responseHeaders(raw: string): Headers {
    const headers = new Headers();
    for (const line of String(raw || '').split(/\r?\n/)) {
        const colon = line.indexOf(':');
        if (colon <= 0) continue;
        const name = line.substring(0, colon).trim();
        if (/^(content-encoding|content-length|transfer-encoding)$/i.test(name)) continue;
        try {
            headers.append(name, line.substring(colon + 1).trim());
        } catch {
            // a value the Headers class refuses
        }
    }
    return headers;
}

// A binding that needs a browser tab answers {pending: id}; the app hands the answer back later.
async function settle<T>(result: T | { pending: number }): Promise<T> {
    const pending = (result as { pending?: number })?.pending;
    const helper = (window as unknown as { __noveldb_async?: { wait(id: number): Promise<T> } }).__noveldb_async;
    return pending && helper ? helper.wait(pending) : result as T;
}

class NovelDBFetchProvider extends FetchProvider {

    async Fetch(request: Request): Promise<Response> {
        const transport = window.__noveldb_engine_transport;
        if (!transport) {
            return fetch(request);
        }
        const body = ['GET', 'HEAD'].includes(request.method) ? null : new Uint8Array(await request.clone().arrayBuffer());
        let result = await transport({
            url: request.url,
            method: request.method,
            headers: revealedHeaders(request),
            responseType: 'arraybuffer',
            timeout: 60000,
            ...(body && body.length ? { dataBase64: bytesToBase64(body) } : {}),
        });
        result = await settle(result);
        if (!result || result.error) {
            throw new TypeError(`Failed to fetch ${request.url}${result?.error ? ': ' + result.error : ''}`);
        }
        const status = Number(result.status) || 0;
        const bytes = result.responseBase64 ? base64ToBytes(result.responseBase64) : new TextEncoder().encode(result.responseText || '');
        const response = new Response([101, 103, 204, 205, 304].includes(status) ? null : bytes, {
            status: status >= 200 && status <= 599 ? status : 502,
            statusText: String(result.statusText || '').replace(/[^\x20-\x7e]/g, ''),
            headers: responseHeaders(result.responseHeaders || ''),
        });
        Object.defineProperty(response, 'url', { value: result.finalUrl || request.url });
        if (/challenge/i.test(response.headers.get('CF-Mitigated') ?? '')) {
            throw new Exception(R.FetchProvider_Fetch_CloudFlareChallenge, response.url);
        }
        if (response.status === 403) {
            throw new Exception(R.FetchProvider_Fetch_Forbidden, response.url);
        }
        return response;
    }

    public override async FetchWindowPreloadScript<T extends void | JSONElement>(request: Request, preload: string, script: string, delay = 0, timeout = 60_000): Promise<T> {
        const open = window.__noveldb_window_script;
        if (!open) {
            throw new Exception(R.FetchProvider_FetchWindow_TimeoutError);
        }
        const result = await settle(await open({ url: request.url, preload: String(preload || ''), script: String(script), delay, timeout }));
        if (!result || result.error) {
            throw new Error(result?.error || 'The browser tab gave no result');
        }
        return result.value as T;
    }
}

const instance = new NovelDBFetchProvider();

export function SetupFetchProvider(featureFlags: unknown) {
    instance.Initialize(featureFlags as never);
}

export const Fetch: typeof instance.Fetch = (request) => instance.Fetch(request);
export const FetchHTML: typeof instance.FetchHTML = (request) => instance.FetchHTML(request);
export const FetchRegex: typeof instance.FetchRegex = (request, regex) => instance.FetchRegex(request, regex);
export const FetchJSON: typeof instance.FetchJSON = (request) => instance.FetchJSON(request);
export const FetchCSS: typeof instance.FetchCSS = (request, query) => instance.FetchCSS(request, query);
export const FetchProto: typeof instance.FetchProto = (request, schema, messageTypePath) => instance.FetchProto(request, schema, messageTypePath);
export const FetchGraphQL: typeof instance.FetchGraphQL = (request, operationName, query, variables, extensions) => instance.FetchGraphQL(request, operationName, query, variables, extensions);
export const FetchNextJS: typeof instance.FetchNextJS = (request, predicate) => instance.FetchNextJS(request, predicate);
export const FetchNextProps: typeof instance.FetchNextProps = (request) => instance.FetchNextProps(request);
export const FetchWindowScript: typeof instance.FetchWindowScript = (request, script, delay?, timeout?) => instance.FetchWindowScript(request, script, delay, timeout);
export const FetchWindowPreloadScript: typeof instance.FetchWindowPreloadScript = (request, preload, script, delay = 0, timeout = 60_000) => instance.FetchWindowPreloadScript(request, preload, script, delay, timeout);
