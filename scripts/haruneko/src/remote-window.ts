// HaruNeko's `platform/RemoteBrowserWindow` and `platform/AntiScrapingDetection` pick an Electron or
// NW.js window. NovelDB's FetchProvider opens tabs through the app instead (see platform.ts), so
// these are never reached; anti-scraping detection rules that websites register are ignored.
export function CreateRemoteBrowserWindow(): never {
    throw new Error('Browser windows go through the app in NovelDB');
}

export enum FetchRedirection {
    None = 0,
    Automatic = 1,
    Interactive = 2,
}

export function AddAntiScrapingDetection(): void {
    // nothing to register
}

export async function CheckAntiScrapingDetection(): Promise<FetchRedirection> {
    return FetchRedirection.None;
}
