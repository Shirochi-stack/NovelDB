// NovelDB's stand-in for HaruNeko's `BackgroundTimers` (a Web Worker so timers keep running in a
// hidden app window). The page's own timers do the same job here.
type Action = () => void;

export async function Delay(ms: number, variance = 0): Promise<void> {
    const delay = variance > 0 && variance < ms ? ms - variance + 2 * variance * Math.random() : ms;
    return new Promise<void>(resolve => setTimeout(resolve, delay));
}

export function SetTimeout(callback: Action, ms: number): Promise<number> {
    return Promise.resolve(setTimeout(callback, ms) as unknown as number);
}

export function ClearTimeout(timerID: number): void {
    clearTimeout(timerID);
}

export function SetInterval(callback: Action, ms: number): Promise<number> {
    return Promise.resolve(setInterval(callback, ms) as unknown as number);
}

export function ClearInterval(timerID: number): void {
    clearInterval(timerID);
}
