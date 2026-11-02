// Stands in for HaruNeko's image descrambler, which some comic viewers need. NovelDB leaves it out
// (and the DRM decryption scripts, `*.DRM.js`, which build.mjs replaces the same way): websites that
// use them load, but reading their pages stops with this error instead of working around the
// site's protection.
export const WITHHELD = 'This site protects its pages; NovelDB does not include HaruNeko\'s code for that.';

export default async function DeScramble(): Promise<never> {
    throw new Error(WITHHELD);
}
