import XCTest
import NpiaCore
@testable import NpiaSources
@testable import NpiaWebEngine

/// How WebKit draws a character a cipher font does not have, compared with how it draws a family that does
/// not exist (the decoder's way of telling the two apart). Prints; asserts only that it ran.
@MainActor
final class HardWebKitDiagnostics: XCTestCase {
    private let covered = "丈上丑丢丧丸举乔九争亏互享京亿仅仔仗仙件估似佛作你依信俩修俯倚借债傍僚兄入全兵内军决凤凭击分刑划则别到刻剂前副办助勺区十卡卧反变另只吐君启吴吸告唐喝嗽嘉回址坐堡填墓壁声壳复夏夹奋奏妻姜娃婆孕字季宅安宏实室害宴尺屋屡岂岛岭已布帜席常庆序式忆怀恭息恶悟悼惜惠惭惹愁愈感愤慎慨我战扭批承抄抛抱押担拒拦括挠挣挨挪振捐换掀授掉排掠搬摆摊撇擦敢敬斜斤断新早易晋暴有服机杀权束松枯架柳栋桑梅梢椒楚榴樱残毒民汁汪沙沫波泪洞津济浩海浸涛淋添渐渗滑滔激灰灵炎烈烧热煎照燕爆爸爹牌牢特犯犹狂狗狡狱珠甲略瘦登皮盈盏监盒目着短石矿砖祖祸福禽禾秃稻空立竭竹符第笼筒策管素繁纠纱纷组细结绣绪缘网羡羽耀考耍职聚肉肢肥育肾肿胖胞胳脆脾臣色芒芬花若荣荷菌萌营蔑虎虹虾表袋誉许评诉诗诵谋象赔趟跑距跳身辆辛辨辫过迎运返这远迟逃逆逐逗逮遇遍邻郑部鄙酒重金鉴铸铺链销锈锤锯镇镜闭闲附难雄面韵顷项须顾顿饶首驳验骡鱼鹰鹿龙"
    private let uncovered = "呃呵咔哒哼啥啪嗯奈妃愣慷憋揣摁斟枉栈榻洛玄瓷睫瞪瞬窍罕翘胚苛襟诶账蹙酌颇"

    func testHowWebKitDrawsCharactersTheCipherFontLacks() async throws {
        let engine = WKWebEngine(persistent: false)
        let page = try await engine.newPage(profile: "hard-diag")
        defer { Task { await page.close() } }
        try await page.load(URL(string: "about:blank")!, until: .domContentLoaded, timeout: 20)
        try await page.ensureHard()
        let sample = try RepoFixtures.json("qdn_chapter76_capture.json")
        let fontData = sample["fontData"] as? [String: String] ?? [:]
        let font0 = fontData["font:0"] ?? ""
        let script = """
        const covered = \(HardScripts.literal(covered));
        const uncovered = \(HardScripts.literal(uncovered));
        const bytes = window.__npiaHard.util.base64ToBytes(\(HardScripts.literal(font0)));
        const face = new FontFace('diag0', bytes.slice().buffer);
        await face.load();
        document.fonts.add(face);
        const side = 64;
        const canvas = document.createElement('canvas');
        canvas.width = side; canvas.height = side;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.fillStyle = '#fff';
        ctx.textBaseline = 'alphabetic';
        function render(css, ch) {
          ctx.clearRect(0, 0, side, side);
          ctx.font = '32px ' + css;
          ctx.fillText(ch, 16, 41.6);
          const px = ctx.getImageData(0, 0, side, side).data;
          let minX = side, minY = side, maxX = -1, maxY = -1;
          for (let y = 0; y < side; y++) for (let x = 0; x < side; x++) {
            if (px[(y * side + x) * 4 + 3] > 0) { if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y; }
          }
          if (maxX < 0) return null;
          const w = maxX - minX + 1, h = maxY - minY + 1;
          const data = new Uint8Array(w * h);
          for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) data[y * w + x] = px[((minY + y) * side + minX + x) * 4 + 3];
          return { w, h, data, width: ctx.measureText(ch).width };
        }
        function diff(a, b) {
          if (!a || !b) return a === b ? 0 : -1;
          if (a.w !== b.w || a.h !== b.h) return -2;
          let n = 0;
          for (let i = 0; i < a.data.length; i++) if (a.data[i] !== b.data[i]) n++;
          return n;
        }
        const probes = {
          decoder: ['"diag0"', '"__qdn_no_such_family"'],
          serif: ['"diag0"', 'serif'],
          sans: ['"diag0"', 'sans-serif'],
          noneSerif: ['"diag0"', '"__no", serif'],
          withSerif: ['"diag0", serif', 'serif'],
          withSans: ['"diag0", sans-serif', 'sans-serif'],
          noneVsNone: ['"__a_no_such_family"', '"__qdn_no_such_family"'],
        };
        const out = {};
        for (const [name, [left, right]] of Object.entries(probes)) {
          const summary = { uncoveredSame: 0, uncoveredDiffer: 0, coveredSame: 0, coveredDiffer: 0, sizeDiffers: 0, samples: [] };
          for (const ch of uncovered) {
            const a = render(left, ch), b = render(right, ch);
            const d = diff(a, b);
            if (d === 0) summary.uncoveredSame++; else summary.uncoveredDiffer++;
            if (d === -2) summary.sizeDiffers++;
            if (summary.samples.length < 4) summary.samples.push([ch, a && [a.w, a.h, Math.round(a.width * 100) / 100], b && [b.w, b.h, Math.round(b.width * 100) / 100], d]);
          }
          for (const ch of covered) {
            if (diff(render(left, ch), render(right, ch)) === 0) summary.coveredSame++; else summary.coveredDiffer++;
          }
          out[name] = summary;
        }
        out.userAgent = navigator.userAgent;
        out.fonts = [...document.fonts].map((f) => f.family + ':' + f.status);
        return out;
        """
        let data = try await page.evaluateJSON(script)
        print("WEBKIT-DIAG " + String(decoding: data, as: UTF8.self))
    }
}
