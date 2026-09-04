"""
external_scraper.py — Playwright-based scraper for non-Novelpia sites.

Uses Playwright (headless Chromium) to navigate to novel pages and inject the
novel-downloader's compiled JavaScript rules (rules-lib.js + bridge.js).
The JS rules run natively in the browser, supporting all 100+ sites without
manual porting.

Data flows:
  1. Playwright navigates to the book URL
  2. Injects rules-lib.js + bridge.js
  3. Calls __ND_parseBook() → gets JSON metadata + chapter list
  4. For each chapter, calls __ND_parseChapter(url) → gets JSON content + images
  5. Results fed into the existing EPUB/PDF/TXT pipeline
"""

import json
import html
import base64
import hashlib
import hmac
import os
import platform
import queue
import random
import re
import shutil
import shlex
import sqlite3
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
if sys.platform == "win32":
    import ctypes


def _hidden_windows_subprocess_kwargs():
    """Hide console windows for child processes launched by the GUI exe."""
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }

# When running from a PyInstaller bundle, point Playwright to the
# bundled Chromium browser so users don't need to install anything.
if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
    _bundled_browsers = os.path.join(_base, 'ms-playwright')
    if os.path.exists(_bundled_browsers):
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = _bundled_browsers

from playwright.sync_api import (
    sync_playwright,
    Page,
    Browser,
    TimeoutError as PlaywrightTimeoutError,
)
from downloader_core import parse_novelpia_notice_html, parse_novelpia_status

APP_DATA_NAME = "NpiaDownloader"

SHUBA_API_BASE = "https://app.novelapis.com/json"
SHUBA_API_KEY = "chuanshuo_09_03"
SHUBA_API_USER_AGENT = "NovelApp/2.1.1 (Android/16)"

SFACG_OBFUSCATED_CHARS = (
    '宦缺泛洁槛掳马杉傲冷衡弗害烬遗刨饵韵动味碳骄寥凹汁姚也鼎勒痈慕来诊箔迅沁羞僚休儿唾摆窘厌椿母残貌盔弦沉恃棒菱沂巳釜没工溃春肢焦占遥浴轩儡栗梦陨嘴荤梢宇钮捆争土枣盛渝信茵厕忆士急败茁徘吁莉旋腆踪钩捂超峙蒙瘦差应漳沥瑚横阅文缸吱滨变形霉霹僻终愧甥券拽盘预奖鸟透真椅取堕舱票厂彩扯时宋檄瘫峨题楼搭报瘴础勤梨宠古坯忠鲍层癌键肠足荒秃认菏百涣坑投滥谚草坞太桨拓剧宾栋奉老上似丈避侥烯准种壁峦臻谴卤隆垂番协愚划鸦攻藏曾对痔跺墓根撰聂闯察咋发衅撵寡竣茶宏议亨艇务狗裤宣鹃逼饱宅孔诲遣警亿贤鄙沮译龟膀蹭絮伙触锌庚吹命躬圣追炯霞叫裴底族疟痴熊闸臆撂访午茨柿巡米鞍邹逃兑绎犀椎己控篡室劈巍参苦瞬冠摩凭胰甫讯否绅汉诛仙扁活醚挎荷篆黍捕汝吊蕊虱抓从略诞惶逆脉塔歧镶衰撮伍片阶凑松九菊固娄姻委惮酷封澎牙在翰裁南挫沾赏赫厅韩葵介鸡编欧捐能炉挠杀勘浦者粤杖幂琢焉刁爱如惫府武找佯捣锈灰茫铸肛敬阿可属沙创概廊墨乳湛咖级蚂淋反豁旺岭星琉凌嗜目侠迄喇靶福矾丹拔绥觉孟沫霖袭帕驼镁泰啊极孙实经趟当歼斗柑嗽录贡雍稽阜损铬尿痒醒岗渡宗莆捧筑岸井燎搐负粕励爵啮庄涌卢蝶垄仍脂操哉句热耽乓拐苍团斌掩孩沃堆眠闻毛歹齐辖育芳榜其蝇侩杰胞魄粱姬蜕淹轧甚寸舌播霓硒啥钎馅力皇擅染寂相熬必羹期腕跌租僵虏矫恿笨元物历砧噎犬帜舰柞腊定洽剐蕉惨摔珠着烘朋咎某丧酉友郡痹俘啦师蔑涅绒侨官戊锡泌误棘嗡顽谱懂捌潮员豢焰岁丽炕缘搁庐叁崭羔龙崩哩体酗穆猎餐机欺悉蔫寿髓庸焚洲技缠绰傍浅泄此嘶祸肪凶锣雪厢摧粗刷乙脯枪稿寝螟席扎杂第漠檀魂品籽氛越芥呕做滴债缕帛及募悬彰酌堑艘煤癣口售津耀冈涪蝗予攀棚木新泡猖宽躁阁健霍揍铰陕非樟问颠话缴况矽吗昂披努疵滩执拟鼓枷摄湃亚昏旁塑含阑荣安掐宁苞监放施辣浙主遍邀告枯释灾啪驭全断缩晌藐孵折据辕设离病呀解汲援娜幻辩很盆毡铆敢粳蔼榨游掉皑挽呈擦舀麦咏唁专肝伯章脸氖揪枚戏惠萍卧隶庙量啄贞烙响柒熙违泪娶吨矢艺培降迁映丙硬碾蠕囚怪删支适佑搽淆蜒判奄想障袄跑窜燕广登停嚷户铡挚要瘁猿甭韦嘿檬瘸烈招铅眶蜘火洛姐圾分雇阳剑练皋凝劳建脓一俏苔严洗睹耗茎国漾辆疲篱煽走歌亥诽藉狞径菠雁言审碰双慰森揣钠世芝炙蜗针奋曹鉴遁冬扫偶赐陶酚束砷兽噬揽永汤庇脖挣泣蓟本润饺常偏垛教悄芭琳计偷詹夏帝韭擂卵吧暗拆插忌橱搅条曲糖恳宙贯甲稼撇鳞鸭臭臀迂回磁淤统羽三功伏烹札杭暑凰弄祭币皿懦店萝眺瘤产菇勾笼引漏惭堡鹊闽恫宿累谰曙厩莽蔡杆烛镜碗费惧碑啡枫慧沧颊恕揭簇材握留伟灼墟噶恨俺腻赌娃蚊膨婴闺碟陵连晕扑递蜡勇沸这玻挑兜耙疫舶乃羌瓷进胖俐基卓谆企边眷独潭毅牢蛆砍而丫唆诺舟殖陈浇勉捡饮完托更腑瞎减幌我吟滑算钱枉睦妊冶红救贴忍旧刊淡依喘共斥侗先峡香紊候肺袒拄猫跳砖剂窑渔假果色挟界名逛膘蘑酪吃翟研翻频提忿饼腾倚疮脆婚甘赠鹰带摘匆锥瑟睛领钵柄祥烩蛮稗矗濒卫溜制扭藻吾赂拙辛肄轰哭哑凛羡窖筷俄姆楔缝兄民表痉舆膊捷岂蓖卡限肿倪好炬淑助且颤斋蔬莹又忻瞩二畜总坤心鸵眨挡戴榔页缨恍几胁帐传迭临锹煌典特沏向尼谷哇状溪胜衣养丛斟萤柠恼讳朱楷肆防禹烃零若醋夕倘铲岿茧薯牌罗妥距苏规瞳赘铜钒奇奸疆魁矿蛤澈呛父详丘叼路吼驮端凳床涕瓤仆撼腺亭禾乐白侯抵诧失铱慷铭识迪哨巴使涝巨棺碱奎芹掸颇浆悦激侣长埃禄袋踢舍侧舜寺盐叭鸿面局私贷比容掀搔藩览吞态左布素侵侮咀憾郸现娥记淀咯馆仿黎敖少寄匣贮脐看亩趴歉样斧酣奠旭筋狂筐蚜最聋遵酶晒擒妒拖窿鸥示抛娘县台导矮窒顺女邑慌齿蔷蜜字数竭瑶谨抒乌耕霸泥兵号亏匈侄猩褒琶署静住饿碉桂抬庆匙扬慈牟倾媚矣搀锤拇构吵嫩讹焊郁漫守贱拌效例弊灭桅尾道供葱狈垃壮筹栈扮躇疤骆揩露莫孰敏订购潍顷盈转胯撞居鞘摇周幽魔镍蚌水树戍谐飞梯豪添业晓挪柴袁烫苟怔冤念决初碎立臣纳陇异喉葬驯确死桌雾嘱臂沟殴歇丝造坛蚤围拴延自芋祟巷猜恭毒辟湖牧傅惺磐瞻砾壕踞疼尝骂咆同症耿呢敝穷饲钨块每耐巧王躯姑藕泳颈肃崖役犹辱您棕满锅垫措深忙酞讽化场馈赞节拒矛宴蛇乞杨赣咬均中邢蛛瞪睁韶悼鞋滇雄站骗于阂茅阀煎斜渴季哼跃暖签耻躲写宫剔叙载尊怒蒋翅请函贝姥穴驰盾势纵瓦罢存冲血龋脑敌瞧营仁扰刻狐啸珍荆知晤乍榴鲁冻咱竹今融烂咕喜踌赴财氏靡右琐忽贺汞赵躺垮瞒拥屏虐蒲邮壤颖破斯既棠誊彤铝蝉止案佐捞童椭凤贩那塘湿陷清圭诣须义萎梧箍蛙息入勃另锚棋舅筒卿收尽骑脏毋屡蔓胆痊膳钙并敞傀秘奶卯谭给峰远瞥禽卉傻掇冗器货悯咨剁维讲子药纲腮和嫡麓诚故些闭档选箕樱气幅酥赊辑虞怀蛊弧抢价俊跪斡雌甩绘罩忘晚驻龚斩靠涂螺锑雷步绚智组骸仅庭涸琴骚络旬乏杯灸呜椽斤寒输阉斑辐烟刑平涯般倒榷湘公氢嚎疚启乡茸芒巩鹿景甄誓秒炼杏绞剿葫筏翱骋管蓝蔗稚网君腥掺臼遇继脊瑰流磨瞅寨鸳送宝拷击汕圈蓬尉磕田疏妄绵畦仑竖刺硕渭棍试悲污豆箩云暴东憨让哈芽妖黔耍栖欠狠秆讶叉狡敷砌暮宪廉鞭彬炭嘘么首账践趣史迟瘪岳谅豺单拨拳船社厄厉天弃寇尤保摹佬尘炒苑夯匪扔顿毁雀窟熔洱湍究献姨蛔治蔽袖姜虹缀盗漱狰嚣哆豫虽煮削滚爸巫峪惕锐脱屑压泼纸荡证模院伞霄混砰谎彦糕奴派夷彻串忱耸酬致滞类郭撑樊辈哎抱缎妈艰绢朵荚颗摸事邱综冒菩稍婆轨库携懈鹏至思蹈滋痢酱论珐蜀咐窃军还强归肥爬秉莲注妓充垒瓶丢绍坊惰测柔炳榆埂掠掌董恶戳禁打菲坍开阻嚼灶逻额罐肯胶凋肤朽只匠腰蛹喊棵潘验倍柱卖裂喂粮贿门鳃勋笆迫蛾笛醛礼称奢纹卞陆趁蝴与升胺贫卸随党镐劲诫轿镊魏伎街缮估囊策屠泊舵皂疑集拼祁俯灿舷范佃征展出侈厦驴缓刀喝厚抿六港希糙才堰苹批辞聚舔缅蹄隔捍蕴弟柳州帖欢佣快桩慑生瓢椰翘骇鬃说阐蚁山瓮氮听殿渍辊办各济坠蹦联屯习月鼻植砚沿嫂区佰唉怕艳始校剥括雕祈旅置徽诈浪轴已邦裙碍窍哄撤哮部匀摊兼蓑潦橙关科绷嘻嘉妨搜值棉惩蟹倦驶税芯泻密运涧仇珊涨昌男琵渺正光呻硅有傣逐袜净吠缔溯将婶钡电迹股昧卜低无嘲兆诌沦恋溺糯钢宰饥悸夫版胃笋牡咒箭把泞虑易纤唐梅咳郑需象玉大譬崔晶洼赦弱丁馋四挂磊硝昆痰撒调蔚胳它抗猪幼镭秸谁恒印诀厨芍显恐猾冀司廷坚篮酵撕锰履位澳市汐哀辨则萧溢秦奈城隧叹茬政责炔铺掣赤兔撩蹬铃馒爪钥震凄畅移是辅讼翼栓瀑搪醉獭懒陌臃毫弛裳祷列毙岛浚遂澜盏郎贾扦酒呐境早余评作坎罚环窥狱炊淌郊怜书外册贰剪际滤油迎渊桶媒付担肌郴盂整袍就坏邻皮用谩瑞采栽吮雹烦克患轮探咸拈墅椒但恬愁裔肘峻敦件枕匿恤散谜央诵链绳讥捶烤薛直栏垣弓睬汀栅刚职畴敲扣读纱搂晰凡痞赃雏徊兴糟篇粒海复郝八赚之冯酸侍漓糜渠惋乎们晨垦吕乾千亡芜项的逸铂池僳往买搞车诬焙辗利妹富你查朝跋砸氦旷娠搓怎澡危氯契押曼帧纬受讨壹愈胚昨柬驱轻勿桓靖锭赖彼袱喧捏靴涩虚薪矩割末恩唱革幢日催五屁线情挖苯哲犊讣盟地十落去伶个苗间闹谋骏唬沼糠迈殉绊罕肖邪晋闷窗鱼嫁狭紫改炮浮灯迸锨孤诡汾抉孺甸增套灵著寞赡梭循疗弯京补乱重灌朔空粉桥演婉即昭鸽泵妆燥过彝具阴错匝人劝描亢抚张金犁氟耪板逮墙拜服喳阔址刽拿匹壶箱赔葛赎画贸帆辫沈射惊训绪沪久扼蹿叛埋劫钦爹庞绕淮颜缉悟楚叶波默颓簧擞蠢背竟驳鼠滔啃祖屿爷氰飘会苫哦雅酋堪篷邵咽伦仕伐秩柏获承屎蜂浩愉竿仗芦琼盎慨林干玄凉滦她李畏挥吝捻毕被胡崇膝馁宜踊指簿疽瘩抖皱侦掘呆巾农咙胎赛跟别剩普菜溅感权钞楞副虾膏鸯肉橡仪俗玩烽赶申万拦坷钉坦糊汛醇戈因望夹行蒸成觅钳绿资乘捎邯座险择钧为嚏述帅护笑炽讫毗不洋眩氓德花烷汪意渣浑许秀曝腿谦稻鹤蛰消雨寅玲壬嘛萄欲代由刘搬缆聘粟泉荧球寻亮村家省扛帽陀谬羊稠爽籍亲舒吭点乒衷晃振熏短汰诸烧梆惹坐屹桑丑肋速次喷理饭谊赋拭彭逝玫起骡贼了瓣谤疥退银娩黑授促河眯砒菌锗薄加圆锯辉睫褐泽或奥抠园伺揉墒刮猛兢埠年替朗玛锁微未艾呵吉琅排炎碘眉逾式趾浓卒尚客械啤帮优却沛腹胀队嵌镣谓怖垢冕叠叮像仔笔渐掷蹋笺令撬蚀接镑铣厘竞众呼痪肾愿桔孝峭疯岔黄橇隙邓媳褥该举弘川洞扒俱潜呸缄豹酝镇筛塞尧西锋脾头摈律检任翔青芬戎吐洪麻鳖诅窝声耶杜交尔拎盲盯姓隅丰伴续霜涟衙帘除标癸辰寓磋龄俞纯拢截鞠配姿郧逢爆英幕盒窄堤氨逞敛仰索痘聪见襟纺恢绝填靳沽盖碧忧鹅疾晾阵铁祝叔欣磅屉枝痕佳槐志吓房穿拧善小酿挝羚苛炸貉约猴颁益挛惯膜粪枢纪宛乖扶槽攒谢掂奏寐狸旨倔嗓蛋夸皖洒澄返豌软凿晴纂剃待蹲拂蚕求困毖积妙旗陡隘华怨憎康拍江督拣颅较膛伤逊淳享颂图滓牺荔尖神液蛀暇哥棱胸淖昼源修穗牵蝎裸合谣陪浸廓曰纫盼囱到夜惜蒂届份蒜下质孪稳涵赁徐釉隋锦颐悠俭耘答凸纽原格扇高戮抑嗣隐嫌扩漂涡劣伊肚骨嗅踩难钻帚愤处莎粥瞄娇狄拾茄鲜秤半璃毯包视馏途巢襄手淫孽硫性唯近浊怯装挤荫惦纷聊汽牛婿淘御航稀前医滁娱扳渗肩序堵硼砂钾驾坟战渤阮誉尸绸殃紧程绣结驹都戒涤畔耳备堂痛揖他拘绩伪精熟免饰焕迷伸潞溶掖绑阎铀换兹甜突踏便坡尹脚烁徒附朴虎憋术尺诗吴群桐衔挨漆料褂段简课噪戌辽吩允佛借荐仟诱殷歪儒影逗七腔抄旦吻核商身迢裹韧锄秽罪殆嘎佩饯坝盅勺绦法音碌挞吸靛戚何婪坪两秧蘸蓉推后乔熄衬曳塌剖昔狙覆度杠壳陋腐美唤谗刹跨悍翌贪怂辜通多鄂框湾梁碴词卑哪废梳赢型殊沤照兰睡款镀攫什抡抽萌闪镰召捅倡翠切织腋桃妮篓彪谈妻方妇冉疙株瓜廖学懊鲸篙持亦哺拱萨偿遮缚犯仓岩疹然褪汗辙翁威怠绽闰旱搏弥诉互等细销汹率系内瘟蕾狼淬庶茂里攘牲衫弹煞肇狮葡畸惟野繁石良仲宵拯趋所泅班博粹涉冰拉按以眼僧慢幸温卷俩舞明角粘陛嫉蓄食酮孜顾唇刃涛掏捉锻圃屈贬玖语遭夺鸣析哗顶涎够虫域苇鬼晦氧风抹码茹皆询哟北骤观惑纠颧考再架磷得慎淄钓溉谍礁喀燃藤暂遏孕疡纶悔屋秋闲裕崎梗埔达撅柯钟奔贵莱储吏墩抨饶囤娟擎啼鲤柜匡丸凯符肮喻磺钝衍汇'
)
SFACG_REPLACEMENT_CHARS = (
    '啊阿埃挨哎唉哀皑癌蔼矮艾碍爱隘鞍氨安俺按暗岸胺案肮昂盎凹敖熬翱袄傲奥懊澳芭捌扒叭吧笆八疤巴拔跋靶把耙坝霸罢爸白柏百摆佰败拜稗斑班搬扳般颁板版扮拌伴瓣半办绊邦帮梆榜膀绑棒磅蚌镑傍谤苞胞包褒剥薄雹保堡饱宝抱报暴豹鲍爆杯碑悲卑北辈背贝钡倍狈备惫焙被奔苯本笨崩绷甭泵蹦迸逼鼻比鄙笔彼碧蓖蔽毕毙毖币庇痹闭敝弊必辟壁臂避陛鞭边编贬扁便变卞辨辩辫遍标彪膘表鳖憋别瘪彬斌濒滨宾摈兵冰柄丙秉饼炳病并玻菠播拨钵波博勃搏铂箔伯帛舶脖膊渤泊驳捕卜哺补埠不布步簿部怖擦猜裁材才财睬踩采彩菜蔡餐参蚕残惭惨灿苍舱仓沧藏操糙槽曹草厕策侧册测层蹭插叉茬茶查碴搽察岔差诧拆柴豺搀掺蝉馋谗缠铲产阐颤昌猖场尝常长偿肠厂敞畅唱倡超抄钞朝嘲潮巢吵炒车扯撤掣彻澈郴臣辰尘晨忱沉陈趁衬撑称城橙成呈乘程惩澄诚承逞骋秤吃痴持匙池迟弛驰耻齿侈尺赤翅斥炽充冲虫崇宠抽酬畴踌稠愁筹仇绸瞅丑臭初出橱厨躇锄雏滁除楚础储矗搐触处揣川穿椽传船喘串疮窗幢床闯创吹炊捶锤垂春椿醇唇淳纯蠢戳绰疵茨磁雌辞慈瓷词此刺赐次聪葱囱匆从丛凑粗醋簇促蹿篡窜摧崔催脆瘁粹淬翠村存寸磋撮搓措挫错搭达答瘩打大呆歹傣戴带殆代贷袋待逮怠耽担丹单郸掸胆旦氮但惮淡诞弹蛋当挡党荡档刀捣蹈倒岛祷导到稻悼道盗德得的蹬灯登等瞪凳邓堤低滴迪敌笛狄涤翟嫡抵底地蒂第帝弟递缔颠掂滇碘点典靛垫电佃甸店惦奠淀殿碉叼雕凋刁掉吊钓调跌爹碟蝶迭谍叠丁盯叮钉顶鼎锭定订丢东冬董懂动栋侗恫冻洞兜抖斗陡豆逗痘都督毒犊独读堵睹赌杜镀肚度渡妒端短锻段断缎堆兑队对墩吨蹲敦顿囤钝盾遁掇哆多夺垛躲朵跺舵剁惰堕蛾峨鹅俄额讹娥恶厄扼遏鄂饿恩而儿耳尔饵洱二贰发罚筏伐乏阀法珐藩帆番翻樊矾钒繁凡烦反返范贩犯饭泛坊芳方肪房防妨仿访纺放菲非啡飞肥匪诽吠肺废沸费芬酚吩氛分纷坟焚汾粉奋份忿愤粪丰封枫蜂峰锋风疯烽逢冯缝讽奉凤佛否夫敷肤孵扶拂辐幅氟符伏俘服浮涪福袱弗甫抚辅俯釜斧脯腑府腐赴副覆赋复傅付阜父腹负富讣附妇缚咐噶嘎该改概钙盖溉干甘杆柑竿肝赶感秆敢赣冈刚钢缸肛纲岗港杠篙皋高膏羔糕搞镐稿告哥歌搁戈鸽胳疙割革葛格蛤阁隔铬个各给根跟耕更庚羹埂耿梗工攻功恭龚供躬公宫弓巩汞拱贡共钩勾沟苟狗垢构购够辜菇咕箍估沽孤姑鼓古蛊骨谷股故顾固雇刮瓜剐寡挂褂乖拐怪棺关官冠观管馆罐惯灌贯光广逛瑰规圭硅归龟闺轨鬼诡癸桂柜跪贵刽辊滚棍锅郭国果裹过哈骸孩海氦亥害骇酣憨邯韩含涵寒函喊罕翰撼捍旱憾悍焊汗汉夯杭航壕嚎豪毫郝好耗号浩呵喝荷菏核禾和何合盒貉阂河涸赫褐鹤贺嘿黑痕很狠恨哼亨横衡恒轰哄烘虹鸿洪宏弘红喉侯猴吼厚候后呼乎忽瑚壶葫胡蝴狐糊湖弧虎唬护互沪户花哗华猾滑画划化话槐徊怀淮坏欢环桓还缓换患唤痪豢焕涣宦幻荒慌黄磺蝗簧皇凰惶煌晃幌恍谎灰挥辉徽恢蛔回毁悔慧卉惠晦贿秽会烩汇讳诲绘荤昏婚魂浑混豁活伙火获或惑霍货祸击圾基机畸稽积箕肌饥迹激讥鸡姬绩缉吉极棘辑籍集及急疾汲即嫉级挤几脊己蓟技冀季伎祭剂悸济寄寂计记既忌际妓继纪嘉枷夹佳家加荚颊贾甲钾假稼价架驾嫁歼监坚尖笺间煎兼肩艰奸缄茧检柬碱拣捡简俭剪减荐槛鉴践贱见键箭件健舰剑饯渐溅涧建僵姜将浆江疆蒋桨奖讲匠酱降蕉椒礁焦胶交郊浇骄娇嚼搅铰矫侥脚狡角饺缴绞剿教酵轿较叫窖揭接皆秸街阶截劫节桔杰捷睫竭洁结解姐戒藉芥界借介疥诫届巾筋斤金今津襟紧锦仅谨进靳晋禁近烬浸尽劲荆兢茎睛晶鲸京惊精粳经井警景颈静境敬镜径痉靖竟竞净炯窘揪究纠玖韭久灸九酒厩救旧臼舅咎就疚鞠拘狙疽居驹菊局咀矩举沮聚拒据巨具距踞锯俱句惧炬剧捐鹃娟倦眷卷绢撅攫抉掘倔爵觉决诀绝均菌钧军君峻俊竣浚郡骏喀咖卡咯开揩楷凯慨刊堪勘坎砍看康慷糠扛抗亢炕考拷烤靠坷苛柯棵磕颗科壳咳可渴克刻客课肯啃垦恳坑吭空恐孔控抠口扣寇枯哭窟苦酷库裤夸垮挎跨胯块筷侩快宽款匡筐狂框矿眶旷况亏盔岿窥葵奎魁傀馈愧溃坤昆捆困括扩廓阔垃拉喇蜡腊辣啦莱来赖蓝婪栏拦篮阑兰澜谰揽览懒缆烂滥琅榔狼廊郎朗浪捞劳牢老佬姥酪烙涝勒乐雷镭蕾磊累儡垒擂肋类泪棱楞冷厘梨犁黎篱狸离漓理李里鲤礼莉荔吏栗丽厉励砾历利僳例俐痢立粒沥隶力璃哩俩联莲连镰廉怜涟帘敛脸链恋炼练粮凉梁粱良两辆量晾亮谅撩聊僚疗燎寥辽潦了撂镣廖料列裂烈劣猎琳林磷霖临邻鳞淋凛赁吝拎玲菱零龄铃伶羚凌灵陵岭领另令溜琉榴硫馏留刘瘤流柳六龙聋咙笼窿隆垄拢陇楼娄搂篓漏陋芦卢颅庐炉掳卤虏鲁麓碌露路赂鹿潞禄录陆戮驴吕铝侣旅履屡缕虑氯律率滤绿峦挛孪滦卵乱掠略抡轮伦仑沦纶论萝螺罗逻锣箩骡裸落洛骆络妈麻玛码蚂马骂嘛吗埋买麦卖迈脉瞒馒蛮满蔓曼慢漫谩芒茫盲氓忙莽猫茅锚毛矛铆卯茂冒帽貌贸么玫枚梅酶霉煤没眉媒镁每美昧寐妹媚门闷们萌蒙檬盟锰猛梦孟眯醚靡糜迷谜弥米秘觅泌蜜密幂棉眠绵冕免勉娩缅面苗描瞄藐秒渺庙妙蔑灭民抿皿敏悯闽明螟鸣铭名命谬摸摹蘑模膜磨摩魔抹末莫墨默沫漠寞陌谋牟某拇牡亩姆母墓暮幕募慕木目睦牧穆拿哪呐钠那娜纳氖乃奶耐奈南男难囊挠脑恼闹淖呢馁内嫩能妮霓倪泥尼拟你匿腻逆溺蔫拈年碾撵捻念娘酿鸟尿捏聂孽啮镊镍涅您柠狞凝宁拧泞牛扭钮纽脓浓农弄奴努怒女暖虐疟挪懦糯诺哦欧鸥殴藕呕偶沤啪趴爬帕怕琶拍排牌徘湃派攀潘盘磐盼畔判叛乓庞旁耪胖抛咆刨炮袍跑泡呸胚培裴赔陪配佩沛喷盆砰抨烹澎彭蓬棚硼篷膨朋鹏捧碰坯砒霹批披劈琵毗啤脾疲皮匹痞僻屁譬篇偏片骗飘漂瓢票撇瞥拼频贫品聘乒坪苹萍平凭瓶评屏坡泼颇婆破魄迫粕剖扑铺仆莆葡菩蒲埔朴圃普浦谱曝瀑期欺栖戚妻七凄漆柒沏其棋奇歧畦崎脐齐旗祈祁骑起岂乞企启契砌器气迄弃汽泣讫掐洽牵扦钎铅千迁签仟谦乾黔钱钳前潜遣浅谴堑嵌欠歉枪呛腔羌墙蔷强抢橇锹敲悄桥瞧乔侨巧鞘撬翘峭俏窍切茄且怯窃钦侵亲秦琴勤芹擒禽寝沁青轻氢倾卿清擎晴氰情顷请庆琼穷秋丘邱球求囚酋泅趋区蛆曲躯屈驱渠取娶龋趣去圈颧权醛泉全痊拳犬券劝缺炔瘸却鹊榷确雀裙群然燃冉染瓤壤攘嚷让饶扰绕惹热壬仁人忍韧任认刃妊纫扔仍日戎茸蓉荣融熔溶容绒冗揉柔肉茹蠕儒孺如辱乳汝入褥软阮蕊瑞锐闰润若弱撒洒萨腮鳃塞赛三叁伞散桑嗓丧搔骚扫嫂瑟色涩森僧莎砂杀刹沙纱傻啥煞筛晒珊苫杉山删煽衫闪陕擅赡膳善汕扇缮墒伤商赏晌上尚裳梢捎稍烧芍勺韶少哨邵绍奢赊蛇舌舍赦摄射慑涉社设砷申呻伸身深娠绅神沈审婶甚肾慎渗声生甥牲升绳省盛剩胜圣师失狮施湿诗尸虱十石拾时什食蚀实识史矢使屎驶始式示士世柿事拭誓逝势是嗜噬适仕侍释饰氏市恃室视试收手首守寿授售受瘦兽蔬枢梳殊抒输叔舒淑疏书赎孰熟薯暑曙署蜀黍鼠属术述树束戍竖墅庶数漱恕刷耍摔衰甩帅栓拴霜双爽谁水睡税吮瞬顺舜说硕朔烁斯撕嘶思私司丝死肆寺嗣四伺似饲巳松耸怂颂送宋讼诵搜艘擞嗽苏酥俗素速粟塑溯宿诉肃酸蒜算虽隋随绥髓碎岁穗遂隧祟孙损笋蓑梭唆缩琐索锁所塌他它她塔獭挞蹋踏胎苔抬台泰酞太态汰坍摊贪瘫滩坛檀痰潭谭谈坦毯袒碳探叹炭汤塘搪堂棠膛唐糖倘躺淌趟烫掏涛滔绦萄桃逃淘陶讨套特藤腾疼誊梯剔踢锑提题蹄啼体替嚏惕涕剃屉天添填田甜恬舔腆挑条迢眺跳贴铁帖厅听烃汀廷停亭庭艇通桐酮瞳同铜彤童桶捅筒统痛偷投头透凸秃突图徒途涂屠土吐兔湍团推颓腿蜕褪退吞屯臀拖托脱鸵陀驮驼椭妥拓唾挖哇蛙洼娃瓦袜歪外豌弯湾玩顽丸烷完碗挽晚皖惋宛婉万腕汪王亡枉网往旺望忘妄威巍微危韦违桅围唯惟为潍维苇萎委伟伪尾纬未蔚味畏胃喂魏位渭谓尉慰卫瘟温蚊文闻纹吻稳紊问嗡翁瓮挝蜗涡窝我斡卧握沃巫呜钨乌污诬屋无芜梧吾吴毋武五捂午舞伍侮坞戊雾晤物勿务悟误昔熙析西硒矽晰嘻吸锡牺稀息希悉膝夕惜熄烯溪汐犀檄袭席习媳喜铣洗系隙戏细瞎虾匣霞辖暇峡侠狭下厦夏吓掀锨先仙鲜纤咸贤衔舷闲涎弦嫌显险现献县腺馅羡宪陷限线相厢镶香箱襄湘乡翔祥详想响享项巷橡像向象萧硝霄削哮嚣销消宵淆晓小孝校肖啸笑效楔些歇蝎鞋协挟携邪斜胁谐写械卸蟹懈泄泻谢屑薪芯锌欣辛新忻心信衅星腥猩惺兴刑型形邢行醒幸杏性姓兄凶胸匈汹雄熊休修羞朽嗅锈秀袖绣墟戌需虚嘘须徐许蓄酗叙旭序畜恤絮婿绪续轩喧宣悬旋玄选癣眩绚靴薛学穴雪血勋熏循旬询寻驯巡殉汛训讯逊迅压押鸦鸭呀丫芽牙蚜崖衙涯雅哑亚讶焉咽阉烟淹盐严研蜒岩延言颜阎炎沿奄掩眼衍演艳堰燕厌砚雁唁彦焰宴谚验殃央鸯秧杨扬佯疡羊洋阳氧仰痒养样漾邀腰妖瑶摇尧遥窑谣姚咬舀药要耀椰噎耶爷野冶也页掖业叶曳腋夜液一壹医揖铱依伊衣颐夷遗移仪胰疑沂宜姨彝椅蚁倚已乙矣以艺抑易邑屹亿役臆逸肄疫亦裔意毅忆义益溢诣议谊译异翼翌绎茵荫因殷音阴姻吟银淫寅饮尹引隐印英樱婴鹰应缨莹萤营荧蝇迎赢盈影颖硬映哟拥佣臃痈庸雍踊蛹咏泳涌永恿勇用幽优悠忧尤由邮铀犹油游酉有友右佑釉诱又幼迂淤于盂榆虞愚舆余俞逾鱼愉渝渔隅予娱雨与屿禹宇语羽玉域芋郁吁遇喻峪御愈欲狱育誉浴寓裕预豫驭鸳渊冤元垣袁原援辕园员圆猿源缘远苑愿怨院曰约越跃钥岳粤月悦阅耘云郧匀陨允运蕴酝晕韵孕匝砸杂栽哉灾宰载再在咱攒暂赞赃脏葬遭糟凿藻枣早澡蚤躁噪造皂灶燥责择则泽贼怎增憎曾赠扎喳渣札轧铡闸眨栅榨咋乍炸诈摘斋宅窄债寨瞻毡詹粘沾盏斩辗崭展蘸栈占战站湛绽樟章彰漳张掌涨杖丈帐账仗胀瘴障招昭找沼赵照罩兆肇召遮折哲蛰辙者锗蔗这浙珍斟真甄砧臻贞针侦枕疹诊震振镇阵蒸挣睁征狰争怔整拯正政帧症郑证芝枝支吱蜘知肢脂汁之织职直植殖执值侄址指止趾只旨纸志挚掷至致置帜峙制智秩稚质炙痔滞治窒中盅忠钟衷终种肿重仲众舟周州洲诌粥轴肘帚咒皱宙昼骤珠株蛛朱猪诸诛逐竹烛煮拄瞩嘱主著柱助蛀贮铸筑住注祝驻抓爪拽专砖转撰赚篆桩庄装妆撞壮状椎锥追赘坠缀谆准捉拙卓桌琢茁酌啄着灼浊兹咨资姿滋淄孜紫仔籽滓子自渍字鬃棕踪宗综总纵邹走奏揍租足卒族祖诅阻组钻纂嘴醉最罪尊遵昨左佐柞做作坐座'
)
SFACG_CHAR_TRANS = str.maketrans(
    dict(zip(SFACG_OBFUSCATED_CHARS, SFACG_REPLACEMENT_CHARS))
)



def _get_base_dir():
    """Get the directory where the exe lives (for user-created files like browser_data)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _get_bundle_dir():
    """Get the directory where bundled data files are extracted (JS files etc)."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _get_app_data_dir():
    """Return a stable per-user data dir that survives app folder changes."""
    override = os.environ.get("NPIA_BROWSER_DATA_DIR")
    if override:
        return override

    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(root, APP_DATA_NAME)
    if sys.platform == "darwin":
        return os.path.join(
            os.path.expanduser("~"),
            "Library",
            "Application Support",
            APP_DATA_NAME,
        )
    return os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        APP_DATA_NAME,
    )


def _is_writable_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("")
        os.remove(probe)
        return True
    except Exception:
        return False


def _dir_has_entries(path):
    try:
        return os.path.isdir(path) and any(os.scandir(path))
    except Exception:
        return False


def _load_js_file(filename):
    """Load a JavaScript file from the bundle directory."""
    path = os.path.join(_get_bundle_dir(), filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


class ExternalScraper:
    """Playwright-based scraper using novel-downloader JS rules.

    Usage:
        scraper = ExternalScraper(logger=print)
        scraper.start()
        book = scraper.parse_book("https://ncode.syosetu.com/n1234ab/")
        chapters = scraper.parse_all_chapters(book['chapters'])
        scraper.stop()
    """

    def __init__(self, logger=None):
        self._raw_log = logger or (lambda msg: None)
        self._stop_requested = False

        # Load JS files once
        try:
            self._gm_stubs_js = _load_js_file('gm_stubs.js')
            self._rules_js = _load_js_file('rules-lib.js')
            self._bridge_js = _load_js_file('bridge.js')
        except FileNotFoundError as e:
            self._gm_stubs_js = None
            self._rules_js = None
            self._bridge_js = None

        self._playwright = None
        self._browser = None
        self._context = None      # BrowserContext (persistent)
        self._page = None
        self._chrome_process = None
        self._ntk_temp_chrome = False
        self._munpia_chrome = False
        self._munpia_cdp_port = None
        self._novelpia_chrome = False
        self._novelpia_cdp_port = None
        self._ridi_chrome = False
        self._ridi_cdp_port = None
        self._global_novelpia_session = None
        self._global_novelpia_login_at = ''
        self._global_novelpia_refresh_attempted = False
        self._global_novelpia_auth_lock = threading.Lock()
        self._global_novelpia_ad_state_lock = threading.Lock()
        self._global_novelpia_ad_queue = None
        self._global_novelpia_ad_thread = None
        self._global_novelpia_ad_stop = threading.Event()
        self._global_novelpia_ad_playwright = None
        self._global_novelpia_ad_browser = None
        self._global_novelpia_ad_context = None
        self._global_novelpia_ad_page = None
        self._global_novelpia_ad_process = None
        self._global_novelpia_ad_cdp_port = None
        self._global_novelpia_ad_profile = None
        self._qidian_profile_snapshot_root = None
        self._worker_pages = []   # Additional pages for parallel downloads
        self._book_data = None
        self._book_url = None     # Stored for initialising worker pages
        self._ntk_api_state = None
        self._ntk_browser_fallback = False
        self.ntk_curl_command = os.environ.get("NPIA_NTK_CURL", "")
        if not self.ntk_curl_command:
            try:
                curl_path = os.path.join(_get_base_dir(), "ntk_curl.txt")
                if os.path.exists(curl_path):
                    with open(curl_path, "r", encoding="utf-8") as f:
                        self.ntk_curl_command = f.read().strip()
            except Exception:
                self.ntk_curl_command = ""
        self.ntk_prefer_novelpia_cover = False
        self.novelpia_include_notices = True
        self.syosetu_amazon_cover_fallback = False
        self._kakao_css_cache = {}
        self.kakao_keep_filler = False
        self.kakao_skip_last_page = False
        self._sfacg_app_cookie = None
        self._sfacg_app_cookie_checked = False
        self._sfacg_app_prefer_logged = False
        self._1qxs_cookies = {}
        self._1qxs_request_lock = threading.Lock()
        self._1qxs_next_request_at = 0.0
        self._1qxs_cooldown_until = 0.0

    def _install_bridge_bindings(self, page):
        """Expose Python-backed helpers used by the JS bridge stubs."""
        if not page:
            return
        try:
            page.expose_binding(
                "__npia_gm_xmlhttp_request",
                self._gm_xmlhttp_request,
            )
        except Exception as e:
            msg = str(e).lower()
            if "already" not in msg and "registered" not in msg:
                self.log(f"Bridge binding warning: {e}")

    def _gm_xmlhttp_request(self, source, details):
        """Python transport for GM_xmlhttpRequest.

        Browser fetch cannot set several userscript/app headers and is still
        subject to CORS. Some novel-downloader rules, including SFACG's app API
        fallback, rely on the stronger Tampermonkey transport.
        """
        details = details or {}
        url = details.get("url") or ""
        if not url:
            return {"error": "missing url"}

        method = (details.get("method") or "GET").upper()
        parsed_url = urllib.parse.urlparse(url)
        is_sfacg_api = parsed_url.netloc.lower() == "api.sfacg.com"
        headers = {
            str(k): str(v)
            for k, v in (details.get("headers") or {}).items()
            if v is not None
        }
        for forbidden in ("host", "content-length"):
            for key in list(headers.keys()):
                if key.lower() == forbidden:
                    headers.pop(key, None)

        has_cookie_header = any(k.lower() == "cookie" for k in headers)
        if not has_cookie_header:
            cookie_parts = []
            raw_cookie = details.get("cookie") or ""
            if raw_cookie:
                cookie_parts.extend(
                    part.strip()
                    for part in raw_cookie.split(";")
                    if part.strip()
                )
            seen = {
                part.split("=", 1)[0].strip()
                for part in cookie_parts
                if "=" in part
            }
            for cookie in self._storage_cookies_for_url(url):
                name = cookie.get("name")
                value = cookie.get("value")
                if name and value is not None and name not in seen:
                    cookie_parts.append(f"{name}={value}")
                    seen.add(name)
            if is_sfacg_api:
                app_cookie = self._get_sfacg_app_cookie()
                for item in self._split_cookie_header(app_cookie):
                    name = item.split("=", 1)[0].strip()
                    if not name:
                        continue
                    cookie_parts = [
                        part
                        for part in cookie_parts
                        if part.split("=", 1)[0].strip() != name
                    ]
                    cookie_parts.append(item)
            if cookie_parts:
                headers["Cookie"] = "; ".join(cookie_parts)

        data = details.get("data")
        timeout = details.get("timeout") or 30000
        try:
            timeout = max(float(timeout) / 1000.0, 1.0)
        except Exception:
            timeout = 30.0

        try:
            import requests
            if is_sfacg_api:
                cookie_header = ""
                for key, value in headers.items():
                    if key.lower() == "cookie":
                        cookie_header = value
                        break
                return self._sfacg_api_gm_request(
                    requests,
                    method,
                    url,
                    data,
                    timeout,
                    details.get("responseType") or "",
                    cookie_header,
                )

            response = requests.request(
                method,
                url,
                headers=headers,
                data=data,
                timeout=timeout,
                allow_redirects=True,
            )
            response_headers = "\r\n".join(
                f"{k}: {v}" for k, v in response.headers.items()
            )
            result = {
                "status": response.status_code,
                "statusText": response.reason,
                "responseHeaders": response_headers,
                "finalUrl": response.url,
            }
            if details.get("responseType") in ("arraybuffer", "blob"):
                result["responseBase64"] = base64.b64encode(
                    response.content
                ).decode("ascii")
                result["responseText"] = ""
            else:
                result["responseText"] = response.text
            return result
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _gm_response_result(response, response_type=""):
        response_headers = "\r\n".join(
            f"{k}: {v}" for k, v in response.headers.items()
        )
        result = {
            "status": response.status_code,
            "statusText": response.reason,
            "responseHeaders": response_headers,
            "finalUrl": response.url,
        }
        if response_type in ("arraybuffer", "blob"):
            result["responseBase64"] = base64.b64encode(
                response.content
            ).decode("ascii")
            result["responseText"] = ""
        else:
            result["responseText"] = response.text
        return result

    @staticmethod
    def _sfacg_api_status_code(response):
        try:
            data = response.json()
            return data.get("status", {}).get("httpCode")
        except Exception:
            return None

    def _sfacg_api_response(
            self, requests, method, url, data, timeout, cookie_header):
        """Run one SFACG app API request with native signing and nonce retry."""
        last_response = None
        for attempt in range(1, 21):
            nonce = str(uuid.uuid4()).upper()
            headers = self._sfacg_headers(nonce, method=method)
            if cookie_header:
                headers["Cookie"] = cookie_header
            response = requests.request(
                method,
                url,
                headers=headers,
                data=data,
                timeout=timeout,
                allow_redirects=True,
            )
            last_response = response
            if self._sfacg_api_status_code(response) != 417:
                return response
            if attempt % 5 == 0 or attempt == 20:
                self.log(
                    "[SFACG] App API signature rejected; rotating "
                    f"native nonce {attempt}/20."
                )
            time.sleep(0.25)
        return last_response

    def _sfacg_api_gm_request(
            self, requests, method, url, data, timeout, response_type,
            cookie_header):
        """GM_xmlhttpRequest transport for SFACG app API calls."""
        response = self._sfacg_api_response(
            requests, method, url, data, timeout, cookie_header
        )
        if response is None:
            return {"error": "sfacg api request failed"}
        return self._gm_response_result(response, response_type)

    @staticmethod
    def _split_cookie_header(cookie_header):
        return [
            item.strip()
            for item in (cookie_header or "").split(";")
            if item.strip() and "=" in item
        ]

    def _storage_cookies_for_url(self, url):
        """Read matching cookies from the saved Playwright storage state.

        This intentionally avoids calling Playwright APIs from inside an
        exposed binding callback, which can deadlock the sync driver.
        """
        try:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.hostname or "").lower()
            path = parsed.path or "/"
            is_https = parsed.scheme == "https"
            state_path = self._get_storage_state_path()
            if not host or not os.path.exists(state_path):
                return []
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            return []

        out = []
        for cookie in state.get("cookies") or []:
            domain = (cookie.get("domain") or "").lower()
            cookie_path = cookie.get("path") or "/"
            if cookie.get("secure") and not is_https:
                continue
            if domain.startswith("."):
                if host != domain[1:] and not host.endswith(domain):
                    continue
            elif host != domain:
                continue
            if not path.startswith(cookie_path):
                continue
            out.append(cookie)
        return out

    @staticmethod
    def _sfacg_sign(nonce, timestamp, device_token, salt):
        long_nonce = nonce * 4

        def index_calc(index):
            char_code = ord(long_nonce[index])
            return char_code - (char_code // 0x24) * 0x24

        nonce_reorder = (
            long_nonce[index_calc(1):index_calc(1) + 13]
            + long_nonce[index_calc(2):index_calc(2) + 16]
            + long_nonce[index_calc(3):index_calc(3) + 36]
            + long_nonce[index_calc(4):index_calc(4) + 36]
        )
        auth_string = f"{timestamp}{salt}{device_token}{nonce}"
        result = "".join(
            chr((ord(auth_string[i]) + ord(nonce_reorder[i])) >> 1)
            for i in range(len(auth_string))
        )
        parts = [result[:13], result[13:29], result[29:65], result[65:]]
        reordered = parts[3] + parts[0] + parts[2] + parts[1]

        final = ""
        for char in reordered:
            char_code = ord(char)
            if char_code < 0x30:
                final += (
                    chr(0x39)
                    if 0x39 < char_code + 19 < 0x41
                    else chr(char_code + 19)
                )
            elif (0x39 < char_code < 0x41) or (0x5A < char_code < 0x61):
                final += chr(char_code + 19)
            else:
                final += char
        return hashlib.md5(final.encode("utf-8")).hexdigest().upper()

    def _sfacg_headers(self, nonce, method="GET"):
        device_token = "910D166A-736E-3231-8B21-8D12DFD75F16"
        salt = "lPQDb9AKO7$LjkPG"
        timestamp = int(time.time() * 1000)
        sfsecurity = (
            f"nonce={nonce}&timestamp={timestamp}&devicetoken={device_token}"
            f"&sign={self._sfacg_sign(nonce, timestamp, device_token, salt)}"
        )
        headers = {
            "accept": "application/vnd.sfacg.api+json;version=1",
            "accept-charset": "UTF-8",
            "accept-encoding": "gzip",
            "authorization": (
                "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE="
            ),
            "sfsecurity": sfsecurity,
            "user-agent": (
                f"boluobao/5.2.16(android;35)/OPPO/"
                f"{device_token.lower()}/OPPO"
            ),
        }
        if method.upper() != "GET":
            headers["content-type"] = "application/json; charset=UTF-8"
        return headers

    def _get_sfacg_app_cookie(self):
        """Return optional SFACG app API cookie for VIP text chapters."""
        if self._sfacg_app_cookie_checked:
            return self._sfacg_app_cookie or ""
        self._sfacg_app_cookie_checked = True

        cookie = (os.environ.get("NPIA_SFACG_COOKIE") or "").strip()
        if not cookie:
            for path in (
                os.path.join(_get_base_dir(), "sfacg_app_cookie.txt"),
                os.path.join(_get_app_data_dir(), "sfacg_app_cookie.txt"),
            ):
                try:
                    if os.path.exists(path):
                        cookie = open(path, encoding="utf-8").read().strip()
                        if cookie:
                            break
                except Exception:
                    pass

        config = {}
        try:
            cfg_path = os.path.join(_get_base_dir(), "config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
        except Exception:
            config = {}

        if not cookie:
            cookie = str(config.get("sfacg_cookie") or "").strip()

        if not cookie:
            username = (
                os.environ.get("NPIA_SFACG_USER")
                or config.get("sfacg_user")
                or ""
            )
            password = (
                os.environ.get("NPIA_SFACG_PASS")
                or config.get("sfacg_pass")
                or ""
            )
            if username and password:
                cookie = self._sfacg_login(str(username), str(password))
                if cookie:
                    try:
                        os.makedirs(_get_app_data_dir(), exist_ok=True)
                        with open(
                            os.path.join(
                                _get_app_data_dir(),
                                "sfacg_app_cookie.txt",
                            ),
                            "w",
                            encoding="utf-8",
                        ) as f:
                            f.write(cookie)
                    except Exception:
                        pass

        if cookie:
            names = [
                item.split("=", 1)[0]
                for item in self._split_cookie_header(cookie)
            ]
            if "session_APP" in names:
                self.log("[SFACG] Using app API session for VIP text chapters.")
            else:
                self.log(
                    "[SFACG] App cookie configured but session_APP is missing."
                )
            self._sfacg_app_cookie = cookie
        return self._sfacg_app_cookie or ""

    def _sfacg_should_prefer_app_api(self):
        """Use SFACG's app API for all chapters when session_APP exists."""
        source_url = (
            (self._book_data or {}).get("bookUrl")
            or self._book_url
            or ""
        )
        try:
            host = urllib.parse.urlparse(source_url).hostname or ""
        except Exception:
            host = ""
        if "sfacg.com" not in host.lower():
            return False
        cookie = self._get_sfacg_app_cookie()
        names = {
            item.split("=", 1)[0]
            for item in self._split_cookie_header(cookie)
        }
        ok = "session_APP" in names
        if ok and not self._sfacg_app_prefer_logged:
            self.log("[SFACG] App session available; preferring app API for all chapters.")
            self._sfacg_app_prefer_logged = True
        return ok

    @staticmethod
    def _sfacg_chapter_id_from_url(url):
        try:
            path = urllib.parse.urlparse(url).path
        except Exception:
            path = str(url or "")
        matches = re.findall(r"\d+", path)
        return matches[-1] if matches else ""

    @staticmethod
    def _sfacg_image_name(url, index):
        path = urllib.parse.urlparse(url).path
        name = os.path.basename(path) or f"sfacg_image_{index}.jpg"
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
        if not name:
            name = f"sfacg_image_{index}.jpg"
        if "." not in name:
            name += ".jpg"
        return name

    @staticmethod
    def _sfacg_decode_content(text):
        """Decode SFACG app API text substitution into readable Chinese."""
        return str(text or "").translate(SFACG_CHAR_TRANS)

    @classmethod
    def _sfacg_content_to_outputs(cls, text):
        raw_text = cls._sfacg_decode_content(text)
        image_pattern = re.compile(
            r"\[img(?:=[^\]]*)?\](https?://.*?)\[/img\]",
            re.IGNORECASE | re.DOTALL,
        )
        images = []
        seen_urls = set()

        def image_html(url):
            url = html.unescape(url.strip())
            if url not in seen_urls:
                seen_urls.add(url)
                images.append({
                    "name": cls._sfacg_image_name(url, len(images) + 1),
                    "url": url,
                    "data": None,
                })
            safe_url = html.escape(url, quote=True)
            return f'<img src="{safe_url}" alt="" />'

        html_parts = []
        text_parts = []
        for line in raw_text.splitlines() or [""]:
            cursor = 0
            line_parts = []
            text_line = ""
            for match in image_pattern.finditer(line):
                before = line[cursor:match.start()]
                if before:
                    line_parts.append(html.escape(before))
                    text_line += before
                url = html.unescape(match.group(1).strip())
                line_parts.append(image_html(url))
                text_line += f"![image]({url})"
                cursor = match.end()
            tail = line[cursor:]
            if tail:
                line_parts.append(html.escape(tail))
                text_line += tail
            if line_parts:
                html_parts.append(f"<p>{''.join(line_parts)}</p>")
            else:
                html_parts.append("<p><br/></p>")
            text_parts.append(text_line)

        return {
            "contentHtml": f"<div>{''.join(html_parts)}</div>",
            "contentText": "\n".join(text_parts).strip(),
            "images": images,
        }

    def _sfacg_app_cookie_header(self):
        cookie = self._get_sfacg_app_cookie()
        parts = self._split_cookie_header(cookie)
        names = {item.split("=", 1)[0] for item in parts}
        if "session_APP" not in names:
            return ""
        return "; ".join(parts)

    def _sfacg_parse_chapter_app_api(
            self, chapter_url, chapter_name, requests_module=None,
            cookie_header=None):
        """Fetch one SFACG chapter directly through the mobile/app API."""
        chapter_id = self._sfacg_chapter_id_from_url(chapter_url)
        cookie_header = cookie_header or self._sfacg_app_cookie_header()
        if not chapter_id or not cookie_header:
            return None
        requests = requests_module
        if requests is None:
            try:
                import requests
            except Exception as e:
                self.log(f"[SFACG] App API unavailable: {e}")
                return None

        api_url = (
            f"https://api.sfacg.com/Chaps/{chapter_id}"
            "?expand=content%2Cexpand.content"
        )
        response = self._sfacg_api_response(
            requests, "GET", api_url, None, 30.0, cookie_header
        )
        if response is None:
            return None
        try:
            payload = response.json()
        except Exception:
            return None

        status = payload.get("status", {}).get("httpCode")
        if status in (401, 403):
            self.log(
                f"[SFACG] API chapter {chapter_id} requires an app session "
                f"with access ({status})"
            )
            return None
        if status != 200:
            return None

        data = payload.get("data") or {}
        content = "\n".join(
            part
            for part in (
                data.get("content"),
                (data.get("expand") or {}).get("content"),
            )
            if isinstance(part, str)
        ).strip()
        if not content:
            return None
        title = data.get("title") or chapter_name
        outputs = self._sfacg_content_to_outputs(content)
        return {
            "chapterName": title,
            "contentHtml": outputs["contentHtml"],
            "contentText": outputs["contentText"],
            "imageCount": len(outputs["images"]),
            "images": outputs["images"],
        }

    def login_sfacg_app(self, username, password):
        """Log in through SFACG's app API and persist session_APP cookie."""
        username = str(username or "").strip()
        password = str(password or "")
        if not username or not password:
            return False
        cookie = self._sfacg_login(username, password)
        if not cookie:
            return False
        try:
            os.makedirs(_get_app_data_dir(), exist_ok=True)
            with open(
                os.path.join(_get_app_data_dir(), "sfacg_app_cookie.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(cookie)
        except Exception as e:
            self.log(f"[SFACG] Could not save app cookie: {e}")
        self._sfacg_app_cookie = cookie
        self._sfacg_app_cookie_checked = True
        return True

    def save_sfacg_app_cookie(self, cookie):
        """Persist a user-supplied SFACG app API cookie."""
        cookie = str(cookie or "").strip()
        names = [
            item.split("=", 1)[0]
            for item in self._split_cookie_header(cookie)
        ]
        if "session_APP" not in names:
            self.log("[SFACG] App cookie import failed: session_APP missing.")
            return False
        try:
            os.makedirs(_get_app_data_dir(), exist_ok=True)
            with open(
                os.path.join(_get_app_data_dir(), "sfacg_app_cookie.txt"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(cookie)
        except Exception as e:
            self.log(f"[SFACG] Could not save app cookie: {e}")
            return False
        self._sfacg_app_cookie = cookie
        self._sfacg_app_cookie_checked = True
        self.log("[SFACG] App cookie imported.")
        return True

    @staticmethod
    def _android_sdk_default_dir():
        if sys.platform == "win32":
            local = os.environ.get("LOCALAPPDATA") or os.path.join(
                os.path.expanduser("~"), "AppData", "Local"
            )
            return os.path.join(local, "Android", "Sdk")
        return os.path.join(os.path.expanduser("~"), "Android", "Sdk")

    @staticmethod
    def _android_sdk_dir():
        for value in (
            os.environ.get("ANDROID_HOME"),
            os.environ.get("ANDROID_SDK_ROOT"),
            ExternalScraper._android_sdk_default_dir(),
        ):
            if value and os.path.exists(value):
                return value
        return ""

    @classmethod
    def _android_tool(cls, *parts):
        sdk = cls._android_sdk_dir()
        if not sdk:
            return ""
        path = os.path.join(sdk, *parts)
        return path if os.path.exists(path) else ""

    @classmethod
    def _adb_path(cls):
        return (
            cls._android_tool("platform-tools", "adb.exe")
            or shutil.which("adb")
            or ""
        )

    @classmethod
    def _emulator_path(cls):
        return (
            cls._android_tool("emulator", "emulator.exe")
            or shutil.which("emulator")
            or ""
        )

    @staticmethod
    def _android_sdk_install_instructions():
        sdk_dir = ExternalScraper._android_sdk_default_dir()
        return (
            "[Android] Android SDK Emulator is not installed.\n"
            "[Android] Install Android Studio / SDK Manager from: "
            "https://developer.android.com/studio\n"
            "[Android] sdkmanager also needs Java; Android Studio includes one, "
            "or install a JDK and set JAVA_HOME.\n"
            "[Android] In SDK Manager > SDK Tools, install: Android Emulator, "
            "Android SDK Platform-Tools, and Android SDK Command-line Tools.\n"
            f"[Android] Expected SDK path on this machine: {sdk_dir}"
        )

    @staticmethod
    def _java_exe_name():
        return "java.exe" if sys.platform == "win32" else "java"

    @classmethod
    def _java_exe_for_home(cls, java_home):
        return os.path.join(java_home, "bin", cls._java_exe_name())

    @classmethod
    def _valid_java_home(cls, java_home):
        return bool(java_home and os.path.exists(cls._java_exe_for_home(java_home)))

    @classmethod
    def _java_home_from_exe(cls, java_exe):
        if not java_exe:
            return ""
        java_exe = os.path.abspath(java_exe)
        bin_dir = os.path.dirname(java_exe)
        if os.path.basename(bin_dir).lower() != "bin":
            return ""
        java_home = os.path.dirname(bin_dir)
        return java_home if cls._valid_java_home(java_home) else ""

    @staticmethod
    def _persist_user_env_var(name, value):
        if sys.platform != "win32" or not name or not value:
            return False
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "[Environment]::SetEnvironmentVariable($args[0], $args[1], 'User')",
                    name,
                    value,
                ],
                capture_output=True,
                text=True,
                timeout=15,
                **_hidden_windows_subprocess_kwargs(),
            )
            return True
        except Exception:
            return False

    def _set_process_and_user_env(self, name, value):
        if not name or not value:
            return
        os.environ[name] = value
        self._persist_user_env_var(name, value)

    def _apply_java_home(self, env, java_home):
        java_home = os.path.abspath(java_home)
        java_bin = os.path.join(java_home, "bin")
        env["JAVA_HOME"] = java_home
        env["PATH"] = java_bin + os.pathsep + env.get("PATH", "")
        os.environ["JAVA_HOME"] = java_home
        os.environ["PATH"] = java_bin + os.pathsep + os.environ.get("PATH", "")
        self._persist_user_env_var("JAVA_HOME", java_home)
        self.log(f"[Android] JAVA_HOME set to: {java_home}")
        return env

    def _java_home_candidates(self):
        candidates = []

        def add(path):
            if path and path not in candidates:
                candidates.append(path)

        add(os.environ.get("JAVA_HOME"))
        add(self._java_home_from_exe(shutil.which(self._java_exe_name())))
        add(os.path.join(_get_app_data_dir(), "android_tools", "jdk"))

        if sys.platform == "win32":
            for path in (
                r"C:\Program Files\Android\Android Studio\jbr",
                r"C:\Program Files\Android\Android Studio\jre",
            ):
                add(path)
            for base in (
                r"C:\Program Files\Java",
                r"C:\Program Files\Eclipse Adoptium",
                r"C:\Program Files\Microsoft",
                r"C:\Program Files\Zulu",
                r"C:\Program Files (x86)\Java",
            ):
                root = Path(base)
                if root.is_dir():
                    for child in sorted(root.glob("*"), reverse=True):
                        add(str(child))

        return [path for path in candidates if self._valid_java_home(path)]

    @staticmethod
    def _adoptium_arch():
        machine = (platform.machine() or "").lower()
        if machine in ("arm64", "aarch64"):
            return "aarch64"
        return "x64"

    def _ensure_portable_jdk(self):
        jdk_dir = os.path.join(_get_app_data_dir(), "android_tools", "jdk")
        if self._valid_java_home(jdk_dir):
            return jdk_dir
        if sys.platform != "win32":
            raise RuntimeError("Java is not installed and automatic JDK install is Windows-only.")

        arch = self._adoptium_arch()
        url = (
            "https://api.adoptium.net/v3/binary/latest/21/ga/"
            f"windows/{arch}/jdk/hotspot/normal/eclipse?project=jdk"
        )
        zip_path = os.path.join(tempfile.gettempdir(), "npia_temurin_jdk.zip")
        self._download_android_sdk_file(url, zip_path, "Temurin JDK 21")

        extract_dir = os.path.join(tempfile.gettempdir(), "npia_temurin_jdk")
        shutil.rmtree(extract_dir, ignore_errors=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        java_exe = ""
        for path in Path(extract_dir).rglob(self._java_exe_name()):
            if path.parent.name.lower() == "bin":
                java_exe = str(path)
                break
        java_home = self._java_home_from_exe(java_exe)
        if not java_home:
            raise RuntimeError("Downloaded JDK did not contain bin\\java.exe.")

        os.makedirs(os.path.dirname(jdk_dir), exist_ok=True)
        shutil.rmtree(jdk_dir, ignore_errors=True)
        shutil.move(java_home, jdk_dir)
        if not self._valid_java_home(jdk_dir):
            raise RuntimeError("Portable JDK install finished, but java.exe was not found.")
        self.log(f"[Android] Portable JDK installed to: {jdk_dir}")
        return jdk_dir

    def _ensure_java_env(self, env):
        for java_home in self._java_home_candidates():
            return self._apply_java_home(env, java_home)
        self.log("[Android] Java was not found; downloading a portable JDK...")
        return self._apply_java_home(env, self._ensure_portable_jdk())

    def _download_android_sdk_file(self, url, dst, label, expected_size=0, sha1=""):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".tmp"
        self.log(f"[Android] Downloading {label}...")
        last_log = 0.0
        done = 0
        h = hashlib.sha1()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "NpiaDownloader/1.0 (+https://github.com)",
                "Accept": "application/octet-stream,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
            total = expected_size or int(resp.headers.get("content-length") or 0)
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                now = time.time()
                if now - last_log >= 5:
                    last_log = now
                    if total:
                        pct = done * 100 / total
                        self.log(
                            f"[Android] {label}: {done // (1024 * 1024)} / "
                            f"{total // (1024 * 1024)} MB ({pct:.0f}%)"
                        )
                    else:
                        self.log(
                            f"[Android] {label}: {done // (1024 * 1024)} MB"
                        )
        if sha1 and h.hexdigest().lower() != sha1.lower():
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise RuntimeError(f"{label} checksum verification failed.")
        os.replace(tmp, dst)
        return dst

    @staticmethod
    def _android_host_os():
        if sys.platform == "win32":
            return "windows"
        if sys.platform == "darwin":
            return "macosx"
        return "linux"

    def _latest_android_cmdline_tools_archive(self):
        repo_url = "https://dl.google.com/android/repository/repository2-1.xml"
        import xml.etree.ElementTree as ET

        with urllib.request.urlopen(repo_url, timeout=30) as resp:
            root = ET.fromstring(resp.read())
        host_os = self._android_host_os()
        for pkg in root.findall(".//{*}remotePackage"):
            if pkg.get("path") != "cmdline-tools;latest":
                continue
            for archive in pkg.findall(".//{*}archive"):
                host = archive.find("{*}host-os")
                if host is not None and host.text != host_os:
                    continue
                complete = archive.find("{*}complete")
                if complete is None:
                    continue
                url_el = complete.find("{*}url")
                size_el = complete.find("{*}size")
                checksum_el = complete.find("{*}checksum")
                if url_el is None or not url_el.text:
                    continue
                return {
                    "url": urllib.parse.urljoin(
                        "https://dl.google.com/android/repository/",
                        url_el.text,
                    ),
                    "size": int(size_el.text or 0) if size_el is not None else 0,
                    "sha1": checksum_el.text if checksum_el is not None else "",
                }
        raise RuntimeError("Could not find latest Android command-line tools.")

    def _ensure_android_cmdline_tools(self, sdk_dir):
        sdkmanager = os.path.join(
            sdk_dir, "cmdline-tools", "latest", "bin", "sdkmanager.bat"
        )
        if os.path.exists(sdkmanager):
            return sdkmanager

        archive = self._latest_android_cmdline_tools_archive()
        zip_path = os.path.join(tempfile.gettempdir(), "npia_android_cmdline_tools.zip")
        self._download_android_sdk_file(
            archive["url"],
            zip_path,
            "Android SDK Command-line Tools",
            expected_size=archive["size"],
            sha1=archive["sha1"],
        )

        extract_dir = os.path.join(tempfile.gettempdir(), "npia_android_cmdline_tools")
        shutil.rmtree(extract_dir, ignore_errors=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        src = os.path.join(extract_dir, "cmdline-tools")
        if not os.path.isdir(src):
            raise RuntimeError("Command-line tools archive had an unexpected layout.")
        dst_parent = os.path.join(sdk_dir, "cmdline-tools")
        dst = os.path.join(dst_parent, "latest")
        os.makedirs(dst_parent, exist_ok=True)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(src, dst)
        if not os.path.exists(sdkmanager):
            raise RuntimeError("sdkmanager.bat was not found after installation.")
        return sdkmanager

    @staticmethod
    def _avdmanager_name():
        return "avdmanager.bat" if sys.platform == "win32" else "avdmanager"

    def _avdmanager_path(self, sdk_dir):
        path = os.path.join(
            sdk_dir, "cmdline-tools", "latest", "bin", self._avdmanager_name()
        )
        return path if os.path.exists(path) else ""

    def _android_sdk_env(self, sdk_dir):
        env = os.environ.copy()
        env["ANDROID_SDK_ROOT"] = sdk_dir
        env["ANDROID_HOME"] = sdk_dir
        self._set_process_and_user_env("ANDROID_SDK_ROOT", sdk_dir)
        self._set_process_and_user_env("ANDROID_HOME", sdk_dir)
        return self._ensure_java_env(env)

    def _run_sdkmanager(self, sdkmanager, sdk_dir, env, args, label, timeout):
        license_input = ("y\n" * 80)
        self.log(f"[Android] Running sdkmanager to {label}...")
        proc = subprocess.run(
            [sdkmanager, f"--sdk_root={sdk_dir}", *args],
            input=license_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            **_hidden_windows_subprocess_kwargs(),
        )
        if proc.returncode != 0:
            output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            tail = "\n".join(output.splitlines()[-12:])
            raise RuntimeError(f"sdkmanager failed to {label}:\n{tail}")
        return proc

    def _available_android_system_images(self, sdkmanager, sdk_dir, env):
        proc = subprocess.run(
            [sdkmanager, f"--sdk_root={sdk_dir}", "--list"],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            **_hidden_windows_subprocess_kwargs(),
        )
        if proc.returncode != 0:
            output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            tail = "\n".join(output.splitlines()[-12:])
            raise RuntimeError(f"sdkmanager could not list system images:\n{tail}")

        images = []
        pattern = re.compile(
            r"(system-images;android-(\d+);([^;\s]+);([^|\s]+))"
        )
        for package, api, tag, abi in pattern.findall(proc.stdout or ""):
            images.append(
                {
                    "package": package,
                    "api": int(api),
                    "tag": tag,
                    "abi": abi,
                }
            )
        return images

    def _choose_android_system_image(self, sdkmanager, sdk_dir, env):
        images = self._available_android_system_images(sdkmanager, sdk_dir, env)
        if not images:
            raise RuntimeError("No Android emulator system images were found.")

        if self._adoptium_arch() == "aarch64":
            preferred_abis = ("arm64-v8a", "x86_64")
        else:
            preferred_abis = ("x86_64",)
        preferred_tags = ("google_apis_playstore", "google_apis", "default")

        def score(image):
            tag_score = (
                len(preferred_tags) - preferred_tags.index(image["tag"])
                if image["tag"] in preferred_tags
                else 0
            )
            abi_score = (
                len(preferred_abis) - preferred_abis.index(image["abi"])
                if image["abi"] in preferred_abis
                else 0
            )
            return tag_score, abi_score, image["api"]

        chosen = max(images, key=score)
        if score(chosen)[0] <= 0 or score(chosen)[1] <= 0:
            raise RuntimeError("No compatible Android phone system image was found.")
        return chosen

    def _android_avd_device_id(self, avdmanager, env):
        preferred = ("pixel_9", "pixel_8", "pixel_7", "pixel_6", "pixel_5", "medium_phone")
        try:
            proc = subprocess.run(
                [avdmanager, "list", "device"],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
                **_hidden_windows_subprocess_kwargs(),
            )
            output = proc.stdout or ""
        except Exception:
            output = ""
        for device in preferred:
            if f'"{device}"' in output:
                return device
        return "medium_phone"

    def _create_android_avd(self, avdmanager, env, system_image):
        avd_name = (
            "Npia_Play_Store"
            if system_image["tag"] == "google_apis_playstore"
            else "Npia_Android"
        )
        if avd_name in self.list_android_avds():
            return avd_name

        device = self._android_avd_device_id(avdmanager, env)
        self.log(
            f"[Android] Creating Android Virtual Device {avd_name} "
            f"from {system_image['package']}..."
        )
        proc = subprocess.run(
            [
                avdmanager,
                "create",
                "avd",
                "-n",
                avd_name,
                "-k",
                system_image["package"],
                "-d",
                device,
                "--force",
            ],
            input="no\n",
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
            **_hidden_windows_subprocess_kwargs(),
        )
        if proc.returncode != 0:
            output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            tail = "\n".join(output.splitlines()[-12:])
            raise RuntimeError(f"avdmanager failed to create an AVD:\n{tail}")
        self.log(f"[Android] Android Virtual Device created: {avd_name}")
        return avd_name

    def _ensure_android_avd(self):
        avds = self.list_android_avds()
        if avds:
            return avds[0]
        if sys.platform != "win32":
            return ""

        sdk_dir = self._android_sdk_dir() or self._android_sdk_default_dir()
        os.makedirs(sdk_dir, exist_ok=True)
        sdkmanager = self._ensure_android_cmdline_tools(sdk_dir)
        avdmanager = self._avdmanager_path(sdk_dir)
        if not avdmanager:
            raise RuntimeError("avdmanager.bat was not found in Android SDK tools.")

        env = self._android_sdk_env(sdk_dir)
        system_image = self._choose_android_system_image(sdkmanager, sdk_dir, env)
        self._run_sdkmanager(
            sdkmanager,
            sdk_dir,
            env,
            [
                f"platforms;android-{system_image['api']}",
                system_image["package"],
            ],
            "install Android Virtual Device image",
            1800,
        )
        return self._create_android_avd(avdmanager, env, system_image)

    def _ensure_android_emulator_tools(self):
        if sys.platform != "win32":
            self.log(self._android_sdk_install_instructions())
            return False
        if self._emulator_path():
            return True

        sdk_dir = self._android_sdk_dir() or self._android_sdk_default_dir()
        os.makedirs(sdk_dir, exist_ok=True)
        self.log(f"[Android] Installing Android SDK tools to: {sdk_dir}")
        sdkmanager = self._ensure_android_cmdline_tools(sdk_dir)

        env = self._android_sdk_env(sdk_dir)
        for args, label, timeout in (
            (["--licenses"], "accept Android SDK licenses", 300),
            (["platform-tools", "emulator"], "install Android Emulator", 900),
        ):
            self._run_sdkmanager(sdkmanager, sdk_dir, env, args, label, timeout)

        if self._emulator_path():
            self.log("[Android] Android Emulator installed.")
            return True
        raise RuntimeError("Android Emulator install finished, but emulator.exe was not found.")

    @classmethod
    def _rootavd_dir(cls):
        return os.path.join(
            _get_app_data_dir(), "android_tools", "rootAVD"
        )

    @classmethod
    def _ensure_rootavd(cls):
        root_dir = cls._rootavd_dir()
        script = os.path.join(root_dir, "rootAVD.bat")
        if os.path.exists(script):
            return root_dir
        os.makedirs(os.path.dirname(root_dir), exist_ok=True)
        tmp_zip = os.path.join(tempfile.gettempdir(), "npia_rootavd.zip")
        tmp_extract = os.path.join(tempfile.gettempdir(), "npia_rootavd")
        shutil.rmtree(tmp_extract, ignore_errors=True)
        url = "https://github.com/newbit1/rootAVD/archive/refs/heads/master.zip"
        urllib.request.urlretrieve(url, tmp_zip)
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(tmp_extract)
        extracted = next(
            (
                os.path.join(tmp_extract, name)
                for name in os.listdir(tmp_extract)
                if os.path.isdir(os.path.join(tmp_extract, name))
            ),
            "",
        )
        if not extracted:
            raise RuntimeError("rootAVD download did not contain a folder.")
        shutil.rmtree(root_dir, ignore_errors=True)
        shutil.move(extracted, root_dir)
        if not os.path.exists(script):
            raise RuntimeError("rootAVD.bat was not found after download.")
        return root_dir

    @classmethod
    def _android_avd_dir(cls, avd_name):
        path = os.path.join(
            os.path.expanduser("~"), ".android", "avd", f"{avd_name}.avd"
        )
        return path if os.path.isdir(path) else ""

    @classmethod
    def _android_avd_ramdisk_arg(cls, avd_name):
        avd_dir = cls._android_avd_dir(avd_name)
        if not avd_dir:
            return ""
        config_path = os.path.join(avd_dir, "config.ini")
        image_sysdir = ""
        try:
            with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("image.sysdir.1="):
                        image_sysdir = line.split("=", 1)[1].strip()
                        break
        except Exception:
            image_sysdir = ""
        if not image_sysdir:
            return ""
        ramdisk = os.path.join(image_sysdir, "ramdisk.img")
        sdk = cls._android_sdk_dir()
        if not sdk:
            return ""
        full = ramdisk
        if not os.path.isabs(full):
            full = os.path.join(sdk, full)
        if not os.path.exists(full):
            return ""
        return os.path.relpath(full, sdk)

    @classmethod
    def _set_android_emulator_saved_position(cls, avd_name):
        if sys.platform != "win32":
            return
        avd_dir = cls._android_avd_dir(avd_name)
        if not avd_dir:
            return
        try:
            user32 = ctypes.windll.user32
            screen_w = int(user32.GetSystemMetrics(0))
            screen_h = int(user32.GetSystemMetrics(1))
            # Approximate the default phone emulator chrome. The emulator
            # reads emulator-user.ini during startup; setting this before
            # launch avoids the visible post-launch window jump.
            window_w = min(700, max(520, screen_w // 3))
            window_h = min(1100, max(820, int(screen_h * 0.82)))
            x = max(0, (screen_w - window_w) // 2)
            y = max(0, (screen_h - window_h) // 2)
            ini_path = os.path.join(avd_dir, "emulator-user.ini")
            values = {
                "window.x": str(x),
                "window.y": str(y),
                "window.scale": "-1.000000",
            }
            lines = []
            seen = set()
            if os.path.exists(ini_path):
                with open(
                    ini_path, "r", encoding="utf-8", errors="ignore"
                ) as f:
                    for raw in f:
                        line = raw.rstrip("\r\n")
                        key = line.split("=", 1)[0].strip()
                        if key in values:
                            lines.append(f"{key} = {values[key]}")
                            seen.add(key)
                        else:
                            lines.append(line)
            for key, value in values.items():
                if key not in seen:
                    lines.append(f"{key} = {value}")
            with open(ini_path, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines).rstrip() + "\n")
        except Exception:
            pass

    def _launch_android_package(self, package, label=None, serial=None):
        label = label or package
        try:
            packages = self._adb(
                "shell",
                "pm",
                "list",
                "packages",
                package,
                timeout=20,
                serial=serial,
            )
            if package not in (packages.stdout or ""):
                self.log(f"[Android] {label} is not installed.")
                return False
            proc = self._adb(
                "shell",
                "monkey",
                "-p",
                package,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
                timeout=20,
                serial=serial,
            )
            if proc.returncode != 0:
                msg = ((proc.stdout or "") + (proc.stderr or "")).strip()
                if msg:
                    self.log(f"[Android] Could not open {label}: {msg}")
                return False
            self.log(f"[Android] Opened {label}.")
            return True
        except Exception as e:
            self.log(f"[Android] Could not open {label}: {e}")
            return False

    def _launch_sfacg_after_android_boot_async(self, avd_name=None):
        def run():
            if self._android_wait_for_device(timeout=240):
                serial = ""
                if avd_name:
                    for item_serial, item_name in self._android_devices():
                        if item_name == avd_name:
                            serial = item_serial
                            break
                self._launch_android_package(
                    "com.sfacg",
                    "SFACG",
                    serial=serial or None,
                )

        try:
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
        except Exception:
            pass

    def _android_devices(self):
        adb = self._adb_path()
        if not adb:
            return []
        try:
            proc = subprocess.run(
                [adb, "devices"],
                capture_output=True,
                text=True,
                timeout=10,
                **_hidden_windows_subprocess_kwargs(),
            )
        except Exception:
            return []
        serials = []
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        if not serials:
            return []
        named = []
        for serial in serials:
            name = ""
            try:
                p = subprocess.run(
                    [adb, "-s", serial, "emu", "avd", "name"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    **_hidden_windows_subprocess_kwargs(),
                )
                for line in (p.stdout or "").splitlines():
                    line = line.strip()
                    if line and line.upper() != "OK":
                        name = line
                        break
            except Exception:
                name = ""
            if not name:
                try:
                    p = subprocess.run(
                        [
                            adb,
                            "-s",
                            serial,
                            "shell",
                            "getprop",
                            "ro.boot.qemu.avd_name",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        **_hidden_windows_subprocess_kwargs(),
                    )
                    name = (p.stdout or "").strip()
                except Exception:
                    name = ""
            named.append((serial, name))
        return named

    def _android_serial_for_avd(self, avd_name):
        for serial, name in self._android_devices():
            if name == avd_name:
                return serial
        return ""

    def _android_avd_process_ids(self, avd_name):
        if sys.platform != "win32" or not avd_name:
            return []
        try:
            needle = avd_name.replace("'", "''").lower()
            script = (
                "$needle = '" + needle + "';"
                "Get-CimInstance Win32_Process | "
                "Where-Object { "
                "($_.Name -in @('emulator.exe','qemu-system-x86_64.exe')) "
                "-and $_.CommandLine "
                "-and $_.CommandLine.ToLowerInvariant().Contains($needle) "
                "} | Select-Object -ExpandProperty ProcessId"
            )
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=10,
                **_hidden_windows_subprocess_kwargs(),
            )
            pids = []
            for line in proc.stdout.splitlines():
                try:
                    pids.append(int(line.strip()))
                except Exception:
                    pass
            return pids
        except Exception:
            return []

    def _cleanup_stale_android_avd(self, avd_name):
        if not avd_name or self._android_serial_for_avd(avd_name):
            return False
        cleaned = False
        pids = self._android_avd_process_ids(avd_name)
        if pids:
            self.log(
                "[Android] Cleaning stale emulator process(es): "
                + ", ".join(str(pid) for pid in pids)
            )
            for pid in pids:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        **_hidden_windows_subprocess_kwargs(),
                    )
                    cleaned = True
                except Exception:
                    pass
            time.sleep(1.0)

        avd_dir = self._android_avd_dir(avd_name)
        if avd_dir and not self._android_avd_process_ids(avd_name):
            for name in ("multiinstance.lock", "hardware-qemu.ini.lock"):
                path = os.path.join(avd_dir, name)
                if os.path.exists(path):
                    try:
                        if os.path.isdir(path):
                            shutil.rmtree(path, ignore_errors=True)
                        else:
                            os.remove(path)
                        self.log(f"[Android] Removed stale lock: {name}")
                        cleaned = True
                    except Exception as e:
                        self.log(
                            f"[Android] Could not remove stale lock "
                            f"{name}: {e}"
                        )
        return cleaned

    def _android_serial(self, prefer_play=False, require_match=False):
        named = self._android_devices()
        if not named:
            return ""
        if prefer_play:
            for serial, name in named:
                if name and "play" in name.lower():
                    return serial
            if require_match:
                return ""
        for serial, name in named:
            if name and "play" not in name.lower():
                return serial
        return named[0][0]

    def _android_root_serial(self):
        """Return a connected emulator serial with adb-root or su access."""
        named = self._android_devices()
        if not named:
            return ""
        # Prefer a rooted Play Store AVD, then any other rooted emulator.
        ordered = sorted(
            named,
            key=lambda item: 0 if "play" in (item[1] or "").lower() else 1,
        )
        adb = self._adb_path()
        for serial, _name in ordered:
            if not adb:
                continue
            for args in (
                ["shell", "id"],
                ["exec-out", "su", "0", "id"],
                ["exec-out", "su", "-c", "id"],
            ):
                try:
                    proc = subprocess.run(
                        [adb, "-s", serial, *args],
                        capture_output=True,
                        timeout=10,
                        **_hidden_windows_subprocess_kwargs(),
                    )
                    output = (proc.stdout or b"") + (proc.stderr or b"")
                    if b"uid=0(root)" in output:
                        return serial
                except Exception:
                    pass
        return ""

    def _adb(
        self,
        *args,
        timeout=30,
        text=True,
        prefer_play=False,
        require_preferred=False,
        serial=None,
    ):
        adb = self._adb_path()
        if not adb:
            raise RuntimeError("Android adb not found.")
        cmd = [adb]
        if args and args[0] not in ("devices", "start-server", "kill-server"):
            serial = serial or self._android_serial(
                prefer_play=prefer_play,
                require_match=require_preferred,
            )
            if require_preferred and not serial:
                raise RuntimeError("Preferred Android emulator not connected.")
            if serial:
                cmd.extend(["-s", serial])
        cmd.extend(args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=text,
            timeout=timeout,
            **_hidden_windows_subprocess_kwargs(),
        )

    def list_android_avds(self):
        emulator = self._emulator_path()
        if not emulator:
            return []
        try:
            proc = subprocess.run(
                [emulator, "-list-avds"],
                capture_output=True,
                text=True,
                timeout=15,
                **_hidden_windows_subprocess_kwargs(),
            )
            return [
                line.strip()
                for line in proc.stdout.splitlines()
                if line.strip()
            ]
        except Exception:
            return []

    def open_android_emulator(self, avd_name=None):
        """Launch an Android emulator so the user can log in to the app."""
        emulator = self._emulator_path()
        if not emulator:
            self.log("[Android] emulator.exe not found in Android SDK.")
            try:
                if self._ensure_android_emulator_tools():
                    emulator = self._emulator_path()
            except Exception as e:
                self.log(f"[Android] Automatic SDK install failed: {e}")
                self.log(self._android_sdk_install_instructions())
                return False
            if not emulator:
                self.log(self._android_sdk_install_instructions())
                return False
        avds = self.list_android_avds()
        avd_name = avd_name or next(
            (
                name
                for name in avds
                if "play" in name.lower()
            ),
            next(
                (
                    name
                    for name in avds
                    if "play" not in name.lower()
                ),
                "",
            ),
        )
        avd_name = avd_name or (avds[0] if avds else "")
        if not avd_name:
            self.log("[Android] No Android Virtual Devices found; creating one...")
            try:
                avd_name = self._ensure_android_avd()
                avds = self.list_android_avds()
            except Exception as e:
                self.log(f"[Android] Automatic AVD creation failed: {e}")
                self.log(
                    "[Android] Open Android Studio > Device Manager and create an AVD, "
                    "preferably a Play Store image for login.\n"
                    "[Android] Then click Enter App > Open Android again."
                )
                return False
            if not avd_name:
                self.log(
                    "[Android] Open Android Studio > Device Manager and create an AVD, "
                    "preferably a Play Store image for login.\n"
                    "[Android] Then click Enter App > Open Android again."
                )
                return False
        if "play" not in avd_name.lower():
            self.log(
                "[Android] Using rootable AVD for SFACG import. "
                "Facebook/SFACG must be installed there."
            )
        running_serial = self._android_serial_for_avd(avd_name)
        if running_serial:
            self.log(
                f"[Android] {avd_name} is already running "
                f"({running_serial}); reusing it."
            )
            self._launch_android_package(
                "com.sfacg",
                "SFACG",
                serial=running_serial,
            )
            return True

        self._cleanup_stale_android_avd(avd_name)
        self.log(f"[Android] Launching emulator: {avd_name}")
        self._set_android_emulator_saved_position(avd_name)
        args = [
            emulator,
            "-avd",
            avd_name,
            "-no-snapshot-load",
            "-no-metrics",
            "-memory",
            "4096",
        ]
        if "play" in avd_name.lower():
            args.extend(["-gpu", "swiftshader_indirect"])
        try:
            proc = subprocess.Popen(
                args,
                cwd=os.path.dirname(emulator),
            )
            self._launch_sfacg_after_android_boot_async(avd_name)
            return True
        except Exception as e:
            self.log(f"[Android] Could not launch emulator: {e}")
            return False

    def restore_android_play_avd(self):
        """Restore a Play Store AVD ramdisk if rootAVD made it unbootable."""
        if sys.platform != "win32":
            self.log("[Android] Play Store AVD restore is Windows-only here.")
            return False
        avds = self.list_android_avds()
        avd_name = next(
            (name for name in avds if "play" in name.lower()),
            "",
        )
        if not avd_name:
            self.log("[Android] No Play Store AVD found to restore.")
            return False
        ramdisk_arg = self._android_avd_ramdisk_arg(avd_name)
        if not ramdisk_arg:
            self.log(
                "[Android] Could not find this AVD's Play Store ramdisk.img."
            )
            return False
        sdk = self._android_sdk_dir()
        ramdisk = os.path.join(sdk, ramdisk_arg)
        backup = ramdisk + ".backup"
        if not os.path.exists(backup):
            self.log("[Android] No rootAVD ramdisk backup was found.")
            return False
        try:
            shutil.copy2(backup, ramdisk)
        except Exception as e:
            self.log(f"[Android] Could not restore Play Store AVD: {e}")
            return False
        self.log(
            "[Android] Play Store AVD restored to its pre-root ramdisk. "
            "Open Android again and it should boot normally."
        )
        return True

    def _android_wait_for_device(self, timeout=120):
        deadline = time.time() + timeout
        try:
            self._adb("start-server", timeout=15)
        except Exception:
            pass
        while time.time() < deadline:
            try:
                proc = self._adb("devices", timeout=10)
                lines = [
                    line.strip()
                    for line in proc.stdout.splitlines()
                    if line.strip().endswith("\tdevice")
                ]
                if lines:
                    boot = self._adb(
                        "shell",
                        "getprop",
                        "sys.boot_completed",
                        timeout=10,
                    )
                    if boot.stdout.strip() == "1":
                        return True
            except Exception:
                pass
            time.sleep(2)
        return False

    def _android_sfacg_packages(self, serial=None):
        try:
            proc = self._adb(
                "shell",
                "pm",
                "list",
                "packages",
                timeout=20,
                serial=serial,
            )
        except Exception as e:
            self.log(f"[Android] Could not list packages: {e}")
            return []
        packages = []
        for line in proc.stdout.splitlines():
            name = line.replace("package:", "").strip()
            lower = name.lower()
            if "sfacg" in lower or "boluobao" in lower:
                packages.append(name)
        return packages

    def _android_exec_out(self, *args, timeout=30, serial=None):
        adb = self._adb_path()
        if not adb:
            raise RuntimeError("Android adb not found.")
        cmd = [adb]
        serial = serial or self._android_serial()
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(["exec-out", *args])
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            **_hidden_windows_subprocess_kwargs(),
        )
        return proc.stdout if proc.returncode == 0 else b""

    def _android_read_file(self, path, serial=None):
        data = self._android_exec_out("cat", path, timeout=30, serial=serial)
        if data:
            return data
        data = self._android_exec_out(
            "su", "0", "cat", path, timeout=30, serial=serial
        )
        if data:
            return data
        quoted = shlex.quote(path)
        return self._android_exec_out(
            "su", "-c", f"cat {quoted}", timeout=30, serial=serial
        )

    @staticmethod
    def _sfacg_cookie_from_sqlite_bytes(raw):
        fd, temp_path = tempfile.mkstemp(prefix="sfacg_cookies_", suffix=".db")
        os.close(fd)
        try:
            with open(temp_path, "wb") as f:
                f.write(raw)
            conn = sqlite3.connect(temp_path)
            try:
                rows = conn.execute(
                    "SELECT name, value, encrypted_value FROM cookies"
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            return "", False
        finally:
            try:
                os.remove(temp_path)
            except Exception:
                pass

        values = {}
        encrypted = False
        for name, value, encrypted_value in rows:
            if name in (".SFCommunity", "session_APP"):
                if value:
                    values[name] = value
                elif encrypted_value:
                    encrypted = True
        if values.get("session_APP"):
            parts = []
            if values.get(".SFCommunity"):
                parts.append(f".SFCommunity={values['.SFCommunity']}")
            parts.append(f"session_APP={values['session_APP']}")
            return "; ".join(parts), encrypted
        return "", encrypted

    @staticmethod
    def _sfacg_cookie_from_text(raw):
        try:
            text = raw.decode("utf-8", errors="ignore")
        except Exception:
            return ""
        session = re.search(
            r"session_APP\s*[=:\"]+\s*([^;\"<>\s]+)",
            text,
        )
        community = re.search(
            r"\.SFCommunity\s*[=:\"]+\s*([^;\"<>\s]+)",
            text,
        )
        if not session:
            return ""
        parts = []
        if community:
            parts.append(f".SFCommunity={community.group(1)}")
        parts.append(f"session_APP={session.group(1)}")
        return "; ".join(parts)

    def import_sfacg_app_cookie_from_android(self):
        """Try to extract session_APP from an emulator after app login."""
        if not self._android_wait_for_device():
            self.log("[Android] No booted emulator/device found.")
            return False
        root_serial = self._android_root_serial()
        try:
            root_proc = self._adb("root", timeout=15)
            time.sleep(1)
        except Exception:
            root_proc = None
        try:
            id_proc = self._adb("shell", "id", timeout=10)
            if (
                "uid=0(root)" not in (id_proc.stdout or "")
                and not root_serial
            ):
                msg = (
                    (root_proc.stdout if root_proc else "")
                    + (root_proc.stderr if root_proc else "")
                ).strip()
                self.log(
                    "[Android] Active emulator is not rootable, so app "
                    "private data cannot be read."
                )
                if msg:
                    self.log(f"[Android] adb root response: {msg}")
                self.log(
                    "[Android] Rooted Play Store AVDs are supported if "
                    "Magisk/su is available. Otherwise use the rootable AVD "
                    "(syfe_poc_api35) for SFACG login/import."
                )
                return False
            if root_serial and "uid=0(root)" not in (id_proc.stdout or ""):
                self.log(
                    "[Android] Using su/root access for SFACG app data "
                    f"on {root_serial}."
                )
        except Exception:
            pass
        if not root_serial:
            root_serial = self._android_serial()
        packages = self._android_sfacg_packages(serial=root_serial)
        if not packages:
            self.log(
                "[Android] SFACG app package not found. Install/login in the "
                "SFACG app from Play Store first."
            )
            return False
        encrypted_seen = False
        for package in packages:
            self.log(f"[Android] Checking SFACG package: {package}")
            paths = [
                f"/data/data/{package}/app_webview/Default/Cookies",
                f"/data/data/{package}/app_webview/Cookies",
                f"/data/data/{package}/app_webview/Default/Network/Cookies",
            ]
            try:
                find_proc = self._adb(
                    "shell",
                    "find",
                    f"/data/data/{package}",
                    "-type",
                    "f",
                    timeout=30,
                    serial=root_serial,
                )
                for line in find_proc.stdout.splitlines():
                    path = line.strip()
                    lower = path.lower()
                    if (
                        "cookie" in lower
                        or "shared_prefs" in lower
                        or lower.endswith((".db", ".xml", ".json"))
                    ):
                        paths.append(path)
            except Exception:
                try:
                    data = self._android_exec_out(
                        "su",
                        "-c",
                        f"find /data/data/{package} -type f",
                        timeout=30,
                        serial=root_serial,
                    )
                    for line in data.decode("utf-8", "ignore").splitlines():
                        path = line.strip()
                        lower = path.lower()
                        if (
                            "cookie" in lower
                            or "shared_prefs" in lower
                            or lower.endswith((".db", ".xml", ".json"))
                        ):
                            paths.append(path)
                except Exception:
                    pass
            for path in dict.fromkeys(paths):
                raw = self._android_read_file(path, serial=root_serial)
                if not raw:
                    continue
                cookie, encrypted = self._sfacg_cookie_from_sqlite_bytes(raw)
                encrypted_seen = encrypted_seen or encrypted
                if not cookie:
                    cookie = self._sfacg_cookie_from_text(raw)
                if cookie and self.save_sfacg_app_cookie(cookie):
                    self.log(f"[Android] Imported session_APP from {package}.")
                    return True
        if encrypted_seen:
            self.log(
                "[Android] Found encrypted WebView cookies, but could not "
                "decrypt them from outside the app."
            )
        else:
            self.log("[Android] session_APP was not found in app data.")
        return False

    def _sfacg_login(self, username, password):
        payload = json.dumps({
            "password": password,
            "shuMeiId": "",
            "username": username,
        })
        try:
            import requests

            session = requests.Session()
            response = self._sfacg_api_response(
                session,
                "POST",
                "https://api.sfacg.com/sessions",
                payload,
                30.0,
                "",
            )
            if response is None:
                return ""
            data = response.json()
            if data.get("status", {}).get("httpCode") == 200:
                cookies = requests.utils.dict_from_cookiejar(session.cookies)
                sfcommunity = cookies.get(".SFCommunity")
                session_app = cookies.get("session_APP")
                if sfcommunity and session_app:
                    self.log("[SFACG] App API login succeeded.")
                    return (
                        f".SFCommunity={sfcommunity}; "
                        f"session_APP={session_app}"
                    )
            msg = data.get("status", {}).get("msg") or response.reason
            self.log(f"[SFACG] App API login failed: {msg}")
        except Exception as e:
            self.log(f"[SFACG] App API login failed: {e}")
        return ""

    @staticmethod
    def _get_user_data_dir():
        """Get persistent browser data directory for cookies/localStorage.

        Older builds kept browser_data next to the exe. That breaks when a
        user extracts a new release into a different folder or runs from a
        read-only install location, so new builds use a stable per-user app
        data path and migrate the old folder once when possible.
        """
        legacy_dir = os.path.join(_get_base_dir(), 'browser_data')
        stable_dir = os.path.join(_get_app_data_dir(), 'browser_data')

        if (os.path.abspath(legacy_dir) != os.path.abspath(stable_dir)
                and _dir_has_entries(legacy_dir)
                and not _dir_has_entries(stable_dir)):
            try:
                os.makedirs(os.path.dirname(stable_dir), exist_ok=True)
                shutil.copytree(legacy_dir, stable_dir, dirs_exist_ok=True)
            except Exception:
                pass

        if _is_writable_dir(stable_dir):
            return stable_dir
        if _is_writable_dir(legacy_dir):
            return legacy_dir

        fallback = os.path.join(
            os.path.expanduser("~"), f".{APP_DATA_NAME}", "browser_data"
        )
        os.makedirs(fallback, exist_ok=True)
        return fallback

    @classmethod
    def _get_storage_state_path(cls):
        return os.path.join(cls._get_user_data_dir(), 'nd_storage_state.json')

    @classmethod
    def _get_ntk_user_data_dir(cls):
        """Dedicated Chrome profile for NewToki Cloudflare/CDP sessions."""
        path = os.path.join(_get_app_data_dir(), 'ntk_chrome_profile')
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def _find_chrome_executable():
        """Find an installed Chrome/Edge executable for anti-bot login pages."""
        env_path = os.environ.get("NPIA_CHROME_PATH")
        candidates = [env_path] if env_path else []
        if sys.platform == "win32":
            program_files = [
                os.environ.get("PROGRAMFILES"),
                os.environ.get("PROGRAMFILES(X86)"),
                os.environ.get("LOCALAPPDATA"),
            ]
            for root in filter(None, program_files):
                candidates.extend([
                    os.path.join(root, "Google", "Chrome", "Application",
                                 "chrome.exe"),
                    os.path.join(root, "Microsoft", "Edge", "Application",
                                 "msedge.exe"),
                ])
        elif sys.platform == "darwin":
            candidates.extend([
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            ])
        else:
            candidates.extend([
                shutil.which("google-chrome"),
                shutil.which("google-chrome-stable"),
                shutil.which("chromium"),
                shutil.which("chromium-browser"),
                shutil.which("microsoft-edge"),
            ])

        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    @staticmethod
    def _get_free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @staticmethod
    def _wait_for_cdp(port, timeout=15):
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{port}/json/version"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1) as r:
                    if r.status == 200:
                        return True
            except Exception:
                time.sleep(0.25)
        return False

    def _request_cdp_browser_close(self, port):
        """Ask Chrome to close gracefully through the DevTools endpoint."""
        if not port:
            return False
        try:
            import websocket
        except Exception:
            return False

        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version",
                timeout=2,
            ) as r:
                data = json.loads(r.read().decode("utf-8", errors="ignore"))
            ws_url = data.get("webSocketDebuggerUrl")
            if not ws_url:
                return False
            ws = websocket.create_connection(ws_url, timeout=3)
            try:
                ws.send(json.dumps({
                    "id": 1,
                    "method": "Browser.close",
                }))
                try:
                    ws.recv()
                except Exception:
                    pass
            finally:
                try:
                    ws.close()
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def _close_ntk_profile_chrome(self, user_data_dir):
        """Close Chrome instances that own the dedicated NewToki profile."""
        if sys.platform != "win32":
            return 0
        profile = os.path.abspath(user_data_dir).replace("'", "''")
        script = (
            "$needle = '" + profile + "'.ToLowerInvariant();\n"
            "$profileName = 'ntk_chrome_profile';\n"
            "$matches = @(Get-CimInstance Win32_Process -Filter "
            "\"name = 'chrome.exe'\" | Where-Object { "
            "$_.CommandLine -and "
            "($_.CommandLine.ToLowerInvariant().Contains($needle) -or "
            "$_.CommandLine.ToLowerInvariant().Contains($profileName)) "
            "});\n"
            "$count = $matches.Count;\n"
            "foreach ($p in $matches) { "
            "try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } "
            "catch {} "
            "};\n"
            "$count\n"
        )
        try:
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=8,
                errors="ignore",
                **_hidden_windows_subprocess_kwargs(),
            ).strip()
            count = int(output.splitlines()[-1]) if output else 0
        except Exception:
            return 0
        if count:
            time.sleep(1.0)
        return count

    @staticmethod
    def _chrome_processes_using_profile(user_data_dir):
        """Return browser PIDs that already have a persistent profile open."""
        if sys.platform != "win32":
            return []
        profile = os.path.abspath(user_data_dir).replace("'", "''")
        script = (
            "$needle = '" + profile + "'.ToLowerInvariant();\n"
            "$names = @('chrome.exe', 'msedge.exe', 'chromium.exe');\n"
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -and $names.Contains($_.Name.ToLowerInvariant()) -and "
            "$_.CommandLine -and "
            "$_.CommandLine.ToLowerInvariant().Contains($needle) "
            "} | Select-Object -ExpandProperty ProcessId\n"
        )
        try:
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
                errors="ignore",
                **_hidden_windows_subprocess_kwargs(),
            )
        except Exception:
            return []
        pids = []
        for line in output.splitlines():
            try:
                pids.append(int(line.strip()))
            except Exception:
                pass
        return pids

    @staticmethod
    def _chrome_remote_debugging_ports_using_profile(user_data_dir):
        """Return CDP ports for browser processes using a profile."""
        if sys.platform != "win32":
            return []
        profile = os.path.abspath(user_data_dir).replace("'", "''")
        script = (
            "$needle = '" + profile + "'.ToLowerInvariant();\n"
            "$names = @('chrome.exe', 'msedge.exe', 'chromium.exe');\n"
            "$ports = @();\n"
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -and $names.Contains($_.Name.ToLowerInvariant()) -and "
            "$_.CommandLine -and "
            "$_.CommandLine.ToLowerInvariant().Contains($needle) "
            "} | ForEach-Object { "
            "if ($_.CommandLine -match '--remote-debugging-port=(\\d+)') { "
            "$ports += $Matches[1] "
            "} "
            "};\n"
            "$ports | Select-Object -Unique\n"
        )
        try:
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
                errors="ignore",
                **_hidden_windows_subprocess_kwargs(),
            )
        except Exception:
            return []
        ports = []
        for line in output.splitlines():
            try:
                ports.append(int(line.strip()))
            except Exception:
                pass
        return ports

    @staticmethod
    def _visible_browser_window_pids(pids):
        """Return PIDs from `pids` that own a visible Chrome/Edge window."""
        if sys.platform != "win32" or not pids:
            return []
        try:
            user32 = ctypes.windll.user32
            target_pids = {int(pid) for pid in pids}
            visible_pids = set()
            gw_owner = 4

            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
            )

            def _class_name(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                if user32.GetClassNameW(hwnd, buf, len(buf)) <= 0:
                    return ""
                return buf.value

            def callback(hwnd, _):
                if not user32.IsWindowVisible(hwnd):
                    return True
                if user32.GetWindow(hwnd, gw_owner):
                    return True
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                proc_id = int(pid.value)
                if proc_id not in target_pids:
                    return True
                class_name = _class_name(hwnd)
                if class_name and not class_name.startswith("Chrome_WidgetWin_"):
                    return True
                visible_pids.add(proc_id)
                return True

            user32.EnumWindows(EnumWindowsProc(callback), 0)
            return sorted(visible_pids)
        except Exception:
            return []

    def _wait_for_profile_processes_to_exit(self, user_data_dir, timeout=12):
        """Wait briefly for Chrome profile processes to exit after UI close."""
        deadline = time.time() + timeout
        pids = self._chrome_processes_using_profile(user_data_dir)
        while pids and time.time() < deadline:
            time.sleep(0.25)
            pids = self._chrome_processes_using_profile(user_data_dir)
        return pids

    def _close_chrome_profile_processes_async(self, user_data_dir):
        """Deprecated: do not force-kill login profile Chrome processes."""
        def cleanup():
            leftovers = self._chrome_processes_using_profile(user_data_dir)
            if leftovers:
                self.log(
                    "[Browser] Chrome still has the login profile open. "
                    "Not force-closing it, to avoid losing cookies. Close "
                    "the remaining Chrome processes before downloading."
                )

        threading.Thread(target=cleanup, daemon=True).start()

    def _wait_for_system_chrome_close(self, proc, user_data_dir, port=None):
        """Wait until the normal Chrome login window has been closed."""
        if sys.platform != "win32":
            proc.wait()
            return True

        saw_window = False
        profile_pids = set()
        next_pid_scan = 0
        launch_deadline = time.time() + 15
        reported_profile_wait = False

        while not self._stop_requested:
            now = time.time()
            if now >= next_pid_scan:
                profile_pids = set(
                    self._chrome_processes_using_profile(user_data_dir)
                )
                if proc.poll() is None:
                    profile_pids.add(int(proc.pid))
                next_pid_scan = now + 1

            window_pids = self._visible_browser_window_pids(profile_pids)
            if window_pids:
                saw_window = True
            elif saw_window:
                self.log(
                    "[Browser] Chrome window closed; waiting for Chrome to "
                    "finish saving the login profile..."
                )
                leftovers = self._wait_for_profile_processes_to_exit(
                    user_data_dir
                )
                if leftovers:
                    if port and self._request_cdp_browser_close(port):
                        leftovers = self._wait_for_profile_processes_to_exit(
                            user_data_dir, timeout=12
                        )
                        if not leftovers:
                            return True
                    self.log(
                        "[Browser] Chrome window closed, but Chrome kept "
                        "the login profile open. Not force-closing it, "
                        "because that can lose cookies. Wait a few seconds "
                        "or close the remaining Chrome processes, then try "
                        "Download again."
                    )
                    return False
                return True
            elif proc.poll() is not None and not profile_pids:
                return True
            elif proc.poll() is not None and time.time() < launch_deadline:
                if not reported_profile_wait:
                    remaining = max(1, int(launch_deadline - time.time()))
                    self.log(
                        "[Browser] Chrome window closed; waiting up to "
                        f"{remaining}s for profile background processes to "
                        "finish saving..."
                    )
                    reported_profile_wait = True
            elif proc.poll() is not None and time.time() >= launch_deadline:
                leftovers = self._chrome_processes_using_profile(user_data_dir)
                if leftovers:
                    if port and self._request_cdp_browser_close(port):
                        leftovers = self._wait_for_profile_processes_to_exit(
                            user_data_dir, timeout=12
                        )
                        if not leftovers:
                            return True
                    self.log(
                        "[Browser] Chrome exited, but the login profile is "
                        "still open. Not force-closing it, because that can "
                        "lose cookies. Close the remaining Chrome processes, "
                        "then try Download again."
                    )
                    return False
                return True

            time.sleep(0.25)

        self.log(
            "[Browser] Stop requested while Chrome was open. Leaving Chrome "
            "running so the login profile can save cleanly."
        )
        return False

    def _close_chrome_profile_processes(self, user_data_dir):
        """Do not force-close the shared login profile; it can lose cookies."""
        pids = self._chrome_processes_using_profile(user_data_dir)
        if pids:
            self.log(
                "[Browser] Refusing to force-close the shared login profile "
                f"process(es): {', '.join(str(pid) for pid in pids)}"
            )
        return []

    @staticmethod
    def _is_profile_lock_error(error):
        msg = str(error)
        return (
            "Opening in existing browser session" in msg
            or "profile is already in use" in msg
            or "ProcessSingleton" in msg
        )

    def _close_ntk_temp_chrome_now(self):
        """Force-close the temporary NTK Chrome without waiting on storage APIs."""
        try:
            if self._chrome_process and self._chrome_process.poll() is None:
                self._chrome_process.terminate()
                try:
                    self._chrome_process.wait(timeout=1.5)
                except Exception:
                    self._chrome_process.kill()
        except Exception:
            pass
        closed = 0
        try:
            closed = self._close_ntk_profile_chrome(
                self._get_ntk_user_data_dir()
            )
        except Exception:
            closed = 0
        self._page = None
        self._context = None
        self._browser = None
        self._chrome_process = None
        self._ntk_temp_chrome = False
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        return closed

    def _focus_system_chrome_window(self):
        """Best-effort: bring the visible Chrome window to the foreground."""
        if sys.platform != "win32" or not self._chrome_process:
            return False
        try:
            user32 = ctypes.windll.user32
            target_pid = int(self._chrome_process.pid)
            found = []

            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
            )

            def _title(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return ""
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                return buf.value

            def callback(hwnd, _):
                if not user32.IsWindowVisible(hwnd):
                    return True
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                title = _title(hwnd)
                if pid.value == target_pid or (
                    "Chrome" in title and (
                        "ntk" in title.lower()
                        or "뉴토끼" in title
                        or "용사파티" in title
                    )
                ):
                    found.append(hwnd)
                    return False
                return True

            user32.EnumWindows(EnumWindowsProc(callback), 0)
            if not found:
                return False
            hwnd = found[0]
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    @staticmethod
    def _force_foreground_window(hwnd):
        """Best-effort: restore, raise, and focus a Windows top-level window."""
        if sys.platform != "win32" or not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            hwnd_topmost = -1
            hwnd_notopmost = -2
            sw_restore = 9
            swp_nosize = 0x0001
            swp_nomove = 0x0002
            swp_showwindow = 0x0040

            try:
                user32.AllowSetForegroundWindow(-1)
            except Exception:
                pass
            user32.ShowWindow(hwnd, sw_restore)
            try:
                user32.BringWindowToTop(hwnd)
            except Exception:
                pass
            # Flash it to the front without leaving it permanently topmost.
            user32.SetWindowPos(
                hwnd, hwnd_topmost, 0, 0, 0, 0,
                swp_nomove | swp_nosize | swp_showwindow
            )
            user32.SetWindowPos(
                hwnd, hwnd_notopmost, 0, 0, 0, 0,
                swp_nomove | swp_nosize | swp_showwindow
            )
            try:
                user32.SwitchToThisWindow(hwnd, True)
            except Exception:
                pass
            return bool(user32.SetForegroundWindow(hwnd))
        except Exception:
            return False

    @staticmethod
    def _centered_chrome_window_geometry():
        """Return x, y, width, height for a centered Chrome window."""
        width, height = 1440, 960
        if sys.platform != "win32":
            return 80, 60, width, height
        try:
            user32 = ctypes.windll.user32

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            spi_get_work_area = 0x0030
            ok = user32.SystemParametersInfoW(
                spi_get_work_area, 0, ctypes.byref(rect), 0
            )
            if ok:
                left, top = rect.left, rect.top
                work_w = max(800, rect.right - rect.left)
                work_h = max(600, rect.bottom - rect.top)
            else:
                left, top = 0, 0
                work_w = max(800, user32.GetSystemMetrics(0))
                work_h = max(600, user32.GetSystemMetrics(1))
            max_w = max(640, work_w - 40)
            max_h = max(480, work_h - 40)
            width = min(max(800, int(work_w * 0.50)), max_w)
            height = min(max(600, int(work_h * 0.50)), max_h)
            x = left + max(0, (work_w - width) // 2)
            y = top + max(0, (work_h - height) // 2)
            return x, y, width, height
        except Exception:
            return 80, 60, width, height

    @classmethod
    def _centered_chrome_window_args(cls):
        x, y, width, height = cls._centered_chrome_window_geometry()
        return [
            f"--window-position={x},{y}",
            f"--window-size={width},{height}",
        ]

    @staticmethod
    def _clear_chrome_restore_state(user_data_dir):
        """Best-effort: prevent Chrome's crash restore bubble for app profile."""
        try:
            profile_root = Path(user_data_dir)
            prefs_paths = []
            direct = profile_root / "Default" / "Preferences"
            if direct.exists():
                prefs_paths.append(direct)
            for item in profile_root.iterdir():
                prefs = item / "Preferences"
                if prefs.exists() and prefs not in prefs_paths:
                    prefs_paths.append(prefs)

            for prefs_path in prefs_paths:
                try:
                    with open(prefs_path, "r", encoding="utf-8") as f:
                        prefs = json.load(f)
                    profile = prefs.setdefault("profile", {})
                    profile["exit_type"] = "Normal"
                    profile["exited_cleanly"] = True
                    with open(prefs_path, "w", encoding="utf-8") as f:
                        json.dump(prefs, f, separators=(",", ":"))
                except Exception:
                    pass
        except Exception:
            pass

    def _center_profile_browser_windows(self, user_data_dir, timeout=5,
                                        focus=False):
        """Center visible Chrome/Edge windows that use the app profile."""
        if sys.platform != "win32":
            return False
        deadline = time.time() + timeout
        moved_any = False
        x, y, width, height = self._centered_chrome_window_geometry()
        while time.time() < deadline:
            pids = set(self._chrome_processes_using_profile(user_data_dir))
            if self._chrome_process and self._chrome_process.poll() is None:
                pids.add(int(self._chrome_process.pid))
            if not pids:
                time.sleep(0.1)
                continue

            try:
                user32 = ctypes.windll.user32
                found = []
                EnumWindowsProc = ctypes.WINFUNCTYPE(
                    ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
                )

                def _class_name(hwnd):
                    buf = ctypes.create_unicode_buffer(256)
                    if user32.GetClassNameW(hwnd, buf, len(buf)) <= 0:
                        return ""
                    return buf.value

                def callback(hwnd, _):
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    if user32.GetWindow(hwnd, 4):  # GW_OWNER
                        return True
                    pid = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if int(pid.value) not in pids:
                        return True
                    class_name = _class_name(hwnd)
                    if class_name and not class_name.startswith(
                        "Chrome_WidgetWin_"
                    ):
                        return True
                    found.append(hwnd)
                    return True

                user32.EnumWindows(EnumWindowsProc(callback), 0)
                for hwnd in found:
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.MoveWindow(hwnd, x, y, width, height, True)
                    moved_any = True
                if found:
                    if focus:
                        self._force_foreground_window(found[0])
                    return True
            except Exception:
                return moved_any
            time.sleep(0.1)
        return moved_any

    def _hide_ntk_chrome_windows(self):
        """Best-effort: hide windows created by the temporary NTK Chrome."""
        return self._hide_chrome_windows_for_profile(
            self._get_ntk_user_data_dir()
        )

    def _hide_chrome_windows_for_profile(self, user_data_dir):
        """Best-effort: hide Chrome windows for a specific app profile."""
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            profile = os.path.normcase(user_data_dir)
            chrome_pids = set()

            try:
                script = (
                    "$needle = '" + profile.replace("'", "''") + "'.ToLowerInvariant();"
                    "$names = @('chrome.exe', 'msedge.exe', 'chromium.exe');"
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.Name -and $names.Contains($_.Name.ToLowerInvariant()) -and "
                    "$_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($needle) } | "
                    "Select-Object -ExpandProperty ProcessId"
                )
                output = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", script],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    errors="ignore",
                    **_hidden_windows_subprocess_kwargs(),
                )
                for line in output.splitlines():
                    try:
                        chrome_pids.add(int(line.strip()))
                    except Exception:
                        pass
            except Exception:
                pass
            if self._chrome_process:
                chrome_pids.add(int(self._chrome_process.pid))
            if not chrome_pids:
                return False

            hidden = []
            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
            )

            def callback(hwnd, _):
                if not user32.IsWindowVisible(hwnd):
                    return True
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if int(pid.value) in chrome_pids:
                    user32.ShowWindow(hwnd, 0)  # SW_HIDE
                    hidden.append(hwnd)
                return True

            user32.EnumWindows(EnumWindowsProc(callback), 0)
            return bool(hidden)
        except Exception:
            return False

    def _park_chrome_windows_for_profile(self, user_data_dir):
        """Move headed Chrome off-screen without minimizing or hiding it.

        Novelpia rejects viewer requests when Chrome is minimized or its
        window is hidden. Keeping the native window in the normal restored
        state preserves headed-browser visibility while preventing it from
        covering the user's desktop.
        """
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            chrome_pids = set(
                self._chrome_processes_using_profile(user_data_dir)
            )
            if self._chrome_process:
                chrome_pids.add(int(self._chrome_process.pid))
            if not chrome_pids:
                return False

            moved = []
            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
            )
            sw_restore = 9
            swp_nozorder = 0x0004
            swp_noactivate = 0x0010
            swp_showwindow = 0x0040

            def callback(hwnd, _):
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if int(pid.value) not in chrome_pids:
                    return True
                class_name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_name, len(class_name))
                if not class_name.value.startswith("Chrome_WidgetWin_"):
                    return True

                # Keep the window restored and visible to Chromium, but park
                # it beyond the desktop bounds. Do not call SW_HIDE.
                user32.ShowWindow(hwnd, sw_restore)
                user32.SetWindowPos(
                    hwnd,
                    0,
                    -32000,
                    -32000,
                    1280,
                    900,
                    swp_nozorder | swp_noactivate | swp_showwindow,
                )
                moved.append(hwnd)
                return True

            user32.EnumWindows(EnumWindowsProc(callback), 0)
            return bool(moved)
        except Exception:
            return False

    @staticmethod
    def _windows_click_screen(x, y):
        """Perform a real OS mouse click at screen coordinates."""
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            user32.SetCursorPos(int(x), int(y))
            time.sleep(0.08)
            mouseeventf_leftdown = 0x0002
            mouseeventf_leftup = 0x0004
            user32.mouse_event(mouseeventf_leftdown, 0, 0, 0, 0)
            time.sleep(0.06)
            user32.mouse_event(mouseeventf_leftup, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    @staticmethod
    def _windows_clipboard_get_text():
        if sys.platform != "win32":
            return None
        try:
            import tkinter as _tk
            root = _tk.Tk()
            root.withdraw()
            try:
                return root.clipboard_get()
            except Exception:
                return ''
            finally:
                root.destroy()
        except Exception:
            return None

    @staticmethod
    def _windows_clipboard_set_text(text):
        if sys.platform != "win32":
            return False
        try:
            import tkinter as _tk
            root = _tk.Tk()
            root.withdraw()
            try:
                root.clipboard_clear()
                if text:
                    root.clipboard_append(text)
                root.update()
                return True
            finally:
                root.destroy()
        except Exception:
            return False

    def _open_system_chrome(self, start_url, remote_debugging=False,
                            user_data_dir=None, hidden=False,
                            headless=False, window_size=None,
                            window_position=None):
        """Open installed Chrome as a normal process using our profile."""
        chrome_path = self._find_chrome_executable()
        if not chrome_path:
            return None, None

        user_data_dir = user_data_dir or self._get_user_data_dir()
        os.makedirs(user_data_dir, exist_ok=True)
        if not self._chrome_processes_using_profile(user_data_dir):
            self._clear_chrome_restore_state(user_data_dir)
        args = [
            chrome_path,
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--disable-background-mode",
            "--disable-features=Translate",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
        ]
        if headless:
            args.extend([
                "--headless=new",
                "--window-size=1280,900",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
            ])
        elif hidden:
            args.extend([
                "--start-minimized",
                "--window-position=-32000,-32000",
                "--window-size=1280,900",
                "--disable-backgrounding-occluded-windows",
            ])
        else:
            if window_size:
                width, height = window_size
                if window_position:
                    x, y = window_position
                else:
                    x, y = 40, 40
                window_args = [
                    f"--window-position={int(x)},{int(y)}",
                    f"--window-size={int(width)},{int(height)}",
                ]
            else:
                window_args = self._centered_chrome_window_args()
            args.extend([
                "--new-window",
                *window_args,
            ])
        args.append(start_url or "about:blank")
        port = None
        if remote_debugging:
            port = self._get_free_port()
            args.insert(2, f"--remote-debugging-port={port}")
            args.insert(3, "--remote-allow-origins=*")

        popen_kwargs = {}
        if hidden or headless:
            popen_kwargs.update({
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            })
            if sys.platform == "win32":
                popen_kwargs.update(_hidden_windows_subprocess_kwargs())
            else:
                popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(args, **popen_kwargs)
        return proc, port

    def _backup_storage_state(self):
        """Persist cookies/localStorage as an extra guard against profile loss."""
        if not self._context or self._qidian_profile_snapshot_root:
            return
        try:
            self._context.storage_state(path=self._get_storage_state_path())
        except Exception:
            pass

    def _create_qidian_profile_snapshot(self, user_data_dir):
        """Copy the real login profile to a disposable Qidian download profile."""
        tmp_root = tempfile.mkdtemp(prefix="npia_qidian_profile_")
        snapshot_dir = os.path.join(tmp_root, "browser_data")

        ignored_names = {
            "lockfile",
            "SingletonCookie",
            "SingletonLock",
            "SingletonSocket",
            "BrowserMetrics-spare.pma",
        }
        ignored_dirs = {
            "Cache",
            "Code Cache",
            "Crashpad",
            "GPUCache",
            "GrShaderCache",
            "ShaderCache",
            "DawnCache",
            "GraphiteDawnCache",
            "Safe Browsing",
        }

        def ignore(_dir, names):
            return {
                name for name in names
                if name in ignored_names or name in ignored_dirs
            }

        try:
            shutil.copytree(user_data_dir, snapshot_dir, ignore=ignore)
        except Exception:
            shutil.rmtree(tmp_root, ignore_errors=True)
            raise

        for stale_name in ignored_names:
            stale_path = os.path.join(snapshot_dir, stale_name)
            try:
                if os.path.isdir(stale_path):
                    shutil.rmtree(stale_path, ignore_errors=True)
                elif os.path.exists(stale_path):
                    os.remove(stale_path)
            except Exception:
                pass

        return tmp_root, snapshot_dir

    def _cleanup_qidian_profile_snapshot(self):
        root = self._qidian_profile_snapshot_root
        self._qidian_profile_snapshot_root = None
        if root:
            shutil.rmtree(root, ignore_errors=True)

    def _restore_storage_state(self):
        """Restore the explicit storage backup into the current context."""
        if not self._context:
            return
        path = self._get_storage_state_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            return

        cookies = state.get('cookies') or []
        if cookies:
            try:
                self._context.add_cookies(cookies)
            except Exception:
                pass

        origins = state.get('origins') or []
        if origins:
            script = """
(() => {
  const origins = __ORIGINS__;
  const current = origins.find((entry) => entry.origin === location.origin);
  if (!current || !Array.isArray(current.localStorage)) {
    return;
  }
  for (const item of current.localStorage) {
    try {
      localStorage.setItem(item.name, item.value);
    } catch (e) {}
  }
})();
""".replace("__ORIGINS__", json.dumps(origins))
            try:
                self._context.add_init_script(script=script)
            except Exception:
                pass

    def log(self, msg):
        """Safe logging that handles encoding issues on Windows consoles."""
        try:
            self._raw_log(msg)
        except UnicodeEncodeError:
            self._raw_log(msg.encode('ascii', 'replace').decode())

    @staticmethod
    def _normalize_interval_range(interval=0.5, interval_max=None):
        """Clamp and order a min/max chapter-delay range."""
        try:
            minimum = float(interval or 0.0)
        except (TypeError, ValueError):
            minimum = 0.5
        try:
            maximum = (
                minimum
                if interval_max is None
                else float(interval_max or 0.0)
            )
        except (TypeError, ValueError):
            maximum = minimum
        minimum = max(0.0, min(300.0, minimum))
        maximum = max(0.0, min(300.0, maximum))
        if maximum < minimum:
            minimum, maximum = maximum, minimum
        return minimum, maximum

    @classmethod
    def _random_interval_delay(cls, interval=0.5, interval_max=None):
        """Draw one delay, preserving legacy fixed intervals by default."""
        minimum, maximum = cls._normalize_interval_range(
            interval, interval_max
        )
        if maximum <= 0:
            return 0.0
        if minimum == maximum:
            return minimum
        return random.uniform(minimum, maximum)

    @classmethod
    def _sleep_interval(cls, interval=0.5, interval_max=None,
                        minimum=0.0):
        """Sleep once using a normalized random delay and optional floor."""
        low, high = cls._normalize_interval_range(interval, interval_max)
        floor = max(0.0, min(300.0, float(minimum or 0.0)))
        low = max(low, floor)
        high = max(high, low)
        delay = low if low == high else random.uniform(low, high)
        if delay > 0:
            time.sleep(delay)
        return delay

    def start(self):
        """Launch headless browser with persistent context."""
        if self._context:
            if self._page:
                try:
                    self._page.evaluate("1")
                    return
                except Exception:
                    self._page = None
            try:
                self._page = self._context.new_page()
                self._page.on("console", self._on_console)
                self.log("Browser ready.")
                return
            except Exception:
                self.cleanup()

        user_data_dir = self._get_user_data_dir()
        self.log("Launching headless browser...")
        self.log(f"Browser profile: {user_data_dir}")
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir,
            headless=True,
            args=[
                '--disable-web-security',       # Allow cross-origin fetches
                '--disable-features=IsolateOrigins,site-per-process',
                '--allow-running-insecure-content',  # Allow HTTP images on HTTPS pages
                '--no-sandbox',
            ],
            ignore_https_errors=True,
        )
        self._restore_storage_state()
        self._page = self._context.new_page()
        # Suppress console noise but capture errors
        self._page.on("console", self._on_console)
        self._munpia_chrome = False
        self.log("Browser ready.")

    def _start_ntk_browser(self, start_url):
        """Launch installed Chrome in real headless mode and attach over CDP."""
        if self._ntk_temp_chrome and self._context and self._page:
            try:
                self._page.evaluate("1")
                return True
            except Exception:
                self.cleanup()

        self.cleanup()
        self.log("Launching headless Chrome for NewToki...")
        user_data_dir = self._get_ntk_user_data_dir()
        self.log(f"Browser profile: {user_data_dir}")
        closed = self._close_ntk_profile_chrome(user_data_dir)
        if closed:
            self.log(
                f"[NewToki] Closed {closed} stale Chrome process(es) using "
                "the dedicated ntk profile."
            )
        proc, port = self._open_system_chrome(
            start_url,
            remote_debugging=True,
            user_data_dir=user_data_dir,
            headless=True,
        )
        if not proc or not port:
            self.log(
                "ERROR: Installed Chrome/Edge was not found for NewToki's "
                "headless browser mode."
            )
            return False

        self._chrome_process = proc
        self._ntk_temp_chrome = True
        if not self._wait_for_cdp(port):
            exit_code = proc.poll()
            if exit_code is not None:
                self.log(
                    "ERROR: Chrome exited before remote debugging started "
                    f"(exit code {exit_code})."
                )
            else:
                self.log(
                    "ERROR: Chrome remote debugging endpoint did not start."
                )
            self.log(
                "Close any NewToki Chrome windows opened by the app and try "
                "again. The scraper now uses a dedicated ntk profile to avoid "
                "Chrome profile locking."
            )
            return False
        if self._ntk_temp_chrome:
            self._hide_ntk_chrome_windows()

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
            self._context = (
                self._browser.contexts[0]
                if self._browser.contexts
                else self._browser.new_context()
            )
            volatile_cookies = (
                'nv',
                'ad_ack',
                'ad_ack_c',
                'ntk_blk_ok_sig',
                '__ntk_ev_id',
                'ntk_blk',
                'ntk_dev_warn',
            )
            for cookie_name in volatile_cookies:
                try:
                    self._context.clear_cookies(name=cookie_name)
                except Exception:
                    pass
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            try:
                self._page.evaluate(
                    """(keys) => {
                      for (const key of keys) {
                        try { localStorage.removeItem(key); } catch (_) {}
                        try { sessionStorage.removeItem(key); } catch (_) {}
                      }
                    }""",
                    list(volatile_cookies),
                )
            except Exception:
                pass
            self._page.on("console", self._on_console)
            if self._ntk_temp_chrome:
                self._hide_ntk_chrome_windows()
            self.log("Headless Chrome session ready.")
            return True
        except Exception as e:
            self.log(f"ERROR: Could not attach to Chrome: {e}")
            return False

    def open_visible_browser(self, start_url="about:blank",
                             regular_browser=False):
        """Open a visible browser for manual login.

        Cookies and localStorage are saved to the persistent data dir.
        The browser blocks until the user closes it.
        """
        # Must close any existing context first (only one per data dir)
        self.cleanup()

        use_regular = (
            regular_browser
            or self.is_ntk_novel(start_url)
            or self.is_novelpia(start_url)
            or self.is_global_novelpia(start_url)
        )
        if use_regular and self.is_ntk_novel(start_url):
            user_data_dir = self._get_ntk_user_data_dir()
        else:
            user_data_dir = self._get_user_data_dir()
        self.log("Opening browser for login...")
        self.log(f"Browser profile: {user_data_dir}")
        if use_regular:
            locked_pids = self._chrome_processes_using_profile(user_data_dir)
            if locked_pids:
                pids = ", ".join(str(pid) for pid in locked_pids)
                self.log(
                    "[Browser] This login profile is already open in "
                    f"browser process(es): {pids}"
                )
                self.log(
                    "[Browser] Asking Chrome to open/focus that existing "
                    "profile session instead of refusing."
                )
            proc, port = self._open_system_chrome(
                start_url,
                remote_debugging=True,
                user_data_dir=user_data_dir,
            )
            if proc:
                self.log(
                    "Using normal installed Chrome for browser session."
                )
                self.log(
                    "Browser opened. Complete any login or verification, "
                    "then close this Chrome window."
                )
                try:
                    self._chrome_process = proc
                    shown = self._center_profile_browser_windows(
                        user_data_dir,
                        timeout=6,
                        focus=True,
                    )
                    if not shown:
                        self.log(
                            "[Browser] Chrome launched, but no visible "
                            "window was found. Check the taskbar or close "
                            "stale Npia Chrome processes and try again."
                        )
                    self._wait_for_cdp(port, timeout=8)
                    saved = self._wait_for_system_chrome_close(
                        proc, user_data_dir, port=port
                    )
                except Exception:
                    saved = False
                finally:
                    if self._chrome_process is proc:
                        self._chrome_process = None
                if saved:
                    self.log("Browser closed. Session data saved.")
                else:
                    self.log(
                        "Browser window closed, but the login profile may "
                        "still be saving. I did not force-close Chrome."
                    )
                return
            self.log(
                "Normal Chrome was not found; falling back to Playwright "
                "browser."
            )

        locked_pids = self._chrome_processes_using_profile(user_data_dir)
        if locked_pids:
            pids = ", ".join(str(pid) for pid in locked_pids)
            self.log(
                "[Browser] This login profile is already open in browser "
                f"process(es): {pids}"
            )
            self.log(
                "[Browser] Playwright cannot attach to a locked profile; "
                "opening/focusing it with normal Chrome instead."
            )
            proc, port = self._open_system_chrome(
                start_url,
                remote_debugging=True,
                user_data_dir=user_data_dir,
            )
            if proc:
                self.log(
                    "Using normal installed Chrome for browser session."
                )
                self.log(
                    "Browser opened. Complete any login or verification, "
                    "then close this Chrome window."
                )
                try:
                    self._chrome_process = proc
                    self._center_profile_browser_windows(
                        user_data_dir,
                        timeout=6,
                        focus=True,
                    )
                    self._wait_for_cdp(port, timeout=8)
                    saved = self._wait_for_system_chrome_close(
                        proc, user_data_dir, port=port
                    )
                except Exception:
                    saved = False
                finally:
                    if self._chrome_process is proc:
                        self._chrome_process = None
                if saved:
                    self.log("Browser closed. Session data saved.")
                else:
                    self.log(
                        "Browser window closed, but the login profile may "
                        "still be saving. I did not force-close Chrome."
                    )
                return
            raise RuntimeError(
                "Browser profile is still in use, and normal Chrome was not "
                "available to focus it. Close the existing Npia login "
                "browser window and try again."
            )

        self._clear_chrome_restore_state(user_data_dir)
        visible_args = self._centered_chrome_window_args()

        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir,
                channel="chrome",
                headless=False,
                args=visible_args,
                ignore_https_errors=True,
            )
            self.log("Using installed Google Chrome for login.")
        except Exception as chrome_error:
            if self._is_profile_lock_error(chrome_error):
                self.log(
                    "[Browser] The Npia browser profile is already open. "
                    "Close that browser window and try again."
                )
                try:
                    self._playwright.stop()
                    self._playwright = None
                except Exception:
                    pass
                raise RuntimeError(
                    "Browser profile is already in use. Close the existing "
                    "Npia login browser window and try again."
                ) from chrome_error
            self.log(
                "Installed Chrome unavailable for login; "
                "falling back to bundled Chromium."
            )
            self.log(f"Chrome launch warning: {chrome_error}")
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir,
                headless=False,
                args=[
                    *visible_args,
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--allow-running-insecure-content',
                    '--no-sandbox',
                ],
                ignore_https_errors=True,
            )
        self._restore_storage_state()
        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page = page
        self._center_profile_browser_windows(
            user_data_dir,
            timeout=5,
            focus=True,
        )
        try:
            page.goto(start_url)
        except Exception as e:
            msg = str(e)
            benign = (
                'ERR_ABORTED' in msg
                or 'frame was detached' in msg
                or 'Target page, context or browser has been closed' in msg
                or 'Browser has been closed' in msg
            )
            if not benign:
                self.log(f"Browser navigation warning: {e}")
        self.log("Browser opened. Login to sites as needed, then close the browser.")

        # Wait for the browser to fully close.  We listen on the *context*
        # "close" event rather than the page – this fires only after
        # Chromium has completely exited and flushed all session data
        # (cookies, localStorage, IndexedDB) to the persistent profile
        # directory.  Using page.wait_for_event("close") previously caused
        # a race: cleanup() would call context.close() while Chromium was
        # still mid-shutdown, interrupting the disk flush and losing the
        # login session.
        while self._context:
            try:
                self._context.wait_for_event("close", timeout=5000)
                break
            except PlaywrightTimeoutError:
                self._backup_storage_state()
            except Exception:
                break

        # Chromium's persistent profile should already be flushed at this
        # point. Take one explicit storage snapshot after close/disconnect as
        # a best-effort fallback; periodic snapshots above are the useful
        # guard if this final call races with a closed context.
        self._backup_storage_state()

        self.log("Browser closed. Session data saved.")
        # The context already disconnected, so just reset Python refs
        # without calling context.close() again (which would error or
        # race with the flush).
        self._page = None
        self._context = None
        try:
            if self._playwright:
                self._playwright.stop()
                self._playwright = None
        except Exception:
            self._playwright = None

    def _on_console(self, msg):
        """Forward JS console messages to Python logger."""
        text = msg.text
        lowered = text.lower()
        # Ridi emits these two known browser/analytics messages on every
        # otherwise-successful product page. They do not affect scraping and
        # should not be presented to the user as downloader failures.
        if (
            'permissions policy violation: unload is not allowed' in lowered
            or (
                'amplitude logger [error]' in lowered
                and 'event rejected due to missing api key' in lowered
            )
        ):
            return
        if 'TypeError: Failed to fetch' in text:
            return
        if 'whoas.xyz/collect' in text:
            return
        if (
            'SlotValidationError' in text
            and "'floor' value must be a positive integer" in text
        ):
            return
        # Show bridge messages, fetch retries, init info, and actual errors
        if (
            "[ND-Bridge]" in text
            or "[ND-Fetch]" in text
            or "[Init]" in text
            or "[sfacg]" in text
        ):
            self.log(f"[JS] {text}")
        elif msg.type == "error" and "Failed to load resource" not in text:
            self.log(f"[JS] {text}")

    @staticmethod
    def is_syosetu_url(url):
        """Check whether a URL is a Shosetsuka ni Naro ncode page."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        host = (parsed.netloc or '').lower()
        return host == 'ncode.syosetu.com' and bool(
            re.match(r'^/n[a-z0-9]+/?', parsed.path or '', re.I)
        )

    @staticmethod
    def is_qidian(url):
        """Check if the URL is a Qidian book or chapter URL."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        host = (parsed.hostname or '').lower()
        if host != 'qidian.com' and not host.endswith('.qidian.com'):
            return False
        return bool(re.match(r'^/(book|chapter)/\d+', parsed.path or ''))

    @staticmethod
    def is_novelpia(url):
        """Return True for Korean Novelpia novel and viewer URLs."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        host = (parsed.hostname or '').lower()
        if host == 'global.novelpia.com':
            return False
        if host != 'novelpia.com' and not host.endswith('.novelpia.com'):
            return False
        return bool(
            re.match(
                r'^/(?:novel|viewer)/\d+(?:[/?#]|$)',
                parsed.path or '',
            )
        )

    @staticmethod
    def is_global_novelpia(url):
        """Return True for Global Novelpia novel index URLs."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        if (parsed.hostname or '').lower() != 'global.novelpia.com':
            return False
        return bool(re.match(
            r'^/novel/\d+(?:[/?#]|$)',
            parsed.path or '',
            re.I,
        ))

    @staticmethod
    def is_ridibooks(url):
        """Return True for Ridi webnovel book and viewer URLs."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        host = (parsed.hostname or '').lower()
        if host != 'ridibooks.com' and not host.endswith('.ridibooks.com'):
            return False
        return bool(re.match(
            r'^/books/\d+(?:/view)?/?$',
            parsed.path or '',
            re.I,
        ))

    @staticmethod
    def is_69shuba(url):
        """Return True for 69shuba.tw book, catalog, and reader URLs."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        host = (parsed.hostname or '').lower()
        if host not in ('69shuba.tw', 'www.69shuba.tw'):
            return False
        return bool(re.match(
            r'^/(?:book|indexlist|read)/\d+(?:/\d+)?/?$',
            parsed.path or '',
        ))

    @staticmethod
    def is_1qxs(url):
        """Return True for supported 1qxs book, catalog, and reader URLs."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        host = (parsed.hostname or '').lower()
        if host not in ('1qxs.com', 'www.1qxs.com', 'm.1qxs.com'):
            return False
        return bool(re.match(
            r'^/(?:catalog|xs)(?:_\d+)?/\d+'
            r'(?:/\d+(?:/\d+)?)?(?:\.html)?/?$',
            parsed.path or '',
        ))

    @staticmethod
    def _normalize_title_for_match(value):
        value = html.unescape(value or '')
        value = unicodedata.normalize('NFKC', value)
        value = re.sub(r'\s+', '', value)
        return value.casefold()

    @staticmethod
    def _syosetu_amazon_title_candidate(bookname):
        title = html.unescape(bookname or '').strip()
        title = unicodedata.normalize('NFKC', title)
        # Syosetu pages often advertise the web version, while Amazon uses
        # the print/Kindle title without that marker.
        title = re.sub(
            r'^[【\[\(（「『〖]\s*(?:web|web版|WEB|WEB版)\s*[】\]\)）」』〗]\s*',
            '',
            title,
            flags=re.I,
        )
        return title.strip()

    def _amazon_request_text(self, url, referer=None, timeout=20):
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': (
                'text/html,application/xhtml+xml,application/xml;q=0.9,'
                'image/avif,image/webp,*/*;q=0.8'
            ),
            'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        if referer:
            headers['Referer'] = referer
        try:
            import requests

            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.text
        except ImportError:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read(2_500_000)
                content_type = response.headers.get('content-type', '')
            match = re.search(r'charset=([\w.-]+)', content_type, re.I)
            encoding = match.group(1) if match else 'utf-8'
            return raw.decode(encoding, 'ignore')

    @staticmethod
    def _amazon_abs_url(url):
        return urllib.parse.urljoin('https://www.amazon.co.jp/', html.unescape(url or ''))

    @staticmethod
    def _amazon_clean_image_url(url):
        url = html.unescape(url or '').strip()
        if not url:
            return ''
        return re.sub(
            r'\._[^./]+_\.(jpe?g|png|webp)(?=($|\?))',
            r'._SL1500_.\1',
            url,
            flags=re.I,
        )

    def _amazon_search_results(self, search_html):
        result_re = re.compile(
            r'<div\s+role=["\']listitem["\']'
            r'(?=[^>]*\bdata-asin=["\']([A-Z0-9]{10})["\'])'
            r'(?=[^>]*\bdata-component-type=["\']s-search-result["\'])'
            r'[^>]*>',
            re.I,
        )
        starts = list(result_re.finditer(search_html or ''))
        for idx, match in enumerate(starts):
            asin = match.group(1)
            if not asin:
                continue
            end = starts[idx + 1].start() if idx + 1 < len(starts) else len(search_html)
            block = search_html[match.start():end]

            title = ''
            title_match = re.search(
                r'<img\b[^>]*\bclass=["\'][^"\']*\bs-image\b[^"\']*["\']'
                r'[^>]*\balt=["\']([^"\']+)["\']',
                block,
                re.I,
            )
            if title_match:
                title = html.unescape(title_match.group(1)).strip()
            if not title:
                title_match = re.search(
                    r'<h2\b[^>]*>.*?<span\b[^>]*>(.*?)</span>',
                    block,
                    re.I | re.S,
                )
                if title_match:
                    title = re.sub(r'<[^>]+>', '', title_match.group(1))
                    title = html.unescape(title).strip()

            link = ''
            link_match = re.search(
                r'href=["\']([^"\']*/dp/' + re.escape(asin) + r'[^"\']*)["\']',
                block,
                re.I,
            )
            if link_match:
                link = self._amazon_abs_url(link_match.group(1))
            else:
                link = f'https://www.amazon.co.jp/dp/{asin}'

            image_url = ''
            image_match = re.search(
                r'<img\b[^>]*\bclass=["\'][^"\']*\bs-image\b[^"\']*["\']'
                r'[^>]*\bsrc=["\']([^"\']+)["\']',
                block,
                re.I,
            )
            if image_match:
                image_url = self._amazon_clean_image_url(image_match.group(1))

            yield {
                'asin': asin,
                'title': title,
                'url': link,
                'imageUrl': image_url,
            }

    def _amazon_title_matches(self, wanted_title, amazon_title):
        wanted = self._normalize_title_for_match(wanted_title)
        found = self._normalize_title_for_match(amazon_title)
        return bool(wanted and found.startswith(wanted))

    def _amazon_extract_cover_url(self, product_html):
        patterns = [
            r'\bdata-old-hires=["\']([^"\']+)["\']',
            r'\bid=["\']landingImage["\'][^>]*\bsrc=["\']([^"\']+)["\']',
            r'\bdata-a-dynamic-image=["\']({.*?})["\']',
        ]
        for pattern in patterns[:2]:
            match = re.search(pattern, product_html or '', re.I | re.S)
            if match:
                url = self._amazon_clean_image_url(match.group(1))
                if url.startswith('https://'):
                    return url
        match = re.search(patterns[2], product_html or '', re.I | re.S)
        if match:
            image_map = html.unescape(match.group(1))
            urls = re.findall(r'"(https://m\.media-amazon\.com/images/I/[^"]+)"', image_map)
            if urls:
                return self._amazon_clean_image_url(urls[-1])
        return ''

    def _find_syosetu_amazon_cover_url(self, bookname):
        title = self._syosetu_amazon_title_candidate(bookname)
        if not title:
            return ''

        query = urllib.parse.quote(title)
        search_url = f'https://www.amazon.co.jp/s?k={query}'
        search_html = self._amazon_request_text(search_url)
        if 'captcha' in search_html.lower() and 'amazon' in search_html.lower():
            self.log("[Syosetu] Amazon cover fallback blocked by CAPTCHA.")
            return ''

        for result in self._amazon_search_results(search_html):
            if not self._amazon_title_matches(title, result.get('title', '')):
                continue
            self.log(
                "[Syosetu] Amazon title match: "
                f"{result.get('title') or result.get('asin')}"
            )
            cover_url = ''
            try:
                product_html = self._amazon_request_text(
                    result['url'],
                    referer=search_url,
                )
                cover_url = self._amazon_extract_cover_url(product_html)
            except Exception:
                cover_url = ''
            if cover_url:
                return cover_url
            if result.get('imageUrl'):
                return result['imageUrl']
        return ''

    def _apply_syosetu_amazon_cover_fallback(self, url, data):
        if not self.syosetu_amazon_cover_fallback:
            return data
        if data.get('coverUrl') or not self.is_syosetu_url(url):
            return data

        self.log("[Syosetu] No rule cover found; checking Amazon.co.jp...")
        try:
            cover_url = self._find_syosetu_amazon_cover_url(
                data.get('bookname', '')
            )
        except Exception as e:
            self.log(f"[Syosetu] Amazon cover fallback failed: {e}")
            return data

        if cover_url:
            data['coverUrl'] = cover_url
            data['_coverFallback'] = 'amazon.co.jp'
            self.log(f"[Syosetu] Amazon cover URL: {cover_url}")
        else:
            self.log("[Syosetu] Amazon cover fallback found no exact title match.")
        return data

    def stop(self):
        """Request cancellation and close browser."""
        self._stop_requested = True
        self.cleanup()

    def cleanup(self):
        """Release browser resources."""
        self._global_novelpia_shutdown_ad_worker()
        self._backup_storage_state()
        site_cdp_port = (
            self._ridi_cdp_port
            if self._ridi_chrome
            else (
                self._novelpia_cdp_port
                if self._novelpia_chrome
                else self._munpia_cdp_port
            )
        )
        if site_cdp_port:
            try:
                self._request_cdp_browser_close(site_cdp_port)
                self._wait_for_profile_processes_to_exit(
                    self._get_user_data_dir(), timeout=8
                )
            except Exception:
                pass
        for wp in self._worker_pages:
            try:
                wp.close()
            except Exception:
                pass
        self._worker_pages = []
        try:
            if self._page:
                self._page.close()
                self._page = None
        except Exception:
            pass
        try:
            if self._context:
                self._context.close()
                self._context = None
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
                self._browser = None
        except Exception:
            self._browser = None
        try:
            if self._playwright:
                self._playwright.stop()
                self._playwright = None
        except Exception:
            pass
        try:
            if self._chrome_process and self._chrome_process.poll() is None:
                self._chrome_process.terminate()
                try:
                    self._chrome_process.wait(timeout=5)
                except Exception:
                    self._chrome_process.kill()
            self._chrome_process = None
        except Exception:
            self._chrome_process = None
        self._munpia_chrome = False
        self._munpia_cdp_port = None
        self._novelpia_chrome = False
        self._novelpia_cdp_port = None
        self._ridi_chrome = False
        self._ridi_cdp_port = None
        try:
            if self._global_novelpia_session:
                self._global_novelpia_session.close()
        except Exception:
            pass
        self._global_novelpia_session = None
        self._global_novelpia_login_at = ''
        self._global_novelpia_refresh_attempted = False
        if self._ntk_temp_chrome:
            try:
                closed = self._close_ntk_profile_chrome(
                    self._get_ntk_user_data_dir()
                )
                if closed:
                    self.log(
                        f"[NewToki] Closed {closed} temporary Chrome "
                        "process(es)."
                    )
            except Exception:
                pass
            self._ntk_temp_chrome = False
        self._cleanup_qidian_profile_snapshot()

    # ------------------------------------------------------------------
    # Multi-page support for parallel chapter downloads
    # ------------------------------------------------------------------
    def create_worker_pages(self, count):
        """Create additional browser pages for parallel chapter downloads.

        Each page navigates to the book URL, gets stubs/rules/bridge
        injected, and has its own rule instance ready for chapterParse.

        Must be called AFTER parse_book() succeeds.
        """
        if count <= 0 or not self._book_url:
            return
        if self._book_data and (
            self._book_data.get('_ntk_novel')
            or self._book_data.get('_qidian')
            or self._book_data.get('_munpia')
            or self._book_data.get('_novelpia')
            or self._book_data.get('_69shuba')
            or self._book_data.get('_1qxs')
        ):
            return

        # Close any existing worker pages
        for wp in self._worker_pages:
            try:
                wp.close()
            except Exception:
                pass
        self._worker_pages = []

        self.log(f"Creating {count} worker pages...")
        for i in range(count):
            try:
                page = self._context.new_page()
                page.on("console", self._on_console)
                page.goto(self._book_url, wait_until="domcontentloaded",
                          timeout=30000)
                self._install_bridge_bindings(page)
                page.evaluate(self._gm_stubs_js)
                page.evaluate(self._rules_js)
                page.evaluate(self._bridge_js)
                # Initialise the rule instance on this page
                page.evaluate("window.__ND_parseBook()")
                self._worker_pages.append(page)
            except Exception as e:
                self.log(f"  Worker page {i} failed: {e}")
        self.log(f"{len(self._worker_pages)} worker pages ready.")

    def get_page(self, index):
        """Get a page by index. 0 = primary, 1..N = worker pages."""
        if index == 0:
            return self._page
        wi = index - 1
        if wi < len(self._worker_pages):
            return self._worker_pages[wi]
        return self._page  # fallback to primary

    def _ensure_page(self):
        """Verify the primary page is alive; restart if needed.

        Returns True if the page is usable, False if recovery failed.
        Called before any page.evaluate() to handle browser crashes
        or disconnected pages mid-download.
        """
        if self._page is not None:
            # Quick liveness check
            try:
                self._page.evaluate("1")
                return True
            except Exception:
                self.log("Page disconnected, attempting recovery...")
                self._page = None

        # Page is None — try to recover
        if self._context is None:
            try:
                self.start()
            except Exception as e:
                self.log(f"ERROR: Could not restart browser: {e}")
                return False

        # Create a new page in the existing context
        try:
            self._page = self._context.new_page()
            self._page.on("console", self._on_console)
        except Exception as e:
            self.log(f"ERROR: Could not create new page: {e}")
            return False

        # Re-navigate and re-inject JS if we have a book URL
        if self._book_url:
            try:
                self._page.goto(self._book_url,
                                wait_until="domcontentloaded", timeout=30000)
                if self._book_data and self._book_data.get('_ntk_novel'):
                    self.log("Page recovered for NewToki scraper.")
                    return True
                if self._book_data and self._book_data.get('_qidian'):
                    self.log("Page recovered for Qidian scraper.")
                    return True
                self._install_bridge_bindings(self._page)
                self._page.evaluate(self._gm_stubs_js)
                self._page.evaluate(self._rules_js)
                self._page.evaluate(self._bridge_js)
                # Re-initialise the rule instance
                self._page.evaluate("window.__ND_parseBook()")
                self.log("Page recovered and bridge re-injected.")
                return True
            except Exception as e:
                self.log(f"ERROR: Page recovery failed: {e}")
                return False

        self.log("Page recovered (no book URL to re-inject).")
        return True

    # ------------------------------------------------------------------
    # Qidian native scraper (rendered Chrome fallback for encrypted reader)
    # ------------------------------------------------------------------
    # Qidian uses the UI's interval setting. Keep this floor at zero unless
    # the site starts requiring a hard minimum delay.
    _QIDIAN_MIN_INTERVAL = 0.0

    @staticmethod
    def _qidian_desktop_user_agent():
        return (
            os.environ.get("NPIA_QIDIAN_USER_AGENT")
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/137.0.0.0 Safari/537.36"
        )

    @staticmethod
    def _qidian_stealth_init_script():
        return """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""

    def _qidian_eval(self, page, script, arg=None, attempts=12, delay=0.75):
        """Evaluate JS on a Qidian page, tolerating SPA reload races."""
        last_error = None
        for _ in range(max(1, attempts)):
            try:
                if arg is None:
                    return page.evaluate(script)
                return page.evaluate(script, arg)
            except Exception as e:
                last_error = e
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=3000)
                except Exception:
                    pass
                try:
                    page.wait_for_timeout(int(delay * 1000))
                except Exception:
                    time.sleep(delay)
        raise last_error

    def _start_qidian_browser(self, start_url):
        """Launch a headless persistent browser for Qidian downloads."""
        if self._context and self._page:
            try:
                self._page.evaluate("1")
                return True
            except Exception:
                self.cleanup()
        elif self._context or self._browser or self._chrome_process:
            self.cleanup()

        user_data_dir = self._get_user_data_dir()
        self.log("[Qidian] Launching headless browser with saved profile...")
        self.log(f"Browser profile: {user_data_dir}")

        locked_pids = self._chrome_processes_using_profile(user_data_dir)
        if locked_pids:
            self.log(
                "ERROR: [Qidian] Browser profile is currently open. Close "
                "the regular login browser window before starting Download."
            )
            return False

        try:
            snapshot_root, qidian_user_data_dir = (
                self._create_qidian_profile_snapshot(user_data_dir)
            )
            self._qidian_profile_snapshot_root = snapshot_root
            self.log(
                "[Qidian] Using disposable copy of browser profile for "
                "download."
            )
        except Exception as e:
            self.log(
                "ERROR: [Qidian] Could not copy browser profile for "
                f"background download: {e}"
            )
            return False

        qidian_args = [
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process',
            '--disable-blink-features=AutomationControlled',
            '--allow-running-insecure-content',
            '--no-sandbox',
            '--disable-background-mode',
            '--disable-session-crashed-bubble',
            '--hide-crash-restore-bubble',
            '--lang=zh-CN',
        ]
        qidian_context_options = {
            "headless": True,
            "args": qidian_args,
            "ignore_https_errors": True,
            "user_agent": self._qidian_desktop_user_agent(),
            "viewport": {"width": 1280, "height": 800},
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        try:
            self._playwright = sync_playwright().start()
            try:
                self._context = (
                    self._playwright.chromium.launch_persistent_context(
                        qidian_user_data_dir,
                        channel="chrome",
                        **qidian_context_options,
                    )
                )
                self.log("[Qidian] Using installed Chrome in headless mode.")
            except Exception as chrome_error:
                self.log(
                    "[Qidian] Installed Chrome headless unavailable; "
                    "falling back to bundled Chromium."
                )
                self.log(f"[Qidian] Chrome launch warning: {chrome_error}")
                self._context = (
                    self._playwright.chromium.launch_persistent_context(
                        qidian_user_data_dir,
                        **qidian_context_options,
                    )
                )

            try:
                self._context.add_init_script(
                    self._qidian_stealth_init_script()
                )
            except Exception:
                pass
            self._restore_storage_state()
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            self._page.on("console", self._on_console)
            self.log("[Qidian] Headless browser ready.")
            return True
        except Exception as e:
            if self._is_profile_lock_error(e):
                self.log(
                    "ERROR: [Qidian] Browser profile is already open. "
                    "Close the Npia login browser window and try again."
                )
            else:
                self.log(
                    "ERROR: [Qidian] Could not start headless browser. "
                    "Use Enter Browser for any login or verification, then "
                    f"retry. Details: {e}"
                )
            self.cleanup()
            return False

    @staticmethod
    def _qidian_abs_url(value):
        value = (value or '').strip()
        if value.startswith('//'):
            return 'https:' + value
        return urllib.parse.urljoin('https://www.qidian.com/', value)

    def _qidian_page_diagnostic(self, page):
        script = r"""
(() => {
  const html = document.documentElement?.innerHTML || '';
  const rawText = document.body?.innerText || document.body?.textContent || '';
  const text = rawText.replace(/\s+/g, ' ').trim().slice(0, 240);
  const verification =
    /TencentCaptcha|WafCaptcha|__captcha/i.test(html) ||
    /captcha|verify|verification|验证码|安全|人机|滑块|请先登录/i.test(text);
  return {
    url: location.href,
    title: document.title || '',
    readyState: document.readyState || '',
    text: text || (
      verification ? 'Tencent/WAF captcha HTML with empty body' : ''
    ),
    verification,
    bookName: !!document.querySelector('#bookName'),
    catalogCount: document.querySelectorAll(
      '#bookCatalogSection a.chapter-name, a.chapter-name'
    ).length,
  };
})()
"""
        try:
            return self._qidian_eval(page, script, attempts=1)
        except Exception:
            return None

    def _qidian_wait_for_book(self, page, timeout=45):
        deadline = time.time() + timeout
        script = """
(() => {
  const html = document.documentElement?.innerHTML || '';
  if (/TencentCaptcha|WafCaptcha|__captcha/i.test(html)) {
    return 'verification';
  }
  const bodyText = document.body?.innerText || document.body?.textContent || '';
  const catalogCount = document.querySelectorAll(
    '#bookCatalogSection a.chapter-name, a.chapter-name'
  ).length;
  const hasBookTitle = !!(
    document.querySelector('#bookName') ||
    document.querySelector('h1')
  );
  return (catalogCount > 0 && (hasBookTitle || bodyText.trim().length > 100))
    ? 'ready'
    : 'waiting';
})()
"""
        while time.time() < deadline and not self._stop_requested:
            try:
                status = page.evaluate(script)
                if status == 'ready' or status is True:
                    return True
                if status == 'verification':
                    return False
            except Exception:
                pass
            try:
                page.wait_for_timeout(1000)
            except Exception:
                time.sleep(1)
        return False

    def _qidian_wait_for_chapter(self, page, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop_requested:
            if self._qidian_chapter_ready(page):
                return True
            try:
                page.wait_for_timeout(1000)
            except Exception:
                time.sleep(1)
        return False

    def _qidian_chapter_ready(self, page):
        script = """
(() => {
  const main = document.querySelector(
    'div.chapter-wrapper div.print main, main.content, #reader main, main'
  );
  const mainText = (main?.innerText || main?.textContent || '').trim();
  const bodyText = document.body?.innerText || document.body?.textContent || '';
  const html = document.documentElement?.innerHTML || '';
  const htmlChallenge = /TencentCaptcha|WafCaptcha|__captcha/i.test(html);
  const textChallenge = /VIP|\\u8ba2\\u9605|\\u8d2d\\u4e70|captcha|verify|verification|\\u9a8c\\u8bc1|\\u5b89\\u5168|\\u4eba\\u673a|\\u6ed1\\u5757/i.test(bodyText);
  return document.querySelectorAll('span.content-text').length >= 3 ||
    mainText.length > 100 ||
    htmlChallenge ||
    textChallenge;
})()
"""
        try:
            return bool(page and not page.is_closed() and page.evaluate(script))
        except Exception:
            return False

    @staticmethod
    def _page_is_usable(page):
        try:
            return bool(page and not page.is_closed())
        except Exception:
            return False

    def _qidian_parallel_pages(self, count, start_url):
        """Return one usable Qidian browser page per parallel chapter."""
        count = max(1, count)
        if not self._context or not self._page:
            if not self._start_qidian_browser(start_url):
                return []

        if not self._page_is_usable(self._page):
            try:
                self._page = self._context.new_page()
                self._page.on("console", self._on_console)
            except Exception as e:
                self.log(f"  [Qidian] Could not create primary page: {e}")
                return []

        usable_workers = []
        for page in self._worker_pages:
            if self._page_is_usable(page):
                usable_workers.append(page)
            else:
                try:
                    page.close()
                except Exception:
                    pass
        self._worker_pages = usable_workers

        needed_workers = max(0, count - 1)
        if len(self._worker_pages) > needed_workers:
            for page in self._worker_pages[needed_workers:]:
                try:
                    page.close()
                except Exception:
                    pass
            self._worker_pages = self._worker_pages[:needed_workers]

        while len(self._worker_pages) < needed_workers:
            try:
                page = self._context.new_page()
                page.on("console", self._on_console)
                self._worker_pages.append(page)
            except Exception as e:
                self.log(f"  [Qidian] Worker page failed: {e}")
                break

        return ([self._page] + self._worker_pages)[:count]

    def _qidian_parse_book(self, url):
        """Scrape Qidian metadata and catalog from the rendered book page."""
        self._stop_requested = False
        if not self._start_qidian_browser(url):
            return None

        self.log(f"[Qidian] Navigating to: {url}")
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            self.log(f"[Qidian] Page load warning: {e}")

        if not self._qidian_wait_for_book(self._page):
            diag = self._qidian_page_diagnostic(self._page)
            if diag and diag.get('verification'):
                self.log(
                    "ERROR: [Qidian] Qidian returned a verification/captcha "
                    "page in headless mode. Use Enter Browser to complete "
                    "verification, then retry Download."
                )
                self.log(
                    "[Qidian] Page diagnostic: "
                    f"url={diag.get('url', '')}, "
                    f"title={diag.get('title', '')}, "
                    f"state={diag.get('readyState', '')}, "
                    f"text={diag.get('text', '')}"
                )
            elif diag:
                self.log(
                    "ERROR: [Qidian] Book page did not render. "
                    f"url={diag.get('url', '')}, "
                    f"title={diag.get('title', '')}, "
                    f"state={diag.get('readyState', '')}, "
                    f"text={diag.get('text', '')}"
                )
            else:
                self.log("ERROR: [Qidian] Book page did not render.")
            return None

        script = r"""
(() => {
  const text = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
  const meta = (key) => (
    document.querySelector(`meta[property="${key}"]`) ||
    document.querySelector(`meta[name="${key}"]`)
  )?.getAttribute('content') || '';
  const abs = (url) => {
    if (!url) return '';
    if (url.startsWith('//')) return 'https:' + url;
    return new URL(url, location.href).href;
  };
  const title = (
    text(document.querySelector('#bookName')) ||
    meta('og:title') ||
    text(document.querySelector('h1')) ||
    document.title.split('-')[0].trim()
  );
  const author = (
    text(document.querySelector('span.author')) ||
    text(document.querySelector('[class*="author"]'))
  ).replace(/^\u4f5c\u8005[:\uff1a]\s*/, '').trim();
  const coverEl =
    document.querySelector('#bookImg img, img#bookImg, #bookImg') ||
    [...document.images].find((img) => /bookcover\.yuewen\.com/.test(img.src));
  const cover = abs(
    coverEl?.currentSrc || coverEl?.src || meta('og:image') || ''
  );
  const description = (
    text(document.querySelector('#book-intro-detail')) ||
    meta('og:description') ||
    meta('description')
  );
  const tags = [
    ...document.querySelectorAll(
      '#all-label a, .book-tag a, .tag-wrap a, a[href*="/all/"]'
    )
  ].map((el) => text(el)).filter(Boolean);
  const seenTags = new Set();
  const cleanTags = tags.filter((tag) => {
    if (seenTags.has(tag)) return false;
    seenTags.add(tag);
    return true;
  });
  const chapterLinks = [
    ...document.querySelectorAll('#bookCatalogSection a.chapter-name, a.chapter-name')
  ];
  const chapters = chapterLinks.map((a, index) => {
    const parent = a.closest('li, dd, div') || a.parentElement || a;
    const contextText = text(parent);
    const name = text(a) || `Chapter ${index + 1}`;
    const isVip = /VIP|\u4ed8\u8d39|\u8ba2\u9605/.test(contextText);
    return {
      url: abs(a.href || a.getAttribute('href') || ''),
      name,
      fullName: name,
      isVIP: isVip,
      isPaid: isVip,
      // The catalog does not tell us whether this account purchased a VIP
      // chapter. Try it and let the rendered reader report locked/login.
      isAccessible: true,
    };
  }).filter((ch) => ch.url);
  return {
    bookname: title,
    author: author || 'Unknown',
    coverUrl: cover,
    description,
    introduction: description,
    introductionHTML: description ? description.replace(/\n/g, '<br/>\n') : '',
    tags: cleanTags,
    language: 'zh',
    chapterCount: chapters.length,
    chapters,
    bookUrl: location.href,
  };
})()
"""
        try:
            data = self._qidian_eval(self._page, script)
        except Exception as e:
            self.log(f"ERROR: [Qidian] Metadata extraction failed: {e}")
            return None

        if not data or not data.get('bookname'):
            self.log("ERROR: [Qidian] Could not extract book title.")
            return None
        if not data.get('chapters'):
            self.log("ERROR: [Qidian] No chapters found.")
            return None

        data['_qidian'] = True
        data['_qidian_min_interval'] = self._QIDIAN_MIN_INTERVAL
        self._book_data = data
        self._book_url = url
        self.log(
            f"[Qidian] Book: {data.get('bookname', '?')} by "
            f"{data.get('author', '?')} - {data.get('chapterCount', 0)} "
            "chapters"
        )
        return data

    def _qidian_parse_chapter(self, chapter_url, chapter_name, page=None):
        """Scrape one rendered Qidian chapter."""
        if not self._context or not self._page:
            if not self._start_qidian_browser(chapter_url):
                return None

        target = page or self._page
        try:
            goto_kwargs = {
                "wait_until": "domcontentloaded",
                "timeout": 45000,
            }
            if self._book_url:
                goto_kwargs["referer"] = self._book_url
            target.goto(chapter_url, **goto_kwargs)
        except Exception as e:
            self.log(f"  [Qidian] Page load warning: {e}")

        if not self._qidian_wait_for_chapter(target):
            self.log(f"  [Qidian] Timed out waiting for: {chapter_name}")
            return None

        script = r"""
(chapterName) => {
  const escapeHtml = (value) => String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
  const text = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
  const deobfuscate = () => {
    const content = document.querySelector(
      'div.chapter-wrapper div.print main, main.content, #reader main, main'
    );
    if (!content) return;
    const spans = [...content.querySelectorAll('span.content-text')];
    if (!spans.length) return;
    const style = spans[0].getAttribute('style') || '';
    const fontMatch = style.match(/font-family:\s*["']?(qd_[^"',;\s]+)/);
    if (!fontMatch) return;
    const fontName = fontMatch[1];
    const mapping = {};
    for (const styleEl of document.querySelectorAll('style')) {
      const css = styleEl.textContent || '';
      if (!css.includes(fontName)) continue;
      const ruleRe = /\.([a-zA-Z0-9_-]+)\s*\{[^}]*content\s*:\s*["']\\([0-9a-fA-F]{1,6})["'][^}]*\}/g;
      let m;
      while ((m = ruleRe.exec(css)) !== null) {
        mapping[m[1]] = parseInt(m[2], 16);
      }
    }
    if (!Object.keys(mapping).length) return;
    for (const span of spans) {
      for (const cls of span.classList) {
        if (Object.prototype.hasOwnProperty.call(mapping, cls)) {
          span.textContent = String.fromCodePoint(mapping[cls]);
          break;
        }
      }
    }
  };
  deobfuscate();
  const main = document.querySelector(
    'div.chapter-wrapper div.print main, main.content, #reader main, main'
  );
  const bodyText = document.body?.innerText || document.body?.textContent || '';
  const pageHtml = document.documentElement?.innerHTML || '';
  const needsVerification =
    /TencentCaptcha|WafCaptcha|__captcha/i.test(pageHtml) ||
    /captcha|verify|verification|\u9a8c\u8bc1|\u5b89\u5168|\u4eba\u673a|\u6ed1\u5757/i.test(bodyText);
  if (!main) {
    if (needsVerification) return {error: 'verification'};
    return {
      error: /VIP|\u8ba2\u9605|\u8d2d\u4e70|\u767b\u5f55/.test(bodyText)
        ? 'locked'
        : 'missing content'
    };
  }
  const clone = main.cloneNode(true);
  clone.querySelectorAll(
    'script, style, noscript, button, textarea, input, svg, canvas, ' +
    'span.review, span.review-count, .ql-block-token, [class*="review"], ' +
    '[class*="comment"], [class*="vote"], [class*="ad"]'
  ).forEach((el) => el.remove());

  let paras = [...clone.querySelectorAll('p')]
    .map((p) => text(p))
    .filter(Boolean);
  if (paras.length < 2) {
    paras = (clone.innerText || clone.textContent || '')
      .split(/\n+/)
      .map((line) => line.replace(/\s+/g, ' ').trim())
      .filter(Boolean);
  }
  paras = paras.filter((line) => !/^(\d+\s*){1,4}$/.test(line));
  const contentText = paras.join('\n');
  if (!contentText || contentText.length < 20) {
    if (needsVerification) return {error: 'verification'};
    return {
      error: /VIP|\u8ba2\u9605|\u8d2d\u4e70|\u767b\u5f55/.test(bodyText)
        ? 'locked'
        : 'empty content'
    };
  }
  const contentHtml = paras
    .map((line) => `<p>${escapeHtml(line)}</p>`)
    .join('\n');
  return {
    chapterName,
    sourceChapterName: chapterName,
    contentText,
    contentHtml,
    images: [],
  };
}
"""
        try:
            data = self._qidian_eval(
                target, script, arg=chapter_name, attempts=10
            )
        except Exception as e:
            self.log(f"  [Qidian] Chapter extraction failed: {e}")
            return None

        if not data:
            self.log(f"  [Qidian] Empty result for: {chapter_name}")
            return None
        if data.get('error') == 'locked':
            self.log(f"  [Qidian] LOCKED or login required: {chapter_name}")
            return {'_locked': True, 'chapterName': chapter_name}
        if data.get('error') == 'verification':
            self.log(
                f"  [Qidian] Verification required for: {chapter_name}. "
                "Use Enter Browser to complete login or verification, then "
                "retry."
            )
            return {
                '_locked': True,
                '_verification_required': True,
                'chapterName': chapter_name,
            }
        if data.get('error'):
            self.log(
                f"  [Qidian] {data.get('error')} for: {chapter_name}"
            )
            return None
        return data

    def _qidian_extract_loaded_chapter(self, page, chapter_name):
        """Extract one Qidian chapter from a page that already navigated."""
        script = r"""
(chapterName) => {
  const escapeHtml = (value) => String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
  const text = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
  const deobfuscate = () => {
    const content = document.querySelector(
      'div.chapter-wrapper div.print main, main.content, #reader main, main'
    );
    if (!content) return;
    const spans = [...content.querySelectorAll('span.content-text')];
    if (!spans.length) return;
    const style = spans[0].getAttribute('style') || '';
    const fontMatch = style.match(/font-family:\s*["']?(qd_[^"',;\s]+)/);
    if (!fontMatch) return;
    const fontName = fontMatch[1];
    const mapping = {};
    for (const styleEl of document.querySelectorAll('style')) {
      const css = styleEl.textContent || '';
      if (!css.includes(fontName)) continue;
      const ruleRe = /\.([a-zA-Z0-9_-]+)\s*\{[^}]*content\s*:\s*["']\\([0-9a-fA-F]{1,6})["'][^}]*\}/g;
      let m;
      while ((m = ruleRe.exec(css)) !== null) {
        mapping[m[1]] = parseInt(m[2], 16);
      }
    }
    if (!Object.keys(mapping).length) return;
    for (const span of spans) {
      for (const cls of span.classList) {
        if (Object.prototype.hasOwnProperty.call(mapping, cls)) {
          span.textContent = String.fromCodePoint(mapping[cls]);
          break;
        }
      }
    }
  };
  deobfuscate();
  const main = document.querySelector(
    'div.chapter-wrapper div.print main, main.content, #reader main, main'
  );
  const bodyText = document.body?.innerText || document.body?.textContent || '';
  const pageHtml = document.documentElement?.innerHTML || '';
  const needsVerification =
    /TencentCaptcha|WafCaptcha|__captcha/i.test(pageHtml) ||
    /captcha|verify|verification|\u9a8c\u8bc1|\u5b89\u5168|\u4eba\u673a|\u6ed1\u5757/i.test(bodyText);
  if (!main) {
    if (needsVerification) return {error: 'verification'};
    return {
      error: /VIP|\u8ba2\u9605|\u8d2d\u4e70|\u767b\u5f55/.test(bodyText)
        ? 'locked'
        : 'missing content'
    };
  }
  const clone = main.cloneNode(true);
  clone.querySelectorAll(
    'script, style, noscript, button, textarea, input, svg, canvas, ' +
    'span.review, span.review-count, .ql-block-token, [class*="review"], ' +
    '[class*="comment"], [class*="vote"], [class*="ad"]'
  ).forEach((el) => el.remove());

  let paras = [...clone.querySelectorAll('p')]
    .map((p) => text(p))
    .filter(Boolean);
  if (paras.length < 2) {
    paras = (clone.innerText || clone.textContent || '')
      .split(/\n+/)
      .map((line) => line.replace(/\s+/g, ' ').trim())
      .filter(Boolean);
  }
  paras = paras.filter((line) => !/^(\d+\s*){1,4}$/.test(line));
  const contentText = paras.join('\n');
  if (!contentText || contentText.length < 20) {
    if (needsVerification) return {error: 'verification'};
    return {
      error: /VIP|\u8ba2\u9605|\u8d2d\u4e70|\u767b\u5f55/.test(bodyText)
        ? 'locked'
        : 'empty content'
    };
  }
  const contentHtml = paras
    .map((line) => `<p>${escapeHtml(line)}</p>`)
    .join('\n');
  return {
    chapterName,
    sourceChapterName: chapterName,
    contentText,
    contentHtml,
    images: [],
  };
}
"""
        try:
            data = self._qidian_eval(page, script, arg=chapter_name,
                                     attempts=10)
        except Exception as e:
            self.log(f"  [Qidian] Chapter extraction failed: {e}")
            return None

        if not data:
            self.log(f"  [Qidian] Empty result for: {chapter_name}")
            return None
        if data.get('error') == 'locked':
            self.log(f"  [Qidian] LOCKED or login required: {chapter_name}")
            return {'_locked': True, 'chapterName': chapter_name}
        if data.get('error') == 'verification':
            self.log(
                f"  [Qidian] Verification required for: {chapter_name}. "
                "Use Enter Browser to complete login or verification, then "
                "retry."
            )
            return {
                '_locked': True,
                '_verification_required': True,
                'chapterName': chapter_name,
            }
        if data.get('error'):
            self.log(
                f"  [Qidian] {data.get('error')} for: {chapter_name}"
            )
            return None
        return data

    def _qidian_parse_chapter_batch_parallel(self, batch_info):
        """Load a Qidian batch across multiple Chrome tabs/pages."""
        if not batch_info:
            return []

        first_url = batch_info[0].get('url', '') or self._book_url
        pages = self._qidian_parallel_pages(len(batch_info), first_url)
        if not pages:
            return [None] * len(batch_info)

        active = []
        for i, (page, ch) in enumerate(zip(pages, batch_info)):
            if self._stop_requested:
                break
            url = ch.get('url', '')
            name = ch.get('fullName', '') or ch.get('name', '')
            try:
                goto_kwargs = {
                    "wait_until": "commit",
                    "timeout": 15000,
                }
                if self._book_url:
                    goto_kwargs["referer"] = self._book_url
                page.goto(url, **goto_kwargs)
            except Exception as e:
                self.log(f"  [Qidian] Page load warning for {name}: {e}")
            active.append((i, page, name))

        results = [None] * len(batch_info)
        pending = {i for i, _, _ in active}
        active_by_index = {i: (page, name) for i, page, name in active}
        deadline = time.time() + 60

        while pending and time.time() < deadline and not self._stop_requested:
            for i in list(pending):
                page, _name = active_by_index[i]
                if self._qidian_chapter_ready(page):
                    pending.remove(i)
            if pending:
                time.sleep(0.25)

        for i in sorted(pending):
            _page, name = active_by_index[i]
            self.log(f"  [Qidian] Timed out waiting for: {name}")

        for i, page, name in active:
            if self._stop_requested or i in pending:
                continue
            results[i] = self._qidian_extract_loaded_chapter(page, name)

        return results

    # ------------------------------------------------------------------
    # KakaoPage native scraper (fallback for unsupported JS rules)
    # ------------------------------------------------------------------
    @staticmethod
    def is_kakaopage(url):
        """Check if the URL is a KakaoPage content URL."""
        return bool(url and re.match(
            r'https?://page\.kakao\.com/content/\d+', url
        ))

    @staticmethod
    def is_ntk_novel(url):
        """Check if the URL is a NewToki/ntk/sbxh novel or webtoon page."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        host = (parsed.netloc or '').lower()
        return bool(
            re.fullmatch(
                r'(?:www\.)?(?:ntk|newtoki|sbxh)\d+\.(?:com|org)', host
            )
            and re.match(r'^/(?:novel|webtoon)/\d+(?:/\d+)?/?$', parsed.path or '')
        )

    @staticmethod
    def is_yeduji(url):
        """Check if the URL is a Yeduji (夜读集) novel page."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        host = (parsed.netloc or '').lower()
        return bool(
            re.fullmatch(r'(?:www\.)?yeduji\.com', host)
            and re.match(r'^/book/\d+', parsed.path or '')
        )

    @staticmethod
    def is_munpia(url):
        """Check if the URL is a Munpia novel or chapter page."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        host = (parsed.netloc or '').lower()
        return bool(
            host == 'novel.munpia.com'
            and re.fullmatch(
                r'/\d+(?:/page/\d+(?:/neSrl/\d+)?)?/?',
                parsed.path or '/'
            )
        )

    @staticmethod
    def _munpia_novel_id(url):
        try:
            match = re.match(
                r'^/(\d+)',
                urllib.parse.urlparse(url or '').path or ''
            )
            return match.group(1) if match else ''
        except Exception:
            return ''

    @staticmethod
    def _ntk_content_kind_from_url(url):
        try:
            match = re.search(r'^/(novel|webtoon)/', urllib.parse.urlparse(url or '').path)
            return match.group(1) if match else 'novel'
        except Exception:
            return 'novel'

    def _ntk_is_challenge_page(self, page):
        """Detect Cloudflare's interstitial so users get a useful message."""
        try:
            title = (page.title() or '').strip().lower()
            if 'just a moment' in title:
                return True
        except Exception:
            pass
        try:
            text = page.locator('body').inner_text(timeout=3000).lower()
            return (
                'enable javascript and cookies to continue' in text
                or 'checking your browser' in text
                or 'cf-challenge' in text
            )
        except Exception:
            return False

    def _ntk_wait_for_access(self, page, timeout=180):
        """Wait for the visible Chrome window to clear Cloudflare."""
        if not self._ntk_is_challenge_page(page):
            return True
        self.log(
            "[NewToki] Waiting for Cloudflare verification in the visible "
            "Chrome window..."
        )
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop_requested:
            if not self._ntk_is_challenge_page(page):
                self.log("[NewToki] Verification cleared.")
                return True
            try:
                page.wait_for_timeout(1000)
            except Exception:
                time.sleep(1)
        return not self._ntk_is_challenge_page(page)

    @staticmethod
    def _ntk_b64url_encode(data):
        return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')

    @staticmethod
    def _ntk_b64url_decode(data):
        padding = '=' * ((4 - len(data) % 4) % 4)
        return base64.urlsafe_b64decode(data + padding)

    @staticmethod
    def _ntk_novel_id_from_url(url):
        try:
            match = re.search(r'/(?:novel|webtoon)/(\d+)', urllib.parse.urlparse(url).path)
            return match.group(1) if match else ''
        except Exception:
            return ''

    @staticmethod
    def _ntk_episode_id_from_url(url):
        try:
            path = urllib.parse.urlparse(url or '').path.rstrip('/')
            return path.rsplit('/', 1)[-1]
        except Exception:
            return ''

    @staticmethod
    def _ntk_requests_module():
        try:
            from curl_cffi import requests as curl_requests
            return curl_requests, True
        except Exception:
            import requests as std_requests
            return std_requests, False

    @staticmethod
    def _ntk_parse_curl_command(curl_command):
        headers = {}
        cookies = {}
        if not curl_command:
            return headers, cookies
        try:
            args = shlex.split(curl_command)
        except ValueError:
            return headers, cookies
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in ('-H', '--header') and i + 1 < len(args):
                header = args[i + 1]
                if ':' in header:
                    key, value = header.split(':', 1)
                    key = key.strip()
                    value = value.strip()
                    if key.lower() != 'accept-encoding':
                        headers[key] = value
                i += 2
                continue
            if arg in ('-b', '--cookie') and i + 1 < len(args):
                for item in args[i + 1].split(';'):
                    if '=' in item:
                        key, value = item.strip().split('=', 1)
                        cookies[key] = value
                i += 2
                continue
            i += 1

        cookie_key = next(
            (key for key in headers if key.lower() == 'cookie'),
            None,
        )
        if cookie_key:
            cookie_value = headers.pop(cookie_key)
            for item in cookie_value.split(';'):
                if '=' in item:
                    key, value = item.strip().split('=', 1)
                    cookies[key] = value
        return headers, cookies

    @staticmethod
    def _ntk_windows_dpapi_unprotect(data):
        if sys.platform != "win32" or not data:
            return None
        try:
            class DATA_BLOB(ctypes.Structure):
                _fields_ = [
                    ("cbData", ctypes.c_uint),
                    ("pbData", ctypes.POINTER(ctypes.c_char)),
                ]

            in_buffer = ctypes.create_string_buffer(data, len(data))
            in_blob = DATA_BLOB(
                len(data),
                ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)),
            )
            out_blob = DATA_BLOB()
            ok = ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(in_blob),
                None,
                None,
                None,
                None,
                0,
                ctypes.byref(out_blob),
            )
            if not ok:
                return None
            try:
                return ctypes.string_at(out_blob.pbData, out_blob.cbData)
            finally:
                ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        except Exception:
            return None

    def _ntk_chrome_master_key(self, profile_dir):
        local_state = os.path.join(profile_dir, "Local State")
        try:
            with open(local_state, "r", encoding="utf-8") as f:
                data = json.load(f)
            encrypted_key = data.get("os_crypt", {}).get("encrypted_key", "")
            raw = base64.b64decode(encrypted_key)
            if raw.startswith(b"DPAPI"):
                raw = raw[5:]
            return self._ntk_windows_dpapi_unprotect(raw)
        except Exception:
            return None

    def _ntk_decrypt_chrome_cookie(self, host_key, encrypted_value, key):
        if not encrypted_value:
            return ""
        try:
            if encrypted_value.startswith((b"v10", b"v11")) and key:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                nonce = encrypted_value[3:15]
                ciphertext = encrypted_value[15:]
                plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
                host_hash = hashlib.sha256((host_key or "").encode()).digest()
                if plaintext.startswith(host_hash):
                    plaintext = plaintext[32:]
                return plaintext.decode("utf-8", "ignore")
            if sys.platform == "win32":
                plaintext = self._ntk_windows_dpapi_unprotect(encrypted_value)
                return plaintext.decode("utf-8", "ignore") if plaintext else ""
        except Exception:
            return ""
        return ""

    def _ntk_load_profile_cookies(self, url):
        """Read Cloudflare/NewToki cookies from the dedicated Chrome profile."""
        profile_dir = self._get_ntk_user_data_dir()
        cookies_db = os.path.join(profile_dir, "Default", "Network", "Cookies")
        if not os.path.exists(cookies_db):
            return {}
        key = self._ntk_chrome_master_key(profile_dir)
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lstrip(".")
        if not host:
            return {}
        now_chrome = int((time.time() + 11644473600) * 1000000)
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="ntk_cookies_", suffix=".db")
            os.close(fd)
            shutil.copy2(cookies_db, tmp_path)
            conn = sqlite3.connect(tmp_path)
            try:
                rows = conn.execute(
                    """
                    SELECT host_key, name, value, encrypted_value, expires_utc
                    FROM cookies
                    WHERE host_key = ? OR host_key = ? OR host_key LIKE ?
                    """,
                    (host, "." + host, "%." + host),
                ).fetchall()
            finally:
                conn.close()
            out = {}
            for host_key, name, value, encrypted_value, expires_utc in rows:
                if expires_utc and expires_utc < now_chrome:
                    continue
                cookie_value = value or self._ntk_decrypt_chrome_cookie(
                    host_key,
                    encrypted_value,
                    key,
                )
                if name and cookie_value:
                    out[name] = cookie_value
            return out
        except Exception as e:
            # Chrome locks its Cookies DB while the visible clearance window is
            # open. That is expected during the automatic fallback; live browser
            # cookies are collected separately.
            if getattr(e, "winerror", None) != 32:
                self.log(f"[NewToki] Chrome profile cookie import failed: {e}")
            return {}
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _ntk_default_headers_and_cookies(self, url):
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        user_agent = (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
        accept_language = 'en-US,en;q=0.9'
        sec_ch_ua = '"Chromium";v="120", "Google Chrome";v="120", "Not?A_Brand";v="99"'
        sec_ch_mobile = '?0'
        sec_ch_platform = '"Windows"'
        curl_headers, curl_cookies = self._ntk_parse_curl_command(
            self.ntk_curl_command
        )
        profile_cookies = self._ntk_load_profile_cookies(url)
        merged_cookies = dict(profile_cookies)
        merged_cookies.update(curl_cookies)

        def header(name, fallback):
            for key, value in curl_headers.items():
                if key.lower() == name.lower():
                    return value
            return fallback

        user_agent = header('user-agent', user_agent)
        accept_language = header('accept-language', accept_language)
        sec_ch_ua = header('sec-ch-ua', sec_ch_ua)
        sec_ch_mobile = header('sec-ch-ua-mobile', sec_ch_mobile)
        sec_ch_platform = header('sec-ch-ua-platform', sec_ch_platform)

        doc_headers = {
            'User-Agent': user_agent,
            'Accept': header(
                'accept',
                'text/html,application/xhtml+xml,application/xml;q=0.9,'
                'image/avif,image/webp,*/*;q=0.8',
            ),
            'Accept-Language': accept_language,
            'sec-ch-ua': sec_ch_ua,
            'sec-ch-ua-mobile': sec_ch_mobile,
            'sec-ch-ua-platform': sec_ch_platform,
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'upgrade-insecure-requests': '1',
        }
        api_headers = {
            'User-Agent': user_agent,
            'Accept': '*/*',
            'Accept-Language': accept_language,
            'sec-ch-ua': sec_ch_ua,
            'sec-ch-ua-mobile': sec_ch_mobile,
            'sec-ch-ua-platform': sec_ch_platform,
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'x-novel-client': 'shadow-v2',
            'Content-Type': 'application/json',
        }
        return {
            'origin': origin,
            'host': parsed.netloc,
            'cookies': merged_cookies,
            'user_agent': user_agent,
            'doc_headers': {k: v for k, v in doc_headers.items() if v},
            'api_headers': {k: v for k, v in api_headers.items() if v},
            'from_curl': bool(curl_headers or curl_cookies),
            'from_profile_cookies': bool(profile_cookies),
        }

    def _ntk_browser_headers_and_cookies(self, url):
        """Collect the verified visible Chrome session for API requests."""
        if not self._page or not self._context:
            return None
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        try:
            cookies = {
                c.get('name'): c.get('value')
                for c in self._context.cookies(origin)
                if c.get('name') and c.get('value') is not None
            }
        except Exception:
            cookies = {}
        try:
            ua_info = self._page.evaluate(r"""
() => {
  const uaData = navigator.userAgentData || null;
  const brands = uaData && Array.isArray(uaData.brands)
    ? uaData.brands.map((b) => `"${b.brand}";v="${b.version}"`).join(', ')
    : '';
  return {
    userAgent: navigator.userAgent || '',
    language: navigator.language || 'en-US,en;q=0.9',
    brands,
    mobile: uaData ? (uaData.mobile ? '?1' : '?0') : '?0',
    platform: uaData && uaData.platform ? `"${uaData.platform}"` : '"Windows"'
  };
}
            """) or {}
        except Exception:
            ua_info = {}
        user_agent = ua_info.get('userAgent') or (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        accept_language = ua_info.get('language') or 'en-US,en;q=0.9'
        sec_ch_ua = ua_info.get('brands') or '"Chromium";v="120", "Google Chrome";v="120"'
        sec_ch_mobile = ua_info.get('mobile') or '?0'
        sec_ch_platform = ua_info.get('platform') or '"Windows"'

        doc_headers = {
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': accept_language,
            'sec-ch-ua': sec_ch_ua,
            'sec-ch-ua-mobile': sec_ch_mobile,
            'sec-ch-ua-platform': sec_ch_platform,
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'upgrade-insecure-requests': '1',
        }
        api_headers = {
            'User-Agent': user_agent,
            'Accept': '*/*',
            'Accept-Language': accept_language,
            'sec-ch-ua': sec_ch_ua,
            'sec-ch-ua-mobile': sec_ch_mobile,
            'sec-ch-ua-platform': sec_ch_platform,
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'x-novel-client': 'shadow-v2',
            'Content-Type': 'application/json',
        }
        return {
            'origin': origin,
            'host': parsed.netloc,
            'cookies': cookies,
            'user_agent': user_agent,
            'doc_headers': {k: v for k, v in doc_headers.items() if v},
            'api_headers': {k: v for k, v in api_headers.items() if v},
        }

    def _ntk_create_api_session(self, state):
        req, is_curl = self._ntk_requests_module()
        try:
            session = req.Session(impersonate='chrome120') if is_curl else req.Session()
        except TypeError:
            session = req.Session()
        host = state.get('host') or 'ntk01.com'
        for name, value in (state.get('cookies') or {}).items():
            if name and name.lower() != 'nv':
                try:
                    session.cookies.set(name, value, domain=host)
                except Exception:
                    session.cookies.set(name, value)
        return session

    def _ntk_issue_nv(self, state, referer):
        session = state.get('session')
        if not session:
            return False
        headers = dict(state['api_headers'])
        headers['Referer'] = referer or state.get('index_url') or state['origin']
        try:
            session.post(
                state['origin'] + '/api/nv-issue',
                headers=headers,
                timeout=15,
            )
            return bool(session.cookies.get('nv'))
        except Exception as e:
            self.log(f"  [NewToki] nv issue failed: {e}")
            return False

    def _ntk_prepare_api_state(self, index_url, novel_id=None):
        state = None
        if self._page and self._context and not self.ntk_curl_command:
            state = self._ntk_browser_headers_and_cookies(index_url)
            if state:
                state['from_live_browser'] = True
        if not state:
            state = self._ntk_default_headers_and_cookies(index_url)
        state['novel_id'] = novel_id or self._ntk_novel_id_from_url(index_url)
        state['index_url'] = index_url
        state['session'] = self._ntk_create_api_session(state)
        self._ntk_issue_nv(state, index_url)
        if state.get('from_profile_cookies'):
            names = ', '.join(sorted((state.get('cookies') or {}).keys()))
            self.log(f"[NewToki] Imported Chrome profile cookies: {names}")
        self._ntk_api_state = state
        return state

    def _ntk_clone_api_state(self):
        """Create an independent API session for a worker thread."""
        base = self._ntk_api_state
        if not base:
            return None
        state = {
            key: value
            for key, value in base.items()
            if key != 'session'
        }
        state['session'] = self._ntk_create_api_session(state)
        self._ntk_issue_nv(state, state.get('index_url') or state['origin'])
        return state

    def _ntk_request_get(self, session, url, headers):
        return session.get(url, headers=headers, timeout=30)

    def _ntk_request_post_json(self, session, url, payload, headers):
        return session.post(url, json=payload, headers=headers, timeout=30)

    def _ntk_clean_plaintext(self, text):
        text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
        text = text.replace('\u200b', '').replace('\ufeff', '')
        lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.split('\n')]
        chunks = []
        blank = False
        for line in lines:
            if not line:
                if chunks and not blank:
                    chunks.append('')
                blank = True
                continue
            chunks.append(line)
            blank = False
        return '\n'.join(chunks).strip()

    def _ntk_build_text_chapter(self, title, plaintext, selector):
        text = self._ntk_clean_plaintext(plaintext)
        if not text:
            return None
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
        if not paragraphs:
            paragraphs = [line.strip() for line in text.split('\n') if line.strip()]
        content_html = '\n'.join(
            f'<p>{html.escape(p).replace(chr(10), "<br/>")}</p>'
            for p in paragraphs
        )
        return {
            'chapterName': title or 'Chapter',
            'sourceChapterName': title or 'Chapter',
            'contentText': text,
            'contentHtml': content_html,
            '_debugSelector': selector,
        }

    def _ntk_html_attrs(self, tag):
        attrs = {}
        for name, _quote, value in re.findall(
            r'([:\w-]+)\s*=\s*(["\'])(.*?)\2',
            tag or '',
            re.S,
        ):
            attrs[name.lower()] = html.unescape(value).strip()
        return attrs

    def _ntk_meta_contents(self, html_text, names):
        wanted = {name.lower() for name in names}
        values = []
        for tag in re.findall(r'<meta\b[^>]*>', html_text or '', re.I | re.S):
            attrs = self._ntk_html_attrs(tag)
            keys = {
                (attrs.get('name') or '').lower(),
                (attrs.get('property') or '').lower(),
                (attrs.get('itemprop') or '').lower(),
            }
            content = attrs.get('content') or ''
            if content and wanted.intersection(keys):
                values.append(content.strip())
        return values

    def _ntk_meta_content(self, html_text, names):
        values = self._ntk_meta_contents(html_text, names)
        return values[0] if values else ''

    def _ntk_plain_fragment(self, value):
        value = re.sub(r'<[^>]+>', ' ', value or '')
        return html.unescape(re.sub(r'\s+', ' ', value)).strip()

    def _ntk_abs_url(self, base_url, value):
        value = html.unescape((value or '').strip())
        value = value.replace('\\/', '/').replace('\\\\/', '/')
        value = value.strip(' \t\r\n"\'\\')
        if not value or value.startswith(('data:', 'javascript:')):
            return ''
        if ',' in value and re.search(r'\s+\d+[wx](?:,|$)', value):
            value = value.split(',', 1)[0].strip()
        if ' ' in value and re.search(r'\s+\d+[wx]$', value):
            value = value.split()[0].strip()
        return urllib.parse.urljoin(base_url, value)

    def _ntk_is_site_image(self, url):
        url = (url or '').lower()
        return any(
            token in url
            for token in (
                'og-default',
                'favicon',
                'apple-touch-icon',
                'newtoki-logo',
                '/logo',
                'logo.',
            )
        )

    def _ntk_extract_cover_url(self, html_text, index_url):
        cover = self._ntk_meta_content(
            html_text,
            ('og:image', 'twitter:image', 'twitter:image:src', 'image'),
        )
        if cover:
            cover = self._ntk_abs_url(index_url, cover)
            if cover and not self._ntk_is_site_image(cover):
                return cover

        for match in re.finditer(
            r'https?:\\?/\\?/[^"\'\s<>]+(?:novel|webtoon)_thumb[^"\'\s<>]+',
            html_text or '',
            re.I,
        ):
            cover = self._ntk_abs_url(index_url, match.group(0))
            if cover and not self._ntk_is_site_image(cover):
                return cover

        for match in re.finditer(
            r'"(?:image|thumbnail|cover)"\s*:\s*"([^"]+)"',
            html_text or '',
            re.I,
        ):
            cover = self._ntk_abs_url(index_url, match.group(1).replace('\\/', '/'))
            if cover:
                return cover

        candidates = []
        first_content_image = ''
        for tag in re.findall(r'<img\b[^>]*>', html_text or '', re.I | re.S):
            attrs = self._ntk_html_attrs(tag)
            src = (
                attrs.get('data-src')
                or attrs.get('data-original')
                or attrs.get('data-lazy-src')
                or attrs.get('srcset')
                or attrs.get('src')
                or ''
            )
            src = self._ntk_abs_url(index_url, src)
            if not src:
                continue
            if self._ntk_is_site_image(src):
                continue
            src_lower = src.lower()
            haystack = ' '.join(
                str(attrs.get(k) or '')
                for k in ('class', 'id', 'alt', 'title', 'src', 'data-src')
            ).lower()
            if any(word in haystack for word in ('logo', 'icon', 'avatar', 'profile')):
                continue
            if any(word in src_lower for word in ('logo', 'icon', 'avatar', 'profile', 'favicon', 'emoji')):
                continue
            if not first_content_image:
                first_content_image = src
            score = 0
            if any(word in haystack for word in ('cover', 'poster', 'thumbnail', 'thumb', 'book', 'novel')):
                score += 4
            if any(word in src_lower for word in ('cover', 'poster', 'thumbnail', 'thumb', 'book', 'novel', 'upload', '/data/', '/file/')):
                score += 2
            candidates.append((score, src))
        candidates.sort(reverse=True)
        if candidates and candidates[0][0] > 0:
            return candidates[0][1]
        return first_content_image

    def _ntk_novelpia_id_from_cover_url(self, cover_url):
        match = re.search(r'/novel_thumb/(\d+)', cover_url or '', re.I)
        return match.group(1) if match else ''

    def _ntk_fetch_novelpia_cover_url(self, novelpia_id):
        if not novelpia_id:
            return ''
        url = f'https://novelpia.com/novel/{novelpia_id}'
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Referer': 'https://novelpia.com/',
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                text = response.read().decode('utf-8', 'ignore')
        except Exception as e:
            self.log(
                f"[NewToki] Novelpia cover lookup failed for "
                f"{novelpia_id}: {e}"
            )
            return ''
        patterns = (
            r'"(//images\.novelpia\.com/imagebox/original/[^"]+)"',
            r'"(//images\.novelpia\.com/imagebox/cover/[^"]+)"',
            r'(https?:)?(//images\.novelpia\.com/imagebox/original/[^\s"\'<>]+)',
            r'(https?:)?(//images\.novelpia\.com/imagebox/cover/[^\s"\'<>]+)',
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if not match:
                continue
            if len(match.groups()) >= 2:
                value = (match.group(1) or 'https:') + match.group(2)
            else:
                value = 'https:' + match.group(1)
            return html.unescape(value).replace('\\/', '/')
        return ''

    def _ntk_prefer_novelpia_cover_url(self, cover_url):
        if not self.ntk_prefer_novelpia_cover:
            return cover_url
        novelpia_id = self._ntk_novelpia_id_from_cover_url(cover_url)
        if not novelpia_id:
            return cover_url
        novelpia_cover = self._ntk_fetch_novelpia_cover_url(novelpia_id)
        if novelpia_cover:
            self.log(
                f"[NewToki] Using Novelpia cover for mapped ID "
                f"{novelpia_id}: {novelpia_cover}"
            )
            return novelpia_cover
        self.log(
            f"[NewToki] Novelpia cover unavailable for mapped ID "
            f"{novelpia_id}; using NewToki cover."
        )
        return cover_url

    def _ntk_clean_tag(self, value):
        value = self._ntk_plain_fragment(value).strip(' #,./\\|[](){}')
        value = value.replace('\\n', ' ').replace('\\r', ' ')
        value = re.sub(r'\s+', ' ', value).strip()
        if not value or len(value) > 40:
            return ''
        if re.fullmatch(r'[\d\s.,:;+\-]+', value):
            return ''
        if re.search(r'https?://|ntk\d+\.(?:com|org)|newtoki', value, re.I):
            return ''
        return value

    def _ntk_extract_tags(self, html_text):
        tags = []

        def add(value):
            value = self._ntk_clean_tag(value)
            if value and value not in tags:
                tags.append(value)

        for meta_value in self._ntk_meta_contents(
            html_text,
            ('keywords', 'news_keywords', 'article:tag', 'book:tag'),
        ):
            for part in re.split(r'[,#|/]+', meta_value):
                add(part)

        for match in re.finditer(
            r'<a\b[^>]*href=["\'][^"\']*(?:tag|genre|keyword)[^"\']*["\'][^>]*>(.*?)</a>',
            html_text or '',
            re.I | re.S,
        ):
            add(match.group(1))

        for match in re.finditer(
            r'(?:href|to)=\\?["\'][^"\']*(?:/novel\?g=|genre|tag|keyword)[^"\']*\\?["\'][^{}]{0,300}?children\\?["\']?\s*:\s*(?:\[\\?["\']#\\?["\']\s*,\s*)?\\?["\']([^"\'\\]+)',
            html_text or '',
            re.I | re.S,
        ):
            add(match.group(1))

        for match in re.finditer(
            r'hero-v2-tag[^{}]{0,500}?children\\?["\']?\s*:\s*(?:\[\\?["\']#\\?["\']\s*,\s*)?\\?["\']([^"\'\\]+)',
            html_text or '',
            re.I | re.S,
        ):
            add(match.group(1))

        page_text = self._ntk_plain_fragment(html_text)
        for match in re.finditer(r'(?:^|\s)#([^\s#]{1,40})', page_text):
            add(match.group(1))
        return tags[:20]

    def _ntk_extract_author(self, html_text):
        author = self._ntk_meta_content(
            html_text,
            ('author', 'article:author', 'book:author'),
        )
        if author:
            return self._ntk_clean_tag(author)
        page_text = self._ntk_plain_fragment(html_text)
        for pattern in (
            r'(?:작가|저자)\s*[:：]\s*([^\n\r|·ㆍ]{1,60})',
            r'\bby\s+([^\n\r|·ㆍ]{1,60})',
        ):
            match = re.search(pattern, page_text, re.I)
            if match:
                author = self._ntk_clean_tag(match.group(1))
                if author:
                    return author
        return ''

    def _ntk_merge_book_metadata(self, primary, fallback):
        if not primary or not fallback:
            return primary
        for key in ('author', 'coverUrl', 'introduction', 'introductionHTML'):
            if not primary.get(key) and fallback.get(key):
                primary[key] = fallback[key]
        if not primary.get('tags') and fallback.get('tags'):
            primary['tags'] = fallback['tags']
        return primary

    def _ntk_parse_index_html(self, html_text, index_url):
        novel_id = self._ntk_novel_id_from_url(index_url)
        raw_title = self._ntk_meta_content(
            html_text,
            ('og:title', 'twitter:title', 'title'),
        )
        if not raw_title:
            title_match = re.search(r'<title>([^<]+)</title>', html_text, re.I)
            raw_title = html.unescape(title_match.group(1)).strip() if title_match else f'Novel_{novel_id}'
        raw_title = re.split(r'\s+[-|]\s+', raw_title)[0].strip()

        intro = self._ntk_meta_content(
            html_text,
            ('description', 'og:description', 'twitter:description'),
        )
        cover = self._ntk_extract_cover_url(html_text, index_url)
        tags = self._ntk_extract_tags(html_text)
        author = self._ntk_extract_author(html_text)

        ep_blocks = re.findall(
            r'<li\s+data-ep=["\'](\d+)["\'][^>]*>.*?href=["\']([^"\']+)["\'].*?<span\s+class=["\']ne-title["\']>(.*?)</span>',
            html_text,
            re.I | re.S,
        )
        if not ep_blocks:
            ep_blocks = re.findall(
                r'data-ep\\?["\']?\s*[:=]\s*(\d+).*?href\\?["\']?\s*[:=]\s*\\?["\']([^"\']+).*?className\\?["\']?\s*:\s*\\?["\'][^"\']*(?:ne-title|ep-title)[^"\']*.*?children\\?["\']?\s*:\s*\\?["\']([^"\\]+)',
                html_text,
                re.I | re.S,
            )
        kind = self._ntk_content_kind_from_url(index_url)
        if not ep_blocks and kind == 'webtoon':
            base_id = self._ntk_novel_id_from_url(index_url)
            webtoon_links = []
            for match in re.finditer(
                rf'((?:https?:\\?/\\?/[^"\'\s<>]+)?/webtoon/{re.escape(base_id)}/(\d+))',
                html_text or '',
                re.I,
            ):
                href = self._ntk_abs_url(index_url, match.group(1))
                ep_id = match.group(2)
                if href and ep_id:
                    webtoon_links.append((ep_id, href))
            seen_links = set()
            for idx, (ep_id, href) in enumerate(webtoon_links, 1):
                if href in seen_links:
                    continue
                seen_links.add(href)
                ep_blocks.append((str(idx), href, f'{idx}\ud654'))
        chapters = []
        seen = set()
        for ep_num, href, ep_title in ep_blocks:
            url = urllib.parse.urljoin(index_url, html.unescape(href))
            ep_id = self._ntk_episode_id_from_url(url)
            if not ep_id or ep_id in seen:
                continue
            seen.add(ep_id)
            title = re.sub(r'<[^>]+>', '', ep_title)
            title = html.unescape(re.sub(r'\s+', ' ', title)).strip()
            try:
                number = int(ep_num)
            except Exception:
                number = len(chapters) + 1
            name = title or f'{number}\ud654'
            chapters.append({
                'url': url,
                'name': name,
                'fullName': name,
                'number': number,
                'episodeId': ep_id,
                'kind': kind,
                'isVIP': False,
                'isPaid': False,
            })
        chapters.sort(key=lambda ch: (ch.get('number') or 10**12, ch.get('episodeId') or ''))
        return {
            'bookUrl': index_url,
            'bookname': raw_title,
            'author': author,
            'coverUrl': cover,
            'introduction': intro,
            'introductionHTML': f'<p>{html.escape(intro)}</p>' if intro else '',
            'language': 'ko',
            'tags': tags,
            'chapterCount': len(chapters),
            'chapters': chapters,
            '_ntk_kind': kind,
        }

    def _ntk_fetch_index_via_api_session(self, url, state):
        session = state.get('session')
        headers = dict(state['doc_headers'])
        headers['Referer'] = state['origin'] + '/'
        try:
            response = self._ntk_request_get(session, url, headers)
        except Exception as e:
            self.log(f"ERROR: [NewToki] Index request failed: {e}")
            return None
        cf_keywords = ('cf-browser-verification', 'Just a moment', 'Ray ID')
        if response.status_code in (403, 503) or any(k in response.text for k in cf_keywords):
            self.log(
                f"[NewToki] API index request blocked "
                f"(HTTP {response.status_code})."
            )
            return None
        if response.status_code != 200:
            self.log(
                f"ERROR: [NewToki] Index request returned HTTP "
                f"{response.status_code}."
            )
            return None
        return response.text

    def _ntk_refresh_cloudflare_session(self, url):
        """Open NewToki in installed headless Chrome and keep it alive."""
        self.log("[NewToki] Navigating with installed headless Chrome...")
        if not self._start_ntk_browser(url):
            return False
        try:
            if self._page:
                try:
                    response = self._page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                    if response:
                        self.log(
                            f"[NewToki] Headless navigation returned HTTP "
                            f"{response.status}."
                        )
                except Exception as e:
                    self.log(f"[NewToki] Headless navigation warning: {e}")
                try:
                    self._page.wait_for_timeout(3000)
                except Exception:
                    time.sleep(3)
            return True
        except Exception as e:
            self.log(f"[NewToki] Chrome clearance refresh failed: {e}")
            return False

    @staticmethod
    def _ntk_browser_html_is_blocked(html_text):
        text = (html_text or '').lower()
        if any(marker in text for marker in (
            'cf-browser-verification',
            'just a moment',
            'app_or_unknown_403',
            'nginx_asn',
            'this request was blocked by site security policy',
            'country restriction: only kr is allowed',
        )):
            return True
        return 'access denied' in text and 'cloudflare' in text

    def _ntk_capture_browser_index_html(self):
        """Capture the already-rendered index before handing off from Chrome."""
        if not self._page:
            return ''
        try:
            try:
                self._page.wait_for_function(
                    """() => document.querySelectorAll(
                      'ul.novel-eps li a, li[data-ep] a, a[href*="/novel/"]'
                    ).length > 0""",
                    timeout=10000,
                )
            except Exception:
                pass
            html_text = self._page.content() or ''
            if self._ntk_browser_html_is_blocked(html_text):
                return ''
            return html_text
        except Exception as e:
            self.log(f"[NewToki] Could not capture rendered Chrome index: {e}")
            return ''

    @staticmethod
    def _ntk_display_chapter_number(item):
        """Return the human episode number exposed by an index row."""
        if not isinstance(item, dict):
            return None
        try:
            display_number = int(item.get('displayNumber'))
        except (TypeError, ValueError):
            display_number = None
        if display_number and display_number > 0:
            return display_number
        matches = re.findall(r'\d+', str(item.get('name') or ''))
        if matches:
            try:
                number = int(matches[-1])
                return number if number > 0 else None
            except ValueError:
                pass
        try:
            number = int(item.get('number'))
        except (TypeError, ValueError):
            number = None
        if number is not None:
            if number > 0:
                return number
        return None

    def _ntk_browser_index_looks_partial(self, chapters):
        """Detect a newest-page-only list such as episodes 69 through 130."""
        numbers = [
            number for number in (
                self._ntk_display_chapter_number(item)
                for item in (chapters or [])
            ) if number is not None
        ]
        return bool(numbers) and min(numbers) > 1

    def _ntk_collect_all_browser_chapters(self, index_url):
        """Collect virtualized and paginated episode rows in live Chrome."""
        if not self._page:
            return []
        novel_id = self._ntk_novel_id_from_url(index_url)
        kind = self._ntk_content_kind_from_url(index_url)
        try:
            return self._page.evaluate(
                r"""
async ({ novelId, kind }) => {
  const basePath = `/${kind}/${novelId}`;
  const chapterPrefix = `${basePath}/`;
  const chapters = new Map();
  const text = (element) => element && element.textContent
    ? element.textContent.trim()
    : '';
  const pause = (milliseconds) => new Promise(
    (resolve) => setTimeout(resolve, milliseconds)
  );

  const collect = (root, baseUrl) => {
    const allLinks = Array.from(root.querySelectorAll('a[href]'));
    const episodeRowLinks = allLinks.filter(
      (link) => link.closest('li[data-ep]')
    );
    const candidateLinks = episodeRowLinks.length ? episodeRowLinks : allLinks;
    for (const link of candidateLinks) {
      let target;
      try {
        const href = link.getAttribute('href') || link.href;
        target = new URL(href, baseUrl);
      } catch (_) {
        continue;
      }
      if (target.origin !== location.origin
          || !target.pathname.startsWith(chapterPrefix)) {
        continue;
      }
      const remainder = target.pathname
        .slice(chapterPrefix.length)
        .replace(/\/$/, '');
      if (!/^\d+$/.test(remainder)) continue;
      const row = link.closest('li[data-ep], li, tr, article, div');
      const titleElement = link.querySelector(
        '.ne-title, .ep-title, [class*="title"]'
      );
      const chapterName = text(titleElement) || text(link) || text(row) || '';
      if (/(?:1\s*화부터\s*보기|최신화부터)/.test(chapterName)) continue;
      chapters.set(remainder, {
        url: target.href,
        episodeId: remainder,
        displayNumber: row && row.getAttribute('data-ep') || '',
        name: chapterName,
      });
    }
  };

  collect(document, location.href);

  // Some sbxh indexes virtualize the long episode list. Walk every relevant
  // scroll container while retaining rows that disappear from the live DOM.
  const initialNumbers = Array.from(chapters.values())
    .map((item) => Number.parseInt(item.displayNumber, 10))
    .filter((number) => Number.isFinite(number) && number > 0);
  if (initialNumbers.length && Math.min(...initialNumbers) > 1) {
    const clicked = new Set();
    let stableRounds = 0;
    for (let round = 0; round < 80 && stableRounds < 3; round += 1) {
      const before = chapters.size;
      let moved = false;

      for (const element of document.querySelectorAll(
        'button, [role="button"], a[href="#"], a[href^="javascript:"]'
      )) {
        if (clicked.has(element)) continue;
        const label = text(element).replace(/\s+/g, ' ');
        if (!/(?:더\s*보기|전체\s*(?:목록|보기)|이전\s*(?:목록|회차)|load\s*more|show\s*all|older)/i.test(label)) {
          continue;
        }
        clicked.add(element);
        try {
          element.click();
          moved = true;
        } catch (_) {}
      }

      const scrollers = new Set();
      for (const link of document.querySelectorAll(
        `a[href*="${chapterPrefix}"]`
      )) {
        let node = link.parentElement;
        while (node && node !== document.body) {
          const style = getComputedStyle(node);
          if (node.scrollHeight > node.clientHeight + 8
              && /(auto|scroll)/.test(style.overflowY)) {
            scrollers.add(node);
          }
          node = node.parentElement;
        }
      }
      if (document.scrollingElement) scrollers.add(document.scrollingElement);
      for (const scroller of scrollers) {
        const oldTop = scroller.scrollTop;
        const step = Math.max(scroller.clientHeight * 0.8, 300);
        scroller.scrollTop = Math.min(
          scroller.scrollHeight - scroller.clientHeight,
          oldTop + step,
        );
        if (scroller.scrollTop > oldTop + 1) moved = true;
        try { scroller.dispatchEvent(new Event('scroll')); } catch (_) {}
      }

      await pause(100);
      collect(document, location.href);
      if (chapters.size === before && !moved) stableRounds += 1;
      else stableRounds = 0;
    }
  }

  // Traditional indexes expose older episode pages as links. Follow them in
  // the same verified browser session and recursively discover further pages.
  const queue = [];
  const queued = new Set();
  const visited = new Set([new URL(location.href).href.split('#')[0]]);
  const discoverPages = (root, baseUrl) => {
    for (const link of root.querySelectorAll('a[href]')) {
      let target;
      try {
        target = new URL(link.getAttribute('href') || link.href, baseUrl);
      } catch (_) {
        continue;
      }
      target.hash = '';
      if (target.origin !== location.origin) continue;
      const normalizedPath = target.pathname.replace(/\/$/, '');
      const suffix = normalizedPath.startsWith(basePath)
        ? normalizedPath.slice(basePath.length)
        : '';
      const isIndexPage = normalizedPath === basePath && Boolean(target.search);
      const isPagedPath = /^\/(?:page|p)\/\d+$/i.test(suffix);
      if (!isIndexPage && !isPagedPath) continue;
      if (visited.has(target.href) || queued.has(target.href)) continue;
      queued.add(target.href);
      queue.push(target.href);
    }
  };

  discoverPages(document, location.href);
  let fetchedPages = 0;
  while (queue.length && fetchedPages < 20) {
    const pageUrl = queue.shift();
    queued.delete(pageUrl);
    if (visited.has(pageUrl)) continue;
    visited.add(pageUrl);
    fetchedPages += 1;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(pageUrl, {
        credentials: 'include',
        cache: 'no-store',
        signal: controller.signal,
      });
      if (!response.ok) continue;
      const pageHtml = await response.text();
      const lowered = pageHtml.toLowerCase();
      if (lowered.includes('access denied')
          || lowered.includes('site security policy')
          || lowered.includes('just a moment')) {
        continue;
      }
      const pageDocument = new DOMParser().parseFromString(pageHtml, 'text/html');
      collect(pageDocument, pageUrl);
      discoverPages(pageDocument, pageUrl);
    } catch (_) {
      // Keep the already collected rows if one optional index page fails.
    } finally {
      clearTimeout(timer);
    }
  }

  return Array.from(chapters.values());
}
                """,
                {'novelId': novel_id, 'kind': kind},
            ) or []
        except Exception as e:
            self.log(f"[NewToki] Extended chapter index scan failed: {e}")
            return []

    def _ntk_parse_index_browser(self, index_url):
        """Extract metadata and chapters from NewToki's rendered headless DOM."""
        if not self._page:
            return None
        novel_id = self._ntk_novel_id_from_url(index_url)
        kind = self._ntk_content_kind_from_url(index_url)
        try:
            try:
                self._page.wait_for_function(
                    r"""
({ novelId, kind }) => {
  const prefix = `/${kind}/${novelId}/`;
  const hasChapter = Array.from(document.querySelectorAll('a[href]'))
    .some((link) => {
      try { return new URL(link.href, location.href).pathname.startsWith(prefix); }
      catch (_) { return false; }
    });
  const body = (document.body && document.body.innerText || '').toLowerCase();
  const blocked = body.includes('access denied')
    || body.includes('site security policy')
    || body.includes('just a moment');
  return hasChapter || blocked;
}
                    """,
                    {'novelId': novel_id, 'kind': kind},
                    timeout=30000,
                )
            except Exception:
                pass

            result = self._page.evaluate(
                r"""
({ novelId, kind }) => {
  const text = (element) => element && element.textContent
    ? element.textContent.trim()
    : '';
  const firstText = (selectors) => {
    for (const selector of selectors) {
      const value = text(document.querySelector(selector));
      if (value) return value;
    }
    return '';
  };
  const firstHtml = (selectors) => {
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (element && element.innerHTML.trim()) return element.innerHTML.trim();
    }
    return '';
  };
  const firstImage = (selectors) => {
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (element && (element.currentSrc || element.src)) {
        return element.currentSrc || element.src;
      }
    }
    return '';
  };

  const prefix = `/${kind}/${novelId}/`;
  const seen = new Set();
  const chapters = [];
  const allLinks = Array.from(document.querySelectorAll('a[href]'));
  const episodeRowLinks = allLinks.filter(
    (link) => link.closest('li[data-ep]')
  );
  const candidateLinks = episodeRowLinks.length ? episodeRowLinks : allLinks;
  for (const link of candidateLinks) {
    let target;
    try { target = new URL(link.href, location.href); } catch (_) { continue; }
    if (target.origin !== location.origin || !target.pathname.startsWith(prefix)) {
      continue;
    }
    const remainder = target.pathname.slice(prefix.length).replace(/\/$/, '');
    if (!/^\d+$/.test(remainder) || seen.has(remainder)) continue;
    seen.add(remainder);
    const row = link.closest('li, tr, article, div');
    const titleElement = link.querySelector('.ne-title, .ep-title, [class*="title"]');
    const chapterName = text(titleElement) || text(link) || text(row) || '';
    if (/(?:1\s*화부터\s*보기|최신화부터)/.test(chapterName)) continue;
    chapters.push({
      url: target.href,
      episodeId: remainder,
      displayNumber: row && row.getAttribute('data-ep') || '',
      name: chapterName || `${chapters.length + 1}화`,
      number: chapters.length + 1,
    });
  }

  const tags = [];
  for (const element of document.querySelectorAll(
    '.hero-v2-tag, [class*="tag"] a, a[href*="tag"], a[href*="genre"]'
  )) {
    const value = text(element).replace(/^#/, '').trim();
    if (value && value.length <= 50 && !tags.includes(value)) tags.push(value);
  }
  const bodyText = document.body && document.body.innerText || '';
  return {
    currentUrl: location.href,
    title: firstText([
      '.novel-detail h1',
      '.novel-title',
      '.nd-title',
      '[data-novel-title]',
    ])
      || (document.querySelector('meta[property="og:title"]') || {}).content
      || firstText(['main h1', 'h1'])
      || document.title,
    author: firstText([
      '.nd-meta span:first-child',
      '[class*="author"]',
      '[rel="author"]',
    ]),
    introduction: firstText(['.nd-desc', '[class*="description"]', '[class*="intro"]']),
    introductionHtml: firstHtml(['.nd-desc', '[class*="description"]', '[class*="intro"]']),
    coverUrl: firstImage(['.nd-thumb img', '.novel-detail img', 'main img']),
    tags,
    chapters,
    bodyText: bodyText.slice(0, 1000),
    html: document.documentElement.outerHTML,
  };
}
                """,
                {'novelId': novel_id, 'kind': kind},
            ) or {}
        except Exception as e:
            self.log(f"ERROR: [NewToki] Headless DOM extraction failed: {e}")
            return None

        page_html = result.pop('html', '') or ''
        body_text = result.pop('bodyText', '') or ''
        if self._ntk_browser_html_is_blocked(page_html + '\n' + body_text):
            reason = re.sub(r'\s+', ' ', body_text).strip()[:240]
            self.log(
                "ERROR: [NewToki] Headless Chrome received an access-denied "
                f"page{': ' + reason if reason else '.'}"
            )
            return None

        initial_chapters = result.get('chapters') or []
        if self._ntk_browser_index_looks_partial(initial_chapters):
            expanded_chapters = self._ntk_collect_all_browser_chapters(index_url)
            if len(expanded_chapters) > len(initial_chapters):
                self.log(
                    f"[NewToki] Expanded chapter index from "
                    f"{len(initial_chapters)} to {len(expanded_chapters)} chapters."
                )
                result['chapters'] = expanded_chapters

        chapters = []
        seen = set()
        for item in result.get('chapters') or []:
            chapter_url = self._ntk_abs_url(index_url, item.get('url') or '')
            episode_id = self._ntk_episode_id_from_url(chapter_url)
            if (
                not episode_id
                or episode_id in seen
                or self._ntk_novel_id_from_url(chapter_url) != novel_id
            ):
                continue
            seen.add(episode_id)
            name = re.sub(
                r'\s+', ' ', html.unescape(item.get('name') or '')
            ).strip()
            source_number = self._ntk_display_chapter_number(item)
            chapters.append({
                'url': chapter_url,
                'name': name,
                'fullName': name,
                'number': 0,
                'episodeId': episode_id,
                'kind': kind,
                'isVIP': False,
                'isPaid': False,
                '_sourceNumber': source_number,
            })

        # Prefer data-ep ordering when every row exposes it. This merges
        # paginated/virtualized sbxh rows reliably. Older pages without that
        # field still render newest-first, so reverse as a fallback.
        source_numbers = [chapter['_sourceNumber'] for chapter in chapters]
        if (
            source_numbers
            and all(number is not None for number in source_numbers)
            and len(set(source_numbers)) == len(source_numbers)
        ):
            chapters.sort(key=lambda chapter: chapter['_sourceNumber'])
        else:
            chapters.reverse()
        for chapter_number, chapter in enumerate(chapters, start=1):
            chapter.pop('_sourceNumber', None)
            chapter['number'] = chapter_number
            if not chapter['name']:
                chapter['name'] = f'{chapter_number}\ud654'
                chapter['fullName'] = chapter['name']

        fallback = self._ntk_parse_index_html(page_html, index_url)
        title = re.split(
            r'\s+[-|]\s+', result.get('title') or fallback.get('bookname') or ''
        )[0].strip()
        author = self._ntk_clean_tag(result.get('author') or '')
        introduction = re.sub(
            r'\s+', ' ', result.get('introduction') or ''
        ).strip()
        cover_url = self._ntk_abs_url(
            index_url, result.get('coverUrl') or fallback.get('coverUrl') or ''
        )
        tags = [
            value for value in (
                self._ntk_clean_tag(tag) for tag in (result.get('tags') or [])
            ) if value
        ]
        return {
            'bookUrl': index_url,
            'bookname': title or f'Novel_{novel_id}',
            'author': author or fallback.get('author') or '',
            'coverUrl': cover_url,
            'introduction': introduction or fallback.get('introduction') or '',
            'introductionHTML': (
                result.get('introductionHtml')
                or fallback.get('introductionHTML')
                or ''
            ),
            'language': 'ko',
            'tags': tags or fallback.get('tags') or [],
            'chapterCount': len(chapters),
            'chapters': chapters,
            '_ntk_kind': kind,
            '_ntk_browser_url': result.get('currentUrl') or '',
        }

    def _ntk_browser_fetch_html(self, url):
        """Fetch HTML in the live Chrome network context (including its proxy)."""
        if not self._page or not url:
            return None
        try:
            result = self._page.evaluate(
                r"""
async ({ url }) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const target = new URL(url, location.href);
    target.searchParams.set('cb', String(Date.now()));
    const response = await fetch(target.href, {
      credentials: 'include',
      cache: 'no-store',
      signal: controller.signal,
    });
    return {
      ok: response.ok,
      status: response.status,
      text: await response.text(),
    };
  } catch (error) {
    return { ok: false, status: 0, error: String(error) };
  } finally {
    clearTimeout(timer);
  }
}
                """,
                {'url': url},
            ) or {}
        except Exception as e:
            self.log(f"  [NewToki] Chrome HTML fetch failed: {e}")
            return None
        if not result.get('ok'):
            self.log(
                f"  [NewToki] Chrome HTML fetch returned HTTP "
                f"{result.get('status', 0)}: {result.get('error', '')}"
            )
            return None
        html_text = result.get('text') or ''
        if self._ntk_browser_html_is_blocked(html_text):
            return None
        return html_text

    def _ntk_build_decrypted_chapter(self, title, plaintext, selector):
        """Build a chapter from either plain text or the site's JSON envelope."""
        value = (plaintext or '').strip()
        payload = None
        if value.startswith('{'):
            try:
                payload = json.loads(value)
            except (TypeError, ValueError):
                payload = None

        if isinstance(payload, dict):
            if payload.get('kind') == 'html' and isinstance(payload.get('html'), str):
                content_html = payload['html'].strip()
                content_html = re.sub(
                    r'<(?:script|style|iframe)\b[^>]*>.*?</(?:script|style|iframe)>',
                    '',
                    content_html,
                    flags=re.I | re.S,
                )
                text_value = re.sub(
                    r'<\s*(?:br\s*/?|/p|/div|/li)\s*>',
                    '\n',
                    content_html,
                    flags=re.I,
                )
                text_value = self._ntk_clean_plaintext(
                    html.unescape(re.sub(r'<[^>]+>', '', text_value))
                )
                if content_html and text_value:
                    return {
                        'chapterName': title or 'Chapter',
                        'sourceChapterName': title or 'Chapter',
                        'contentText': text_value,
                        'contentHtml': content_html,
                        '_debugSelector': selector,
                    }
            kind = payload.get('kind')
            if (
                kind == 'text-shuffled'
                and isinstance(payload.get('paragraphs'), list)
                and isinstance(payload.get('perm'), list)
            ):
                paragraphs = payload['paragraphs']
                permutation = payload['perm']
                ordered = [''] * len(paragraphs)
                for source_index, paragraph in enumerate(paragraphs):
                    if source_index >= len(permutation):
                        continue
                    try:
                        destination_index = int(permutation[source_index])
                    except (TypeError, ValueError):
                        continue
                    if (
                        0 <= destination_index < len(ordered)
                        and isinstance(paragraph, str)
                    ):
                        ordered[destination_index] = paragraph
                value = '\n\n'.join(part for part in ordered if part.strip())
            elif kind == 'text' and isinstance(payload.get('paragraphs'), list):
                value = '\n\n'.join(
                    paragraph for paragraph in payload['paragraphs']
                    if isinstance(paragraph, str) and paragraph.strip()
                )
            elif isinstance(payload.get('text'), str):
                value = payload['text']

        return self._ntk_build_text_chapter(title, value, selector)

    def _ntk_prepare_chapter_page_browser(self, chapter_url, page=None):
        """Enter the chapter so NewToki can establish its page-bound ad ack."""
        page = page or self._page
        if not page or not hasattr(page, 'goto'):
            return True
        try:
            target = urllib.parse.urlparse(chapter_url)
            current = urllib.parse.urlparse(page.url or '')
        except Exception:
            return True
        if current.netloc == target.netloc and current.path == target.path:
            return True

        # NewToki validates /api/ad/challenge against the current document's
        # chapter path. Let its own frontend complete that handshake, but abort
        # the frontend's content request so the scraper performs it only once.
        content_pattern = "**/api/novel-content"
        content_attempted = []

        def hold_content(route):
            content_attempted.append(True)
            try:
                route.abort()
            except Exception:
                pass

        try:
            page.route(content_pattern, hold_content)
            response = page.goto(
                chapter_url,
                wait_until='domcontentloaded',
                timeout=45000,
            )
            if response and response.status >= 400:
                self.log(
                    f"  [NewToki] Chapter navigation returned HTTP "
                    f"{response.status}."
                )
                return False
            for _ in range(50):
                if content_attempted:
                    break
                page.wait_for_timeout(100)
        except Exception as e:
            self.log(f"  [NewToki] Chapter handshake navigation failed: {e}")
            return False
        finally:
            try:
                page.unroute(content_pattern, hold_content)
            except Exception:
                pass
        return bool(content_attempted)

    def _ntk_parallel_pages(self, count):
        """Return one shared-session Chrome page per NewToki worker."""
        count = max(1, count)
        if not self._context or not self._page_is_usable(self._page):
            return []

        usable_workers = []
        for page in self._worker_pages:
            if self._page_is_usable(page):
                usable_workers.append(page)
            else:
                try:
                    page.close()
                except Exception:
                    pass
        self._worker_pages = usable_workers

        needed_workers = max(0, count - 1)
        if len(self._worker_pages) > needed_workers:
            for page in self._worker_pages[needed_workers:]:
                try:
                    page.close()
                except Exception:
                    pass
            self._worker_pages = self._worker_pages[:needed_workers]

        while len(self._worker_pages) < needed_workers:
            try:
                page = self._context.new_page()
                page.on("console", self._on_console)
                self._worker_pages.append(page)
            except Exception as e:
                self.log(f"  [NewToki] Worker page failed: {e}")
                break
        return ([self._page] + self._worker_pages)[:count]

    @staticmethod
    def _ntk_content_response_matches(url):
        try:
            return urllib.parse.urlparse(url or '').path == '/api/novel-content'
        except Exception:
            return False

    def _ntk_decrypt_payload_browser(self, page, chapter_url, payload):
        """Decrypt a v3 content payload using that worker page's nv cookie."""
        try:
            return page.evaluate(
                r"""
async ({ chapterUrl, payload }) => {
  const target = new URL(chapterUrl, location.href);
  const ids = target.pathname.match(/^\/(?:novel|webtoon)\/(\d+)\/(\d+)/);
  if (!ids) return { ok: false, error: 'url' };
  const html = document.documentElement && document.documentElement.innerHTML || '';
  const cookieMatch = html.match(/\\"cookieName\\":\\"([^\\"]+)\\"/)
    || html.match(/"cookieName"\s*:\s*"([^"]+)"/);
  const cookieName = cookieMatch ? cookieMatch[1] : 'nv';
  let nv = '';
  for (const part of document.cookie.split(';')) {
    const item = part.trim();
    const split = item.indexOf('=');
    if (split < 0 || item.slice(0, split) !== cookieName) continue;
    nv = item.slice(split + 1);
    try { nv = decodeURIComponent(nv); } catch (_) {}
    break;
  }
  if (!nv) return { ok: false, error: 'cookie' };
  const fromB64url = (value) => {
    let normalized = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
    while (normalized.length % 4) normalized += '=';
    const binary = atob(normalized);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  };
  const encoder = new TextEncoder();
  const cookieKey = fromB64url(nv.split('.')[0] || '');
  const encrypted = fromB64url(payload);
  if (!cookieKey.length || encrypted.length < 29) {
    return { ok: false, error: 'payload' };
  }
  const suffix = encoder.encode(`:${ids[1]}:${ids[2]}:v3`);
  const material = new Uint8Array(cookieKey.length + suffix.length);
  material.set(cookieKey, 0);
  material.set(suffix, cookieKey.length);
  const aesKeyBytes = await crypto.subtle.digest('SHA-256', material);
  const aesKey = await crypto.subtle.importKey(
    'raw', aesKeyBytes, { name: 'AES-GCM' }, false, ['decrypt']
  );
  const plaintext = await crypto.subtle.decrypt(
    {
      name: 'AES-GCM',
      iv: encrypted.slice(0, 12),
      tagLength: 128,
    },
    aesKey,
    encrypted.slice(12),
  );
  return {
    ok: true,
    plaintext: new TextDecoder('utf-8').decode(plaintext),
  };
}
                """,
                {'chapterUrl': chapter_url, 'payload': payload},
            ) or {}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def _ntk_fetch_chapter_batch_browser(
        self, batch_info, interval=0.5, success_callback=None,
        interval_max=None,
    ):
        """Fetch a batch and report each worker as soon as it completes."""
        if not batch_info:
            return []
        pages = self._ntk_parallel_pages(len(batch_info))
        if len(pages) < len(batch_info):
            return [None] * len(batch_info)

        responses = [None] * len(batch_info)
        results = [None] * len(batch_info)
        finished = set()
        handlers = []
        for index, page in enumerate(pages):
            def capture(response, worker_index=index):
                if self._ntk_content_response_matches(response.url):
                    responses[worker_index] = response

            handlers.append(capture)
            page.on('response', capture)

        def finish_response(index):
            page = pages[index]
            chapter = batch_info[index]
            response = responses[index]
            try:
                content = response.json()
            except Exception as e:
                self.log(f"  [NewToki] Invalid content response: {e}")
                return
            if (
                response.status != 200
                or not isinstance(content, dict)
                or not content.get('ok')
                or not content.get('payload')
            ):
                error = content.get('error', '') if isinstance(content, dict) else ''
                self.log(
                    f"  [NewToki] Chrome chapter fetch failed at content "
                    f"(HTTP {response.status})"
                    + (f": {error}" if error else '')
                )
                return
            decrypted = self._ntk_decrypt_payload_browser(
                page,
                chapter.get('url', ''),
                content.get('payload', ''),
            )
            if not decrypted.get('ok'):
                self.log(
                    f"  [NewToki] Chrome chapter decrypt failed: "
                    f"{decrypted.get('error', 'unknown')}"
                )
                return
            name = chapter.get('fullName', '') or chapter.get('name', '')
            data = self._ntk_build_decrypted_chapter(
                name,
                decrypted.get('plaintext', ''),
                'ntk parallel browser api/novel-content',
            )
            if data and len(data.get('contentText', '')) >= 40:
                results[index] = data
                if success_callback is not None:
                    success_callback(index, data)
            finished.add(index)

        try:
            for index, (page, chapter) in enumerate(zip(pages, batch_info)):
                if self._stop_requested:
                    break
                try:
                    goto_options = {
                        'wait_until': 'commit',
                        'timeout': 45000,
                    }
                    if self._book_url:
                        goto_options['referer'] = self._book_url
                    page.goto(chapter.get('url', ''), **goto_options)
                except Exception as e:
                    self.log(
                        f"  [NewToki] Worker navigation failed for "
                        f"{chapter.get('name', 'chapter')}: {e}"
                    )
                if index < len(batch_info) - 1:
                    delay = self._random_interval_delay(
                        interval, interval_max
                    )
                else:
                    delay = 0.0
                if delay > 0:
                    try:
                        page.wait_for_timeout(round(delay * 1000))
                    except Exception:
                        time.sleep(delay)
                for completed_index, response in enumerate(responses):
                    if response is not None and completed_index not in finished:
                        finish_response(completed_index)

            pending = set(range(len(batch_info))) - finished
            deadline = time.time() + 45
            while pending and time.time() < deadline and not self._stop_requested:
                for index in list(pending):
                    if responses[index] is None:
                        try:
                            pages[index].wait_for_timeout(25)
                        except Exception:
                            pass
                        continue
                    finish_response(index)
                    pending.remove(index)

            # Process responses that arrived at the deadline boundary.
            for index in list(pending):
                if responses[index] is not None:
                    finish_response(index)
                    pending.remove(index)
            for index in sorted(pending):
                self.log(
                    f"  [NewToki] Timed out waiting for chapter content: "
                    f"{batch_info[index].get('name', 'chapter')}"
                )
            return results
        finally:
            for page, handler in zip(pages, handlers):
                try:
                    page.remove_listener('response', handler)
                except Exception:
                    pass

    def _ntk_fetch_chapter_browser(self, chapter_url, chapter_name):
        """Fetch and decrypt the chapter using NewToki's browser-only API."""
        if not self._page:
            self.log("  [NewToki] Live Chrome page is not available.")
            return None
        if not self._ntk_prepare_chapter_page_browser(chapter_url):
            self.log(
                "  [NewToki] Chapter page did not complete its acknowledgement "
                "handshake."
            )
            return None
        try:
            result = self._page.evaluate(
                r"""
async ({ chapterUrl }) => {
  const fetchWithTimeout = async (url, options = {}) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  };
  const cookieValue = (name) => {
    for (const part of document.cookie.split(';')) {
      const item = part.trim();
      const split = item.indexOf('=');
      if (split < 0 || item.slice(0, split) !== name) continue;
      const value = item.slice(split + 1);
      try { return decodeURIComponent(value); } catch (_) { return value; }
    }
    return '';
  };
  const deleteCookie = (name) => {
    const secure = location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = `${name}=; Path=/; Max-Age=0; SameSite=Lax${secure}`;
  };
  const b64url = (bytes) => {
    let binary = '';
    for (const value of bytes) binary += String.fromCharCode(value);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };
  const fromB64url = (value) => {
    let normalized = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
    while (normalized.length % 4) normalized += '=';
    const binary = atob(normalized);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  };
  const randomHex = (size) => Array.from(
    crypto.getRandomValues(new Uint8Array(size)),
    (value) => value.toString(16).padStart(2, '0'),
  ).join('');
  const jsonPost = async (url, body, extraHeaders = {}) => {
    const response = await fetchWithTimeout(url, {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
      headers: {
        'content-type': 'application/json',
        ...extraHeaders,
      },
      body: JSON.stringify(body),
    });
    return {
      response,
      data: await response.json().catch(() => null),
    };
  };
  const normalizeChallenge = (data, fallbackScope) => {
    if (!data || typeof data !== 'object') return null;
    const nestedData = data.data && typeof data.data === 'object'
      ? data.data
      : null;
    const source = data.challenge
      || (nestedData && nestedData.challenge)
      || nestedData
      || data;
    if (!source || typeof source !== 'object') return null;
    const token = source.token || source.challengeToken || '';
    if (!token) return null;
    const slotNonces = Array.isArray(source.slotNonces)
      ? source.slotNonces
      : (Array.isArray(source.nonces) ? source.nonces : []);
    return {
      token,
      slotNonces,
      scope: source.scope || data.scope || fallbackScope,
    };
  };

  try {
    const target = new URL(chapterUrl, location.href);
    const ids = target.pathname.match(/^\/(?:novel|webtoon)\/(\d+)\/(\d+)/);
    if (!ids) return { ok: false, stage: 'url', status: 0 };
    const contentScope = target.pathname.split('/')[1] || 'novel';
    const encoder = new TextEncoder();
    const extractTokens = (pageHtml) => {
      const pattern = /(?:\\"|")token(?:\\"|")\s*:\s*(?:\\"|")([A-Za-z0-9_=.-]+)(?:\\"|")/g;
      return Array.from(pageHtml.matchAll(pattern), (match) => match[1]);
    };

    for (const badCookie of ['ntk_blk', 'ntk_dev_warn']) {
      if (cookieValue(badCookie)) deleteCookie(badCookie);
    }

    if (!window.__npiaNtkEventSynced) {
      await fetchWithTimeout(new URL('/api/me', target.origin).href, {
        credentials: 'include',
        cache: 'no-store',
      }).catch(() => null);
      let eventId = cookieValue('__ntk_ev_id');
      if (!eventId) {
        eventId = randomHex(32);
        const secure = location.protocol === 'https:' ? '; Secure' : '';
        document.cookie = `__ntk_ev_id=${eventId}; Path=/; SameSite=Lax${secure}`;
      }
      const sync = await jsonPost(
        new URL('/api/ev/sync', target.origin).href,
        { evId: eventId },
      ).catch(() => null);
      if (sync && sync.response.ok) window.__npiaNtkEventSynced = true;
    }

    let forceNvReissue = false;
    let lastRetryFailure = { ok: false, stage: 'retry', status: 0 };
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      target.searchParams.set('cb', String(Date.now()));
      const chapterResponse = await fetchWithTimeout(target.href, {
        credentials: 'include',
        cache: 'no-store',
      });
      const chapterHtml = await chapterResponse.text();
      if (!chapterResponse.ok) {
        return { ok: false, stage: 'chapter', status: chapterResponse.status };
      }

      const tokens = extractTokens(chapterHtml);
      let token = tokens.find((value) => (
        value.startsWith('eyJuIjoi') || value.startsWith('eyJlIjoi')
      ));
      if (!token) return { ok: false, stage: 'token', status: 0 };

      const cookieMatch = chapterHtml.match(/\\"cookieName\\":\\"([^\\"]+)\\"/)
        || chapterHtml.match(/"cookieName"\s*:\s*"([^"]+)"/);
      let cookieName = cookieMatch ? cookieMatch[1] : 'nv';
      const chapterPath = target.pathname;
      const handshake = [];

      const explicitChallengeMatch = chapterHtml.match(
        /(?:\\"|")challengeToken(?:\\"|")\s*:\s*(?:\\"|")([^"\\]+)(?:\\"|")/,
      );
      const challengeToken = explicitChallengeMatch
        ? explicitChallengeMatch[1]
        : tokens.find((value) => value !== token && value.startsWith('eyJ'));
      const scopePattern = /(?:\\"|")(?:adScope|challengeScope|scope)(?:\\"|")\s*:\s*(?:\\"|")([^"\\]+)(?:\\"|")/g;
      const embeddedScopes = Array.from(
        chapterHtml.matchAll(scopePattern),
        (match) => match[1],
      ).filter((value, index, values) => (
        value && value.length <= 500 && values.indexOf(value) === index
      ));
      const slotMatch = chapterHtml.match(
        /(?:\\"|")slotNonces(?:\\"|")\s*:\s*\[(.*?)\]/,
      );
      const slotNonces = slotMatch
        ? Array.from(
          slotMatch[1].matchAll(/(?:\\"|")([^"\\]+)(?:\\"|")/g),
          (match) => match[1],
        )
        : [];
      let challenge = challengeToken
        ? {
          token: challengeToken,
          slotNonces,
          scope: embeddedScopes[0] || contentScope,
        }
        : null;
      if (!challenge) {
        const challengeUrl = new URL('/api/ad/challenge', target.origin).href;
        const scopeCandidates = [
          ...embeddedScopes,
          'chapter',
          `${contentScope}-content`,
          contentScope,
          chapterPath,
        ].filter((value, index, values) => values.indexOf(value) === index);
        const challengePayloads = [{ path: chapterPath, force: false }];
        challengePayloads.push(...scopeCandidates.map((scope) => ({
          scope,
          path: chapterPath,
        })));
        challengePayloads.push({ path: chapterPath });
        for (const challengePayload of challengePayloads) {
          const challengeReply = await jsonPost(
            challengeUrl,
            challengePayload,
          );
          const challengeData = challengeReply.data;
          const challengeError = challengeData && challengeData.error
            ? String(challengeData.error)
            : '';
          handshake.push(
            `challenge=${challengeReply.response.status}`
            + (challengeError ? `/${challengeError}` : ''),
          );
          if (challengeReply.response.ok && challengeData) {
            challenge = normalizeChallenge(
              challengeData,
              challengePayload.scope || contentScope,
            );
            if (challenge) break;
          }
          if (challengeError !== 'bad_scope') break;
        }
      } else {
        handshake.push('challenge=embedded');
      }

      if (challenge && challenge.token) {
        const canary = await jsonPost(
          new URL('/api/ad/canary', target.origin).href,
          {
            adAckCanary: true,
            challengeToken: challenge.token,
            scope: challenge.scope || contentScope,
            path: chapterPath,
          },
        );
        const canaryError = canary.data && canary.data.error
          ? String(canary.data.error)
          : '';
        handshake.push(
          `canary=${canary.response.status}`
          + (canaryError ? `/${canaryError}` : ''),
        );
        let totalAds = Array.from(
          chapterHtml.matchAll(/data-br-n=(?:\\"|")?(\d+)(?:\\"|")?/g),
          (match) => Number.parseInt(match[1], 10) || 0,
        ).reduce((total, value) => total + value, 0);
        const nonces = Array.isArray(challenge.slotNonces)
          ? challenge.slotNonces
          : [];
        if (!totalAds && nonces.length) totalAds = nonces.length;
        const visibleNonces = totalAds > 0 ? nonces.slice(0, totalAds) : nonces;
        const ack = await jsonPost(
          new URL('/api/ad/ack', target.origin).href,
          {
            challengeToken: challenge.token,
            scope: challenge.scope || contentScope,
            total: totalAds,
            visible: totalAds,
            path: chapterPath,
            slotNonces: visibleNonces,
          },
        );
        if (!ack.response.ok || !ack.data || !ack.data.ok) {
          return {
            ok: false,
            stage: 'ad-ack',
            status: ack.response.status,
            error: ack.data && ack.data.error ? String(ack.data.error) : '',
          };
        }
        handshake.push(`ack=${ack.response.status}`);

        // The content token is scoped to the acknowledgement state. Fetch a
        // fresh chapter document after a successful acknowledgement instead
        // of reusing the token issued before it.
        target.searchParams.set('cb', String(Date.now() + 1));
        const refreshedResponse = await fetchWithTimeout(target.href, {
          credentials: 'include',
          cache: 'no-store',
        });
        const refreshedHtml = await refreshedResponse.text();
        if (!refreshedResponse.ok) {
          return {
            ok: false,
            stage: 'post-ack-chapter',
            status: refreshedResponse.status,
          };
        }
        const refreshedToken = extractTokens(refreshedHtml).find((value) => (
          value.startsWith('eyJuIjoi') || value.startsWith('eyJlIjoi')
        ));
        if (!refreshedToken) {
          return { ok: false, stage: 'post-ack-token', status: 0 };
        }
        token = refreshedToken;
        const refreshedCookieMatch = refreshedHtml.match(
          /\\"cookieName\\":\\"([^\\"]+)\\"/,
        ) || refreshedHtml.match(/"cookieName"\s*:\s*"([^"]+)"/);
        if (refreshedCookieMatch) cookieName = refreshedCookieMatch[1];
        handshake.push('refresh=token');
      }

      let nv = cookieValue(cookieName);
      if (!nv || forceNvReissue) {
        if (forceNvReissue) deleteCookie(cookieName);
        await fetchWithTimeout(new URL('/api/nv-issue', target.origin).href, {
          method: 'POST',
          credentials: 'include',
          cache: 'no-store',
        });
        nv = cookieValue(cookieName);
        forceNvReissue = false;
      }
      if (!nv) return { ok: false, stage: 'cookie', status: 0 };

      const nonce = b64url(crypto.getRandomValues(new Uint8Array(24)));
      const hmacKey = await crypto.subtle.importKey(
        'raw',
        encoder.encode(nv),
        { name: 'HMAC', hash: 'SHA-256' },
        false,
        ['sign'],
      );
      const signature = await crypto.subtle.sign(
        'HMAC',
        hmacKey,
        encoder.encode(`${token}.${nonce}.${navigator.userAgent}`),
      );
      const proof = b64url(new Uint8Array(signature));
      const contentReply = await jsonPost(
        new URL('/api/novel-content', target.origin).href,
        {
          novelId: ids[1],
          episodeId: ids[2],
          token,
          nonce,
          proof,
        },
        { 'x-novel-client': 'shadow-v3' },
      );
      const content = contentReply.data;
      if (
        !contentReply.response.ok
        || !content
        || !content.ok
        || !content.payload
      ) {
        const error = content && content.error ? String(content.error) : '';
        if (error === 'expired') {
          lastRetryFailure = {
            ok: false,
            stage: 'content',
            status: contentReply.response.status,
            error: `${error} (${handshake.join(', ')})`,
          };
          forceNvReissue = true;
          continue;
        }
        if (error === 'ad_ack_required') {
          lastRetryFailure = {
            ok: false,
            stage: 'content',
            status: contentReply.response.status,
            error: `${error} (${handshake.join(', ')})`,
          };
          continue;
        }
        return {
          ok: false,
          stage: 'content',
          status: contentReply.response.status,
          error,
        };
      }

      const cookieKey = fromB64url(nv.split('.')[0] || '');
      const encrypted = fromB64url(content.payload);
      if (!cookieKey.length || encrypted.length < 29) {
        return { ok: false, stage: 'decrypt', status: 0 };
      }
      const suffix = encoder.encode(`:${ids[1]}:${ids[2]}:v3`);
      const material = new Uint8Array(cookieKey.length + suffix.length);
      material.set(cookieKey, 0);
      material.set(suffix, cookieKey.length);
      const aesKeyBytes = await crypto.subtle.digest('SHA-256', material);
      const aesKey = await crypto.subtle.importKey(
        'raw',
        aesKeyBytes,
        { name: 'AES-GCM' },
        false,
        ['decrypt'],
      );
      const plaintext = await crypto.subtle.decrypt(
        {
          name: 'AES-GCM',
          iv: encrypted.slice(0, 12),
          tagLength: 128,
        },
        aesKey,
        encrypted.slice(12),
      );
      return {
        ok: true,
        plaintext: new TextDecoder('utf-8').decode(plaintext),
      };
    }
    return lastRetryFailure;
  } catch (error) {
    return { ok: false, stage: 'exception', status: 0, error: String(error) };
  }
}
                """,
                {'chapterUrl': chapter_url},
            ) or {}
        except Exception as e:
            self.log(f"  [NewToki] Chrome chapter fetch failed: {e}")
            return None

        if not result.get('ok'):
            self.log(
                f"  [NewToki] Chrome chapter fetch failed at "
                f"{result.get('stage', 'unknown')} "
                f"(HTTP {result.get('status', 0)})"
                + (f": {result.get('error')}" if result.get('error') else '')
            )
            return None
        data = self._ntk_build_decrypted_chapter(
            chapter_name,
            result.get('plaintext') or '',
            'ntk browser api/novel-content',
        )
        if data and len(data.get('contentText', '')) >= 40:
            return data
        self.log("  [NewToki] Chrome returned empty/short chapter content.")
        return None

    def _ntk_fetch_webtoon_chapter_browser(self, chapter_url, chapter_name):
        html_text = self._ntk_browser_fetch_html(chapter_url)
        if not html_text:
            return None
        image_urls = self._ntk_image_url_candidates(html_text, chapter_url)
        return self._ntk_build_image_chapter(
            chapter_name,
            image_urls,
            'ntk browser webtoon image scrape',
        )

    def _ntk_fetch_binary_browser(self, url):
        if not self._page or not url:
            return None
        try:
            result = self._page.evaluate(
                r"""
async ({ url }) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const target = new URL(url, location.href);
    const sameOrigin = target.origin === location.origin;
    const response = await fetch(target.href, {
      // CDN covers commonly reply with Access-Control-Allow-Origin: *.
      // Browsers reject that response when cookies are included, so only
      // send the NewToki session to same-origin assets.
      credentials: sameOrigin ? 'include' : 'omit',
      cache: 'no-store',
      signal: controller.signal,
    });
    if (!response.ok) return { ok: false, status: response.status };
    const bytes = new Uint8Array(await response.arrayBuffer());
    let binary = '';
    const chunkSize = 0x8000;
    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
    }
    return {
      ok: true,
      status: response.status,
      contentType: response.headers.get('content-type') || '',
      data: btoa(binary),
    };
  } catch (error) {
    return { ok: false, status: 0, error: String(error) };
  } finally {
    clearTimeout(timer);
  }
}
                """,
                {'url': url},
            ) or {}
            if result.get('ok') and result.get('data'):
                return base64.b64decode(result['data'])
            self.log(
                f"  [NewToki] Chrome asset fetch failed "
                f"(HTTP {result.get('status', 0)})."
            )
        except Exception as e:
            self.log(f"  [NewToki] Chrome asset fetch failed: {e}")
        return None

    def _ntk_fetch_chapter_api(self, chapter_url, chapter_name, state=None):
        state = state or self._ntk_api_state
        if not state:
            state = self._ntk_prepare_api_state(self._book_url or chapter_url)
        if not state or not state.get('session'):
            self.log("  [NewToki] API session is not ready.")
            return None

        session = state['session']
        novel_id = state.get('novel_id') or self._ntk_novel_id_from_url(chapter_url)
        episode_id = self._ntk_episode_id_from_url(chapter_url)
        if not novel_id or not episode_id:
            self.log(f"  [NewToki] Invalid chapter URL: {chapter_url}")
            return None

        for attempt in range(1, 7):
            if self._stop_requested:
                return None
            try:
                doc_headers = dict(state['doc_headers'])
                doc_headers['Referer'] = state.get('index_url') or self._book_url or state['origin']
                cache_bust = int(time.time() * 1000)
                chapter_page_url = f"{chapter_url}?cb={cache_bust}"
                chapter_res = self._ntk_request_get(session, chapter_page_url, doc_headers)
                if (
                    chapter_res.status_code != 200
                    or 'Just a moment' in chapter_res.text
                    or 'cf-browser-verification' in chapter_res.text
                ):
                    self.log(
                        f"  [NewToki] Chapter HTML blocked/failed "
                        f"(attempt {attempt}/6, HTTP {chapter_res.status_code})."
                    )
                    time.sleep(0.5)
                    continue

                if '\ubcf8\ubb38\uc774 \uc544\uc9c1 \uc900\ube44\ub418\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4' in chapter_res.text:
                    self.log(f"  [NewToki] Chapter not ready: {chapter_name}")
                    return None

                token_match = re.search(r'\\"token\\":\\"([^\\"]+)\\"', chapter_res.text)
                if not token_match:
                    token_match = re.search(r'"token"\s*:\s*"([^"]+)"', chapter_res.text)
                if not token_match:
                    self.log(f"  [NewToki] No content token found (attempt {attempt}/6).")
                    time.sleep(0.4)
                    continue
                token = token_match.group(1)

                nv_cookie = session.cookies.get('nv')
                if not nv_cookie:
                    self._ntk_issue_nv(state, chapter_url)
                    nv_cookie = session.cookies.get('nv')
                if not nv_cookie:
                    self.log(f"  [NewToki] nv cookie missing (attempt {attempt}/6).")
                    time.sleep(0.4)
                    continue

                nonce = self._ntk_b64url_encode(os.urandom(24))
                message = f"{token}.{nonce}.{state['user_agent']}".encode('utf-8')
                proof = self._ntk_b64url_encode(
                    hmac.new(nv_cookie.encode('utf-8'), message, hashlib.sha256).digest()
                )
                payload = {
                    'novelId': novel_id,
                    'episodeId': episode_id,
                    'token': token,
                    'nonce': nonce,
                    'proof': proof,
                }
                api_headers = dict(state['api_headers'])
                api_headers['Referer'] = chapter_url
                content_res = self._ntk_request_post_json(
                    session,
                    state['origin'] + '/api/novel-content',
                    payload,
                    api_headers,
                )
                try:
                    content_json = content_res.json()
                except Exception:
                    self.log(f"  [NewToki] Content API returned non-JSON (attempt {attempt}/6).")
                    time.sleep(0.4)
                    continue

                if not content_json.get('ok'):
                    error = content_json.get('error') or 'unknown'
                    if error == 'expired':
                        self._ntk_issue_nv(state, chapter_url)
                    elif error == 'blocked':
                        self.log("  [NewToki] Content API reported blocked.")
                        return None
                    else:
                        self.log(f"  [NewToki] Content API error: {error}")
                    time.sleep(0.5)
                    continue

                encrypted_payload = content_json.get('payload') or ''
                key_text = nv_cookie.split('.')[0]
                key = self._ntk_b64url_decode(key_text)
                encrypted = self._ntk_b64url_decode(encrypted_payload)
                decrypted = bytearray(len(encrypted))
                for i, value in enumerate(encrypted):
                    decrypted[i] = value ^ key[i % len(key)]
                plaintext = decrypted.decode('utf-8')
                data = self._ntk_build_text_chapter(
                    chapter_name,
                    plaintext,
                    'ntk api/novel-content',
                )
                if data and len(data.get('contentText', '')) >= 40:
                    return data
                self.log(f"  [NewToki] Decrypted content was empty/short (attempt {attempt}/6).")
            except Exception as e:
                self.log(f"  [NewToki] API chapter attempt {attempt}/6 failed: {e}")
            time.sleep(0.5)
        return None

    def _ntk_image_url_candidates(self, html_text, base_url):
        urls = []

        def add(value):
            value = self._ntk_abs_url(base_url, value)
            if not value or self._ntk_is_site_image(value):
                return
            lower = value.lower()
            if not re.search(r'\.(?:jpg|jpeg|png|webp|gif|avif)(?:[?#]|$)', lower):
                return
            if any(skip in lower for skip in ('favicon', 'avatar', 'profile', 'emoji')):
                return
            if value not in urls:
                urls.append(value)

        for tag in re.findall(r'<img\b[^>]*>', html_text or '', re.I | re.S):
            attrs = self._ntk_html_attrs(tag)
            for key in ('data-src', 'data-original', 'data-lazy-src', 'srcset', 'src'):
                if attrs.get(key):
                    add(attrs[key])
                    break

        for match in re.finditer(
            r'https?:\\?/\\?/[^"\'\s<>]+(?:toonflix|ntk|newtoki|imagebox)[^"\'\s<>]+',
            html_text or '',
            re.I,
        ):
            add(match.group(0))

        for match in re.finditer(
            r'["\']((?:/[^"\'\s<>]+)?/(?:data|upload|uploads|webtoon|toon|image|images|file)/[^"\'\s<>]+\.(?:jpg|jpeg|png|webp|gif|avif)(?:\?[^"\'\s<>]*)?)["\']',
            html_text or '',
            re.I,
        ):
            add(match.group(1))

        page_images = [
            url for url in urls
            if not re.search(r'(?:novel|webtoon)_thumb|og-default', url, re.I)
        ]
        return page_images or urls

    def _ntk_build_image_chapter(self, title, image_urls, selector):
        if not image_urls:
            return None
        images = []
        html_parts = []
        for idx, img_url in enumerate(image_urls, 1):
            parsed = urllib.parse.urlparse(img_url)
            name = os.path.basename(parsed.path) or f'page_{idx:04d}.jpg'
            name = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('._')
            if '.' not in name:
                name += '.jpg'
            name = f'ntk_webtoon_{idx:04d}_{name}'
            images.append({'url': img_url, 'name': name})
            html_parts.append(
                f'<div class="ntk-webtoon-page">'
                f'<img src="{html.escape(img_url, quote=True)}" alt="page {idx}" />'
                f'</div>'
            )
        return {
            'chapterName': title or 'Chapter',
            'sourceChapterName': title or 'Chapter',
            'contentText': '',
            'contentHtml': '\n'.join(html_parts),
            'images': images,
            'contentCss': (
                '.ntk-webtoon-page { margin: 0; padding: 0; text-align: center; } '
                '.ntk-webtoon-page img { display: block; width: 100%; '
                'max-width: 100%; height: auto; margin: 0 auto; }'
            ),
            '_debugSelector': selector,
        }

    def _ntk_fetch_webtoon_chapter(self, chapter_url, chapter_name, state=None):
        state = state or self._ntk_api_state
        if not state:
            state = self._ntk_prepare_api_state(self._book_url or chapter_url)
        if not state or not state.get('session'):
            self.log("  [NewToki] API session is not ready.")
            return None
        session = state['session']
        headers = dict(state['doc_headers'])
        headers['Referer'] = state.get('index_url') or self._book_url or state['origin']
        for attempt in range(1, 5):
            try:
                url = f"{chapter_url}?cb={int(time.time() * 1000)}"
                response = self._ntk_request_get(session, url, headers)
                if (
                    response.status_code != 200
                    or 'Just a moment' in response.text
                    or 'cf-browser-verification' in response.text
                ):
                    self.log(
                        f"  [NewToki] Webtoon HTML blocked/failed "
                        f"(attempt {attempt}/4, HTTP {response.status_code})."
                    )
                    time.sleep(0.5)
                    continue
                image_urls = self._ntk_image_url_candidates(
                    response.text,
                    chapter_url,
                )
                data = self._ntk_build_image_chapter(
                    chapter_name,
                    image_urls,
                    'ntk webtoon image scrape',
                )
                if data:
                    return data
                self.log(
                    f"  [NewToki] No webtoon images found "
                    f"(attempt {attempt}/4)."
                )
            except Exception as e:
                self.log(
                    f"  [NewToki] Webtoon chapter attempt "
                    f"{attempt}/4 failed: {e}"
                )
            time.sleep(0.5)
        return None

    def fetch_ntk_binary(self, url, referer=None):
        """Fetch a NewToki binary asset with the verified API session."""
        state = self._ntk_api_state
        if not url:
            return None
        if self._ntk_browser_fallback and self._page:
            return self._ntk_fetch_binary_browser(url)
        if state and state.get('session'):
            headers = dict(state.get('doc_headers') or {})
            headers.update({
                'Accept': (
                    'image/avif,image/webp,image/apng,image/svg+xml,'
                    'image/*,*/*;q=0.8'
                ),
                'Referer': (
                    referer
                    or state.get('index_url')
                    or state.get('origin')
                    or ''
                ),
                'sec-fetch-dest': 'image',
                'sec-fetch-mode': 'no-cors',
                'sec-fetch-site': 'same-origin',
            })
            try:
                response = self._ntk_request_get(state['session'], url, headers)
                content_type = response.headers.get('content-type', '')
                if (
                    response.status_code == 200
                    and len(response.content) > 100
                    and (
                        content_type.startswith('image/')
                        or response.content[:4] == b'\x89PNG'
                        or response.content[:3] == b'\xff\xd8\xff'
                        or response.content[:4] == b'RIFF'
                        or response.content[:4] == b'GIF8'
                        or response.content[:12] == b'\x00\x00\x00\x18ftypavif'
                    )
                ):
                    return response.content
                self.log(
                    f"  [NewToki] Direct cover asset fetch failed "
                    f"(HTTP {response.status_code}, {len(response.content)} bytes, "
                    f"{content_type or 'unknown content-type'})."
                )
            except Exception as e:
                self.log(f"  [NewToki] Direct cover asset fetch failed: {e}")
        return None

    def _ntk_dump_debug_page(self, page, label):
        """Save rendered NewToki page state for debugging only."""
        try:
            logs_dir = os.path.join(_get_base_dir(), 'logs')
            os.makedirs(logs_dir, exist_ok=True)
            safe_label = re.sub(r'[^A-Za-z0-9._-]+', '_', label or 'chapter')
            safe_label = safe_label.strip('._')[:80] or 'chapter'
            stamp = time.strftime('%Y%m%d_%H%M%S')
            html_path = os.path.join(logs_dir, f'ntk_debug_{stamp}_{safe_label}.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(page.content() if page else '')
            self.log(f"  [NewToki] Debug dump saved: {html_path}")
            return html_path
        except Exception as e:
            self.log(f"  [NewToki] Debug dump failed: {e}")
            return None

    def _ntk_parse_book(self, url):
        """Scrape NewToki entirely through installed headless Chrome."""
        self._stop_requested = False
        self._ntk_browser_fallback = True
        self._ntk_api_state = None
        kind = self._ntk_content_kind_from_url(url)
        self.log(
            f"[NewToki] Detected {kind} URL, using headless browser scraper."
        )
        self.log(f"[NewToki] Opening book in headless Chrome: {url}")

        novel_id = self._ntk_novel_id_from_url(url)
        if not self._ntk_refresh_cloudflare_session(url):
            self.log("ERROR: [NewToki] Could not start headless Chrome.")
            return None
        data = self._ntk_parse_index_browser(url)
        if not data or not data.get('chapters'):
            self.log(
                "ERROR: [NewToki] Headless Chrome did not expose a usable "
                "chapter list."
            )
            self._ntk_dump_debug_page(self._page, f'index_{novel_id}')
            return None
        chapters = data.get('chapters') or []

        data['coverUrl'] = self._ntk_prefer_novelpia_cover_url(
            data.get('coverUrl', '')
        )
        if data.get('coverUrl'):
            self.log(f"[NewToki] Cover URL: {data.get('coverUrl')}")
        else:
            self.log("[NewToki] Cover URL not found on index page.")

        data['_ntk_novel'] = True
        data['_ntk_api'] = False
        data['_ntk_headless_browser'] = True
        data['_ntk_novel_id'] = novel_id
        data['_ntk_kind'] = kind
        self._book_data = data
        self._book_url = url
        self.log(
            f"[NewToki] Book: {data.get('bookname', '?')} - "
            f"{len(chapters)} chapters"
        )
        return data

    def _ntk_parse_chapter(self, chapter_url, chapter_name, page=None):
        """Fetch one ntk novel/webtoon chapter."""
        if self._book_data and self._book_data.get('_ntk_kind') == 'webtoon':
            if self._ntk_browser_fallback:
                data = self._ntk_fetch_webtoon_chapter_browser(
                    chapter_url, chapter_name
                )
            else:
                data = self._ntk_fetch_webtoon_chapter(
                    chapter_url, chapter_name
                )
            if data:
                debug_selector = data.pop('_debugSelector', '')
                self.log(
                    "  [NewToki] Used "
                    + ("Chrome " if self._ntk_browser_fallback else "")
                    + "webtoon image scrape."
                )
                if debug_selector:
                    self.log(f"  [NewToki] Content selector: {debug_selector}")
                return data
            self.log(f"  [NewToki] Webtoon chapter fetch failed: {chapter_name}")
            return None
        if self._ntk_browser_fallback:
            data = self._ntk_fetch_chapter_browser(chapter_url, chapter_name)
        else:
            data = self._ntk_fetch_chapter_api(chapter_url, chapter_name)
        if data:
            debug_selector = data.pop('_debugSelector', '')
            self.log(
                "  [NewToki] Used encrypted content API through "
                + ("Chrome." if self._ntk_browser_fallback else "direct HTTP.")
            )
            if debug_selector:
                self.log(f"  [NewToki] Content selector: {debug_selector}")
            return data
        self.log(f"  [NewToki] API chapter fetch failed: {chapter_name}")
        return None

    # ------------------------------------------------------------------
    # Global Novelpia scraper (ported from the user's pia-scrap project)
    # ------------------------------------------------------------------
    _GLOBAL_NOVELPIA_BASE = 'https://global.novelpia.com'
    _GLOBAL_NOVELPIA_API = 'https://api-global.novelpia.com'
    _GLOBAL_NOVELPIA_TIMEOUT = 90
    _GLOBAL_NOVELPIA_IMAGE_COOKIES = (
        'CloudFront-Policy',
        'CloudFront-Key-Pair-Id',
        'CloudFront-Signature',
    )

    @staticmethod
    def _global_novelpia_novel_id(url):
        try:
            path = urllib.parse.urlparse(url or '').path or ''
        except Exception:
            return ''
        match = re.search(r'/novel/(\d+)', path, re.I)
        return match.group(1) if match else ''

    @staticmethod
    def _global_novelpia_episode_id(url):
        try:
            path = urllib.parse.urlparse(url or '').path or ''
        except Exception:
            return ''
        match = re.search(r'/viewer/(\d+)', path, re.I)
        return match.group(1) if match else ''

    def _global_novelpia_profile_cookies(self):
        """Read current cookies from the saved External Browser profile.

        Enter Browser uses installed Chrome directly, so its cookie database
        can be newer than ``nd_storage_state.json``. In particular, Global
        Novelpia may add TKEY during login; using the older snapshot causes
        the API refresh to reject an otherwise valid browser session.
        """
        profile_dir = self._get_user_data_dir()
        cookies_db = os.path.join(
            profile_dir, 'Default', 'Network', 'Cookies'
        )
        if not os.path.exists(cookies_db):
            return []
        key = self._ntk_chrome_master_key(profile_dir)
        now_chrome = int((time.time() + 11644473600) * 1000000)
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(
                prefix='global_novelpia_cookies_', suffix='.db'
            )
            os.close(fd)
            shutil.copy2(cookies_db, temp_path)
            connection = sqlite3.connect(temp_path)
            try:
                rows = connection.execute(
                    """
                    SELECT host_key, name, value, encrypted_value,
                           expires_utc, path, is_secure
                    FROM cookies
                    WHERE host_key = 'novelpia.com'
                       OR host_key = '.novelpia.com'
                       OR host_key LIKE '%.novelpia.com'
                    """
                ).fetchall()
            finally:
                connection.close()

            records = []
            for (
                domain, name, value, encrypted_value,
                expires_utc, path, is_secure,
            ) in rows:
                if expires_utc and expires_utc < now_chrome:
                    continue
                cookie_value = value or self._ntk_decrypt_chrome_cookie(
                    domain, encrypted_value, key
                )
                if not name or not cookie_value:
                    continue
                records.append({
                    'name': name,
                    'value': cookie_value,
                    'domain': domain,
                    'path': path or '/',
                    'secure': bool(is_secure),
                })
            return records
        except Exception:
            return []
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def _global_novelpia_sync_browser_cookies(self, session):
        """Copy the dedicated External Downloader profile into requests."""
        if self._context:
            try:
                cookies = self._context.cookies()
            except Exception:
                cookies = []
        else:
            # The anonymous fast path does not launch Chromium. Preserve the
            # ability to authenticate lazily by reading cookies previously
            # saved through Enter Browser.
            cookie_records = {}
            for origin in (
                self._GLOBAL_NOVELPIA_BASE,
                self._GLOBAL_NOVELPIA_API,
            ):
                for cookie in self._storage_cookies_for_url(origin):
                    key = (
                        cookie.get('name'), cookie.get('domain'),
                        cookie.get('path'),
                    )
                    cookie_records[key] = cookie
            # Current Chrome data wins over the exported fallback snapshot.
            for cookie in self._global_novelpia_profile_cookies():
                key = (
                    cookie.get('name'), cookie.get('domain'),
                    cookie.get('path'),
                )
                cookie_records[key] = cookie
            cookies = list(cookie_records.values())
        copied = 0
        for cookie in cookies or []:
            domain = (cookie.get('domain') or '').lower()
            cookie_host = domain.lstrip('.')
            if (
                cookie_host != 'novelpia.com'
                and not cookie_host.endswith('.novelpia.com')
            ):
                continue
            name = cookie.get('name')
            value = cookie.get('value')
            if not name or value is None:
                continue
            try:
                session.cookies.set(
                    name,
                    value,
                    domain=cookie.get('domain') or '.novelpia.com',
                    path=cookie.get('path') or '/',
                )
                copied += 1
            except Exception:
                pass
        return copied

    @staticmethod
    def _global_novelpia_has_saved_login(session):
        """Recognize browser auth cookies without making a network probe."""
        values = {}
        try:
            for cookie in session.cookies:
                values[cookie.name] = cookie.value
        except Exception:
            return False
        userkey = str(values.get('USERKEY') or '').strip()
        tkey = str(values.get('TKEY') or '').strip()
        login_key = str(values.get('LOGINKEY') or '').strip()
        is_login = str(values.get('ISLOGIN') or '').strip().lower()
        login_marker = is_login not in ('', '0', 'false', 'n', 'no')
        return bool(userkey and (tkey or (login_key and login_marker)))

    def _global_novelpia_ensure_session(self, refresh_login=False):
        """Create an API session and import the latest browser credentials."""
        try:
            import requests
        except Exception as e:
            self.log(f'[Global Novelpia] requests is unavailable: {e}')
            return None

        if self._global_novelpia_session is None:
            session = requests.Session()
            session.headers.update({
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Origin': self._GLOBAL_NOVELPIA_BASE,
                'Referer': f'{self._GLOBAL_NOVELPIA_BASE}/',
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/147.0.0.0 Safari/537.36'
                ),
                'X-Requested-With': 'XMLHttpRequest',
            })
            self._global_novelpia_session = session

        session = self._global_novelpia_session
        self._global_novelpia_sync_browser_cookies(session)
        cookie_names = {cookie.name for cookie in session.cookies}
        if 'USERKEY' not in cookie_names:
            session.cookies.set(
                'USERKEY', uuid.uuid4().hex,
                domain='.novelpia.com', path='/',
            )
        if 'last_login' not in cookie_names:
            session.cookies.set(
                'last_login', 'basic',
                domain='.novelpia.com', path='/',
            )

        # Book discovery and free chapters begin anonymously. Account access
        # is refreshed lazily only after an episode endpoint rejects anonymous
        # access, unless a caller explicitly requests a refresh.
        if refresh_login:
            self._global_novelpia_refresh_login()
        return session

    def _global_novelpia_refresh_login(self, expected_token=None):
        """Exchange Global Novelpia cookies for a fresh LOGINAT token."""
        with self._global_novelpia_auth_lock:
            if (
                expected_token is not None
                and self._global_novelpia_login_at
                and self._global_novelpia_login_at != expected_token
            ):
                return True
            if (
                not expected_token
                and self._global_novelpia_refresh_attempted
                and not self._global_novelpia_login_at
            ):
                return False
            self._global_novelpia_refresh_attempted = True
            session = self._global_novelpia_session
            if session is None:
                return False
            self._global_novelpia_sync_browser_cookies(session)
            try:
                response = session.get(
                    f'{self._GLOBAL_NOVELPIA_API}/v1/login/refresh',
                    timeout=self._GLOBAL_NOVELPIA_TIMEOUT,
                )
                payload = response.json()
            except Exception:
                return False
            result = payload.get('result') if isinstance(payload, dict) else {}
            token = result.get('LOGINAT') if isinstance(result, dict) else None
            status_code = payload.get('statusCode') if isinstance(payload, dict) else None
            if (
                response.status_code == 200
                and str(status_code or 200) == '200'
                and isinstance(token, str)
                and bool(token.strip())
            ):
                self._global_novelpia_login_at = urllib.parse.unquote(token)
                return True
            return False

    def _global_novelpia_clone_session(self):
        source = self._global_novelpia_session
        if source is None:
            source = self._global_novelpia_ensure_session()
        if source is None:
            return None
        import requests
        session = requests.Session()
        session.headers.update(source.headers)
        session.cookies.update(source.cookies)
        return session

    @staticmethod
    def _global_novelpia_compact_ad_geometry():
        """Place the real 300x250 reward ad in a small visible window."""
        width, height = 520, 500
        if sys.platform != 'win32':
            return 40, 40, width, height
        try:
            user32 = ctypes.windll.user32
            screen_width = max(width, int(user32.GetSystemMetrics(0)))
            return max(0, screen_width - width - 24), 64, width, height
        except Exception:
            return 40, 40, width, height

    def _global_novelpia_browser_cookie_records(self):
        """Build Playwright cookies from the live and saved API sessions."""
        records = {}
        for cookie in self._global_novelpia_profile_cookies():
            key = (
                cookie.get('name'), cookie.get('domain'),
                cookie.get('path') or '/',
            )
            records[key] = cookie
        session = self._global_novelpia_session
        if session is not None:
            try:
                for cookie in session.cookies:
                    record = {
                        'name': cookie.name,
                        'value': cookie.value,
                        'domain': cookie.domain or '.novelpia.com',
                        'path': cookie.path or '/',
                        'secure': bool(cookie.secure),
                    }
                    records[(
                        record['name'], record['domain'], record['path'],
                    )] = record
            except Exception:
                pass

        cookies = []
        for record in records.values():
            domain = str(record.get('domain') or '').lower()
            host = domain.lstrip('.')
            if host != 'novelpia.com' and not host.endswith('.novelpia.com'):
                continue
            name = record.get('name')
            value = record.get('value')
            if not name or value is None:
                continue
            cookies.append({
                'name': str(name),
                'value': str(value),
                'domain': domain or '.novelpia.com',
                'path': str(record.get('path') or '/'),
                'secure': bool(record.get('secure')),
            })
        return cookies

    def _global_novelpia_apply_ad_cookies(self, context):
        cookies = self._global_novelpia_browser_cookie_records()
        if not cookies:
            return 0
        try:
            context.add_cookies(cookies)
            return len(cookies)
        except Exception:
            return 0

    def _global_novelpia_apply_ad_storage(self, context):
        """Restore saved Global Novelpia localStorage into the ad window."""
        try:
            with open(self._get_storage_state_path(), 'r', encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            return 0

    @staticmethod
    def _global_novelpia_install_spoiler_shield(context):
        """Install a reload-persistent neutral cover for unlocked viewers."""
        script = r"""
(() => {
  const marker = '__npia_global_spoiler_shield';
  const shieldId = '__npia-global-spoiler-shield';
  const install = () => {
    if (document.getElementById(shieldId)) return true;
    const parent = document.body || document.documentElement;
    if (!parent) return false;
    const shield = document.createElement('div');
    shield.id = shieldId;
    shield.setAttribute('role', 'status');
    shield.setAttribute('aria-live', 'polite');
    shield.style.cssText = [
      'position:fixed !important', 'inset:0 !important',
      'z-index:2147483647 !important', 'display:flex !important',
      'align-items:center !important', 'justify-content:center !important',
      'padding:24px !important', 'box-sizing:border-box !important',
      'background:#111827 !important', 'color:#d1d5db !important',
      'font:600 15px/1.5 system-ui,sans-serif !important',
      'text-align:center !important', 'pointer-events:none !important'
    ].join(';');
    shield.textContent = 'Advertisement complete. Preparing the chapter…';
    parent.appendChild(shield);
    return true;
  };
  window.__npiaInstallSpoilerShield = () => {
    try { sessionStorage.setItem(marker, '1'); } catch (_) {}
    if (!install()) {
      document.addEventListener('DOMContentLoaded', install, {once: true});
    }
    return true;
  };

  const normalize = (value) => String(value || '')
    .replace(/\s+/g, ' ').trim().toLowerCase();
  const isContinue = (node) => {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
    if (!node.matches('button, [role="button"], a')) return false;
    return normalize(node.innerText || node.textContent) === 'continue';
  };
  const isActionable = (node) => {
    if (!isContinue(node) || node.disabled
        || node.getAttribute('aria-disabled') === 'true') return false;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden'
      && style.pointerEvents !== 'none' && rect.width > 0 && rect.height > 0;
  };

  // Arm the cover during the capture phase. This runs before the site's own
  // handler, so a manual pointer, touch, or keyboard activation cannot reveal
  // the viewer before the next document installs its shield.
  const guardContinue = (event) => {
    let candidate = null;
    if (event.type === 'keydown') {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      candidate = document.activeElement;
    } else {
      const path = typeof event.composedPath === 'function'
        ? event.composedPath() : [event.target];
      candidate = path.find(isContinue);
    }
    if (isContinue(candidate)) window.__npiaInstallSpoilerShield();
  };
  for (const eventName of ['pointerdown', 'mousedown', 'touchstart', 'click']) {
    document.addEventListener(eventName, guardContinue, true);
  }
  document.addEventListener('keydown', guardContinue, true);

  // The site's Continue control is created only when the real advertisement
  // has finished. Observe that main-page UI and activate it in the same task
  // in which it becomes actionable. The Playwright loop remains as a fallback
  // for controls hosted in an open shadow root.
  let scanQueued = false;
  let lastAutoClick = 0;
  const scanForContinue = () => {
    scanQueued = false;
    if (!/^\/viewer\//i.test(location.pathname)) return;
    const roots = [document];
    for (let rootIndex = 0; rootIndex < roots.length; rootIndex += 1) {
      const root = roots[rootIndex];
      for (const element of root.querySelectorAll('*')) {
        if (element.shadowRoot) roots.push(element.shadowRoot);
      }
      const button = [...root.querySelectorAll('button, [role="button"], a')]
        .find(isActionable);
      if (!button) continue;
      const now = Date.now();
      if (now - lastAutoClick < 100) return;
      lastAutoClick = now;
      window.__npiaInstallSpoilerShield();
      button.click();
      return;
    }
  };
  const queueContinueScan = () => {
    if (scanQueued) return;
    scanQueued = true;
    queueMicrotask(scanForContinue);
  };
  new MutationObserver(queueContinueScan).observe(document, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: ['class', 'style', 'disabled', 'aria-disabled']
  });
  queueContinueScan();

  try {
    if (sessionStorage.getItem(marker) === '1') {
      window.__npiaInstallSpoilerShield();
    }
  } catch (_) {}
})();
"""
        try:
            context.add_init_script(script=script)
            return True
        except Exception:
            return False

    @staticmethod
    def _global_novelpia_prepare_ad_navigation(page):
        """Clear only the next-document marker, keeping the current story hidden."""
        try:
            return bool(page.evaluate(r"""
() => {
  try { sessionStorage.removeItem('__npia_global_spoiler_shield'); }
  catch (_) {}
  return true;
}
"""))
        except Exception:
            return False

    @staticmethod
    def _global_novelpia_hide_ad_story(page):
        """Cover viewer content without obscuring an ad that is still running."""
        try:
            return bool(page.evaluate(r"""
() => {
  if (typeof window.__npiaInstallSpoilerShield === 'function') {
    return window.__npiaInstallSpoilerShield();
  }
  try { sessionStorage.setItem('__npia_global_spoiler_shield', '1'); }
  catch (_) {}
  let shield = document.getElementById('__npia-global-spoiler-shield');
  if (!shield) {
    shield = document.createElement('div');
    shield.id = '__npia-global-spoiler-shield';
    shield.style.cssText = 'position:fixed!important;inset:0!important;'
      + 'z-index:2147483647!important;background:#111827!important;'
      + 'color:#d1d5db!important;display:flex!important;'
      + 'align-items:center!important;justify-content:center!important;'
      + 'padding:24px!important;box-sizing:border-box!important;'
      + 'font:600 15px/1.5 system-ui,sans-serif!important;'
      + 'text-align:center!important;pointer-events:none!important';
    shield.textContent = 'Advertisement complete. Preparing the chapter…';
    (document.body || document.documentElement).appendChild(shield);
  }
  return true;
}
"""))
        except Exception:
            return False

    @staticmethod
    def _global_novelpia_click_ad_continue(page):
        """Click only Novelpia's exact Continue control, shielding first."""
        # Playwright's role locator pierces open shadow DOM, which plain
        # querySelector does not. Ezoic commonly renders its completion UI in
        # such a shadow root. Page-level locators intentionally do not search
        # advertiser frames, so an ad creative's own CTA cannot be clicked.
        def click_first(matches):
            for index in range(matches.count()):
                candidate = matches.nth(index)
                if not candidate.is_visible():
                    continue
                ExternalScraper._global_novelpia_hide_ad_story(page)
                try:
                    candidate.click(timeout=1500)
                except Exception:
                    candidate.evaluate('(node) => node.click()')
                return True
            return False

        exact_continue = re.compile(r'^\s*continue\s*$', re.I)
        get_by_role = getattr(page, 'get_by_role', None)
        if get_by_role:
            try:
                if click_first(get_by_role('button', name=exact_continue)):
                    return True
            except Exception:
                pass
        get_by_text = getattr(page, 'get_by_text', None)
        if get_by_text:
            try:
                if click_first(get_by_text(exact_continue)):
                    return True
            except Exception:
                pass
        try:
            return bool(page.evaluate(r"""
() => {
  const normalize = (value) => String(value || '')
    .replace(/\s+/g, ' ').trim().toLowerCase();
  const candidates = [...document.querySelectorAll(
    'button, [role="button"], a'
  )];
  const button = candidates.find((node) => {
    if (normalize(node.innerText || node.textContent) !== 'continue') {
      return false;
    }
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden'
      && rect.width > 0 && rect.height > 0;
  });
  if (!button) return false;

  // Install the neutral cover before dispatching the site's Continue event.
  // The saved session marker reapplies it at document start if the button
  // causes a reload, preventing a one-frame story-content flash.
  if (typeof window.__npiaInstallSpoilerShield === 'function') {
    window.__npiaInstallSpoilerShield();
  } else {
    try { sessionStorage.setItem('__npia_global_spoiler_shield', '1'); }
    catch (_) {}
    const shield = document.createElement('div');
    shield.id = '__npia-global-spoiler-shield';
    shield.style.cssText = 'position:fixed!important;inset:0!important;'
      + 'z-index:2147483647!important;background:#111827!important;'
      + 'pointer-events:none!important';
    (document.body || document.documentElement).appendChild(shield);
  }
  button.click();
  return true;
}
"""))
        except Exception:
            return False
        origins = [
            origin for origin in (state.get('origins') or [])
            if isinstance(origin, dict)
            and str(origin.get('origin') or '').rstrip('/')
            == self._GLOBAL_NOVELPIA_BASE
        ]
        if not origins:
            return 0
        script = r"""
(() => {
  const saved = __ORIGINS__;
  const current = saved.find((entry) => entry.origin === location.origin);
  for (const item of (current?.localStorage || [])) {
    try { localStorage.setItem(item.name, item.value); } catch (_) {}
  }
})();
""".replace('__ORIGINS__', json.dumps(origins))
        try:
            context.add_init_script(script=script)
            return len(origins)
        except Exception:
            return 0

    def _global_novelpia_close_ad_browser_owned(self):
        """Close compact-ad resources on their owning worker thread."""
        page = self._global_novelpia_ad_page
        context = self._global_novelpia_ad_context
        browser = self._global_novelpia_ad_browser
        playwright = self._global_novelpia_ad_playwright
        process = self._global_novelpia_ad_process
        port = self._global_novelpia_ad_cdp_port
        profile = self._global_novelpia_ad_profile
        self._global_novelpia_ad_page = None
        self._global_novelpia_ad_context = None
        self._global_novelpia_ad_browser = None
        self._global_novelpia_ad_playwright = None
        self._global_novelpia_ad_process = None
        self._global_novelpia_ad_cdp_port = None
        self._global_novelpia_ad_profile = None
        for resource in (page, context, browser):
            try:
                if resource:
                    resource.close()
            except Exception:
                pass
        try:
            if playwright:
                playwright.stop()
        except Exception:
            pass
        try:
            if process and process.poll() is None:
                self._request_cdp_browser_close(port)
                process.wait(timeout=5)
        except Exception:
            try:
                if process and process.poll() is None:
                    process.terminate()
            except Exception:
                pass
        try:
            if profile:
                profile.cleanup()
        except Exception:
            pass

    def _global_novelpia_start_ad_browser_owned(self):
        """Start/reuse the compact visible Chrome on the ad worker thread."""
        if self._page_is_usable(self._global_novelpia_ad_page):
            self._global_novelpia_apply_ad_cookies(
                self._global_novelpia_ad_context
            )
            return self._global_novelpia_ad_page

        self._global_novelpia_close_ad_browser_owned()
        profile = tempfile.TemporaryDirectory(prefix='npia_global_ad_')
        profile_dir = os.path.join(profile.name, 'browser_data')
        x, y, width, height = self._global_novelpia_compact_ad_geometry()
        process = None
        port = None
        playwright = None
        try:
            process, port = self._open_system_chrome(
                'about:blank',
                remote_debugging=True,
                user_data_dir=profile_dir,
                window_size=(width, height),
                window_position=(x, y),
            )
            if not process or not port or not self._wait_for_cdp(port, timeout=15):
                raise RuntimeError('installed Chrome did not expose DevTools')
            playwright = sync_playwright().start()
            browser = playwright.chromium.connect_over_cdp(
                f'http://127.0.0.1:{port}'
            )
            contexts = browser.contexts
            if not contexts:
                raise RuntimeError('Chrome did not create a browser context')
            context = contexts[0]
            pages = context.pages
            page = pages[-1] if pages else context.new_page()
            self._global_novelpia_ad_profile = profile
            self._global_novelpia_ad_process = process
            self._global_novelpia_ad_cdp_port = port
            self._global_novelpia_ad_playwright = playwright
            self._global_novelpia_ad_browser = browser
            self._global_novelpia_ad_context = context
            self._global_novelpia_ad_page = page
            self._global_novelpia_apply_ad_cookies(context)
            self._global_novelpia_apply_ad_storage(context)
            self._global_novelpia_install_spoiler_shield(context)
            return page
        except Exception as error:
            self.log(
                '[Global Novelpia] Could not open the compact ad window: '
                f'{error}'
            )
            try:
                if playwright:
                    playwright.stop()
            except Exception:
                pass
            try:
                if process and process.poll() is None:
                    self._request_cdp_browser_close(port)
                    process.terminate()
            except Exception:
                pass
            try:
                profile.cleanup()
            except Exception:
                pass
            return None

    def _global_novelpia_ticket_available(self, episode_no):
        session = self._global_novelpia_clone_session()
        if session is None:
            return False
        try:
            status, payload = self._global_novelpia_request_json(
                session,
                f'{self._GLOBAL_NOVELPIA_API}/v1/novel/episode',
                params={'episode_no': str(episode_no)},
                login_at=self._global_novelpia_login_at or None,
                max_retries=1,
            )
            if status != 200:
                return False
            token, direct_url = self._global_novelpia_extract_ticket_token(
                payload
            )
            return bool(token or direct_url)
        finally:
            session.close()

    def _global_novelpia_run_ad_request(self, request):
        """Render one real site reward ad and wait for server confirmation."""
        episode_no = request['episode_no']
        chapter_name = request['chapter_name']
        if self._global_novelpia_ticket_available(episode_no):
            return True

        page = self._global_novelpia_start_ad_browser_owned()
        if not page:
            return False
        context = self._global_novelpia_ad_context
        self._global_novelpia_apply_ad_cookies(context)
        grant_seen = threading.Event()

        def inspect_response(response):
            try:
                parsed = urllib.parse.urlparse(response.url or '')
                if (
                    parsed.hostname == 'api-global.novelpia.com'
                    and parsed.path == '/v1/ad/reward/grant'
                    and response.request.method.upper() == 'POST'
                    and 200 <= int(response.status) < 300
                ):
                    grant_seen.set()
            except Exception:
                pass

        try:
            context.on('response', inspect_response)
        except Exception:
            pass
        self.log(
            f'  [Global Novelpia] Advertisement required: {chapter_name}'
        )
        self.log(
            '  [Global Novelpia] A compact ad window is open. Let the ad '
            'finish; Continue will be clicked automatically.'
        )
        chapter_url = f'{self._GLOBAL_NOVELPIA_BASE}/viewer/{episode_no}'
        self._global_novelpia_prepare_ad_navigation(page)
        try:
            page.goto(
                chapter_url,
                wait_until='domcontentloaded',
                timeout=self._GLOBAL_NOVELPIA_TIMEOUT * 1000,
            )
        except PlaywrightTimeoutError:
            self.log(
                '  [Global Novelpia] The ad page is responding slowly; '
                'continuing to wait in the compact window.'
            )
        except Exception as error:
            self.log(
                f'  [Global Novelpia] Ad page failed to open for '
                f'{chapter_name}: {error}'
            )
            try:
                context.remove_listener('response', inspect_response)
            except Exception:
                pass
            return False

        deadline = time.monotonic() + 240
        next_ticket_check = time.monotonic() + 3
        next_continue_check = time.monotonic()
        activation_attempted = False
        continue_clicked = False
        ticket_confirmed_at = None
        try:
            while (
                time.monotonic() < deadline
                and not self._global_novelpia_ad_stop.is_set()
                and not self._stop_requested
            ):
                if not self._page_is_usable(page):
                    self.log(
                        '  [Global Novelpia] The compact ad window was closed '
                        f'before completion: {chapter_name}'
                    )
                    return False
                now = time.monotonic()
                if not continue_clicked and now >= next_continue_check:
                    continue_clicked = self._global_novelpia_click_ad_continue(
                        page
                    )
                    if continue_clicked:
                        activation_attempted = True
                    next_continue_check = now + 0.1
                if not activation_attempted and now + 3 >= next_ticket_check:
                    # The current viewer normally opens its reward modal with
                    # `instant: true`. This narrowly-scoped fallback clicks
                    # only Novelpia's own main-page "watch ad" control, never
                    # content inside an advertiser iframe.
                    try:
                        activation_attempted = bool(page.evaluate(r"""
() => {
  const pattern = /(?:watch|view|show|play).{0,20}(?:ad|advertisement)|free pass/i;
  const candidates = [...document.querySelectorAll('button, [role="button"]')];
  const button = candidates.find((node) => pattern.test(
    (node.innerText || node.textContent || '').trim()
  ));
  if (!button) return false;
  button.click();
  return true;
}
"""))
                    except Exception:
                        activation_attempted = True
                if now >= next_ticket_check:
                    if self._global_novelpia_ticket_available(episode_no):
                        # Some ad providers grant the ticket just before the
                        # Continue control is painted. Hide the viewer now,
                        # keep looking briefly, and click it as soon as it
                        # exists. Providers without a Continue step resume
                        # after the short grace period.
                        self._global_novelpia_hide_ad_story(page)
                        if not continue_clicked:
                            continue_clicked = (
                                self._global_novelpia_click_ad_continue(page)
                            )
                        ticket_confirmed_at = ticket_confirmed_at or now
                        if (
                            continue_clicked
                            or now - ticket_confirmed_at >= 3
                        ):
                            self.log(
                                '  [Global Novelpia] Advertisement confirmed; '
                                f'resuming {chapter_name}.'
                            )
                            return True
                    next_ticket_check = now + (1 if grant_seen.is_set() else 3)
                try:
                    page.wait_for_timeout(50)
                except Exception:
                    time.sleep(0.05)
            self.log(
                '  [Global Novelpia] Advertisement was not confirmed within '
                f'four minutes: {chapter_name}'
            )
            return False
        finally:
            try:
                context.remove_listener('response', inspect_response)
            except Exception:
                pass

    def _global_novelpia_ad_worker_main(self, work_queue):
        try:
            while not self._global_novelpia_ad_stop.is_set():
                try:
                    request = work_queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                if request is None:
                    break
                try:
                    request['success'] = bool(
                        self._global_novelpia_run_ad_request(request)
                    )
                except Exception as error:
                    self.log(
                        '[Global Novelpia] Compact ad window error: '
                        f'{error}'
                    )
                    request['success'] = False
                finally:
                    request['done'].set()
        finally:
            self._global_novelpia_close_ad_browser_owned()
            while True:
                try:
                    pending = work_queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(pending, dict):
                    pending['success'] = False
                    pending['done'].set()
            with self._global_novelpia_ad_state_lock:
                if self._global_novelpia_ad_thread is threading.current_thread():
                    self._global_novelpia_ad_thread = None

    def _global_novelpia_complete_ad(
        self, novel_no, episode_no, chapter_name
    ):
        """Queue an ad-gated episode on the single browser-owning thread."""
        request = {
            'novel_no': str(novel_no or ''),
            'episode_no': str(episode_no),
            'chapter_name': chapter_name,
            'done': threading.Event(),
            'success': False,
        }
        with self._global_novelpia_ad_state_lock:
            thread = self._global_novelpia_ad_thread
            if not thread or not thread.is_alive():
                self._global_novelpia_ad_stop.clear()
                self._global_novelpia_ad_queue = queue.Queue()
                thread = threading.Thread(
                    target=self._global_novelpia_ad_worker_main,
                    args=(self._global_novelpia_ad_queue,),
                    name='GlobalNovelpiaAd',
                    daemon=True,
                )
                self._global_novelpia_ad_thread = thread
                thread.start()
            work_queue = self._global_novelpia_ad_queue
            work_queue.put(request)

        while not request['done'].wait(0.25):
            if self._global_novelpia_ad_stop.is_set() or self._stop_requested:
                return False
        return bool(request['success'])

    def _global_novelpia_shutdown_ad_worker(self):
        """Stop the ad owner without touching the downloader's main Chrome."""
        thread = self._global_novelpia_ad_thread
        if not thread:
            return
        self._global_novelpia_ad_stop.set()
        work_queue = self._global_novelpia_ad_queue
        if work_queue:
            try:
                work_queue.put_nowait(None)
            except Exception:
                pass
        port = self._global_novelpia_ad_cdp_port
        if port:
            self._request_cdp_browser_close(port)
        if thread is not threading.current_thread():
            thread.join(timeout=8)
        process = self._global_novelpia_ad_process
        if thread.is_alive() and process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

    def _global_novelpia_request_json(
        self,
        session,
        url,
        params=None,
        login_at=None,
        max_retries=3,
    ):
        """Request one pia-scrap API payload with bounded retry/backoff."""
        headers = {}
        if login_at:
            headers['login-at'] = urllib.parse.unquote(login_at)
        last_status = 0
        last_payload = {}
        for attempt in range(1, max(1, max_retries) + 1):
            if self._stop_requested:
                return 0, {'error': 'cancelled'}
            try:
                response = session.get(
                    url,
                    headers=headers or None,
                    params=params,
                    timeout=self._GLOBAL_NOVELPIA_TIMEOUT,
                )
                last_status = int(response.status_code or 0)
                try:
                    last_payload = response.json()
                except Exception:
                    last_payload = {
                        'error': (response.text or '')[:300],
                    }
            except Exception as e:
                last_status = 0
                last_payload = {'error': str(e)}

            retryable = (
                last_status == 429 or last_status >= 500 or not last_status
            ) and not self._global_novelpia_ad_required(
                last_status, last_payload
            )
            if not retryable or attempt >= max_retries:
                return last_status, last_payload
            if last_status == 429:
                delay = max(5.0, 1.25 ** (attempt + 2))
            else:
                delay = min(3.0, 1.25 ** attempt)
            delay += random.uniform(0.2, 0.8)
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline and not self._stop_requested:
                time.sleep(max(0.0, min(
                    0.1, deadline - time.monotonic()
                )))
        return last_status, last_payload

    @staticmethod
    def _global_novelpia_safe_int(value, fallback=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _global_novelpia_pick_strings(items, *keys):
        values = []
        if not isinstance(items, list):
            return values
        for item in items:
            if isinstance(item, str):
                value = item.strip()
                if value:
                    values.append(value)
                continue
            if not isinstance(item, dict):
                continue
            for key in keys:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())
                    break
        return values

    @staticmethod
    def _global_novelpia_payload_message(payload):
        if not isinstance(payload, dict):
            return str(payload or '')
        result = payload.get('result') or {}
        values = [
            payload.get('errmsg'), payload.get('message'), payload.get('error'),
            payload.get('code'),
        ]
        if isinstance(result, dict):
            values.extend([
                result.get('errmsg'), result.get('message'),
                result.get('name'), result.get('code'),
            ])
        return ' '.join(str(value) for value in values if value).strip()

    @classmethod
    def _global_novelpia_ad_required(cls, status, payload):
        """Recognize the server's basic-advertisement episode gate."""
        if not isinstance(payload, dict):
            return False
        result = payload.get('result')
        result = result if isinstance(result, dict) else {}
        code = payload.get('code')
        if code in (None, ''):
            code = result.get('code')
        code_text = str(code or '').strip()
        name = str(result.get('name') or payload.get('name') or '').upper()
        message = cls._global_novelpia_payload_message(payload).lower()
        code_match = code_text in ('0010', '10')
        return bool(
            status >= 400
            and (
                (code_match and (name == 'NOVEL_ERROR' or 'advertisement' in message))
                or 'basic advertisement' in message
            )
        )

    @staticmethod
    def _global_novelpia_ad_novel_id(payload):
        """Extract novel_no from the episode error payload when provided."""
        def walk(value):
            if isinstance(value, dict):
                novel_no = value.get('novel_no')
                if novel_no not in (None, ''):
                    return str(novel_no)
                for nested in value.values():
                    found = walk(nested)
                    if found:
                        return found
            elif isinstance(value, list):
                for nested in value:
                    found = walk(nested)
                    if found:
                        return found
            return ''

        return walk(payload)

    @classmethod
    def _global_novelpia_access_denied(cls, status, payload):
        message = cls._global_novelpia_payload_message(payload).lower()
        return bool(
            status in (401, 403)
            or re.search(
                r'login|logged in|purchase|paid|permission|forbidden|'
                r'access denied|not available|구매|로그인|권한',
                message,
                re.I,
            )
        )

    def _global_novelpia_parse_book(self, url):
        """Load Global Novelpia metadata and its canonical ASC episode list."""
        novel_id = self._global_novelpia_novel_id(url)
        if not novel_id:
            self.log('[Global Novelpia] ERROR: Invalid novel URL.')
            return None

        self._stop_requested = False
        book_url = f'{self._GLOBAL_NOVELPIA_BASE}/novel/{novel_id}'
        self.log(
            '[Global Novelpia] Note: this site can respond slowly. API '
            f'requests may wait up to {self._GLOBAL_NOVELPIA_TIMEOUT} seconds.'
        )

        # Metadata and episode lists do not require loading the website. Read
        # the saved profile locally, then exchange its login cookies for the
        # API token only when an authenticated browser session actually exists.
        session = self._global_novelpia_ensure_session(refresh_login=False)
        if session is None:
            return None
        if self._global_novelpia_has_saved_login(session):
            if self._global_novelpia_refresh_login():
                self.log(
                    '[Global Novelpia] Using the saved authenticated browser '
                    'session.'
                )
            else:
                self.log(
                    '[Global Novelpia] Saved browser login could not be used; '
                    'continuing with anonymous access.'
                )
        else:
            self.log(
                '[Global Novelpia] No saved login found; using anonymous '
                'access without an account check.'
            )

        status, novel_payload = self._global_novelpia_request_json(
            session,
            f'{self._GLOBAL_NOVELPIA_API}/v1/novel',
            params={'novel_no': novel_id},
            login_at=self._global_novelpia_login_at or None,
        )
        result = novel_payload.get('result') if isinstance(novel_payload, dict) else None
        novel = result.get('novel') if isinstance(result, dict) else None
        if status != 200 or not isinstance(novel, dict):
            message = self._global_novelpia_payload_message(novel_payload)
            self.log(
                f'[Global Novelpia] ERROR: Metadata request failed '
                f'(HTTP {status or "network"})'
                + (f': {message}' if message else '.')
            )
            return None

        info = result.get('info') or {}
        episode_count = info.get('epi_cnt') or novel.get('count_epi') or 0
        try:
            rows = max(1, int(episode_count or 1000))
        except (TypeError, ValueError):
            rows = 1000
        list_status, list_payload = self._global_novelpia_request_json(
            session,
            f'{self._GLOBAL_NOVELPIA_API}/v1/novel/episode/list',
            params={'novel_no': novel_id, 'rows': rows, 'sort': 'ASC'},
            login_at=self._global_novelpia_login_at or None,
        )
        list_result = list_payload.get('result') if isinstance(list_payload, dict) else None
        episodes = list_result.get('list') if isinstance(list_result, dict) else None
        if list_status != 200 or not isinstance(episodes, list):
            message = self._global_novelpia_payload_message(list_payload)
            self.log(
                f'[Global Novelpia] ERROR: Episode list failed '
                f'(HTTP {list_status or "network"})'
                + (f': {message}' if message else '.')
            )
            return None

        indexed_episodes = list(enumerate(episodes))
        indexed_episodes.sort(key=lambda item: (
            self._global_novelpia_safe_int(
                item[1].get('epi_num'), item[0] + 1
            ),
            item[0],
        ))
        chapters = []
        seen = set()
        for source_index, episode in indexed_episodes:
            if not isinstance(episode, dict):
                continue
            episode_no = episode.get('episode_no')
            try:
                episode_no = str(int(episode_no))
            except (TypeError, ValueError):
                continue
            if episode_no in seen:
                continue
            seen.add(episode_no)
            episode_number = self._global_novelpia_safe_int(
                episode.get('epi_num'), len(chapters) + 1
            )
            title = str(
                episode.get('epi_title') or f'Episode {episode_number}'
            ).strip()
            chapters.append({
                'id': episode_no,
                'url': f'{self._GLOBAL_NOVELPIA_BASE}/viewer/{episode_no}',
                'name': title,
                'fullName': title,
                'isVIP': False,
                'isPaid': False,
                'isAccessible': True,
                '_globalNovelpiaChapterNumber': episode_number,
                '_globalNovelpiaEpisode': episode,
            })

        if not chapters:
            self.log('[Global Novelpia] ERROR: No episodes were found.')
            return None

        writers = result.get('writer_list') or []
        author = ''
        if writers and isinstance(writers[0], dict):
            author = str(writers[0].get('writer_name') or '').strip()
        tags = self._global_novelpia_pick_strings(
            result.get('tag_list'), 'tag_name', 'name', 'title'
        ) + self._global_novelpia_pick_strings(
            novel.get('tag_list'), 'tag_name', 'name', 'title'
        )
        categories = self._global_novelpia_pick_strings(
            result.get('cate_list'), 'cate_name', 'name', 'title'
        ) + self._global_novelpia_pick_strings(
            novel.get('cate_list'), 'cate_name', 'name', 'title'
        ) + self._global_novelpia_pick_strings(
            result.get('genre_list'), 'genre_name', 'name', 'title'
        ) + self._global_novelpia_pick_strings(
            novel.get('genre_list'), 'genre_name', 'name', 'title'
        )
        subjects = list(dict.fromkeys(categories + tags))
        cover_url = novel.get('novel_full_img') or novel.get('novel_img') or ''
        if str(cover_url).startswith('//'):
            cover_url = f'https:{cover_url}'
        else:
            cover_url = urllib.parse.urljoin(book_url, str(cover_url or ''))
        introduction = str(novel.get('novel_story') or '').strip()
        publisher = str(
            novel.get('cp_name')
            or novel.get('publisher_name')
            or (result.get('cp_info') or {}).get('cp_name')
            or ''
        ).strip()
        title = str(novel.get('novel_name') or f'Novel {novel_id}').strip()
        data = {
            'bookname': title,
            'author': author,
            'coverUrl': cover_url,
            'description': introduction,
            'introduction': introduction,
            'introductionHTML': (
                '<p>' + html.escape(introduction).replace('\n', '<br/>\n') + '</p>'
                if introduction else ''
            ),
            'publisher': publisher,
            'status': (
                'Completed' if str(novel.get('flag_complete', 0)) == '1'
                else 'Ongoing'
            ),
            'bookUrl': book_url,
            'chapterCount': len(chapters),
            'chapters': chapters,
            'language': 'en',
            'tags': subjects,
            '_global_novelpia': True,
            '_global_novelpia_novel_id': novel_id,
        }
        self._book_data = data
        self._book_url = book_url
        self.log(
            f'[Global Novelpia] Book: {title} by {author or "?"} - '
            f'{len(chapters)} chapters'
        )
        return data

    @staticmethod
    def _global_novelpia_looks_like_jwt(value):
        if not isinstance(value, str):
            return False
        parts = value.split('.')
        if len(parts) != 3 or not all(parts):
            return False
        try:
            for part in parts:
                base64.urlsafe_b64decode(part + '===')
        except Exception:
            return False
        return True

    @classmethod
    def _global_novelpia_extract_ticket_token(cls, payload):
        """Return the ticket token/direct URL using pia-scrap's strict order."""
        result = payload.get('result') if isinstance(payload, dict) else {}
        result = result if isinstance(result, dict) else {}
        fallback = ''
        for key in ('_t', 't', 'token'):
            value = result.get(key)
            if isinstance(value, str) and value:
                if cls._global_novelpia_looks_like_jwt(value):
                    return value, ''
                fallback = fallback or value
        for value in result.values():
            if not isinstance(value, dict):
                continue
            for key in ('_t', 't', 'token'):
                nested = value.get(key)
                if isinstance(nested, str) and nested:
                    if cls._global_novelpia_looks_like_jwt(nested):
                        return nested, ''
                    fallback = fallback or nested

        def iter_strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for nested in value.values():
                    yield from iter_strings(nested)
            elif isinstance(value, list):
                for nested in value:
                    yield from iter_strings(nested)

        for value in iter_strings(payload):
            if not value.startswith(('http://', 'https://')):
                continue
            try:
                parsed = urllib.parse.urlparse(value)
                if (
                    parsed.hostname == 'api-global.novelpia.com'
                    and parsed.path.endswith('/v1/novel/episode/content')
                ):
                    token = (
                        urllib.parse.parse_qs(parsed.query).get('_t') or ['']
                    )[0]
                    if token:
                        if cls._global_novelpia_looks_like_jwt(token):
                            return token, value
                        fallback = fallback or token
            except Exception:
                pass
        return (fallback, '') if fallback else ('', '')

    @classmethod
    def _global_novelpia_signed_image_cookies(cls, ticket_payload):
        result = ticket_payload.get('result') if isinstance(ticket_payload, dict) else {}
        signed = result.get('signed_key') if isinstance(result, dict) else {}
        if not isinstance(signed, dict):
            return {}
        return {
            name: str(signed[name])
            for name in cls._GLOBAL_NOVELPIA_IMAGE_COOKIES
            if signed.get(name)
        }

    @staticmethod
    def _global_novelpia_raw_content(content_payload):
        result = content_payload.get('result') if isinstance(content_payload, dict) else {}
        result = result if isinstance(result, dict) else {}
        data = result.get('data') or {}
        parts = []
        if isinstance(data, dict):
            def content_key(key):
                match = re.search(r'(\d+)$', str(key))
                return (
                    0 if str(key) == 'epi_content' else 1,
                    int(match.group(1)) if match else 0,
                )
            keys = [key for key in data if str(key).startswith('epi_content')]
            for key in sorted(keys, key=content_key):
                value = data.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)
        raw = ''.join(parts).strip()
        if raw:
            return raw
        for value in (
            result.get('content'), result.get('html'), result.get('text'),
            content_payload.get('content') if isinstance(content_payload, dict) else '',
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ''

    @staticmethod
    def _global_novelpia_best_srcset(value):
        value = str(value or '').strip()
        if value.startswith('data:'):
            return value
        candidates = []
        for raw_candidate in value.split(','):
            bits = raw_candidate.strip().split()
            if not bits:
                continue
            score = 1.0
            if len(bits) > 1:
                descriptor = bits[-1].lower()
                try:
                    if descriptor.endswith('w'):
                        score = float(descriptor[:-1])
                    elif descriptor.endswith('x'):
                        score = float(descriptor[:-1]) * 10000.0
                except ValueError:
                    score = 1.0
            candidates.append((bits[0], score))
        return max(candidates, key=lambda item: item[1])[0] if candidates else ''

    @staticmethod
    def _global_novelpia_image_name(image_url, episode_no, image_index):
        if str(image_url).startswith('data:'):
            mime = str(image_url).split(';', 1)[0].lower()
            extension = {
                'data:image/png': 'png',
                'data:image/gif': 'gif',
                'data:image/webp': 'webp',
                'data:image/avif': 'avif',
                'data:image/svg+xml': 'svg',
            }.get(mime, 'jpg')
            return f'global_novelpia_{episode_no}_{image_index}.{extension}'
        try:
            name = urllib.parse.unquote(
                os.path.basename(urllib.parse.urlparse(image_url).path)
            )
        except Exception:
            name = ''
        name = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('._')
        if not name or not re.search(r'\.[A-Za-z0-9]{2,5}$', name):
            name = f'global_novelpia_{episode_no}_{image_index}.jpg'
        return name

    def _global_novelpia_build_chapter_result(
        self,
        raw_html,
        chapter_name,
        chapter_url,
        signed_cookies=None,
    ):
        """Normalize pia-scrap chapter HTML and expose all assets to writers."""
        try:
            from bs4 import BeautifulSoup
            from bs4.element import Tag
        except Exception as e:
            self.log(f'  [Global Novelpia] BeautifulSoup unavailable: {e}')
            return None
        soup = BeautifulSoup(raw_html or '', 'html.parser')
        lazy_attributes = (
            'data-original', 'data-lazy-src', 'data-src', 'data-url',
            'data-image', 'data-cfsrc',
        )
        source_attributes = ('data-srcset', 'srcset')

        def normalize_url(value):
            value = html.unescape(str(value or '').strip())
            if not value or value.startswith('blob:'):
                return ''
            if value.startswith('data:'):
                return value
            return urllib.parse.urldefrag(
                urllib.parse.urljoin(chapter_url, value)
            )[0]

        def choose_source(image):
            picture = (
                image.parent
                if isinstance(image.parent, Tag) and image.parent.name == 'picture'
                else None
            )
            if picture is not None:
                for source in picture.find_all('source'):
                    for attribute in source_attributes:
                        candidate = self._global_novelpia_best_srcset(
                            source.get(attribute)
                        )
                        if candidate:
                            return normalize_url(candidate)
            candidate = self._global_novelpia_best_srcset(
                image.get('data-srcset')
            )
            if candidate:
                return normalize_url(candidate)
            for attribute in lazy_attributes:
                candidate = image.get(attribute)
                if candidate:
                    return normalize_url(candidate)
            candidate = self._global_novelpia_best_srcset(image.get('srcset'))
            if candidate:
                return normalize_url(candidate)
            return normalize_url(image.get('src'))

        asset_urls = []
        for image in soup.find_all('img'):
            source = choose_source(image)
            if source:
                image['src'] = source
                asset_urls.append(source)
            for attribute in (*lazy_attributes, *source_attributes):
                image.attrs.pop(attribute, None)
        for source in soup.find_all('source'):
            source.decompose()
        for picture in soup.find_all('picture'):
            picture.unwrap()

        background_re = re.compile(
            r'url\(\s*(["\']?)(.*?)\1\s*\)', re.I
        )

        def replace_background(match):
            source = normalize_url(match.group(2))
            if not source:
                return 'none'
            asset_urls.append(source)
            return f'url("{source}")'

        for element in soup.find_all(style=True):
            element['style'] = background_re.sub(
                replace_background,
                str(element.get('style') or ''),
            )
        for style_node in soup.find_all('style'):
            if style_node.string:
                style_node.string.replace_with(background_re.sub(
                    replace_background,
                    str(style_node.string),
                ))
        for node in soup.find_all(['script', 'noscript', 'iframe']):
            node.decompose()

        content_root = soup.body or soup
        content_html = ''.join(str(node) for node in content_root.contents).strip()
        content_text = content_root.get_text('\n')
        content_text = '\n'.join(
            line.strip() for line in content_text.splitlines() if line.strip()
        )
        if not content_html:
            return None

        images = []
        seen = set()
        cookies = dict(signed_cookies or {})
        episode_no = self._global_novelpia_episode_id(chapter_url) or 'chapter'
        for image_index, image_url in enumerate(asset_urls, start=1):
            if not image_url or image_url in seen:
                continue
            seen.add(image_url)
            entry = {
                'url': image_url,
                'name': self._global_novelpia_image_name(
                    image_url, episode_no, image_index
                ),
            }
            if image_url.startswith('data:'):
                entry['data'] = image_url
            try:
                image_host = (
                    urllib.parse.urlparse(image_url).hostname or ''
                ).lower()
            except Exception:
                image_host = ''
            if cookies and image_host == 'pv-gn.novelpia.com':
                entry['_cookies'] = cookies
            images.append(entry)

        return {
            'chapterName': chapter_name,
            'sourceChapterName': chapter_name,
            'contentText': content_text,
            'contentHtml': (
                f'<div class="global-novelpia-content">{content_html}</div>'
            ),
            'contentCss': (
                '.global-novelpia-content p { margin: 0 0 0.75em; '
                'line-height: 1.8; }\n'
                '.global-novelpia-content img { max-width: 100%; height: auto; }'
            ),
            'images': images,
            'chapterUrl': chapter_url,
            '_imageCookies': cookies,
        }

    def _global_novelpia_parse_chapter(self, chapter_url, chapter_name):
        """Fetch a Global Novelpia ticket, content payload, and signed images."""
        episode_no = self._global_novelpia_episode_id(chapter_url)
        if not episode_no:
            self.log(f'  [Global Novelpia] Invalid chapter URL: {chapter_url}')
            return None

        if self._global_novelpia_session is None:
            self._global_novelpia_ensure_session()
        auth_refreshed = False
        ad_attempted = False
        for _request_attempt in range(4):
            session = self._global_novelpia_clone_session()
            if session is None:
                return None
            login_at = self._global_novelpia_login_at
            try:
                status, ticket = self._global_novelpia_request_json(
                    session,
                    f'{self._GLOBAL_NOVELPIA_API}/v1/novel/episode',
                    params={'episode_no': episode_no},
                    login_at=login_at,
                    max_retries=4,
                )
                ticket_message = self._global_novelpia_payload_message(
                    ticket
                ).lower()
                auth_expired = (
                    status in (401, 403)
                    or 'logged in' in ticket_message
                    or 'login' in ticket_message
                    or (
                        'token' in ticket_message
                        and 'expire' in ticket_message
                    )
                )
                if auth_expired and not auth_refreshed:
                    auth_refreshed = True
                    if self._global_novelpia_refresh_login(login_at):
                        continue
                if self._global_novelpia_ad_required(status, ticket):
                    if ad_attempted:
                        self.log(
                            '  [Global Novelpia] Advertisement completion was '
                            f'not accepted for {chapter_name}.'
                        )
                        return {
                            '_locked': True,
                            '_ad_required': True,
                            'chapterName': chapter_name,
                        }
                    ad_attempted = True
                    novel_no = (
                        self._global_novelpia_ad_novel_id(ticket)
                        or str((self._book_data or {}).get(
                            '_global_novelpia_novel_id', ''
                        ))
                    )
                    if self._global_novelpia_complete_ad(
                        novel_no, episode_no, chapter_name
                    ):
                        continue
                    return {
                        '_locked': True,
                        '_ad_required': True,
                        'chapterName': chapter_name,
                    }
                if status >= 400 or not status:
                    if self._global_novelpia_access_denied(status, ticket):
                        self.log(
                            f'  [Global Novelpia] LOCKED or login required: '
                            f'{chapter_name}'
                        )
                        return {'_locked': True, 'chapterName': chapter_name}
                    message = self._global_novelpia_payload_message(ticket)
                    self.log(
                        f'  [Global Novelpia] Ticket failed for {chapter_name} '
                        f'(HTTP {status or "network"})'
                        + (f': {message}' if message else '.')
                    )
                    return None

                token, direct_url = self._global_novelpia_extract_ticket_token(
                    ticket
                )
                if not token and not direct_url:
                    if self._global_novelpia_access_denied(status, ticket):
                        return {'_locked': True, 'chapterName': chapter_name}
                    self.log(
                        f'  [Global Novelpia] Ticket contained no content '
                        f'token: {chapter_name}'
                    )
                    return None
                content_url = (
                    direct_url
                    or f'{self._GLOBAL_NOVELPIA_API}/v1/novel/episode/content'
                )
                content_status, content_payload = (
                    self._global_novelpia_request_json(
                        session,
                        content_url,
                        params=None if direct_url else {'_t': token},
                        max_retries=3,
                    )
                )
                if content_status >= 400 or not content_status:
                    if self._global_novelpia_access_denied(
                        content_status, content_payload
                    ):
                        return {'_locked': True, 'chapterName': chapter_name}
                    message = self._global_novelpia_payload_message(
                        content_payload
                    )
                    self.log(
                        f'  [Global Novelpia] Content failed for {chapter_name} '
                        f'(HTTP {content_status or "network"})'
                        + (f': {message}' if message else '.')
                    )
                    return None
                raw_html = self._global_novelpia_raw_content(content_payload)
                if not raw_html:
                    self.log(
                        f'  [Global Novelpia] Empty content: {chapter_name}'
                    )
                    return None
                result = self._global_novelpia_build_chapter_result(
                    raw_html,
                    chapter_name,
                    chapter_url,
                    self._global_novelpia_signed_image_cookies(ticket),
                )
                return result
            finally:
                session.close()
        return None

    def _global_novelpia_parse_chapter_batch(
        self, batch_info, success_callback=None
    ):
        """Fetch one External Downloader batch through pia-scrap's API path."""
        if not batch_info:
            return []
        self._global_novelpia_ensure_session()
        from concurrent.futures import ThreadPoolExecutor

        def fetch_one(item):
            index, chapter = item
            if self._stop_requested:
                return None
            result = self._global_novelpia_parse_chapter(
                chapter.get('url', ''),
                chapter.get('fullName', '') or chapter.get('name', ''),
            )
            if (
                result
                and not result.get('_locked')
                and success_callback is not None
            ):
                try:
                    success_callback(index, result)
                except Exception:
                    pass
            return result

        with ThreadPoolExecutor(max_workers=max(1, len(batch_info))) as executor:
            return list(executor.map(fetch_one, enumerate(batch_info)))

    # ------------------------------------------------------------------
    # Ridibooks webnovel scraper (ported from the contributed ridi-dl extension)
    # ------------------------------------------------------------------
    @staticmethod
    def _ridi_book_id(url):
        try:
            path = urllib.parse.urlparse(url or '').path or ''
        except Exception:
            return ''
        match = re.search(r'/books/(\d+)', path, re.I)
        return match.group(1) if match else ''

    @staticmethod
    def _ridi_is_viewer_url(url):
        try:
            path = urllib.parse.urlparse(url or '').path or ''
        except Exception:
            return False
        return bool(re.search(r'/books/\d+/view(?:/)?$', path, re.I))

    @staticmethod
    def _ridi_image_name(image_url, chapter_id, image_index):
        """Build a stable EPUB-safe name for a Ridi chapter image."""
        try:
            parsed = urllib.parse.urlparse(image_url or '')
            name = urllib.parse.unquote(os.path.basename(parsed.path))
        except Exception:
            name = ''
        name = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('._')
        if not name or not re.search(r'\.[A-Za-z0-9]{2,5}$', name):
            name = f'ridi_{chapter_id or "chapter"}_{image_index}.jpg'
        return name

    @staticmethod
    def _ridi_page_is_usable(page):
        """Treat a disconnected Playwright page as unavailable."""
        if page is None:
            return False
        checker = getattr(page, 'is_closed', None)
        if checker is None:
            # Lightweight test/fallback page objects do not expose is_closed.
            return True
        try:
            return not checker()
        except Exception:
            return False

    @staticmethod
    def _ridi_browser_closed_error(error):
        message = str(error or '').lower()
        return any(fragment in message for fragment in (
            'target page, context or browser has been closed',
            'browser has been closed',
            'context has been closed',
            'page has been closed',
            'connection closed',
            'browser disconnected',
        ))

    def _ridi_connect_cdp(self, port):
        """Attach to the installed-Chrome session used for Ridi.

        Ridi currently challenges the bundled headless Chromium build.  The
        contributed extension runs in the user's normal Chrome session, so
        the native port needs to preserve that browser/fingerprint boundary
        as well as the profile's Ridi login cookies.
        """
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(
                f'http://127.0.0.1:{port}'
            )
            self._context = (
                self._browser.contexts[0]
                if self._browser.contexts
                else self._browser.new_context()
            )
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            self._page.on('console', self._on_console)
            self._ridi_chrome = True
            self._ridi_cdp_port = port
            self.log('[Ridi] Installed-Chrome session ready.')
            return True
        except Exception as e:
            self.log(f'ERROR: [Ridi] Could not attach to Chrome: {e}')
            self.cleanup()
            return False

    def _start_ridi_browser(self, start_url):
        """Run Ridi in installed Chrome with the saved external profile."""
        if self._context and self._page:
            try:
                self._page.evaluate('1')
                if self._ridi_chrome:
                    return True
                self.cleanup()
            except Exception:
                self.cleanup()
        elif self._context or self._browser or self._chrome_process:
            self.cleanup()

        user_data_dir = self._get_user_data_dir()
        self.log(
            '[Ridi] Starting headed installed Chrome off-screen with the '
            'External Downloader profile...'
        )
        self.log(f'Browser profile: {user_data_dir}')

        locked_pids = self._chrome_processes_using_profile(user_data_dir)
        if locked_pids:
            visible = self._visible_browser_window_pids(locked_pids)
            ports = (
                []
                if visible
                else self._chrome_remote_debugging_ports_using_profile(
                    user_data_dir
                )
            )
            for port in ports:
                if self._wait_for_cdp(port, timeout=2):
                    self.log(
                        '[Ridi] Reusing the existing External Downloader '
                        'browser session.'
                    )
                    return self._ridi_connect_cdp(port)

            pids = ', '.join(str(pid) for pid in locked_pids)
            self.log(
                '[Ridi] The External Downloader browser profile is already '
                f'open in process(es): {pids}'
            )
            self.log(
                '[Ridi] Close the Enter Browser window, then click Download '
                'again.'
            )
            return False

        proc, port = self._open_system_chrome(
            start_url,
            remote_debugging=True,
            user_data_dir=user_data_dir,
            hidden=False,
            window_size=(1280, 900),
            window_position=(-32000, -32000),
        )
        if not proc or not port:
            self.log('ERROR: [Ridi] Installed Chrome/Edge was not found.')
            return False

        self._chrome_process = proc
        ready = False
        deadline = time.time() + 15
        while time.time() < deadline:
            self._park_chrome_windows_for_profile(user_data_dir)
            if self._wait_for_cdp(port, timeout=0.75):
                ready = True
                break
        if not ready:
            self.log('ERROR: [Ridi] External browser did not become ready.')
            self.cleanup()
            return False

        self._park_chrome_windows_for_profile(user_data_dir)
        return self._ridi_connect_cdp(port)

    @staticmethod
    def _ridi_api_title(payload):
        """Read a usable episode title from one book-api response."""
        title = (payload or {}).get('title')
        if isinstance(title, str):
            return title.strip()
        if isinstance(title, dict):
            for key in ('main', 'full', 'name', 'short'):
                value = title.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ''

    @staticmethod
    def _ridi_api_next_id(payload):
        prop = ((payload or {}).get('series') or {}).get('property') or {}
        next_books = prop.get('next_books') or {}
        if not isinstance(next_books, dict):
            return ''
        for value in next_books.keys():
            value = str(value or '').strip()
            if value.isdigit():
                return value
        return ''

    def _ridi_open_api_book(self, book_id):
        """Open the API as a top-level document and return its first JSON.

        A top-level navigation is deliberately used here.  Fetching the API
        from the Ridi product page is blocked by that page's CSP, while a raw
        Python/APIRequestContext client gets a separate Cloudflare challenge.
        Installed Chrome navigation uses the same browser identity as the
        contributed extension and establishes a same-origin API page for the
        remaining requests.
        """
        if not self._context:
            raise RuntimeError('Ridi browser context is unavailable')
        page = self._context.new_page()
        page.on('console', self._on_console)
        api_url = f'https://book-api.ridibooks.com/books/{book_id}'
        try:
            page.goto(
                api_url,
                wait_until='domcontentloaded',
                timeout=45000,
                referer=f'https://ridibooks.com/books/{book_id}',
            )
            deadline = time.monotonic() + 30
            last_text = ''
            while time.monotonic() < deadline and not self._stop_requested:
                try:
                    last_text = page.locator('body').inner_text(timeout=3000)
                except Exception:
                    last_text = ''
                stripped = last_text.lstrip()
                if stripped.startswith('{'):
                    return page, json.loads(stripped)
                page.wait_for_timeout(500)
            title = ''
            try:
                title = page.title()
            except Exception:
                pass
            if 'just a moment' in title.lower() or 'cloudflare' in last_text.lower():
                raise RuntimeError(
                    'Cloudflare verification did not complete. Use Enter '
                    'Browser on the Ridi URL, finish verification, close the '
                    'browser, and retry.'
                )
            raise RuntimeError('book API did not return JSON')
        except Exception:
            try:
                page.close()
            except Exception:
                pass
            raise

    def _ridi_api_fetch_book(self, api_page, book_id, retries=3):
        """Fetch one book through the API page's same-origin context."""
        for attempt in range(1, max(1, int(retries)) + 1):
            if self._stop_requested:
                return None
            if not self._ridi_page_is_usable(api_page):
                return None
            try:
                response = api_page.evaluate(
                    """
                    async ({bookId}) => {
                      try {
                        const result = await fetch(`/books/${bookId}`, {
                          credentials: 'include',
                          headers: {Accept: 'application/json'},
                        });
                        return {
                          status: result.status,
                          text: await result.text(),
                        };
                      } catch (error) {
                        return {status: 0, error: String(error || '')};
                      }
                    }
                    """,
                    {'bookId': str(book_id)},
                ) or {}
                status = int(response.get('status') or 0)
                if 200 <= status < 300:
                    return json.loads(response.get('text') or '{}')
                delay = 2 * attempt if status == 429 else attempt
            except Exception as e:
                if self._ridi_browser_closed_error(e):
                    return None
                delay = attempt
            if attempt < retries:
                try:
                    api_page.wait_for_timeout(delay * 1000)
                except Exception:
                    time.sleep(delay)
        return None

    @classmethod
    def _ridi_contiguous_catalog_ids(
        cls, series_id, total, rendered_links, first_data
    ):
        """Infer a serial catalog only when Ridi proves IDs are contiguous.

        Ridi's product page renders an initial run of episode links. For
        series whose official root payload reports the same consecutive next
        ID, that run safely establishes the numeric catalog pattern. The
        caller still validates the final generated ID against book-api before
        accepting this fast path.
        """
        series_id = str(series_id or '')
        if not series_id.isdigit() or total < 2 or total > 10000:
            return []
        rendered_ids = []
        for link in rendered_links or []:
            book_id = cls._ridi_book_id(link)
            if book_id and book_id not in rendered_ids:
                rendered_ids.append(book_id)
        if len(rendered_ids) < 2 or series_id not in rendered_ids:
            return []

        start = int(series_id)
        rendered_numbers = sorted(int(book_id) for book_id in rendered_ids)
        expected_rendered = list(range(start, start + len(rendered_numbers)))
        if rendered_numbers != expected_rendered:
            return []

        width = len(series_id)
        expected_next = str(start + 1).zfill(width)
        if cls._ridi_api_next_id(first_data) != expected_next:
            return []
        return [
            str(start + offset).zfill(width)
            for offset in range(total)
        ]

    @classmethod
    def _ridi_generated_catalog_titles(cls, first_title, ordered_ids):
        """Expand an API title such as ``Book 1화`` for the fast catalog."""
        first_title = str(first_title or '').strip()
        if not first_title or not ordered_ids:
            return {}
        match = re.match(
            r'^(.*?)(\d+)(\s*(?:화|회|권|episode|chapter|volume))(.*)$',
            first_title,
            re.I,
        )
        if not match:
            return {ordered_ids[0]: first_title}
        number = int(match.group(2))
        prefix, marker, suffix = match.group(1), match.group(3), match.group(4)
        return {
            book_id: f'{prefix}{number + index}{marker}{suffix}'
            for index, book_id in enumerate(ordered_ids)
        }

    def _ridi_discover_episode_chain(self, series_id, rendered_links=None):
        """Follow series.property.next_books outside the product-page CSP."""
        result = {
            'links': [],
            'titleById': {},
            'diagnostics': [],
            'isSerial': None,
            'error': '',
        }
        api_page = None
        try:
            api_page, first_data = self._ridi_open_api_book(series_id)
            prop = (first_data.get('series') or {}).get('property') or {}
            total = int(
                prop.get('total_book_count')
                or prop.get('opened_book_count')
                or 0
            )
            result['isSerial'] = bool(prop.get('is_serial'))
            result['diagnostics'].append(
                f'Series episode count: {total or "unknown"}'
            )

            ordered_ids = [str(series_id)]
            seen = set(ordered_ids)
            current_data = first_data
            first_title = self._ridi_api_title(first_data)
            if first_title:
                result['titleById'][str(series_id)] = first_title

            # A non-serial book must not be turned into a one-chapter
            # webnovel merely because the API request itself succeeded.
            if not result['isSerial']:
                return result

            # Most Ridi webnovels use a contiguous episode-ID range. Prove
            # that from the rendered initial run and the root API's next ID,
            # then validate the official final ID. This replaces hundreds of
            # sequential next_books requests with one validation request.
            fast_ids = self._ridi_contiguous_catalog_ids(
                series_id, total, rendered_links, first_data
            )
            if fast_ids:
                last_id = fast_ids[-1]
                last_data = self._ridi_api_fetch_book(
                    api_page, last_id, retries=2
                )
                last_prop = (
                    ((last_data or {}).get('series') or {}).get('property')
                    or {}
                )
                try:
                    last_total = int(
                        last_prop.get('total_book_count')
                        or last_prop.get('opened_book_count')
                        or 0
                    )
                except (TypeError, ValueError):
                    last_total = 0
                final_is_valid = bool(
                    last_data
                    and last_prop.get('is_serial')
                    and (not last_total or last_total == total)
                    and not self._ridi_api_next_id(last_data)
                )
                if final_is_valid:
                    ordered_ids = fast_ids
                    result['titleById'].update(
                        self._ridi_generated_catalog_titles(
                            first_title, ordered_ids
                        )
                    )
                    last_title = self._ridi_api_title(last_data)
                    if last_title:
                        result['titleById'][last_id] = last_title
                    result['links'] = [
                        f'https://ridibooks.com/books/{episode_id}/view'
                        for episode_id in ordered_ids
                    ]
                    result['diagnostics'].append(
                        f'Fast episode catalog: {len(ordered_ids)} '
                        'contiguous links validated.'
                    )
                    return result

            safety_limit = min(max(total or 10000, 1), 10000)
            while len(ordered_ids) < safety_limit and not self._stop_requested:
                next_id = self._ridi_api_next_id(current_data)
                if not next_id or next_id in seen:
                    break
                current_data = self._ridi_api_fetch_book(api_page, next_id)
                if not current_data:
                    # A long series can outlive a transient API tab or Chrome
                    # process. Reopen at the exact failed ID and continue the
                    # next_books chain instead of silently returning a partial
                    # catalog that leaves chapter workers with a dead context.
                    recovery_attempt = 0
                    while recovery_attempt < 3 and not current_data:
                        recovery_attempt += 1
                        try:
                            if api_page:
                                api_page.close()
                        except Exception:
                            pass
                        api_page = None
                        if not self._ridi_page_is_usable(self._page):
                            self.log(
                                '[Ridi] Browser session closed during episode '
                                'discovery; restarting it.'
                            )
                            if not self._start_ridi_browser(
                                f'https://ridibooks.com/books/{series_id}'
                            ):
                                break
                        try:
                            api_page, current_data = (
                                self._ridi_open_api_book(next_id)
                            )
                        except Exception:
                            current_data = None
                        if current_data:
                            result['diagnostics'].append(
                                'Episode API session recovered at '
                                f'{next_id}.'
                            )
                    if not current_data:
                        result['error'] = (
                            f'book API stopped after episode {len(ordered_ids)} '
                            f'({next_id}) after recovery attempts'
                        )
                        break
                seen.add(next_id)
                ordered_ids.append(next_id)
                episode_title = self._ridi_api_title(current_data)
                if episode_title:
                    result['titleById'][next_id] = episode_title
                if len(ordered_ids) % 20 == 0:
                    try:
                        api_page.wait_for_timeout(300)
                    except Exception:
                        # The next fetch performs the normal session recovery
                        # if Chrome disappeared during this courtesy pause.
                        time.sleep(0.3)

            result['links'] = [
                f'https://ridibooks.com/books/{episode_id}/view'
                for episode_id in ordered_ids
            ]
            result['diagnostics'].append(
                f'Chained episode links: {len(ordered_ids)}'
            )
            return result
        except Exception as e:
            result['error'] = str(e)
            return result
        finally:
            if api_page:
                try:
                    api_page.close()
                except Exception:
                    pass

    def _ridi_parse_book(self, url):
        """Read Ridi metadata and discover its complete webnovel episode chain."""
        book_id = self._ridi_book_id(url)
        if not book_id:
            self.log('[Ridi] ERROR: Invalid Ridibooks URL.')
            return None

        self._stop_requested = False
        book_url = f'https://ridibooks.com/books/{book_id}'
        # The bundled headless browser is currently challenged by Ridi's
        # Cloudflare configuration. Match the contributed Chrome extension's
        # execution environment by retaining the real installed-Chrome
        # profile for metadata, API discovery, and chapter rendering.
        if (
            not self._ridi_chrome
            or not self._ridi_page_is_usable(self._page)
        ):
            if not self._start_ridi_browser(book_url):
                return None
        if not self._ridi_page_is_usable(self._page):
            self.log('[Ridi] ERROR: Browser could not be started.')
            return None

        self.log(f'[Ridi] Opening: {book_url}')
        try:
            self._page.goto(
                book_url,
                wait_until='domcontentloaded',
                timeout=45000,
            )
            # Do not wait for the full load event: Ridi's analytics resources
            # can keep it pending for 15 seconds even though the server-rendered
            # metadata and series shell are already ready.
            wait_for_function = getattr(self._page, 'wait_for_function', None)
            if wait_for_function:
                try:
                    wait_for_function(
                        r"""
() => {
  if (!document.querySelector('h1, meta[property="og:title"]')) return false;
  if (document.querySelector('.serial_book_direct_view_button')) return true;
  return [...document.querySelectorAll('script:not([src])')].some((node) =>
    /"seriesId"\s*:\s*"\d+"/.test(node.textContent || '')
  );
}
""",
                        timeout=5000,
                    )
                except Exception:
                    pass
        except Exception as e:
            if 'ERR_ABORTED' not in str(e):
                self.log(f'[Ridi] ERROR: Page load failed: {e}')
                return None

        # Collect metadata, any rendered episode buttons, and the embedded
        # series root here. API chaining happens afterward in a separate
        # book-api origin page so this product page's CSP cannot block it.
        script = r"""
        async () => {
          const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
          const absolute = (value) => {
            try { return new URL(value || '', location.href).href; }
            catch (_) { return value || ''; }
          };
          const meta = (...names) => {
            for (const name of names) {
              const node = document.querySelector(
                `meta[property="${name}"], meta[name="${name}"]`
              );
              const value = node?.getAttribute('content');
              if (value && value.trim()) return value.trim();
            }
            return '';
          };
          const pickText = (selectors) => {
            for (const selector of selectors) {
              try {
                const node = document.querySelector(selector);
                const value = clean(node?.innerText || node?.textContent || '');
                if (value) return value;
              } catch (_) {}
            }
            return '';
          };

          // Metadata logic ported from scrapeNovelMeta() in ridi-dl.
          const jsonData = {
            author: '', description: '', image: '', title: '', publisher: ''
          };
          const descriptions = [];
          for (const script of document.querySelectorAll(
            'script[type="application/json"]'
          )) {
            let root;
            try { root = JSON.parse(script.textContent || ''); }
            catch (_) { continue; }
            const seen = new Set();
            (function walk(value) {
              if (!value || typeof value !== 'object' || seen.has(value)) return;
              seen.add(value);
              if (Array.isArray(value)) {
                for (const item of value) walk(item);
                return;
              }
              if (value.author != null && !jsonData.author) {
                if (typeof value.author === 'string' && value.author.trim()) {
                  jsonData.author = value.author.trim();
                } else if (typeof value.author === 'object') {
                  const names = Object.values(value.author)
                    .filter((item) => typeof item === 'string' && item.trim());
                  if (names.length) jsonData.author = names.join(', ');
                }
              }
              if (typeof value.description === 'string' && value.description.trim()) {
                descriptions.push(value.description);
              }
              if (typeof value.image === 'string'
                  && /ridicdn\.net\/cover/.test(value.image)
                  && !jsonData.image) {
                jsonData.image = value.image;
              }
              if (typeof value.title === 'string' && value.title.trim()
                  && value.title.length < 200 && !jsonData.title) {
                jsonData.title = value.title;
              }
              if (typeof value.publisher === 'string' && value.publisher.trim()
                  && !jsonData.publisher) {
                jsonData.publisher = value.publisher;
              }
              for (const key in value) {
                try { walk(value[key]); } catch (_) {}
              }
            })(root);
          }

          let bestDescription = '';
          let bestDescriptionScore = -Infinity;
          for (const description of descriptions) {
            if (/리디\s*가입|포인트|가입하면|이벤트|쿠폰|할인받|무료.?체험/.test(
              description
            )) continue;
            let score = description.length;
            if (/책\s*소개|작가\s*의\s*말|줄거리/.test(description)) score += 3000;
            if (/&lt;|<\/?[a-z][^>]*>/i.test(description)) score += 5000;
            if (/[.。…]{2,}\s*$/.test(description)) score -= 2000;
            if (score > bestDescriptionScore) {
              bestDescriptionScore = score;
              bestDescription = description;
            }
          }

          const synopsisFromScripts = () => {
            let best = null;
            const expression = /"description"\s*:\s*"((?:[^"\\]|\\.)*)"/g;
            for (const script of document.querySelectorAll('script')) {
              const source = script.textContent || '';
              if (!source.includes('description')) continue;
              expression.lastIndex = 0;
              let match;
              while ((match = expression.exec(source))) {
                let value;
                try { value = JSON.parse('"' + match[1] + '"'); }
                catch (_) { continue; }
                if (!value || !value.trim()) continue;
                if (/리디\s*가입|포인트|가입하면|이벤트|쿠폰|할인받|무료.?체험/.test(
                  value
                )) continue;
                const marker = /책\s*소개|작품\s*소개|작가\s*의\s*말|줄거리|<b>|&lt;/i.test(
                  value
                );
                const truncated = /[.。…]{2,}\s*$/.test(value);
                if (!marker && value.length < 60) continue;
                const score = value.length + (marker ? 100000 : 0)
                  + (truncated ? 0 : 5000);
                if (!best || score > best.score) best = {value, score};
              }
            }
            return best?.value || '';
          };
          const synopsisByHeading = () => {
            for (const heading of document.querySelectorAll('h1,h2,h3,h4')) {
              if (!/^(작품\s*소개|책\s*소개|줄거리|소개)$/.test(
                clean(heading.textContent)
              )) continue;
              let node = heading.nextElementSibling;
              for (let index = 0; node && index < 3;
                   index += 1, node = node.nextElementSibling) {
                if (node.tagName === 'BUTTON') continue;
                const value = clean(node.innerText || node.textContent);
                if (value.length > 4) return value;
              }
            }
            return '';
          };
          let synopsis = synopsisFromScripts() || synopsisByHeading()
            || bestDescription
            || meta('og:description', 'twitter:description', 'description');
          if (synopsis) {
            synopsis = synopsis
              .replace(/<br\s*\/?>/gi, '\n')
              .replace(/<\/(p|div|li)>/gi, '\n')
              .replace(/<[^>]+>/g, '')
              .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
              .replace(/&amp;/g, '&').replace(/&quot;/g, '"')
              .replace(/&#0?39;/g, "'").replace(/&nbsp;/g, ' ')
              .replace(/^\s*[<\[]\s*(책\s*소개|작품\s*소개)\s*[>\]]\s*/i, '')
              .replace(/\s*(더보기|접기)\s*$/, '')
              .replace(/\n{3,}/g, '\n\n')
              .trim();
          }

          const coverNode = document.querySelector(
            'img[src*="ridicdn.net/cover"], img[srcset*="ridicdn.net/cover"], '
            + '[class*="cover"] img, [class*="Cover"] img, .thumbnail img'
          );
          const cover = absolute(
            meta('og:image', 'twitter:image', 'twitter:image:src')
            || jsonData.image
            || coverNode?.currentSrc || coverNode?.src
            || (coverNode?.getAttribute('srcset') || '').split(',').pop()
              ?.trim().split(' ')[0]
            || coverNode?.getAttribute('data-src') || ''
          );
          const title = meta('og:title') || jsonData.title
            || pickText(['h1', '[class*="title"]', '.book_title'])
            || 'Untitled Novel';
          const author = jsonData.author || pickText([
            'a[href^="/author/"]', 'a[href*="/author/"]',
            '[class*="author"] a', '[class*="Author"]'
          ]) || meta('og:novel:author', 'book:author', 'author');

          // Save the cheaply available DOM titles before episode discovery.
          const titleById = {};
          const collectLinks = () => Array.from(document.querySelectorAll(
            '.serial_book_direct_view_button'
          )).map((node) => node.getAttribute('href')).filter(Boolean);
          for (const button of document.querySelectorAll(
            '.serial_book_direct_view_button'
          )) {
            const href = button.getAttribute('href') || '';
            const id = href.match(/\/books\/(\d+)/)?.[1];
            if (!id) continue;
            let node = button;
            let episodeTitle = '';
            for (let depth = 0; depth < 5 && node; depth += 1) {
              node = node.parentElement;
              if (!node) break;
              const candidate = node.querySelector(
                '[class*="title"], [class*="Title"], h3, h4, .book_title, strong'
              );
              if (candidate && clean(candidate.textContent)) {
                episodeTitle = clean(candidate.textContent);
                break;
              }
            }
            if (episodeTitle && episodeTitle.length <= 200) {
              titleById[id] = episodeTitle;
            }
          }

          let links = collectLinks();
          const diagnostics = [`Rendered episode links: ${links.length}`];
          let seriesId = '';
          for (const script of document.querySelectorAll('script:not([src])')) {
            const match = (script.textContent || '').match(
              /"seriesId"\s*:\s*"(\d+)"/
            );
            if (match) { seriesId = match[1]; break; }
          }
          if (!seriesId) seriesId = links[0]?.match(/\/books\/(\d+)/)?.[1] || '';
          if (!seriesId) seriesId = location.pathname.match(/\/books\/(\d+)/)?.[1] || '';
          diagnostics.push(`Series ID: ${seriesId || 'not found'}`);

          // A final DOM check preserves the extension's rendered-list fallback.
          const finalLinks = collectLinks();
          if (finalLinks.length > links.length) links = finalLinks;
          const seenLinks = new Set();
          links = links.map(absolute).filter((href) => {
            if (!href || seenLinks.has(href)) return false;
            seenLinks.add(href);
            return /\/books\/\d+\/view(?:[/?#]|$)/.test(href);
          });

          const tags = Array.from(document.querySelectorAll(
            '[class*="keyword"] a, [class*="tag"] a'
          )).map((node) => clean(node.textContent).replace(/^#/, ''))
            .filter(Boolean);
          return {
            title, author, synopsis, cover,
            publisher: jsonData.publisher || '',
            tags: [...new Set(tags)],
            links, titleById, seriesId, diagnostics,
          };
        }
        """
        try:
            meta = self._page.evaluate(script) or {}
        except Exception as e:
            self.log(f'[Ridi] ERROR: Book discovery failed: {e}')
            return None

        for line in meta.get('diagnostics') or []:
            self.log(f'[Ridi] {line}')

        links = meta.get('links') or []
        title_by_id = dict(meta.get('titleById') or {})
        series_id = str(meta.get('seriesId') or '')
        api_result = None
        if series_id and self._context:
            api_result = self._ridi_discover_episode_chain(
                series_id, rendered_links=links
            )
            for line in api_result.get('diagnostics') or []:
                self.log(f'[Ridi] {line}')
            api_links = api_result.get('links') or []
            if len(api_links) > len(links):
                links = api_links
            title_by_id.update(api_result.get('titleById') or {})
            if api_result.get('error'):
                self.log(
                    '[Ridi] Episode API warning: '
                    f"{api_result.get('error')}"
                )
        chapters = []
        seen = set()
        for chapter_number, raw_chapter_url in enumerate(links, start=1):
            chapter_url = urllib.parse.urljoin(book_url, raw_chapter_url)
            chapter_id = self._ridi_book_id(chapter_url)
            if not chapter_id or chapter_id in seen:
                continue
            seen.add(chapter_id)
            title = title_by_id.get(chapter_id) or f'Episode {chapter_number}'
            chapters.append({
                'id': chapter_id,
                'url': chapter_url,
                'name': title,
                'fullName': title,
                'isVIP': False,
                'isPaid': False,
                # Ridi's catalog does not reveal whether this browser profile
                # owns a chapter. The rendered viewer makes that determination.
                'isAccessible': True,
                '_ridiChapterNumber': chapter_number,
            })

        if not chapters:
            if api_result and api_result.get('error'):
                self.log(
                    '[Ridi] ERROR: Webnovel episode discovery failed. '
                    'Complete Ridi/Cloudflare verification with Enter Browser '
                    'and retry.'
                )
            else:
                self.log(
                    '[Ridi] ERROR: No webnovel episodes were found. This '
                    'scraper supports Ridi webnovels, not downloadable volume '
                    'ebooks.'
                )
            return None

        title = meta.get('title') or f'Ridi {book_id}'
        introduction = meta.get('synopsis') or ''
        data = {
            'bookname': title,
            'author': meta.get('author') or '',
            'coverUrl': meta.get('cover') or '',
            'description': introduction,
            'introduction': introduction,
            'introductionHTML': (
                '<p>' + html.escape(introduction).replace('\n', '<br/>\n') + '</p>'
                if introduction else ''
            ),
            'publisher': meta.get('publisher') or '',
            'bookUrl': book_url,
            'chapterCount': len(chapters),
            'chapters': chapters,
            'language': 'ko',
            'tags': meta.get('tags') or [],
            '_ridibooks': True,
            '_ridi_book_id': book_id,
        }
        self._book_data = data
        self._book_url = book_url
        self.log(
            f'[Ridi] Book: {title} by {data.get("author") or "?"} - '
            f'{len(chapters)} chapters'
        )
        return data

    def _ridi_content_state(self, page):
        """Return the contribution's viewer readiness signal."""
        try:
            return page.evaluate(r"""
            () => {
              const selectors = [
                'div.pages', '#viewer_contents', '[id^="ridi_c"]',
                'article.chapter', '.chapter'
              ];
              let content = null;
              for (const selector of selectors) {
                content = document.querySelector(selector);
                if (content) break;
              }
              const length = (content?.innerText || '').trim().length;
              return {
                complete: document.readyState === 'complete',
                found: !!content,
                ready: document.readyState === 'complete' && length > 500,
                length,
              };
            }
            """) or {}
        except Exception:
            return {}

    def _ridi_wait_for_content(self, page, timeout=30):
        """Wait until the rendered viewer text is both complete and stable."""
        deadline = time.monotonic() + max(1, timeout)
        last_length = -1
        stable_count = 0
        while time.monotonic() < deadline and not self._stop_requested:
            state = self._ridi_content_state(page)
            length = int(state.get('length') or 0)
            if state.get('ready') and length >= 500:
                if length == last_length:
                    stable_count += 1
                    if stable_count >= 2:
                        return True
                else:
                    stable_count = 0
                    last_length = length
            else:
                stable_count = 0
                last_length = -1
            try:
                page.wait_for_timeout(700)
            except Exception:
                time.sleep(0.7)
        return False

    def _ridi_detect_access_wall(self, page):
        """Classify Ridi purchase/login redirects without retrying them."""
        try:
            return page.evaluate(r"""
            () => {
              const selectors = [
                'div.pages', '#viewer_contents', '[id^="ridi_c"]',
                'article.chapter', '.chapter'
              ];
              if (selectors.some((selector) => document.querySelector(selector))) {
                return {wall: false, offViewer: false, hits: 0, url: location.href};
              }
              const offViewer = !/\/books\/\d+\/view(?:\/|$|\?|#)/.test(
                location.href
              );
              const text = (document.body?.innerText || '').slice(0, 6000);
              const keywords = ['구매', '대여', '로그인', '결제', '소장', '미리보기'];
              const hits = keywords.filter((keyword) => text.includes(keyword)).length;
              return {
                wall: offViewer || hits >= 2,
                offViewer,
                hits,
                url: location.href,
              };
            }
            """) or {'wall': False}
        except Exception:
            return {'wall': False}

    def _ridi_extract_loaded_content(self, page, fallback_title):
        """Extract and clean the currently rendered Ridi viewer document."""
        try:
            return page.evaluate(r"""
            (fallbackTitle) => {
              const stripInvisible = (value) => String(value || '').replace(
                /[\u200B-\u200F\u2028-\u202F\u2060-\u2069\uFEFF\u00AD]/g,
                ''
              );
              const selectors = [
                'div.pages', '#viewer_contents', '[id^="ridi_c"]',
                'article.chapter', '.chapter'
              ];
              let source = null;
              for (const selector of selectors) {
                source = document.querySelector(selector);
                if (source) break;
              }
              if (!source) return null;

              // Work on a clone: the contributed extension cleaned the live
              // node because its worker tab was disposable; our tab is reused.
              const content = source.cloneNode(true);
              let title = (
                document.querySelector('h2.wv-1xn0gxv')?.textContent || ''
              ).trim() || fallbackTitle;
              const imageUrls = [];
              for (const image of content.querySelectorAll('img[src]')) {
                const original = image.getAttribute('src');
                if (!original) continue;
                let absolute = original;
                try { absolute = new URL(original, location.href).href; }
                catch (_) { continue; }
                image.setAttribute('src', absolute);
                imageUrls.push({original, absolute});
              }

              content.querySelectorAll('.content_footer').forEach(
                (node) => node.remove()
              );
              const chapterHeader = content.querySelector('.chapter-header');
              if (chapterHeader) {
                const heading = chapterHeader.querySelector('h1,h2,h3,h4');
                const value = (heading?.textContent || '').trim();
                if (value && value.length < 200) title = value;
                chapterHeader.remove();
              } else {
                const heading = content.querySelector('h1,h2,h3');
                const value = (heading?.textContent || '').trim();
                if (value && value.length < 200) {
                  title = value;
                  heading.remove();
                } else {
                  const firstParagraph = content.querySelector('p');
                  const bold = firstParagraph?.querySelector('strong,b');
                  const paragraphText = (firstParagraph?.textContent || '').trim();
                  const boldText = (bold?.textContent || '').trim();
                  if (bold && paragraphText === boldText
                      && boldText && boldText.length < 200) {
                    title = boldText;
                    firstParagraph.remove();
                  }
                }
              }

              content.querySelectorAll(
                'script,style,iframe,noscript'
              ).forEach((node) => node.remove());
              content.querySelectorAll('pre').forEach((node) => {
                if (!(node.textContent || '').trim()) node.remove();
              });
              content.querySelectorAll('hr').forEach((node) => node.remove());

              content.querySelectorAll('div.ridiborder').forEach((node) => {
                const blockquote = document.createElement('blockquote');
                while (node.firstChild) blockquote.appendChild(node.firstChild);
                node.parentNode.replaceChild(blockquote, node);
              });
              content.querySelectorAll('.ridicenter').forEach((node) => {
                const paragraph = document.createElement('p');
                paragraph.setAttribute('align', 'center');
                while (node.firstChild) paragraph.appendChild(node.firstChild);
                node.parentNode.replaceChild(paragraph, node);
              });
              for (const selector of ['article', '.aegix', '.chapter-content']) {
                content.querySelectorAll(selector).forEach((node) => {
                  while (node.firstChild) {
                    node.parentNode.insertBefore(node.firstChild, node);
                  }
                  node.remove();
                });
              }

              content.querySelectorAll('*').forEach((node) => {
                node.removeAttribute('id');
                node.removeAttribute('class');
                node.removeAttribute('style');
                for (const attribute of [...node.attributes]) {
                  if (attribute.name.startsWith('data-')) {
                    node.removeAttribute(attribute.name);
                  }
                }
              });
              const walker = document.createTreeWalker(
                content, NodeFilter.SHOW_TEXT
              );
              const textNodes = [];
              while (walker.nextNode()) textNodes.push(walker.currentNode);
              for (const node of textNodes) {
                node.nodeValue = stripInvisible(node.nodeValue);
                if (!node.nodeValue) node.remove();
              }
              content.querySelectorAll('div').forEach((node) => {
                if (!(node.textContent || '').trim() && !node.querySelector('img')) {
                  node.remove();
                }
              });

              let contentHtml = content.innerHTML
                .replace(/<!--.*?-->/gs, '')
                .replace(/<br\s*>/gi, '<br/>')
                .replace(/<img([^>]*?)(?<!\/)>/gi, '<img$1/>')
                .replace(/\s+/g, ' ')
                .replace(/>\s+</g, '>\n<');
              contentHtml = stripInvisible(contentHtml).trim();
              let blocks = [...content.querySelectorAll(
                'p,h1,h2,h3,h4,h5,h6,li,blockquote'
              )].map((node) => stripInvisible(node.textContent).replace(
                /[ \t\f\v]+/g, ' '
              ).trim()).filter(Boolean);
              if (!blocks.length) {
                blocks = stripInvisible(content.textContent).split(/\n+/)
                  .map((line) => line.replace(/\s+/g, ' ').trim())
                  .filter(Boolean);
              }
              return {
                title: stripInvisible(title).trim() || fallbackTitle,
                content: contentHtml,
                contentText: blocks.join('\n'),
                imageUrls,
              };
            }
            """, fallback_title) or None
        except Exception as e:
            self.log(f'  [Ridi] Chapter extraction failed: {fallback_title}: {e}')
            return None

    def _ridi_build_chapter_result(
        self,
        payload,
        chapter_name,
        chapter_url,
    ):
        """Convert the cleaned extension payload to External Downloader data."""
        if not isinstance(payload, dict) or not payload.get('content'):
            return None
        content_html = payload.get('content') or ''
        images = []
        seen = set()
        chapter_id = self._ridi_book_id(chapter_url)
        for image_index, item in enumerate(payload.get('imageUrls') or [], start=1):
            if not isinstance(item, dict):
                continue
            original = html.unescape(str(item.get('original') or '')).strip()
            absolute = urllib.parse.urljoin(
                chapter_url,
                html.unescape(str(item.get('absolute') or original)).strip(),
            )
            if not absolute or absolute in seen:
                continue
            seen.add(absolute)
            if original and original != absolute:
                content_html = content_html.replace(original, absolute)
                content_html = content_html.replace(
                    html.escape(original, quote=True),
                    html.escape(absolute, quote=True),
                )
            images.append({
                'url': absolute,
                'name': self._ridi_image_name(
                    absolute,
                    chapter_id,
                    image_index,
                ),
            })
        display_name = payload.get('title') or chapter_name
        return {
            'chapterName': display_name,
            'sourceChapterName': chapter_name,
            'contentText': payload.get('contentText') or '',
            'contentHtml': f'<div class="ridi-content">{content_html}</div>',
            'contentCss': (
                '.ridi-content p { margin: 0 0 0.75em; line-height: 1.8; }\n'
                '.ridi-content blockquote { margin: 1em 1.5em; padding: 0.5em 1em; '
                'border-left: 0.25em solid #999; }\n'
                '.ridi-content img { max-width: 100%; height: auto; }'
            ),
            'images': images,
            'chapterUrl': chapter_url,
        }

    def _ridi_finish_loaded_chapter(self, page, chapter_url, chapter_name):
        """Validate, retry, and extract a Ridi page after navigation starts."""
        # Never interpret the last URL cached on a dead Playwright page. A
        # disconnected page commonly retains the series product URL, which
        # previously made a browser crash look like a paid-chapter redirect.
        if not self._ridi_page_is_usable(page):
            return None
        try:
            page.wait_for_load_state('load', timeout=30000)
        except Exception as e:
            if (
                self._ridi_browser_closed_error(e)
                or not self._ridi_page_is_usable(page)
            ):
                return None

        if not self._ridi_page_is_usable(page):
            return None

        try:
            current_url = page.url or ''
        except Exception:
            return None
        if current_url and not self._ridi_is_viewer_url(current_url):
            self.log(
                f'  [Ridi] LOCKED or unpurchased (redirected to {current_url}): '
                f'{chapter_name}'
            )
            return {'_locked': True, 'chapterName': chapter_name}

        content_ready = False
        for attempt in range(1, 4):
            if self._stop_requested:
                return None
            if not self._ridi_page_is_usable(page):
                return None
            if self._ridi_wait_for_content(page, timeout=30):
                content_ready = True
                break
            if not self._ridi_page_is_usable(page):
                return None
            wall = self._ridi_detect_access_wall(page)
            if wall.get('wall'):
                self.log(
                    f'  [Ridi] LOCKED or unpurchased: {chapter_name} '
                    f'(redirect={bool(wall.get("offViewer"))}, '
                    f'purchase/login markers={wall.get("hits", 0)})'
                )
                return {'_locked': True, 'chapterName': chapter_name}
            self.log(
                f'  [Ridi] Content validation failed for {chapter_name} '
                f'(attempt {attempt}/3).'
            )
            if attempt < 3:
                try:
                    kwargs = {
                        'wait_until': 'domcontentloaded',
                        'timeout': 45000,
                    }
                    if self._book_url:
                        kwargs['referer'] = self._book_url
                    page.goto(chapter_url, **kwargs)
                    try:
                        page.wait_for_load_state('load', timeout=30000)
                    except Exception:
                        pass
                except Exception as e:
                    self.log(
                        f'  [Ridi] Reload warning for {chapter_name}: {e}'
                    )
                    if (
                        self._ridi_browser_closed_error(e)
                        or not self._ridi_page_is_usable(page)
                    ):
                        return None

        if not content_ready:
            self.log(f'  [Ridi] Viewer did not stabilize: {chapter_name}')
            return None

        if not self._ridi_page_is_usable(page):
            return None
        payload = self._ridi_extract_loaded_content(page, chapter_name)
        if not payload or not payload.get('content'):
            if not self._ridi_page_is_usable(page):
                return None
            wall = self._ridi_detect_access_wall(page)
            if wall.get('wall'):
                self.log(f'  [Ridi] LOCKED or unpurchased: {chapter_name}')
                return {'_locked': True, 'chapterName': chapter_name}
            self.log(f'  [Ridi] No chapter content extracted: {chapter_name}')
            return None

        return self._ridi_build_chapter_result(
            payload,
            chapter_name,
            chapter_url,
        )

    def _ridi_parse_chapter(self, chapter_url, chapter_name, page=None):
        """Open and extract one authenticated Ridi webnovel viewer page."""
        target = page or self._page
        for session_attempt in range(2):
            if not self._ridi_page_is_usable(target):
                if target is not None or session_attempt:
                    self.log(
                        '  [Ridi] Browser session closed before chapter load; '
                        'restarting it.'
                    )
                if not self._start_ridi_browser(
                    self._book_url or chapter_url
                ):
                    return None
                target = self._page
                if not self._ridi_page_is_usable(target):
                    return None

            navigation_failed = False
            try:
                kwargs = {
                    'wait_until': 'domcontentloaded',
                    'timeout': 45000,
                }
                if self._book_url:
                    kwargs['referer'] = self._book_url
                target.goto(chapter_url, **kwargs)
            except Exception as e:
                navigation_failed = True
                self.log(f'  [Ridi] Page load warning for {chapter_name}: {e}')
                if (
                    self._ridi_browser_closed_error(e)
                    or not self._ridi_page_is_usable(target)
                ):
                    target = None
                    continue

            # A timeout can occur after the viewer itself committed. It is safe
            # to finish that page only when it is the requested viewer. A stale
            # product URL after a failed goto is not evidence of a paywall.
            if navigation_failed:
                try:
                    current_url = target.url or ''
                except Exception:
                    return None
                if (
                    not self._ridi_is_viewer_url(current_url)
                    or self._ridi_book_id(current_url)
                    != self._ridi_book_id(chapter_url)
                ):
                    return None

            result = self._ridi_finish_loaded_chapter(
                target,
                chapter_url,
                chapter_name,
            )
            if (
                result is None
                and not self._ridi_page_is_usable(target)
                and not session_attempt
            ):
                target = None
                continue
            return result
        return None

    def _ridi_parallel_pages(self, count, _restarted=False):
        """Return up to the extension's four authenticated Ridi worker pages."""
        count = max(1, min(4, int(count or 1)))
        if not self._context or not self._ridi_page_is_usable(self._page):
            if not self._start_ridi_browser(
                self._book_url or 'https://ridibooks.com/'
            ):
                return []
        if not self._context or not self._ridi_page_is_usable(self._page):
            return []

        usable = []
        for worker in self._worker_pages:
            if self._ridi_page_is_usable(worker):
                usable.append(worker)
            else:
                try:
                    worker.close()
                except Exception:
                    pass
        self._worker_pages = usable
        needed = count - 1
        if len(self._worker_pages) > needed:
            for worker in self._worker_pages[needed:]:
                try:
                    worker.close()
                except Exception:
                    pass
            self._worker_pages = self._worker_pages[:needed]
        while len(self._worker_pages) < needed:
            try:
                worker = self._context.new_page()
                worker.on('console', self._on_console)
                self._worker_pages.append(worker)
            except Exception as e:
                self.log(f'  [Ridi] Worker page failed: {e}')
                if self._ridi_browser_closed_error(e) and not _restarted:
                    self.log(
                        '  [Ridi] Browser session closed while creating '
                        'chapter workers; it will be restarted.'
                    )
                    self.cleanup()
                    if self._start_ridi_browser(
                        self._book_url or 'https://ridibooks.com/'
                    ):
                        return self._ridi_parallel_pages(count, _restarted=True)
                break
        return ([self._page] + self._worker_pages)[:count]

    def _ridi_parse_chapter_batch_parallel(
        self, batch_info, interval=0, interval_max=None,
        success_callback=None,
    ):
        """Render Ridi chapters in a polite worker pool capped at four pages."""
        if not batch_info:
            return []
        results = [None] * len(batch_info)
        reported_successes = set()
        for chunk_start in range(0, len(batch_info), 4):
            if self._stop_requested:
                break
            chunk = batch_info[chunk_start:chunk_start + 4]
            chunk_results = {}
            for session_attempt in range(2):
                pages = self._ridi_parallel_pages(len(chunk))
                if not pages:
                    break
                active = []
                session_lost = False
                for offset, (page, chapter) in enumerate(zip(pages, chunk)):
                    url = chapter.get('url', '')
                    name = (
                        chapter.get('fullName', '')
                        or chapter.get('name', '')
                    )
                    navigation_failed = False
                    try:
                        kwargs = {'wait_until': 'commit', 'timeout': 15000}
                        if self._book_url:
                            kwargs['referer'] = self._book_url
                        page.goto(url, **kwargs)
                    except Exception as e:
                        navigation_failed = True
                        self.log(
                            f'  [Ridi] Page load warning for {name}: {e}'
                        )
                        if (
                            self._ridi_browser_closed_error(e)
                            or not self._ridi_page_is_usable(page)
                        ):
                            session_lost = True
                            break

                    if navigation_failed:
                        try:
                            current_url = page.url or ''
                        except Exception:
                            current_url = ''
                        if (
                            not self._ridi_is_viewer_url(current_url)
                            or self._ridi_book_id(current_url)
                            != self._ridi_book_id(url)
                        ):
                            continue
                    active.append((chunk_start + offset, page, url, name))

                if not session_lost:
                    for result_index, page, url, name in active:
                        if self._stop_requested:
                            break
                        value = self._ridi_finish_loaded_chapter(
                            page,
                            url,
                            name,
                        )
                        if (
                            value is None
                            and not self._ridi_page_is_usable(page)
                        ):
                            session_lost = True
                            break
                        chunk_results[result_index] = value
                        if (
                            value
                            and not value.get('_locked')
                            and success_callback is not None
                            and result_index not in reported_successes
                        ):
                            reported_successes.add(result_index)
                            try:
                                success_callback(result_index, value)
                            except Exception:
                                pass

                if not session_lost or self._stop_requested:
                    break
                if session_attempt == 0:
                    self.log(
                        '  [Ridi] Browser session closed during chapter '
                        'download; restarting this batch.'
                    )
                    self.cleanup()
                    if not self._start_ridi_browser(
                        self._book_url or chunk[0].get('url', '')
                    ):
                        break
                    chunk_results = {}

            for result_index, value in chunk_results.items():
                results[result_index] = value
            if (
                chunk_start + len(chunk) < len(batch_info)
                and not self._stop_requested
            ):
                self._sleep_interval(interval, interval_max)
        return results

    # ------------------------------------------------------------------
    # Novelpia native scraper
    # ------------------------------------------------------------------
    @staticmethod
    def _novelpia_novel_id(url):
        try:
            path = urllib.parse.urlparse(url or '').path or ''
        except Exception:
            return ''
        match = re.search(r'/(?:novel|viewer)/(\d+)', path)
        return match.group(1) if match else ''

    @staticmethod
    def _novelpia_chapter_id(url):
        try:
            path = urllib.parse.urlparse(url or '').path or ''
        except Exception:
            return ''
        match = re.search(r'/viewer/(\d+)', path)
        return match.group(1) if match else ''

    @staticmethod
    def _novelpia_parse_episode_html(source):
        """Convert one episode_list HTML response into chapter records."""
        source = source or ''
        pattern = re.compile(
            r'id=["\']bookmark_(\d+)["\'][^>]*>\s*</i>(.*?)</b>',
            re.I | re.S,
        )
        chapters = []
        seen = set()
        for match in pattern.finditer(source):
            chapter_id = match.group(1)
            if chapter_id in seen:
                continue
            seen.add(chapter_id)
            raw_title = re.sub(r'<[^>]+>', '', match.group(2))
            title = html.unescape(raw_title).strip()
            row_start = source.rfind('<tr', 0, match.start())
            row_end = source.find('</tr>', match.end())
            fragment = source[
                row_start if row_start >= 0 else match.start():
                row_end + 5 if row_end >= 0 else match.end()
            ]
            is_plus = bool(re.search(
                r'(?:\bPLUS\b|플러스|b_plus|icon[-_]?plus)',
                fragment,
                re.I,
            ))
            chapters.append({
                'id': chapter_id,
                'url': f'https://novelpia.com/viewer/{chapter_id}',
                'name': title or f'Chapter {len(chapters) + 1}',
                'fullName': title or f'Chapter {len(chapters) + 1}',
                'isVIP': is_plus,
                'isPaid': is_plus,
                # Access is determined by the authenticated browser when the
                # viewer is opened. Do not pre-emptively skip PLUS chapters.
                'isAccessible': True,
            })
        return chapters

    @staticmethod
    def _novelpia_parse_notice_html(source):
        """Convert the novel page's notice table into chapter records."""
        notices = []
        for index, notice in enumerate(
            parse_novelpia_notice_html(source), start=1
        ):
            chapter_id = notice['id']
            title = notice['title']
            notices.append({
                'id': chapter_id,
                'url': f'https://novelpia.com/viewer/{chapter_id}',
                'name': title,
                'fullName': title,
                'isVIP': False,
                'isPaid': False,
                'isAccessible': True,
                'isNotice': True,
                '_novelpiaNoticeNumber': index,
            })
        return notices

    @staticmethod
    def _novelpia_tag_chapter_result(result, chapter_info):
        """Carry notice/source-position metadata into parsed chapter data."""
        if not isinstance(result, dict):
            return result
        is_notice = bool(chapter_info.get('isNotice'))
        result.setdefault('_is_notice', is_notice)
        if is_notice:
            source_number = chapter_info.get('_novelpiaNoticeNumber')
        else:
            source_number = chapter_info.get('_novelpiaChapterNumber')
        if source_number is not None:
            result.setdefault('_chapter_number', source_number)
        return result

    def _novelpia_connect_cdp(self, port):
        """Attach the external scraper to its installed-Chrome session."""
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(
                f'http://127.0.0.1:{port}'
            )
            self._context = (
                self._browser.contexts[0]
                if self._browser.contexts
                else self._browser.new_context()
            )
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            self._page.on('console', self._on_console)
            self._novelpia_chrome = True
            self._novelpia_cdp_port = port
            self.log('[Novelpia] External browser session ready.')
            return True
        except Exception as e:
            self.log(f'ERROR: [Novelpia] Could not attach to Chrome: {e}')
            self.cleanup()
            return False

    def _start_novelpia_browser(self, start_url):
        """Reuse External Downloader's installed Chrome and saved profile."""
        if self._context and self._page:
            try:
                self._page.evaluate('1')
                if self._novelpia_chrome:
                    return True
                self.cleanup()
            except Exception:
                self.cleanup()
        elif self._context or self._browser or self._chrome_process:
            self.cleanup()

        user_data_dir = self._get_user_data_dir()
        self.log(
            '[Novelpia] Starting headed installed Chrome off-screen with the '
            'External Downloader profile...'
        )
        self.log(f'Browser profile: {user_data_dir}')

        locked_pids = self._chrome_processes_using_profile(user_data_dir)
        if locked_pids:
            visible = self._visible_browser_window_pids(locked_pids)
            ports = (
                []
                if visible
                else self._chrome_remote_debugging_ports_using_profile(
                    user_data_dir
                )
            )
            for port in ports:
                if self._wait_for_cdp(port, timeout=2):
                    self.log(
                        '[Novelpia] Reusing the existing External Downloader '
                        'browser session.'
                    )
                    return self._novelpia_connect_cdp(port)

            pids = ', '.join(str(pid) for pid in locked_pids)
            self.log(
                '[Novelpia] The External Downloader browser profile is '
                f'already open in process(es): {pids}'
            )
            self.log(
                '[Novelpia] Close the Enter Browser window, then click '
                'Download again.'
            )
            return False

        proc, port = self._open_system_chrome(
            start_url,
            remote_debugging=True,
            user_data_dir=user_data_dir,
            hidden=False,
        )
        if not proc or not port:
            self.log(
                'ERROR: [Novelpia] Installed Chrome/Edge was not found.'
            )
            return False

        self._chrome_process = proc
        ready = False
        deadline = time.time() + 15
        while time.time() < deadline:
            self._park_chrome_windows_for_profile(user_data_dir)
            if self._wait_for_cdp(port, timeout=0.75):
                ready = True
                break
        if not ready:
            self.log(
                'ERROR: [Novelpia] External browser did not become ready.'
            )
            self.cleanup()
            return False

        self._park_chrome_windows_for_profile(user_data_dir)
        return self._novelpia_connect_cdp(port)

    def _novelpia_parse_book(self, url):
        """Read Novelpia metadata and its paginated episode list."""
        novel_id = self._novelpia_novel_id(url)
        if not novel_id:
            self.log('[Novelpia] ERROR: Invalid novel URL.')
            return None

        self._stop_requested = False
        book_url = f'https://novelpia.com/novel/{novel_id}'
        if not self._start_novelpia_browser(book_url):
            return None

        self.log(f'[Novelpia] Opening: {book_url}')
        try:
            self._page.goto(
                book_url,
                wait_until='domcontentloaded',
                timeout=30000,
            )
        except Exception as e:
            if 'ERR_ABORTED' not in str(e):
                self.log(f'[Novelpia] ERROR: Page load failed: {e}')
                return None

        try:
            meta = self._page.evaluate("""
                () => {
                    const clean = (value) => (value || '')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    const meta = (selector) => {
                        const node = document.querySelector(selector);
                        return node ? (node.getAttribute('content') || '') : '';
                    };
                    const abs = (value) => {
                        try { return new URL(value || '', location.href).href; }
                        catch (e) { return value || ''; }
                    };
                    let title = '';
                    try {
                        if (typeof productName !== 'undefined') {
                            title = clean(productName);
                        }
                    } catch (e) {}
                    if (!title) {
                        title = clean(meta('meta[property="og:title"]'));
                        const parts = title.split(' - ');
                        title = clean(parts[parts.length - 1] || title);
                    }
                    const author = clean(
                        document.querySelector('.writer-name')?.textContent
                        || document.querySelector('[class*="writer"]')?.textContent
                        || ''
                    );
                    const synopsis = document.querySelector('.synopsis');
                    const introduction = clean(
                        synopsis?.innerText
                        || meta('meta[property="og:description"]')
                        || meta('meta[name="description"]')
                    );
                    const tags = Array.from(document.querySelectorAll('.tag'))
                        .map((node) => clean(node.textContent).replace(/^#/, ''))
                        .filter(Boolean);
                    const cover = abs(
                        meta('meta[property="og:image"]')
                        || document.querySelector(
                            'img.cover_img, img[class*="cover"]'
                        )?.getAttribute('src')
                        || ''
                    );
                    return {
                        title,
                        author,
                        introduction,
                        introductionHTML: synopsis ? synopsis.innerHTML : '',
                        tags,
                        cover,
                    };
                }
            """) or {}
        except Exception as e:
            self.log(f'[Novelpia] ERROR: Metadata extraction failed: {e}')
            return None

        page_html = ''
        try:
            page_html = self._page.content()
            meta['status'] = parse_novelpia_status(page_html)
        except Exception as e:
            self.log(f'[Novelpia] Status extraction failed: {e}')
        notices = []
        if self.novelpia_include_notices:
            try:
                notices = self._novelpia_parse_notice_html(
                    page_html
                )
            except Exception as e:
                # Notices are optional; a page-layout issue must not prevent
                # the regular episode list from being downloaded.
                self.log(f'[Novelpia] Author notice scan failed: {e}')
            if notices:
                self.log(
                    f'[Novelpia] Found {len(notices)} author notice(s).'
                )

        chapters = []
        seen = set()
        page_no = 0
        self.log('[Novelpia] Reading episode list...')
        while not self._stop_requested:
            try:
                response = self._page.evaluate(
                    """
                    async ({novelId, pageNo}) => {
                        const body = new URLSearchParams({
                            novel_no: novelId,
                            sort: 'DOWN',
                            page: String(pageNo),
                        });
                        const result = await fetch('/proc/episode_list', {
                            method: 'POST',
                            credentials: 'include',
                            headers: {
                                'Content-Type':
                                    'application/x-www-form-urlencoded; charset=UTF-8',
                                'X-Requested-With': 'XMLHttpRequest',
                            },
                            body,
                        });
                        return {
                            status: result.status,
                            text: await result.text(),
                        };
                    }
                    """,
                    {'novelId': novel_id, 'pageNo': page_no},
                ) or {}
            except Exception as e:
                self.log(
                    f'[Novelpia] ERROR: Episode page {page_no} failed: {e}'
                )
                return None

            if int(response.get('status') or 0) != 200:
                self.log(
                    '[Novelpia] ERROR: Episode list returned HTTP '
                    f"{response.get('status')} on page {page_no}."
                )
                return None
            source = response.get('text') or ''
            if 'Authentication required' in source:
                self.log(
                    '[Novelpia] Login required. Use Enter Browser, log in, '
                    'close that window, and retry.'
                )
                return None

            page_chapters = self._novelpia_parse_episode_html(source)
            new_count = 0
            for chapter in page_chapters:
                chapter_id = chapter['id']
                if chapter_id in seen:
                    continue
                seen.add(chapter_id)
                chapters.append(chapter)
                new_count += 1
            if not page_chapters or new_count == 0:
                break
            page_no += 1
            if page_no >= 1000:
                self.log(
                    '[Novelpia] ERROR: Episode pagination exceeded the '
                    'safety limit.'
                )
                return None

        if not chapters:
            self.log(
                '[Novelpia] ERROR: No chapters were found. Use Enter Browser '
                'to confirm the saved login session.'
            )
            return None

        for chapter_number, chapter in enumerate(chapters, start=1):
            chapter['_novelpiaChapterNumber'] = chapter_number

        title = meta.get('title') or f'Novelpia {novel_id}'
        data = {
            'bookname': title,
            'author': meta.get('author') or '',
            'coverUrl': meta.get('cover') or '',
            'introduction': meta.get('introduction') or '',
            'introductionHTML': meta.get('introductionHTML') or '',
            'bookUrl': book_url,
            'chapterCount': len(chapters),
            'chapters': notices + chapters,
            'noticeCount': len(notices),
            'language': 'ko',
            'tags': meta.get('tags') or [],
            '_novelpia': True,
            '_novelpia_novel_id': novel_id,
            'status': meta.get('status') or '',
        }
        self._book_data = data
        self._book_url = book_url
        self.log(
            f'[Novelpia] Book: {title} by '
            f"{data.get('author') or '?'} - {len(chapters)} chapters, "
            f'{len(notices)} author notice(s)'
        )
        return data

    def _novelpia_build_chapter_result(
        self,
        payload,
        chapter_name,
        chapter_url,
    ):
        """Validate viewer JSON and convert it to External Downloader data."""
        text = (
            payload.decode('utf-8', errors='replace')
            if isinstance(payload, bytes)
            else str(payload or '')
        )
        stripped = text.lstrip()
        if stripped[:128].lower().startswith(('<!doctype html', '<html')):
            self.log(
                f'  [Novelpia] Rejected HTML response: {chapter_name}'
            )
            return None
        try:
            data = json.loads(stripped)
        except (TypeError, json.JSONDecodeError) as e:
            self.log(
                f'  [Novelpia] Invalid viewer JSON: {chapter_name}: {e}'
            )
            return None
        if not isinstance(data, dict):
            self.log(
                f'  [Novelpia] Invalid viewer response: {chapter_name}'
            )
            return None

        status = data.get('status')
        code = data.get('code')

        def is_server_error(value):
            try:
                return int(value) == 500
            except (TypeError, ValueError):
                return False

        rejected = is_server_error(status) or is_server_error(code)
        if rejected:
            message = (
                data.get('errmsg')
                or data.get('message')
                or data.get('error')
                or 'server rejected viewer access'
            )
            self.log(
                f'  [Novelpia] Viewer rejected {chapter_name}: {message}'
            )
            return None

        segments = data.get('s')
        if not isinstance(segments, list) or not segments:
            self.log(
                f'  [Novelpia] Viewer returned no chapter content: '
                f'{chapter_name}'
            )
            return None

        html_parts = []
        plain_parts = []
        images = []
        seen_images = set()
        image_pattern = re.compile(
            r'<img\b[^>]*\bsrc=(["\'])(.*?)\1',
            re.I | re.S,
        )
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            segment_html = str(segment.get('text') or '')
            if not segment_html or 'cover-wrapper' in segment_html:
                continue
            html_parts.append(segment_html)

            plain = re.sub(
                r'<\s*br\s*/?\s*>|</\s*p\s*>',
                '\n',
                segment_html,
                flags=re.I,
            )
            plain = html.unescape(re.sub(r'<[^>]+>', '', plain))
            plain = re.sub(r'[ \t\f\v]+', ' ', plain)
            plain = re.sub(r'\n{3,}', '\n\n', plain).strip()
            if plain:
                plain_parts.append(plain)

            for image_index, match in enumerate(
                image_pattern.finditer(segment_html),
                start=1,
            ):
                image_url = urllib.parse.urljoin(
                    chapter_url,
                    html.unescape(match.group(2)).strip(),
                )
                if not image_url or image_url in seen_images:
                    continue
                seen_images.add(image_url)
                parsed = urllib.parse.urlparse(image_url)
                name = urllib.parse.unquote(
                    os.path.basename(parsed.path)
                ).split('?', 1)[0]
                name = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('._')
                if not name or not re.search(r'\.[A-Za-z0-9]{2,5}$', name):
                    chapter_id = self._novelpia_chapter_id(chapter_url)
                    name = (
                        f'novelpia_{chapter_id or "chapter"}_'
                        f'{image_index}.jpg'
                    )
                images.append({'url': image_url, 'name': name})

        content_html = ''.join(html_parts).strip()
        content_text = '\n'.join(plain_parts).strip()
        error_probe = f'{content_text}\n{content_html}'.lower()
        has_embedded_500 = bool(re.search(
            r'["\'](?:status|code)["\']\s*:\s*500',
            error_probe,
        ))
        if (
            not content_html
            or (
                has_embedded_500
                and ('잘못된 접근' in error_probe or 'errmsg' in error_probe)
            )
        ):
            self.log(
                f'  [Novelpia] Invalid rendered content rejected: '
                f'{chapter_name}'
            )
            return None

        return {
            'chapterName': chapter_name,
            'sourceChapterName': chapter_name,
            'contentText': content_text,
            'contentHtml': (
                f'<div class="novelpia-content">{content_html}</div>'
            ),
            'contentCss': (
                '.novelpia-content p { margin: 0 0 0.75em; '
                'line-height: 1.8; }\n'
                '.novelpia-content img { max-width: 100%; height: auto; }'
            ),
            'images': images,
        }

    def _novelpia_parse_chapter(
        self,
        chapter_url,
        chapter_name,
        page=None,
    ):
        """Open one viewer page and consume its single viewer_data response."""
        target = page or self._page
        if target is None:
            if not self._start_novelpia_browser(chapter_url):
                return None
            target = self._page

        chapter_id = self._novelpia_chapter_id(chapter_url)
        if not chapter_id:
            self.log(
                f'  [Novelpia] Invalid chapter URL: {chapter_url}'
            )
            return None

        response = None
        try:
            navigation_options = {
                'wait_until': 'domcontentloaded',
                'timeout': 30000,
            }
            if self._book_url:
                navigation_options['referer'] = self._book_url
            with target.expect_response(
                lambda item: bool(re.search(
                    rf'/proc/viewer_data/{re.escape(chapter_id)}'
                    r'(?:[/?#]|$)',
                    str(getattr(item, 'url', '') or ''),
                )),
                timeout=30000,
            ) as response_info:
                target.goto(
                    chapter_url,
                    **navigation_options,
                )
            response = response_info.value
        except Exception as e:
            self.log(
                f'  [Novelpia] Viewer response was not received: '
                f'{chapter_name}: {e}'
            )
            return None

        try:
            status = int(getattr(response, 'status', 0) or 0)
            payload = response.text()
        except Exception as e:
            self.log(
                f'  [Novelpia] Could not read viewer response: '
                f'{chapter_name}: {e}'
            )
            return None
        if status != 200:
            self.log(
                f'  [Novelpia] Viewer returned HTTP {status}: {chapter_name}'
            )
            return None

        return self._novelpia_build_chapter_result(
            payload,
            chapter_name,
            chapter_url,
        )

    def _munpia_connect_cdp(self, port):
        """Attach Playwright to an existing Munpia Chrome CDP port."""
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
            self._context = (
                self._browser.contexts[0]
                if self._browser.contexts
                else self._browser.new_context()
            )
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            self._page.on("console", self._on_console)
            self._munpia_chrome = True
            self._munpia_cdp_port = port
            self.log("[Munpia] Chrome session ready.")
            return True
        except Exception as e:
            self.log(f"ERROR: [Munpia] Could not attach to Chrome: {e}")
            self.cleanup()
            return False

    def _start_munpia_browser(self, start_url):
        """Launch installed Chrome with the saved profile for Munpia."""
        if self._context and self._page:
            try:
                self._page.evaluate("1")
                if self._munpia_chrome:
                    return True
                self.cleanup()
            except Exception:
                self.cleanup()
        elif self._context or self._browser or self._chrome_process:
            self.cleanup()

        user_data_dir = self._get_user_data_dir()
        self.log("[Munpia] Launching background Chrome with saved profile...")
        self.log(f"Browser profile: {user_data_dir}")

        locked_pids = self._chrome_processes_using_profile(user_data_dir)
        if locked_pids:
            visible = self._visible_browser_window_pids(locked_pids)
            ports = (
                []
                if visible
                else self._chrome_remote_debugging_ports_using_profile(
                    user_data_dir
                )
            )
            for port in ports:
                if self._wait_for_cdp(port, timeout=2):
                    self.log(
                        "[Munpia] Reusing existing hidden Chrome session."
                    )
                    return self._munpia_connect_cdp(port)

            pids = ", ".join(str(pid) for pid in locked_pids)
            self.log(
                "[Munpia] Browser profile is already open in process(es): "
                f"{pids}"
            )
            self.log(
                "[Munpia] Close the Npia login browser window, then retry "
                "the download."
            )
            return False

        proc, port = self._open_system_chrome(
            start_url,
            remote_debugging=True,
            user_data_dir=user_data_dir,
            hidden=True,
        )
        if not proc or not port:
            self.log(
                "ERROR: [Munpia] Installed Chrome/Edge was not found. "
                "Munpia blocks the bundled headless browser."
            )
            return False

        self._chrome_process = proc
        ready = False
        deadline = time.time() + 15
        while time.time() < deadline:
            self._hide_chrome_windows_for_profile(user_data_dir)
            if self._wait_for_cdp(port, timeout=0.75):
                ready = True
                break
        if not ready:
            self.log(
                "ERROR: [Munpia] Chrome remote debugging endpoint did not "
                "start."
            )
            self.cleanup()
            return False
        self._hide_chrome_windows_for_profile(user_data_dir)

        return self._munpia_connect_cdp(port)

    def _munpia_wait_for_selector(self, page, selector, timeout=45):
        """Wait through Munpia's security interstitial for a target selector."""
        deadline = time.time() + max(1, timeout)
        last_marker = ''
        while time.time() < deadline:
            try:
                if page.evaluate(
                    "(selector) => !!document.querySelector(selector)",
                    selector,
                ):
                    return True
                last_marker = page.evaluate("""
                    () => {
                        const clean = (value) => (value || '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        return clean(
                            document.querySelector('h1')?.textContent
                            || document.title
                            || document.body?.textContent?.slice(0, 120)
                            || ''
                        );
                    }
                """)
            except Exception:
                pass
            try:
                page.wait_for_timeout(1000)
            except Exception:
                time.sleep(1)
        if last_marker:
            self.log(f"[Munpia] Still waiting on page marker: {last_marker}")
        return False

    def _munpia_page_has_selector(self, page, selector):
        try:
            return bool(
                page
                and not page.is_closed()
                and page.evaluate(
                    "(selector) => !!document.querySelector(selector)",
                    selector,
                )
            )
        except Exception:
            return False

    def _munpia_parallel_pages(self, count, start_url):
        """Return one usable Munpia Chrome page per parallel chapter."""
        count = max(1, count)
        if not self._context or not self._page or not self._munpia_chrome:
            if not self._start_munpia_browser(start_url):
                return []

        if not self._page_is_usable(self._page):
            try:
                self._page = self._context.new_page()
                self._page.on("console", self._on_console)
            except Exception as e:
                self.log(f"  [Munpia] Could not create primary page: {e}")
                return []

        usable_workers = []
        for page in self._worker_pages:
            if self._page_is_usable(page):
                usable_workers.append(page)
            else:
                try:
                    page.close()
                except Exception:
                    pass
        self._worker_pages = usable_workers

        needed_workers = max(0, count - 1)
        if len(self._worker_pages) > needed_workers:
            for page in self._worker_pages[needed_workers:]:
                try:
                    page.close()
                except Exception:
                    pass
            self._worker_pages = self._worker_pages[:needed_workers]

        while len(self._worker_pages) < needed_workers:
            try:
                page = self._context.new_page()
                page.on("console", self._on_console)
                self._worker_pages.append(page)
                self._hide_chrome_windows_for_profile(
                    self._get_user_data_dir()
                )
            except Exception as e:
                self.log(f"  [Munpia] Worker page failed: {e}")
                break

        self._hide_chrome_windows_for_profile(self._get_user_data_dir())
        return ([self._page] + self._worker_pages)[:count]

    def _munpia_extract_chapters_from_current_page(self):
        """Extract Munpia episode rows from the current listing page."""
        return self._page.evaluate("""
            () => {
                const clean = (value) => (value || '')
                    .replace(/\\s+/g, ' ')
                    .trim();
                const abs = (value) => {
                    try { return new URL(value || '', location.href).href; }
                    catch (e) { return value || ''; }
                };
                const rows = [];
                document.querySelectorAll('#ENTRIES tbody tr').forEach((tr) => {
                    const indexCell = tr.querySelector('td.index');
                    const subjectCell = tr.querySelector('td.subject');
                    const link = subjectCell
                        ? subjectCell.querySelector('a[href*="neSrl"]')
                        : null;
                    const href = link ? abs(link.getAttribute('href')) : '';
                    let title = clean(link
                        ? link.textContent
                        : (subjectCell ? subjectCell.textContent : ''));
                    title = title.replace(/\\s+NEW$/i, '').trim();
                    const indexText = clean(indexCell ? indexCell.textContent : '');
                    const cells = Array.from(tr.children || []);
                    const markerText = clean(Array.from(
                        tr.querySelectorAll('img, span, em, i, button')
                    ).map((el) => [
                        el.getAttribute('alt') || '',
                        el.getAttribute('title') || '',
                        el.className || '',
                        el.textContent || '',
                    ].join(' ')).join(' '));
                    const classText = String(tr.className || '');
                    const markerHaystack = `${classText} ${markerText}`.toLowerCase();
                    const isNotice = /notice/i.test(classText)
                        || indexText === '공지'
                        || /공지/.test(indexText);
                    const locked = !href
                        || /(lock|locked|purchase|buy|rent|paid|coin|gold|유료|구매|대여|결제|캐시|골드)/i
                            .test(markerHaystack);
                    const paid = /(유료|구매|대여|결제|캐시|골드|gold|paid|coin)/i
                        .test(markerHaystack);
                    const neMatch = href.match(/neSrl\\/(\\d+)/);
                    const order = parseInt(indexText.replace(/[^0-9]/g, ''), 10);
                    if (!title && !href) return;
                    rows.push({
                        url: href,
                        name: title || `Episode ${rows.length + 1}`,
                        fullName: title || `Episode ${rows.length + 1}`,
                        indexText,
                        order: Number.isFinite(order) ? order : 0,
                        neSrl: neMatch ? neMatch[1] : '',
                        date: clean(cells[2] ? cells[2].textContent : ''),
                        views: clean(cells[3] ? cells[3].textContent : ''),
                        recommends: clean(cells[4] ? cells[4].textContent : ''),
                        pagesText: clean(cells[5] ? cells[5].textContent : ''),
                        isVIP: !!(paid || locked),
                        isPaid: !!paid,
                        isAccessible: !!href && !locked,
                        _munpiaNotice: isNotice,
                    });
                });
                return rows;
            }
        """) or []

    def _munpia_parse_book(self, url):
        """Scrape Munpia metadata and the paginated chapter list."""
        self._stop_requested = False
        novel_id = self._munpia_novel_id(url)
        if not novel_id:
            self.log("[Munpia] ERROR: Could not extract novel id from URL.")
            return None

        book_url = f"https://novel.munpia.com/{novel_id}"
        if not self._start_munpia_browser(book_url):
            return None

        self.log(f"[Munpia] Navigating to: {book_url}")
        try:
            self._page.goto(book_url, wait_until="domcontentloaded",
                            timeout=30000)
        except Exception as e:
            if 'ERR_ABORTED' in str(e):
                self.log(f"[Munpia] Page load warning: {e}")
            else:
                self.log(f"[Munpia] ERROR: Page load failed: {e}")
                return None
        if not self._munpia_wait_for_selector(self._page, '#ENTRIES'):
            self.log(
                "[Munpia] ERROR: Novel page did not pass Munpia's security "
                "check. Use Enter Browser to log in/verify, then retry."
            )
            return None

        try:
            meta = self._page.evaluate("""
                () => {
                    const clean = (value) => (value || '')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    const metaContent = (selector) => {
                        const el = document.querySelector(selector);
                        return el ? (el.getAttribute('content') || '') : '';
                    };
                    const abs = (value) => {
                        try { return new URL(value || '', location.href).href; }
                        catch (e) { return value || ''; }
                    };
                    const info = document.querySelector('#board .novel-info')
                        || document.querySelector('#board')
                        || document.body;
                    const title = clean(
                        info.querySelector('h2')?.textContent
                        || metaContent('meta[property="og:title"]')
                            .replace(/\\s*-\\s*웹소설\\s*문피아\\s*$/i, '')
                    );
                    const authorText = clean(
                        info.querySelector('.meta-author')?.textContent || ''
                    );
                    let author = '';
                    const authorMatch = authorText.match(
                        /글\\s+(.+?)(?:\\s+그림\\/삽화|\\s*$)/
                    );
                    if (authorMatch) {
                        author = clean(authorMatch[1]);
                    }
                    const story = document.querySelector('#STORY-BOX .story');
                    const introduction = clean(
                        story?.innerText
                        || metaContent('meta[name="description"]')
                        || metaContent('meta[property="og:description"]')
                    );
                    const coverNode = info.querySelector('.cover-box img.cover')
                        || info.querySelector('img.cover');
                    const cover = abs(
                        coverNode?.getAttribute('src')
                        || metaContent('meta[property="og:image"]')
                    );
                    const tags = Array.from(
                        document.querySelectorAll('#board .novel-tag-selected')
                    ).map((el) => clean(el.textContent).replace(/^#/, ''))
                        .filter(Boolean);
                    const stats = clean(Array.from(
                        document.querySelectorAll('#board .meta-etc')
                    ).map((el) => el.textContent || '').join(' '));
                    const countMatch = stats.match(/연재수\\s*:\\s*([0-9,]+)\\s*회/);
                    const declaredCount = countMatch
                        ? parseInt(countMatch[1].replace(/,/g, ''), 10)
                        : 0;
                    const pageNumbers = Array.from(document.querySelectorAll(
                        '#board .pagination a[href], #board .paging a[href], ' +
                        '#board a.home[href], #board a.prev[href], ' +
                        '#board a.next[href], #board a.end[href]'
                    )).map((a) => {
                        const match = abs(a.getAttribute('href'))
                            .match(/\\/page\\/(\\d+)(?:[/?#]|$)/);
                        return match ? parseInt(match[1], 10) : 0;
                    }).filter((n) => Number.isFinite(n) && n > 0);
                    return {
                        title,
                        author,
                        introduction,
                        introductionHTML: story ? story.innerHTML : '',
                        cover,
                        tags,
                        declaredCount,
                        lastPage: Math.max(1, ...pageNumbers),
                    };
                }
            """) or {}
        except Exception as e:
            self.log(f"[Munpia] ERROR: Metadata extraction failed: {e}")
            return None

        title = meta.get('title') or f"Munpia {novel_id}"
        self.log(
            f"[Munpia] Title: {title}, Author: {meta.get('author') or '?'}"
        )

        last_page = max(1, int(meta.get('lastPage') or 1))
        self.log(f"[Munpia] Fetching chapter list ({last_page} page(s))...")
        all_rows = []
        for page_no in range(1, last_page + 1):
            if self._stop_requested:
                break
            if page_no > 1:
                page_url = f"https://novel.munpia.com/{novel_id}/page/{page_no}"
                try:
                    self._page.goto(page_url, wait_until="domcontentloaded",
                                    timeout=30000)
                    self._munpia_wait_for_selector(
                        self._page, '#ENTRIES', timeout=15
                    )
                except Exception as e:
                    self.log(
                        f"[Munpia] WARNING: Page {page_no} load failed: {e}"
                    )
                    continue
            try:
                all_rows.extend(self._munpia_extract_chapters_from_current_page())
            except Exception as e:
                self.log(
                    f"[Munpia] WARNING: Page {page_no} parse failed: {e}"
                )

        chapters = []
        seen = set()
        for row in all_rows:
            if row.get('_munpiaNotice'):
                continue
            chapter_url = row.get('url') or ''
            key = row.get('neSrl') or chapter_url
            if not chapter_url or key in seen:
                continue
            seen.add(key)
            chapters.append(row)

        chapters.sort(key=lambda ch: (
            ch.get('order') or 10**12,
            int(ch.get('neSrl') or 0),
        ))

        self.log(f"[Munpia] Found {len(chapters)} chapter(s).")
        data = {
            'bookname': title,
            'author': meta.get('author') or '',
            'coverUrl': meta.get('cover') or '',
            'introduction': meta.get('introduction') or '',
            'introductionHTML': meta.get('introductionHTML') or '',
            'bookUrl': book_url,
            'chapterCount': len(chapters),
            'chapters': chapters,
            'language': 'ko',
            'tags': meta.get('tags') or [],
            '_munpia': True,
            '_munpia_novel_id': novel_id,
            '_munpia_declared_count': meta.get('declaredCount') or 0,
        }
        self._book_data = data
        self._book_url = book_url
        return data

    def _munpia_extract_loaded_chapter(self, target, chapter_name):
        """Extract Munpia chapter content from an already-loaded page."""
        try:
            data = target.evaluate("""
                () => {
                    const clean = (value) => (value || '')
                        .replace(/[\\u00a0\\t ]+/g, ' ')
                        .replace(/\\n\\s+/g, '\\n')
                        .trim();
                    const abs = (value) => {
                        try { return new URL(value || '', location.href).href; }
                        catch (e) { return value || ''; }
                    };
                    const entry = document.querySelector('#ENTRY-CONTENT');
                    const content = document.querySelector(
                        '#ENTRY-CONTENT .tcontent'
                    );
                    const heading = clean(
                        entry?.querySelector('.subinfo h3')?.textContent
                        || entry?.querySelector('h3')?.textContent
                        || document.querySelector('h4')?.textContent
                        || document.title.replace(/\\s*-\\s*.*$/, '')
                    );
                    if (!entry || !content) {
                        const bodyText = clean(document.body?.textContent || '');
                        return {
                            locked: /(로그인|구매|대여|결제|성인인증|권한|이용권|보안 점검)/
                                .test(bodyText),
                            chapterName: heading,
                            message: bodyText.slice(0, 240),
                        };
                    }

                    const clone = content.cloneNode(true);
                    clone.querySelectorAll(
                        '.dummy, script, style, noscript, iframe, button'
                    ).forEach((el) => el.remove());
                    clone.querySelectorAll('img').forEach((img) => {
                        const src = abs(img.getAttribute('src') || '');
                        if (!src || /blank\\.png/i.test(src)) {
                            img.remove();
                        } else {
                            img.setAttribute('src', src);
                        }
                    });

                    const images = Array.from(clone.querySelectorAll('img'))
                        .map((img, idx) => {
                            const src = abs(img.getAttribute('src') || '');
                            if (!src) return null;
                            let name = '';
                            try {
                                const parsed = new URL(src);
                                name = decodeURIComponent(
                                    parsed.pathname.split('/').pop() || ''
                                );
                            } catch (e) {
                                name = '';
                            }
                            name = name.split('?')[0].replace(
                                /[^A-Za-z0-9._-]+/g, '_'
                            ).replace(/^[_\\.]+|[_\\.]+$/g, '');
                            if (!name || !/\\.[A-Za-z0-9]{2,5}$/.test(name)) {
                                name = `munpia_image_${idx + 1}.jpg`;
                            }
                            return {url: src, name};
                        }).filter(Boolean);

                    let blocks = Array.from(clone.querySelectorAll(
                        'p, h1, h2, h3, h4, h5, h6, li, blockquote'
                    )).map((el) => clean(el.textContent || ''))
                        .filter(Boolean);
                    if (!blocks.length) {
                        blocks = clean(clone.textContent || '')
                            .split('\\n')
                            .map((line) => clean(line))
                            .filter(Boolean);
                    }
                    const contentText = blocks.join('\\n');
                    const contentHtml = clone.innerHTML.trim();
                    return {
                        locked: !contentText && !images.length,
                        chapterName: heading,
                        contentText,
                        contentHtml: `<div class="munpia-content">${contentHtml}</div>`,
                        images,
                    };
                }
            """) or {}
        except Exception as e:
            self.log(f"  [Munpia] Extract failed: {chapter_name}: {e}")
            return None

        if data.get('locked'):
            self.log(f"  [Munpia] Locked or unreadable: {chapter_name}")
            return {
                '_locked': True,
                'chapterName': data.get('chapterName') or chapter_name,
            }

        return {
            'chapterName': data.get('chapterName') or chapter_name,
            'sourceChapterName': chapter_name,
            'contentText': data.get('contentText') or '',
            'contentHtml': data.get('contentHtml') or '',
            'contentCss': (
                '.munpia-content p { margin: 0 0 0.75em; '
                'line-height: 1.8; }\\n'
                '.munpia-content img { max-width: 100%; height: auto; }'
            ),
            'images': data.get('images') or [],
        }

    def _munpia_parse_chapter(self, chapter_url, chapter_name, page=None):
        """Fetch one Munpia chapter from its server-rendered reader page."""
        target = page or self._page
        if target is None:
            if not self._start_munpia_browser(chapter_url):
                return None
            target = self._page

        try:
            target.goto(chapter_url, wait_until="domcontentloaded",
                        timeout=30000)
            self._munpia_wait_for_selector(
                target, '#ENTRY-CONTENT', timeout=20
            )
        except Exception as e:
            if 'ERR_ABORTED' in str(e):
                self.log(
                    f"  [Munpia] Page load warning for {chapter_name}: {e}"
                )
                self._munpia_wait_for_selector(
                    target, '#ENTRY-CONTENT', timeout=20
                )
            else:
                self.log(f"  [Munpia] Page load failed: {chapter_name}: {e}")
                return None

        return self._munpia_extract_loaded_chapter(target, chapter_name)

    def _munpia_parse_chapter_batch_parallel(self, batch_info):
        """Load a Munpia batch across multiple Chrome tabs/pages."""
        if not batch_info:
            return []

        first_url = batch_info[0].get('url', '') or self._book_url
        pages = self._munpia_parallel_pages(len(batch_info), first_url)
        if not pages:
            return [None] * len(batch_info)

        active = []
        for i, (page, ch) in enumerate(zip(pages, batch_info)):
            if self._stop_requested:
                break
            url = ch.get('url', '')
            name = ch.get('fullName', '') or ch.get('name', '')
            try:
                goto_kwargs = {
                    "wait_until": "commit",
                    "timeout": 15000,
                }
                if self._book_url:
                    goto_kwargs["referer"] = self._book_url
                page.goto(url, **goto_kwargs)
            except Exception as e:
                self.log(f"  [Munpia] Page load warning for {name}: {e}")
            self._hide_chrome_windows_for_profile(self._get_user_data_dir())
            active.append((i, page, name))

        results = [None] * len(batch_info)
        pending = {i for i, _, _ in active}
        active_by_index = {i: (page, name) for i, page, name in active}
        deadline = time.time() + 45

        while pending and time.time() < deadline and not self._stop_requested:
            self._hide_chrome_windows_for_profile(self._get_user_data_dir())
            for i in list(pending):
                page, _name = active_by_index[i]
                if self._munpia_page_has_selector(page, '#ENTRY-CONTENT'):
                    pending.remove(i)
            if pending:
                time.sleep(0.2)
        self._hide_chrome_windows_for_profile(self._get_user_data_dir())

        for i in sorted(pending):
            _page, name = active_by_index[i]
            self.log(f"  [Munpia] Timed out waiting for: {name}")

        for i, page, name in active:
            if self._stop_requested:
                continue
            results[i] = self._munpia_extract_loaded_chapter(page, name)

        return results

    def _kakao_parse_book(self, url):
        """Scrape book metadata + episode list from a KakaoPage content page.

        Uses Playwright DOM scraping instead of novel-downloader JS rules.
        Returns the standard book data dict or None on error.
        """
        if not self._page:
            self.start()

        self._stop_requested = False
        self.log(f"[KakaoPage] Navigating to: {url}")

        # Extract series ID from URL
        m = re.search(r'/content/(\d+)', url)
        series_id = m.group(1) if m else ''

        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Wait for the SPA to render content
            self._page.wait_for_timeout(3000)
        except Exception as e:
            self.log(f"ERROR: Page load failed: {e}")
            return None

        self.log("[KakaoPage] Extracting metadata...")

        # --- Extract metadata via JS ---
        try:
            meta = self._page.evaluate("""
                (function() {
                    var og = function(prop) {
                        var el = document.querySelector('meta[property="og:' + prop + '"]');
                        return el ? el.content : '';
                    };
                    // Title: try og:title first, then first h2
                    var title = og('title') || '';
                    // Clean "- 웹소설 | 카카오페이지" suffix from og:title
                    title = title.replace(/\\s*[-–]\\s*(웹소설|웹툰).*$/i, '').trim();
                    if (!title) {
                        var h2 = document.querySelector('h2');
                        if (h2) title = h2.innerText.trim();
                    }
                    // Author: look for text near the title
                    var author = '';
                    var spans = document.querySelectorAll('span, div, a');
                    for (var i = 0; i < spans.length; i++) {
                        var s = spans[i];
                        var t = s.innerText.trim();
                        // Author is typically a short name appearing after the title
                        if (s.previousElementSibling) {
                            var prev = s.previousElementSibling;
                            if (prev.tagName === 'H2' ||
                                (prev.innerText && prev.innerText.trim() === title)) {
                                if (t.length > 0 && t.length < 50 && !t.includes('|')) {
                                    author = t;
                                    break;
                                }
                            }
                        }
                    }
                    // Fallback: look for author pattern in page text
                    if (!author) {
                        var bodyText = document.body.innerText;
                        // Pattern: title followed by author name on next line
                        var idx = bodyText.indexOf(title);
                        if (idx >= 0) {
                            var after = bodyText.substring(idx + title.length, idx + title.length + 200);
                            var lines = after.split('\\n').filter(function(l) {
                                return l.trim().length > 0;
                            });
                            if (lines.length > 0 && lines[0].trim().length < 50) {
                                author = lines[0].trim();
                            }
                        }
                    }
                    var cover = og('image') || '';
                    var desc = og('description') || '';

                    // Total episode count: look for "전체 NNN" text
                    var totalText = document.body.innerText;
                    var totalMatch = totalText.match(/전체\\s+(\\d+)/);
                    var totalEpisodes = totalMatch ? parseInt(totalMatch[1]) : 0;

                    return {
                        title: title,
                        author: author,
                        cover: cover,
                        description: desc,
                        totalEpisodes: totalEpisodes
                    };
                })()
            """)
        except Exception as e:
            self.log(f"ERROR: Metadata extraction failed: {e}")
            return None

        title = meta.get('title', '')
        author = meta.get('author', '')
        cover = meta.get('cover', '')
        description = meta.get('description', '')
        total_episodes = meta.get('totalEpisodes', 0)

        if not title:
            self.log("ERROR: Could not extract title from KakaoPage.")
            return None

        self.log(f"[KakaoPage] Title: {title}, Author: {author}, "
                 f"Episodes: {total_episodes}")

        # --- Fetch episode list via BFF API ---
        # The DOM only shows ~5-6 episodes initially and expanding is
        # unreliable.  Instead, call the BFF API directly from the
        # browser context to get ALL episodes in a single request.
        self.log("[KakaoPage] Fetching episode list via API...")
        try:
            episodes = self._page.evaluate("""
                async (seriesId) => {
                    const url = `https://bff-page.kakao.com/api/gateway/api/v2/content/product/list?series_id=${seriesId}&cursor_index=0&cursor_direction=ANCHOR&window_size=10000&sort_opt=asc`;
                    const resp = await fetch(url, { credentials: 'include' });
                    const data = await resp.json();
                    const list = (data.result || {}).list || [];
                    const hasUserAccessFlag = (obj) => {
                        const seen = new Set();
                        const walk = (value, path) => {
                            if (!value || typeof value !== 'object') return false;
                            if (seen.has(value)) return false;
                            seen.add(value);
                            for (const [rawKey, v] of Object.entries(value)) {
                                const key = String(rawKey || '').toLowerCase();
                                const full = path ? `${path}.${key}` : key;
                                if (/price|count|blocked|lock|free_change/.test(full)) {
                                    continue;
                                }
                                const accessKey = /purchas|bought|owned|rented|rental|ticket|pass|usable|useable|access|viewable|readable/.test(full);
                                if (accessKey) {
                                    if (v === true) return true;
                                    if (typeof v === 'number' && v > 0) return true;
                                    if (typeof v === 'string') {
                                        const s = v.trim().toLowerCase();
                                        if (/^(yes|y|true|1|buy|bought|purchase|purchased|yes[_-]?purchase|yes[_-]?purchased|rent|rented|rental|yes[_-]?rent|yes[_-]?rented|ticket|pass|owned|available|viewable|readable|accessible)$/.test(s)) {
                                            return true;
                                        }
                                        if (/^(false|no|n|none|null|0|not[_-]?purchased|not[_-]?rented|no[_-]?purchase|no[_-]?rent|unowned|unavailable|expired)$/.test(s)) {
                                            continue;
                                        }
                                    }
                                }
                                if (v && typeof v === 'object' && walk(v, full)) return true;
                            }
                            return false;
                        };
                        return walk(obj, '');
                    };
                    return list.map(entry => {
                        const item = entry.item || {};
                        const fullTitle = item.title || '';
                        const epMatch = fullTitle.match(/([0-9]+화.*)$/);
                        const shortName = epMatch ? epMatch[1]
                            : (fullTitle || 'Episode ' + (item.order_value || 0));
                        const isFree = !!item.is_free;
                        const isAccessible = isFree || hasUserAccessFlag(item);
                        return {
                            url: `/content/${seriesId}/viewer/${item.product_id}`,
                            name: shortName,
                            fullName: fullTitle,
                            isVIP: !isAccessible,
                            isPaid: !isAccessible,
                            isFree: isFree,
                            isAccessible: isAccessible,
                            order: item.order_value || 0,
                            productId: item.product_id || 0,
                            slideType: item.slide_type || '',
                        };
                    });
                }
            """, series_id)
        except Exception as e:
            self.log(f"ERROR: Episode extraction failed: {e}")
            return None

        if not episodes:
            self.log("WARNING: No episodes found on page.")

        # Sort oldest-to-newest like Kakao's first-episode-first option. Some Kakao
        # responses mix cursor order, missing order_value, and title-only
        # numbering, so use every stable signal we have.
        episodes.sort(key=self._kakao_episode_sort_key)

        # Fix relative URLs to absolute
        for ep in episodes:
            if ep['url'] and not ep['url'].startswith('http'):
                ep['url'] = 'https://page.kakao.com' + ep['url']

        self.log(f"[KakaoPage] Found {len(episodes)} episodes.")
        paid_accessible = sum(
            1 for ep in episodes
            if ep.get('isAccessible') and not ep.get('isFree')
        )
        if paid_accessible:
            self.log(
                f"[KakaoPage] Detected {paid_accessible} rented/purchased "
                "episode(s) as accessible."
            )

        # --- Extract tags from the About tab ---
        tags = []
        try:
            about_url = re.sub(r'(\?.*)?$', '?tab_type=about', url)
            self._page.goto(about_url, wait_until="domcontentloaded",
                            timeout=15000)
            self._page.wait_for_timeout(2000)
            tags = self._page.evaluate("""
                (function() {
                    // Tags appear as "#태그" links under the 키워드 heading
                    var tags = [];
                    var links = document.querySelectorAll('a, span, div');
                    for (var i = 0; i < links.length; i++) {
                        var t = links[i].innerText.trim();
                        if (t.startsWith('#') && t.length > 1 && t.length < 30) {
                            var tag = t.substring(1);  // strip leading #
                            if (tags.indexOf(tag) === -1) tags.push(tag);
                        }
                    }
                    return tags;
                })()
            """) or []
            if tags:
                self.log(f"[KakaoPage] Tags: {', '.join(tags)}")
        except Exception:
            pass  # Tags are optional

        data = {
            'bookname': title,
            'author': author,
            'coverUrl': cover,
            'introduction': description,
            'introductionHTML': f'<p>{description}</p>' if description else '',
            'bookUrl': url,
            'chapterCount': len(episodes),
            'chapters': episodes,
            'language': 'ko',
            'tags': tags,
            '_kakaopage': True,  # Flag for chapter parser
        }

        self._book_data = data
        self._book_url = url
        return data

    @staticmethod
    def _kakao_title_number(title):
        """Best-effort episode number from Korean/Arabic Kakao titles."""
        if not title:
            return 0
        m = re.search(r'(\d+)\s*화', str(title))
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return 0
        return 0

    def _kakao_episode_sort_key(self, ep):
        """Oldest-to-newest sort key for Kakao episode rows."""
        order = ep.get('order') or 0
        if not order:
            order = self._kakao_title_number(
                ep.get('fullName') or ep.get('name') or ''
            )
        product_id = ep.get('productId') or 0
        return (order or 10**12, product_id)

    def _kakao_build_image_chapter(self, image_files, chapter_name):
        """Build standard chapter data from Kakao ImageViewerData files."""
        images = []
        html_parts = ['<div class="kakao-image-chapter">']
        text_parts = []

        ordered = sorted(
            image_files or [],
            key=lambda f: (f.get('no') or 0, f.get('url') or '')
        )
        for idx, info in enumerate(ordered, 1):
            img_url = html.unescape((info.get('url') or '').strip())
            if not img_url:
                continue

            filename = urllib.parse.unquote(info.get('filename') or '')
            filename = filename.split('?', 1)[0].split('&', 1)[0]
            filename = re.sub(r'[^A-Za-z0-9._-]+', '_', filename).strip('._')
            if not filename or '.' not in filename:
                filename = f'kakao_image_{idx:04d}.jpg'

            images.append({'url': img_url, 'name': filename})
            alt = f"{chapter_name} image {idx}"
            width = info.get('width') or ''
            height = info.get('height') or ''
            size_attrs = ''
            if width:
                size_attrs += f' width="{int(width)}"'
            if height:
                size_attrs += f' height="{int(height)}"'
            html_parts.append(
                '<div class="kakao-image-page">'
                f'<img src="{html.escape(img_url, quote=True)}" '
                f'alt="{html.escape(alt, quote=True)}"{size_attrs}/>'
                '</div>'
            )
            text_parts.append(f'[Image {idx}]')

        html_parts.append('</div>')
        if not images:
            return None

        return {
            'chapterName': chapter_name,
            'sourceChapterName': chapter_name,
            'contentText': '\n'.join(text_parts),
            'contentHtml': '\n'.join(html_parts),
            'contentCss': (
                '.kakao-image-chapter { text-align: center; }\n'
                '.kakao-image-page { margin: 0 auto 0.5rem; '
                'page-break-inside: avoid; }\n'
                '.kakao-image-page img { display: block; max-width: 100%; '
                'height: auto; margin: 0 auto; }'
            ),
            'images': images,
        }

    def _kakao_load_all_episodes(self, expected_count):
        """Expand the episode list on a KakaoPage content page.

        Clicks the expand chevron and scrolls until all episodes are visible.
        """
        max_attempts = 100  # Safety limit for expansion clicks/scrolls
        last_count = 0

        for attempt in range(max_attempts):
            if self._stop_requested:
                break

            # Count current visible episode links
            count = self._page.evaluate(
                "document.querySelectorAll('a[href*=\"/viewer/\"]').length"
            )

            if expected_count > 0 and count >= expected_count:
                break
            if count == last_count and attempt > 5:
                # No new episodes loaded after several attempts
                break
            last_count = count

            # Try clicking expand/chevron/load-more buttons
            clicked = self._page.evaluate("""
                (function() {
                    // Look for SVG chevron-down or expand button near episode list
                    var svgs = document.querySelectorAll('svg');
                    for (var i = 0; i < svgs.length; i++) {
                        var svg = svgs[i];
                        var parent = svg.closest('button') || svg.closest('a')
                                     || svg.parentElement;
                        if (!parent) continue;
                        var rect = parent.getBoundingClientRect();
                        // The expand chevron is typically centered below the
                        // last visible episode, with a small height
                        if (rect.height > 10 && rect.height < 80 &&
                            rect.width > 10 && rect.width < 200 &&
                            rect.top > 300) {
                            // Check if it looks like a down-arrow area
                            var path = svg.querySelector('path');
                            if (path) {
                                parent.click();
                                return true;
                            }
                        }
                    }
                    // Fallback: look for a "more" or expand button by text
                    var buttons = document.querySelectorAll('button');
                    for (var j = 0; j < buttons.length; j++) {
                        var b = buttons[j];
                        var t = b.innerText.trim();
                        if (t.includes('더보기') || t.includes('전체') ||
                            t.includes('펼치기')) {
                            b.click();
                            return true;
                        }
                    }
                    return false;
                })()
            """)

            if clicked:
                self._page.wait_for_timeout(1000)
            else:
                # Scroll down to trigger lazy loading
                self._page.evaluate(
                    "window.scrollBy(0, window.innerHeight)"
                )
                self._page.wait_for_timeout(800)

    def _kakao_parse_chapter(self, chapter_url, chapter_name, page=None):
        """Scrape a single KakaoPage chapter from the viewer.

        Uses a fast API-only approach:
          1. Call the BFF viewer data API to get sdownload resource URLs.
          2. Fetch all content JSONs in parallel via Promise.all.
          3. Parse the paragraphList from each JSON chunk.

        No page navigation needed — runs ~15x faster than loading the
        full React SPA for each chapter.

        Returns the standard chapter data dict or None on error.
        """
        target = page or self._page
        if target is None:
            return None

        # Extract series_id and product_id from the chapter URL
        m = re.search(r'/content/(\d+)/viewer/(\d+)', chapter_url)
        if not m:
            self.log(f"  [KakaoPage] Invalid viewer URL: {chapter_url}")
            return None
        s_id, p_id = m.group(1), m.group(2)
        # Ensure the page is on the kakao.com domain so that fetch()
        # sends the correct cookies.  After "Enter Browser" restarts the
        # headless browser, the page is on about:blank — the BFF API
        # returns 403 Forbidden for requests from a null origin.
        try:
            current_url = target.url or ''
        except Exception:
            current_url = ''
        if 'kakao.com' not in current_url or 'tab_type=about' in current_url:
            try:
                target.goto(
                    f'https://page.kakao.com/content/{s_id}',
                    wait_until="domcontentloaded", timeout=30000,
                )
                target.wait_for_timeout(1000)
            except Exception as e:
                self.log(f"  [KakaoPage] Failed to navigate to book page: {e}")

        # ---- Strategy 1: Direct API fetch (fast, no navigation) ----
        api_result = None
        try:
            api_result = target.evaluate("""
            async ([seriesId, productId]) => {
                // bff-page.kakao.com is a different origin from page.kakao.com.
                // Include credentials or purchased/age-verified viewer data can
                // look anonymously locked even while the user is logged in.
                const vResp = await fetch(
                    `https://bff-page.kakao.com/api/gateway/api/v1/viewer/data`
                    + `?series_id=${seriesId}&product_id=${productId}`,
                    { credentials: 'include' }
                );
                const httpStatus = vResp.status;
                let vData;
                try { vData = await vResp.json(); } catch(e) {
                    return { locked: false, unavailable: true, chunks: [], httpStatus,
                             msg: 'Non-JSON response', pageUrl: location.href,
                             reason: 'non-json response' };
                }
                const roots = [
                    vData,
                    vData.result || null,
                    vData.data || null,
                    vData.body || null
                ].filter(Boolean);
                const pick = (...values) => values.find(v => v !== undefined && v !== null);
                const findProp = (...names) => {
                    for (const root of roots) {
                        for (const name of names) {
                            if (root && root[name] !== undefined && root[name] !== null) {
                                return root[name];
                            }
                        }
                    }
                    return undefined;
                };
                const vd = findProp('viewerData', 'viewer_data') || {};
                const rawBaseUrl = vd.atsServerUrl || vd.ats_server_url || '';
                const baseUrl = rawBaseUrl.replace(/^http:/i, 'https:');
                const contents = vd.contentsList || vd.contents_list || vd.contents || [];
                const imageData = vd.imageDownloadData || vd.image_download_data || {};
                const imageFiles = (
                    imageData.files || imageData.fileList || imageData.file_list ||
                    vd.imageFiles || vd.image_files || []
                );
                const msg = pick(
                    findProp('message', 'msg'),
                    vData.message,
                    vData.msg,
                    null
                );
                const msgKey = pick(
                    findProp('message_key', 'messageKey'),
                    vData.message_key,
                    vData.messageKey,
                    null
                );
                const resultCode = pick(
                    findProp('result_code', 'resultCode'),
                    vData.result_code,
                    vData.resultCode,
                    null
                );
                const accessText = `${msg || ''} ${msgKey || ''}`.toLowerCase();
                const accessDenied = (
                    httpStatus === 401 || httpStatus === 403 ||
                    /(not[_-]?purchased|purchase|rental|login|adult|age|auth|permission|forbidden|locked|구매|대여|로그인|성인|권한|인증|열람)/i.test(accessText)
                );
                if (accessDenied) {
                    return { locked: true, chunks: [], httpStatus, msg, msgKey,
                             resultCode, pageUrl: location.href,
                             reason: msgKey || msg || 'access denied' };
                }

                // If the API gives neither text nor image resources, the fast
                // path cannot read this chapter. Only access-denied messages
                // above are treated as true locks.
                if ((!baseUrl || !contents.length) && !imageFiles.length)
                    return { locked: false, unavailable: true, chunks: [],
                             httpStatus, msg, msgKey, resultCode,
                             pageUrl: location.href,
                             reason: resultCode
                                 ? `api returned no viewer resources (code ${resultCode})`
                                 : 'missing viewer resources',
                             responseKeys: Object.keys(vData || {}).join(',') };

                if (imageFiles.length) {
                    return {
                        locked: false,
                        chunks: [],
                        imageFiles: imageFiles.map((f, idx) => ({
                            no: f.no || idx + 1,
                            url: f.secureUrl || f.secure_url || '',
                            width: f.width || 0,
                            height: f.height || 0,
                            filename: ((f.secureUrl || f.secure_url || '').match(/filename=([^&]+)/) || [])[1] || ''
                        })).filter(f => f.url),
                        httpStatus, msg, msgKey, resultCode, pageUrl: location.href
                    };
                }

                const fetches = contents.map(async (c) => {
                    const secureUrl = c.secureUrl || c.secure_url || '';
                    if (!secureUrl) return null;
                    try {
                        const r = await fetch(baseUrl + secureUrl);
                        const ct = r.headers.get('content-type') || '';
                        if (!ct.includes('json')) return null;
                        return await r.text();
                    } catch(e) { return null; }
                });
                const results = (await Promise.all(fetches)).filter(r => r !== null);
                return { locked: false, chunks: results, imageFiles: [],
                         httpStatus, msg, msgKey, resultCode, pageUrl: location.href };
            }
            """, [s_id, p_id])
        except Exception as e:
            self.log(f"  [KakaoPage] API fetch error: {e}")

        if api_result:
            http_st = api_result.get('httpStatus', '?')
            page_url = api_result.get('pageUrl', '?')
            api_msg = api_result.get('msg', '')
            if api_result.get('locked'):
                reason = api_result.get('reason') or 'locked'
                self.log(
                    f"  [KakaoPage] LOCKED: {chapter_name} "
                    f"(HTTP {http_st}, msg={api_msg}, "
                    f"reason={reason}, "
                    f"page={page_url})"
                )
                return {'_locked': True, 'chapterName': chapter_name}
            if api_result.get('unavailable'):
                self.log(
                    f"  [KakaoPage] Fast API unavailable: {chapter_name} "
                    f"(HTTP {http_st}, msg={api_msg}, "
                    f"reason={api_result.get('reason')}, "
                    f"keys={api_result.get('responseKeys', '')}, "
                    f"page={page_url})"
                )
                return None
            else:
                image_files = api_result.get('imageFiles') or []
                if image_files:
                    return self._kakao_build_image_chapter(
                        image_files, chapter_name)

                raw_chunks = api_result.get('chunks', [])
                json_chunks = [r.encode('utf-8') for r in raw_chunks]
                para_tuples, content_css = self._kakao_extract_from_json(
                    json_chunks)
                if para_tuples:
                    full_text, content_html = self._kakao_build_output(
                        para_tuples)
                    display_name = self._kakao_heading_title(
                        para_tuples, chapter_name)
                    return {
                        'chapterName': display_name,
                        'sourceChapterName': chapter_name,
                        'contentText': full_text,
                        'contentHtml': content_html,
                        'contentCss': content_css,
                        'images': [],
                    }

        self.log(
            f"  [KakaoPage] Fast API returned no readable text: "
            f"{chapter_name}"
        )
        return None

    @staticmethod
    def _kakao_strip_headings(para_tuples):
        """Remove redundant episode heading paragraphs from chapter text.

        KakaoPage chapters start with a heading like '제1화' or '제103화'
        that duplicates info already in the chapter title.  Strip these
        and any surrounding &nbsp; spacers from the very beginning.

        para_tuples: list of (plain_text, html_fragment, type) tuples.
        Returns the trimmed list (may be unchanged).
        """
        import re
        cleaned = list(para_tuples)
        while cleaned:
            text = cleaned[0][0].strip()
            if not text or text == '&nbsp;':
                cleaned.pop(0)
            elif re.fullmatch(r'제\d+화\.?', text):
                cleaned.pop(0)
            else:
                break
        return cleaned

    @staticmethod
    def _kakao_build_output_legacy(para_tuples):
        """Convert (plain_text, html_fragment, type, style) tuples to
        full text and HTML output.

        Uses paragraph type to choose the appropriate HTML tag:
        - HEAD/HEADING → <h3>
        - Everything else → <p>
        Paragraph-level style (text-align) is applied via inline CSS.
        """
        text_parts = []
        html_parts = []
        for item in para_tuples:
            plain, html_frag, p_type = item[0], item[1], item[2]
            p_style = item[3] if len(item) > 3 else {}
            if not plain.strip():
                continue
            text_parts.append(plain)

            # Paragraph-level inline CSS (text-align)
            align = (p_style.get('textAlign') or p_style.get('align')
                     or '') if p_style else ''
            style_attr = f' style="text-align:{align.lower()}"' if align else ''

            if p_type in ('HEAD', 'HEADING', 'TITLE'):
                html_parts.append(f'<h3{style_attr}>{html_frag}</h3>')
            else:
                html_parts.append(f'<p{style_attr}>{html_frag}</p>')
        return '\n'.join(text_parts), '\n'.join(html_parts)

    @staticmethod
    def _kakao_heading_title(para_tuples, fallback):
        """Use the first source heading as the EPUB title/TOC label."""
        for item in para_tuples:
            plain = (item[0] or '').strip()
            p_type = (item[2] or '').lower()
            if plain and (p_type in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
                          or p_type in {'head', 'heading', 'title'}):
                return plain
        return fallback

    @staticmethod
    def _kakao_build_output(para_tuples):
        """Convert parsed Kakao paragraph tuples to full text and HTML."""
        from html import escape as _esc

        def _attrs_to_html(attrs, style, extra_class=''):
            attrs = attrs or {}
            style = style or {}
            parts = []

            cls = attrs.get('class') or attrs.get('className') or ''
            if extra_class:
                cls = f'{cls} {extra_class}'.strip()
            if cls:
                parts.append(f' class="{_esc(str(cls), quote=True)}"')

            style_parts = []
            raw_style = attrs.get('style') or ''
            if raw_style:
                style_parts.append(str(raw_style).strip().rstrip(';'))

            align = attrs.get('align') or ''
            if align:
                style_parts.append(f'text-align:{str(align).lower()}')

            color = (style.get('color') or style.get('fontColor')
                     or style.get('textColor') or '')
            if color:
                if not color.startswith('#') and not color.startswith('rgb'):
                    color = '#' + color
                style_parts.append(f'color:{color}')

            bg = style.get('backgroundColor') or style.get('highlight') or ''
            if bg:
                if not bg.startswith('#') and not bg.startswith('rgb'):
                    bg = '#' + bg
                style_parts.append(f'background-color:{bg}')

            fs = style.get('fontSize') or style.get('size') or ''
            if fs:
                style_parts.append(f'font-size:{fs}px'
                                   if isinstance(fs, (int, float))
                                   else f'font-size:{fs}')

            text_align = style.get('textAlign') or style.get('align') or ''
            if text_align:
                style_parts.append(f'text-align:{str(text_align).lower()}')

            if (style.get('lineThrough') or style.get('strikethrough')
                    or style.get('strike')):
                style_parts.append('text-decoration:line-through')

            if style_parts:
                safe_style = _esc('; '.join(
                    p for p in style_parts if p), quote=True)
                parts.append(f' style="{safe_style}"')

            return ''.join(parts)

        def _tag_for(p_type):
            tag = (p_type or '').lower()
            if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                       'p', 'div', 'blockquote', 'li'}:
                return tag
            if tag in {'head', 'heading', 'title'}:
                return 'h3'
            return 'p'

        def _append_page_break(parts):
            if parts and parts[-1] != '<div class="kakao-page-break">&#160;</div>':
                parts.append('<div class="kakao-page-break">&#160;</div>')

        text_parts = []
        html_parts = []
        previous_chunk = None
        first_heading_seen = False
        pending_heading_break = False
        inserted_heading_break = False
        for item in para_tuples:
            plain, html_frag, p_type = item[0], item[1], item[2]
            p_style = item[3] if len(item) > 3 else {}
            p_attrs = item[4] if len(item) > 4 else {}
            p_meta = item[5] if len(item) > 5 else {}
            if not plain.strip() and '<br' not in html_frag.lower():
                continue
            tag = _tag_for(p_type)

            chunk_index = p_meta.get('chunkIndex') if p_meta else None
            if (previous_chunk is not None and chunk_index is not None
                    and chunk_index != previous_chunk):
                _append_page_break(html_parts)

            is_blank = not plain.strip() and '<br' in html_frag.lower()
            is_heading = tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
            if (pending_heading_break and not inserted_heading_break
                    and not is_blank and not is_heading):
                _append_page_break(html_parts)
                inserted_heading_break = True
                pending_heading_break = False

            text_plain = plain
            extra_class = ''
            if is_heading and not first_heading_seen:
                first_heading_seen = True
                pending_heading_break = True
                extra_class = 'kakao-source-heading'

            text_parts.append(text_plain)
            attr_html = _attrs_to_html(p_attrs, p_style, extra_class)
            html_parts.append(f'<{tag}{attr_html}>{html_frag}</{tag}>')

            if chunk_index is not None:
                previous_chunk = chunk_index
        return '\n'.join(text_parts), '\n'.join(html_parts)

    @staticmethod
    def _is_colophon_chunk(paragraphs_text):
        """Check if a chunk's combined text looks like publisher boilerplate.

        KakaoPage embeds a copyright/colophon page in every chapter EPUB
        with ISBN, copyright notices, and publisher info.  These share
        contentId=0 with the actual chapter-title paragraphs, so we
        can't filter by ID alone — we detect them by content markers.

        Args:
            paragraphs_text: concatenated text of all paragraphs in a chunk.
        Returns True if the chunk is publisher boilerplate.
        """
        text = paragraphs_text or ''
        compact = re.sub(r'\s+', '', text)
        lower = text.lower()
        lower_compact = compact.lower()

        # Strong legal/identifier markers. One of these plus ordinary
        # publisher/contact labels is enough to identify a colophon page.
        strong_markers = [
            'ISBN', 'UCI', 'ⓒ', '©', '저작권법',
            '재가공할 수 없습니다', '서면 허락',
            '이 책의 내용을 이용하지 못합니다',
        ]
        publisher_markers = [
            '발행인', '발행처', '펴낸곳', '펴낸 곳',
            '기획/편집', '기획 / 편집', '책임편집',
            '표지', '주소',
        ]
        contact_markers = [
            '블로그', '트위터', '투고', 'blog.naver.com',
            'dreambook', 'samyangcnc',
        ]

        def _has(marker):
            marker_compact = re.sub(r'\s+', '', marker)
            return (marker in text or marker_compact in compact
                    or marker.lower() in lower
                    or marker_compact.lower() in lower_compact)

        strong_hits = sum(1 for marker in strong_markers if _has(marker))
        publisher_hits = sum(1 for marker in publisher_markers if _has(marker))
        contact_hits = sum(1 for marker in contact_markers if _has(marker))

        if strong_hits >= 1 and (publisher_hits + contact_hits) >= 2:
            return True
        if publisher_hits >= 3 and contact_hits >= 1:
            return True
        if publisher_hits >= 2 and contact_hits >= 2:
            return True
        return False

    def _kakao_fetch_css_resource(self, style_info):
        """Download a Kakao EPUB CSS resource referenced by styleList."""
        src = (style_info or {}).get('src') or ''
        if not src:
            return ''
        file_name = (style_info or {}).get('fileName') or 'style.css'
        cache_key = f'{src}|{file_name}'
        if cache_key in self._kakao_css_cache:
            return self._kakao_css_cache[cache_key]

        if src.startswith('http://') or src.startswith('https://'):
            url = src
        else:
            kid = urllib.parse.quote(src, safe='/')
            fname = urllib.parse.quote(file_name)
            url = (
                'https://dn-img-page.kakao.com/download/resource'
                f'?kid={kid}&filename={fname}'
            )

        css = ''
        last_error = None
        for attempt, timeout in enumerate((20, 35, 60), 1):
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36'
                    ),
                    'Referer': 'https://page.kakao.com/',
                })
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                css = raw.decode('utf-8-sig', errors='replace')
                css = re.sub(r'@charset\s+["\']UTF-8["\'];?', '', css,
                             flags=re.IGNORECASE)
                css = css.replace('\r\n', '\n').replace('\r', '\n').strip()
                break
            except Exception as e:
                last_error = e
                if attempt < 3:
                    self.log(
                        f"  [KakaoPage] CSS fetch retry {attempt + 1}/3: "
                        f"{file_name} ({e})"
                    )
                    time.sleep(0.75)

        if not css and last_error:
            self.log(
                f"  [KakaoPage] CSS fetch failed after 3 tries: "
                f"{file_name}: {last_error}"
            )

        self._kakao_css_cache[cache_key] = css
        return css

    def _kakao_extract_from_json(self, json_chunks):
        """Parse paragraphList from intercepted JSON API responses.

        Each JSON chunk has structure:
          { contentInfo: {
              paragraphList: [{
                id, type, text,
                style: { bold, italic, underline, ... },
                childParagraphList: [...]
              }, ...]
          } }
        Text nodes can be nested arbitrarily deep (e.g. P → SPAN → TEXT).
        Returns a list of (plain_text, html_fragment) tuples sorted by
        content order.
        """
        import json as _json
        from html import escape as _esc
        from html import unescape as _unesc

        def _safe_escape(text):
            """Unescape first, then re-escape to avoid double-encoding.

            The API text may already contain HTML entities like &lt; &gt;
            &nbsp;.  A plain html.escape() would turn & into &amp;,
            producing &amp;lt; in the output.
            """
            return _esc(_unesc(text))

        def _style_to_css(style):
            """Convert KakaoPage style dict to an inline CSS string."""
            css_parts = []
            # Color — check multiple possible field names
            color = (style.get('color') or style.get('fontColor')
                     or style.get('textColor') or '')
            if color:
                # Ensure # prefix for hex colors
                if color and not color.startswith('#') and not color.startswith('rgb'):
                    color = '#' + color
                css_parts.append(f'color:{color}')

            # Background / highlight
            bg = style.get('backgroundColor') or style.get('highlight') or ''
            if bg:
                if bg and not bg.startswith('#') and not bg.startswith('rgb'):
                    bg = '#' + bg
                css_parts.append(f'background-color:{bg}')

            # Font size
            fs = style.get('fontSize') or style.get('size') or ''
            if fs:
                css_parts.append(f'font-size:{fs}px' if isinstance(fs, (int, float))
                                 else f'font-size:{fs}')

            # Text alignment (paragraph-level, will be applied via wrapper)
            align = style.get('textAlign') or style.get('align') or ''
            if align:
                css_parts.append(f'text-align:{align.lower()}')

            # Line-through / strikethrough via CSS
            decoration = str(
                style.get('textDecoration') or style.get('text-decoration')
                or style.get('textDecorationLine')
                or style.get('text-decoration-line') or ''
            ).lower()
            line_through = (style.get('lineThrough')
                            or style.get('line-through')
                            or style.get('strikethrough')
                            or style.get('strike')
                            or 'line-through' in decoration)
            if line_through:
                css_parts.append('text-decoration:line-through')

            # Underline via CSS (backup if not using <u>)
            # (handled separately with <u> tag below)

            return '; '.join(css_parts)

        def _apply_tags(h, style):
            """Wrap HTML fragment in semantic tags for bold/italic/underline."""
            if style.get('bold') or style.get('fontWeight') == 'bold':
                h = f'<b>{h}</b>'
            if style.get('italic') or style.get('fontStyle') == 'italic':
                h = f'<i>{h}</i>'
            if style.get('underline'):
                h = f'<u>{h}</u>'
            return h

        def _wrap_with_style(h, style, attrs=None):
            """Apply inline CSS and semantic tags to an HTML fragment."""
            attrs = attrs or {}
            css_parts = []
            attr_style = attrs.get('style') or ''
            if attr_style:
                css_parts.append(str(attr_style).strip().rstrip(';'))
            css = _style_to_css(style)
            if css:
                css_parts.append(css)
            h = _apply_tags(h, style)
            attr_parts = []
            cls = attrs.get('class') or attrs.get('className') or ''
            if cls:
                attr_parts.append(f' class="{_esc(str(cls), quote=True)}"')
            if css_parts:
                safe_css = _esc('; '.join(css_parts), quote=True)
                attr_parts.append(f' style="{safe_css}"')
            if attr_parts:
                h = f'<span{"".join(attr_parts)}>{h}</span>'
            return h

        def _collect_html(node, is_root=False):
            """Recursively collect HTML from a paragraph node and children."""
            n_type = (node.get('type') or '').upper()
            if n_type == 'BR':
                return '<br />'
            if n_type == 'IMG':
                return ''

            text = (node.get('text') or '')
            children = node.get('childParagraphList') or []
            style = node.get('style') or {}
            attrs = node.get('attributes') or {}

            # Leaf node with text
            if text and not children:
                h = _safe_escape(text)
                h = _wrap_with_style(h, style, None if is_root else attrs)
                if n_type in {'S', 'STRIKE', 'DEL'}:
                    h = f'<s>{h}</s>'
                return h

            # Parent node: collect children
            parts = []
            if text:
                parts.append(_safe_escape(text))
            for child in children:
                ch = _collect_html(child)
                if ch:
                    parts.append(ch)
            result = ''.join(parts)

            # Apply style to the whole group
            result = _wrap_with_style(result, style, None if is_root else attrs)
            if n_type in {'S', 'STRIKE', 'DEL'}:
                result = f'<s>{result}</s>'
            return result

        def _collect_text(node):
            """Recursively collect plain text."""
            parts = []
            text = (node.get('text') or '').strip()
            if text:
                parts.append(_unesc(text))
            for child in (node.get('childParagraphList') or []):
                parts.extend(_collect_text(child))
            return parts

        def _node_has_image(node):
            if (node.get('type') or '').upper() == 'IMG':
                return True
            return any(_node_has_image(child)
                       for child in (node.get('childParagraphList') or []))

        parsed_chunks = []
        css_parts = []
        seen_css = set()
        for chunk_index, raw in enumerate(json_chunks):
            try:
                data = _json.loads(raw)
                info = data.get('contentInfo', {})
                content_id = info.get('contentId', 0)
                para_list = info.get('paragraphList', [])
                for style_info in (info.get('styleList') or []):
                    css = self._kakao_fetch_css_resource(style_info)
                    if css and css not in seen_css:
                        seen_css.add(css)
                        css_parts.append(css)

                chunk_paras = []
                for p in para_list:
                    p_id = int(p.get('id', 0))
                    p_type = (p.get('type') or '').upper()
                    p_style = p.get('style') or {}
                    p_attrs = p.get('attributes') or {}
                    plain = ''.join(_collect_text(p))
                    html_frag = _collect_html(p, is_root=True)
                    if _node_has_image(p) and not plain.strip():
                        continue
                    if plain.strip() or '<br' in html_frag.lower():
                        chunk_paras.append(
                            (chunk_index, content_id, p_id, plain,
                             html_frag, p_type, p_style, p_attrs))

                if chunk_paras:
                    parsed_chunks.append((chunk_index, chunk_paras))
            except Exception:
                continue

        skip_last_index = None
        if (self.kakao_skip_last_page and not self.kakao_keep_filler
                and len(parsed_chunks) > 1):
            skip_last_index = parsed_chunks[-1][0]

        all_paras = []
        for chunk_index, chunk_paras in parsed_chunks:
            combined = ' '.join(t for _, _, _, t, _, _, _, _ in chunk_paras)
            if not self.kakao_keep_filler:
                if skip_last_index is not None and chunk_index == skip_last_index:
                    continue

                # Skip publisher colophon/copyright chunks
                if self._is_colophon_chunk(combined):
                    continue

            all_paras.extend(chunk_paras)

        if not all_paras:
            return [], '\n\n'.join(css_parts)

        # Sort by chunk order first. Some Kakao chapters have multiple
        # contentId=0 resources (cover, then body), so contentId alone can
        # interleave unrelated pages.
        all_paras.sort(key=lambda x: (x[0], x[1], x[2]))
        return ([
                    (p[3], p[4], p[5], p[6], p[7],
                     {'chunkIndex': p[0], 'contentId': p[1], 'paragraphId': p[2]})
                    for p in all_paras
                ],
                '\n\n'.join(css_parts))

    # ------------------------------------------------------------------
    # 1qxs.com native scraper
    # ------------------------------------------------------------------
    _1QXS_ORIGIN = 'https://www.1qxs.com'
    _1QXS_UA = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/140.0.0.0 Safari/537.36'
    )
    _1QXS_MIN_REQUEST_INTERVAL = 1.2

    @staticmethod
    def _1qxs_url_parts(url):
        try:
            path = urllib.parse.urlparse(url or '').path or ''
        except Exception:
            return None
        match = re.match(
            r'^/(?P<kind>catalog|xs)(?P<variant>_\d+)?/'
            r'(?P<book_id>\d+)'
            r'(?:/(?P<chapter_id>\d+)(?:/(?P<page>\d+))?)?'
            r'(?:\.html)?/?$',
            path,
        )
        if not match:
            return None
        return match.groupdict(default='')

    def _1qxs_new_session(self):
        """Create an isolated HTTP session; no page scripts or ads run."""
        import requests

        session = requests.Session()
        session.headers.update({
            'User-Agent': self._1QXS_UA,
            'Accept': (
                'text/html,application/xhtml+xml,application/xml;q=0.9,'
                'image/avif,image/webp,*/*;q=0.8'
            ),
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.7',
        })
        # Keep cookies isolated per worker. Reusing one uid concurrently causes
        # 1qxs to return 403 responses on later pages of split chapters.
        return session

    def _1qxs_wait_for_request_slot(self):
        """Pace requests from all chapter workers to avoid the 30s ban."""
        while True:
            if self._stop_requested:
                raise RuntimeError('download stopped')
            with self._1qxs_request_lock:
                now = time.monotonic()
                ready_at = max(
                    self._1qxs_next_request_at,
                    self._1qxs_cooldown_until,
                )
                if now >= ready_at:
                    self._1qxs_next_request_at = (
                        now + self._1QXS_MIN_REQUEST_INTERVAL
                    )
                    return
                delay = min(0.2, ready_at - now)
            time.sleep(delay)

    def _1qxs_fetch_html(self, session, url, referer='', timeout=30):
        """Fetch HTML, retrying the site's initial cookie handshake page."""
        last_error = None
        headers = {'Referer': referer} if referer else None
        for attempt in range(6):
            try:
                self._1qxs_wait_for_request_slot()
                response = session.get(url, headers=headers, timeout=timeout)
                if response.status_code == 403:
                    last_error = RuntimeError('1qxs returned HTTP 403')
                    # This site uses HTTP 403 for its documented 30-second
                    # "access too frequently" response.
                    cooldown = 30.5
                    should_log = False
                    with self._1qxs_request_lock:
                        now = time.monotonic()
                        if self._1qxs_cooldown_until <= now + 1.0:
                            should_log = True
                        self._1qxs_cooldown_until = max(
                            self._1qxs_cooldown_until,
                            now + cooldown,
                        )
                    if should_log:
                        self.log(
                            '[1qxs] Site rate limit reached; waiting 30 '
                            'seconds before retrying.'
                        )
                    try:
                        session.cookies.clear()
                    except Exception:
                        pass
                    continue
                response.raise_for_status()
                text = response.text
                is_handshake_error = bool(re.search(
                    r'<div\s+class=["\']error["\']', text, re.I
                ))
                if is_handshake_error:
                    last_error = RuntimeError(
                        '1qxs returned its cookie handshake page'
                    )
                    continue
                try:
                    self._1qxs_cookies = session.cookies.get_dict()
                except Exception:
                    pass
                return text
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f'1qxs request failed: {last_error}')

    def _1qxs_parse_book(self, url):
        """Fetch 1qxs metadata and the complete static chapter catalog."""
        self._stop_requested = False
        parts = self._1qxs_url_parts(url)
        if not parts:
            self.log('[1qxs] ERROR: Could not parse the supplied URL.')
            return None

        book_id = parts['book_id']
        variant = parts['variant']
        reader_prefix = f'xs{variant}'
        catalog_prefix = f'catalog{variant}'
        book_url = f'{self._1QXS_ORIGIN}/{reader_prefix}/{book_id}.html'
        catalog_url = (
            f'{self._1QXS_ORIGIN}/{catalog_prefix}/{book_id}.html'
        )
        self.log(
            f'[1qxs] Fetching book {book_id} via direct HTTP '
            '(site JavaScript and ads are not executed)...'
        )

        session = self._1qxs_new_session()
        try:
            # The first request also establishes the uid/refresh_token cookies.
            catalog_html = self._1qxs_fetch_html(
                session, catalog_url, referer=book_url
            )
            book_html = self._1qxs_fetch_html(
                session, book_url, referer=catalog_url
            )
        except Exception as exc:
            self.log(f'ERROR: [1qxs] Book request failed: {exc}')
            return None
        finally:
            try:
                session.close()
            except Exception:
                pass

        chapters = []
        seen = set()
        chapter_path_re = re.compile(
            rf'^/{re.escape(reader_prefix)}/{re.escape(book_id)}/'
            r'(\d+)\.html/?$'
        )
        for anchor in re.finditer(
            r'<a\b([^>]*)>(.*?)</a>', catalog_html, re.I | re.S
        ):
            attrs = self._ntk_html_attrs(anchor.group(1))
            href = html.unescape(attrs.get('href') or '').strip()
            path = urllib.parse.urlparse(href).path or ''
            chapter_match = chapter_path_re.match(path)
            if not chapter_match:
                continue
            chapter_id = chapter_match.group(1)
            if chapter_id in seen:
                continue
            name = self._ntk_plain_fragment(anchor.group(2))
            if not name:
                continue
            seen.add(chapter_id)
            chapters.append({
                'url': (
                    f'{self._1QXS_ORIGIN}/{reader_prefix}/'
                    f'{book_id}/{chapter_id}.html'
                ),
                'name': name,
                'fullName': name,
                'isVIP': False,
                'isPaid': False,
                'isAccessible': True,
                '_chapterId': chapter_id,
            })

        if not chapters:
            self.log('ERROR: [1qxs] No chapters found in the catalog.')
            return None

        title = self._ntk_meta_content(
            book_html, ('og:title', 'og:novel:book_name')
        )
        author = self._ntk_meta_content(
            book_html, ('og:novel:author',)
        )
        description = self._ntk_meta_content(
            book_html, ('og:description', 'description')
        )
        description = re.sub(
            r'^.*?提供精彩免费全文阅读\s*[:：]\s*',
            '',
            description,
            count=1,
        ).strip()
        cover_url = self._ntk_meta_content(book_html, ('og:image',))
        category = self._ntk_meta_content(
            book_html, ('og:novel:category',)
        )
        status = self._ntk_meta_content(
            book_html, ('og:novel:status',)
        )

        if not title:
            title_match = re.search(
                r'<div\s+class=["\']book["\'][^>]*>.*?'
                r'<h1[^>]*>(.*?)</h1>',
                catalog_html,
                re.I | re.S,
            )
            if title_match:
                title = self._ntk_plain_fragment(title_match.group(1))
        if not author:
            author_match = re.search(
                r'作者\s*[：:]\s*([^<&]+)', catalog_html, re.I
            )
            if author_match:
                author = html.unescape(author_match.group(1)).strip()

        data = {
            'bookname': title or f'1qxs Book {book_id}',
            'author': author or 'Unknown',
            'coverUrl': urllib.parse.urljoin(
                self._1QXS_ORIGIN + '/', cover_url
            ) if cover_url else '',
            'description': description,
            'introduction': description,
            'introductionHTML': (
                f'<p>{html.escape(description)}</p>' if description else ''
            ),
            'tags': [],
            'category': [category] if category else [],
            'status': status,
            'bookUrl': book_url,
            'chapterCount': len(chapters),
            'chapters': chapters,
            'language': 'zh',
            '_1qxs': True,
            '_1qxs_book_id': book_id,
            '_1qxs_variant': variant,
            '_1qxs_reader_prefix': reader_prefix,
        }
        self._book_data = data
        self._book_url = book_url
        self.log(
            f"[1qxs] Book: {data['bookname']} by {data['author']} - "
            f'{len(chapters)} chapters'
        )
        return data

    @staticmethod
    def _1qxs_is_junk_paragraph(value):
        compact = re.sub(r'\s+', '', value or '')
        return bool(
            ('小说免费阅读' in compact and '1qxs.com' in compact)
            or ('一秒记住' in compact and '更新快' in compact)
            or '如遇到内容无法显示或者显示不全' in compact
            or '无法显示本章节全部内容' in compact
            or ('本章未完' in compact and '下一页' in compact)
            or compact in ('加载更多', '加|载|更|多')
        )

    @classmethod
    def _1qxs_extract_paragraphs(cls, fragment):
        paragraphs = []
        for paragraph in re.finditer(
            r'<p\b[^>]*>(.*?)</p>', fragment or '', re.I | re.S
        ):
            value = re.sub(
                r'<br\s*/?>', '\n', paragraph.group(1), flags=re.I
            )
            value = html.unescape(re.sub(r'<[^>]+>', '', value))
            value = value.replace('\r', '').strip()
            if value and not cls._1qxs_is_junk_paragraph(value):
                paragraphs.append(value)
        return paragraphs

    @staticmethod
    def _1qxs_decode_p_key(page_html):
        match = re.search(
            r'\bp_key\s*=\s*["\']([^"\']+)["\']', page_html or ''
        )
        if not match:
            return ''
        encoded = match.group(1).strip()
        encoded += '=' * (-len(encoded) % 4)
        try:
            return base64.b64decode(encoded).decode('utf-8')
        except Exception as exc:
            raise ValueError(f'invalid 1qxs p_key: {exc}') from exc

    def _1qxs_parse_chapter(self, chapter_url, chapter_name):
        parts = self._1qxs_url_parts(chapter_url)
        book_data = self._book_data or {}
        book_id = (parts or {}).get('book_id') or str(
            book_data.get('_1qxs_book_id') or ''
        )
        chapter_id = (parts or {}).get('chapter_id') or ''
        variant = (parts or {}).get('variant')
        if variant is None:
            variant = str(book_data.get('_1qxs_variant') or '')
        if not book_id or not chapter_id:
            self.log(f'  [1qxs] Invalid chapter URL: {chapter_url}')
            return None

        reader_prefix = f'xs{variant}'
        base_url = (
            f'{self._1QXS_ORIGIN}/{reader_prefix}/'
            f'{book_id}/{chapter_id}.html'
        )
        catalog_url = (
            f'{self._1QXS_ORIGIN}/catalog{variant}/{book_id}.html'
        )
        session = self._1qxs_new_session()
        try:
            first_html = self._1qxs_fetch_html(
                session, base_url, referer=catalog_url
            )
            heading_match = re.search(
                r'<h1[^>]*>(.*?)</h1>', first_html, re.I | re.S
            )
            heading = self._ntk_plain_fragment(
                heading_match.group(1) if heading_match else ''
            )
            page_match = re.search(r'\((\d+)\s*/\s*(\d+)\)\s*$', heading)
            page_count = int(page_match.group(2)) if page_match else 1
            if page_count < 1 or page_count > 100:
                raise RuntimeError(f'invalid page count: {page_count}')
            clean_heading = re.sub(
                r'\s*\(\d+\s*/\s*\d+\)\s*$', '', heading
            ).strip()

            all_paragraphs = []
            for page_number in range(1, page_count + 1):
                if self._stop_requested:
                    return None
                if page_number == 1:
                    page_html = first_html
                else:
                    page_url = (
                        f'{self._1QXS_ORIGIN}/{reader_prefix}/'
                        f'{book_id}/{chapter_id}/{page_number}.html'
                    )
                    page_html = self._1qxs_fetch_html(
                        session, page_url, referer=base_url
                    )

                content_match = re.search(
                    r'<div\s+class=["\']content["\'][^>]*>'
                    r'(.*?)</div>',
                    page_html,
                    re.I | re.S,
                )
                if not content_match:
                    raise RuntimeError(
                        f'content missing on page {page_number}/{page_count}'
                    )
                page_paragraphs = self._1qxs_extract_paragraphs(
                    content_match.group(1)
                )
                hidden_html = self._1qxs_decode_p_key(page_html)
                page_paragraphs.extend(
                    self._1qxs_extract_paragraphs(hidden_html)
                )
                if not page_paragraphs:
                    raise RuntimeError(
                        f'empty content on page {page_number}/{page_count}'
                    )
                all_paragraphs.extend(page_paragraphs)
        except Exception as exc:
            self.log(
                f'  [1qxs] Chapter request failed: {chapter_name}: {exc}'
            )
            return None
        finally:
            try:
                session.close()
            except Exception:
                pass

        result_name = clean_heading or chapter_name or 'Chapter'
        if (
            all_paragraphs
            and self._normalize_title_for_match(all_paragraphs[0])
            == self._normalize_title_for_match(result_name)
        ):
            all_paragraphs.pop(0)
        content_text = '\n'.join(all_paragraphs)
        content_html = '\n'.join(
            f'<p>{html.escape(paragraph)}</p>'
            for paragraph in all_paragraphs
        )
        return {
            'chapterName': result_name,
            'sourceChapterName': chapter_name or result_name,
            'contentText': content_text,
            'contentHtml': (
                '<div class="oneqxs-content">\n'
                f'{content_html}\n'
                '</div>'
            ),
            'contentCss': (
                '.oneqxs-content p { margin: 0 0 0.75em; '
                'line-height: 1.8; }'
            ),
            'images': [],
        }

    # ------------------------------------------------------------------
    # 69shuba.tw native scraper
    # ------------------------------------------------------------------
    @staticmethod
    def _69shuba_book_id(url):
        try:
            match = re.match(
                r'^/(?:book|indexlist|read)/(\d+)',
                urllib.parse.urlparse(url or '').path or '',
            )
            return match.group(1) if match else ''
        except Exception:
            return ''

    @staticmethod
    def _69shuba_chapter_id(url):
        try:
            match = re.match(
                r'^/read/\d+/(\d+)',
                urllib.parse.urlparse(url or '').path or '',
            )
            return match.group(1) if match else ''
        except Exception:
            return ''

    def _69shuba_api_request(self, endpoint, params=None, timeout=30):
        """Call the JSON API used by 69shuba's official mobile client."""
        import requests

        timestamp = int(time.time())
        token = hashlib.md5(
            f'{SHUBA_API_KEY}{timestamp}'.encode('utf-8')
        ).hexdigest()
        payload = dict(params or {})
        payload.update({
            'token': token,
            'timestamp': timestamp,
            'lang': 'ft',
        })
        response = requests.post(
            f"{SHUBA_API_BASE}/{endpoint.lstrip('/')}",
            data=payload,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': SHUBA_API_USER_AGENT,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data == '-1' or data == -1:
            raise RuntimeError('69shuba API rejected the request signature')
        if isinstance(data, dict):
            code = data.get('code')
            if str(code).isdigit() and int(code) >= 400:
                raise RuntimeError(
                    data.get('msg') or f'69shuba API error {code}'
                )
        return data

    def _69shuba_parse_book(self, url):
        """Fetch 69shuba metadata and its complete chapter catalog."""
        self._stop_requested = False
        book_id = self._69shuba_book_id(url)
        if not book_id:
            self.log('[69shuba] ERROR: Could not extract book id from URL.')
            return None

        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme or 'https'
        host = parsed.netloc or '69shuba.tw'
        origin = f'{scheme}://{host}'
        book_url = f'{origin}/book/{book_id}/'
        self.log(f'[69shuba] Fetching book {book_id}...')

        try:
            info = self._69shuba_api_request(
                'api_info.php', {'aid': book_id}
            )
            catalog = self._69shuba_api_request(
                'api_indexlist.php', {'aid': book_id}
            )
        except Exception as e:
            self.log(f'ERROR: [69shuba] API request failed: {e}')
            return None

        if not isinstance(info, dict) or not isinstance(catalog, dict):
            self.log('ERROR: [69shuba] API returned an invalid book response.')
            return None

        rows = catalog.get('list')
        if not isinstance(rows, list):
            rows = catalog.get('chapterrows') or catalog.get('chapter_rows')
        if not isinstance(rows, list):
            rows = []

        chapters = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            chapter_id = str(
                row.get('chapterid') or row.get('cid') or ''
            ).strip()
            if not chapter_id.isdigit() or chapter_id in seen:
                continue
            seen.add(chapter_id)
            name = str(
                row.get('chaptername')
                or row.get('cname')
                or f'Chapter {len(chapters) + 1}'
            ).strip()
            chapter_path = row.get('chapter_url')
            if not chapter_path:
                chapter_path = f'/read/{book_id}/{chapter_id}'
            chapter_url = urllib.parse.urljoin(origin + '/', chapter_path)
            chapters.append({
                'url': chapter_url,
                'name': name,
                'fullName': name,
                'isVIP': False,
                'isPaid': False,
                'isAccessible': True,
                '_chapterId': chapter_id,
            })

        if not chapters:
            self.log('ERROR: [69shuba] No chapters found in the catalog.')
            return None

        title = str(
            info.get('articlename') or info.get('bookname') or ''
        ).strip()
        author = str(info.get('author') or '').strip()
        introduction = str(
            info.get('intro_des') or info.get('introduction') or ''
        ).strip()
        introduction_html = str(info.get('intro_p') or '').strip()
        if not introduction_html and introduction:
            introduction_html = f'<p>{html.escape(introduction)}</p>'

        cover_url = str(
            info.get('img_url') or info.get('coverUrl') or ''
        ).strip()
        if cover_url:
            cover_url = urllib.parse.urljoin(origin + '/', cover_url)

        tags = info.get('keywords_arr')
        if not isinstance(tags, list):
            tags = [
                value.strip()
                for value in str(info.get('keywords') or '').split('|')
                if value.strip()
            ]
        else:
            tags = [str(value).strip() for value in tags if str(value).strip()]
        category = str(
            info.get('sortname') or info.get('sortname_2') or ''
        ).strip()
        status = str(info.get('isfull') or '').strip()

        data = {
            'bookname': title or f'69shuba Book {book_id}',
            'author': author or 'Unknown',
            'coverUrl': cover_url,
            'description': introduction,
            'introduction': introduction,
            'introductionHTML': introduction_html,
            'tags': tags,
            'category': [category] if category else [],
            'status': status,
            'bookUrl': book_url,
            'chapterCount': len(chapters),
            'chapters': chapters,
            'language': 'zh',
            '_69shuba': True,
            '_69shuba_origin': origin,
            '_69shuba_book_id': book_id,
        }
        self._book_data = data
        self._book_url = book_url
        self.log(
            f"[69shuba] Book: {data['bookname']} by {data['author']} - "
            f"{len(chapters)} chapters"
        )
        return data

    @staticmethod
    def _69shuba_build_chapter_result(payload, source_name=''):
        if not isinstance(payload, dict) or payload.get('contentMissing'):
            return None

        raw_paragraphs = payload.get('paragraphs')
        if isinstance(raw_paragraphs, list):
            paragraphs = [
                str(value).replace('\r', '').strip()
                for value in raw_paragraphs
                if value is not None
                and str(value).replace('\r', '').strip()
            ]
        else:
            content = str(
                payload.get('content') or payload.get('article_content') or ''
            ).replace('\r\n', '\n').replace('\r', '\n')
            paragraphs = [
                line.strip() for line in content.split('\n') if line.strip()
            ]

        if not paragraphs:
            return None

        chapter_name = str(
            payload.get('chaptername')
            or payload.get('chapter_name')
            or source_name
            or 'Chapter'
        ).strip()
        content_text = '\n'.join(paragraphs)
        content_html = '\n'.join(
            f'<p>{html.escape(paragraph)}</p>'
            for paragraph in paragraphs
        )
        return {
            'chapterName': chapter_name,
            'sourceChapterName': source_name or chapter_name,
            'contentText': content_text,
            'contentHtml': (
                '<div class="shuba-content">\n'
                f'{content_html}\n'
                '</div>'
            ),
            'contentCss': (
                '.shuba-content p { margin: 0 0 0.75em; '
                'line-height: 1.8; }'
            ),
            'images': [],
        }

    def _69shuba_parse_chapter(self, chapter_url, chapter_name):
        book_id = (
            self._69shuba_book_id(chapter_url)
            or str((self._book_data or {}).get('_69shuba_book_id') or '')
        )
        chapter_id = self._69shuba_chapter_id(chapter_url)
        if not book_id or not chapter_id:
            self.log(f'  [69shuba] Invalid chapter URL: {chapter_url}')
            return None
        try:
            payload = self._69shuba_api_request(
                'api_read.php',
                {'aid': book_id, 'cid': chapter_id},
            )
        except Exception as e:
            self.log(
                f'  [69shuba] Chapter request failed: {chapter_name}: {e}'
            )
            return None

        result = self._69shuba_build_chapter_result(payload, chapter_name)
        if result is None:
            self.log(f'  [69shuba] Empty chapter: {chapter_name}')
        return result

    # ------------------------------------------------------------------
    # Yeduji (夜读集) native scraper
    # ------------------------------------------------------------------
    _YEDUJI_UA = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )

    def _yeduji_fetch(self, url):
        """Fetch a Yeduji page via urllib and return the HTML."""
        req = urllib.request.Request(url, headers={
            'User-Agent': self._YEDUJI_UA,
            'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode('utf-8', errors='replace')

    def _yeduji_parse_book(self, url):
        """Scrape Yeduji book metadata and chapter list.

        Fetches the book index page and the chapter list page.
        Returns the standard book data dict or None on error.
        """
        self._stop_requested = False
        self.log(f"[Yeduji] Navigating to: {url}")

        # Extract book ID from URL
        m = re.search(r'/book/(\d+)', url)
        if not m:
            self.log("ERROR: [Yeduji] Could not extract book ID from URL.")
            return None
        book_id = m.group(1)

        # Normalise URL
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        index_url = f"{origin}/book/{book_id}/"

        try:
            index_html = self._yeduji_fetch(index_url)
        except Exception as e:
            self.log(f"ERROR: [Yeduji] Index page fetch failed: {e}")
            return None

        # --- Metadata ---
        # Title from <h1>
        title_match = re.search(r'<h1[^>]*>(.*?)</h1>', index_html, re.S)
        title = html.unescape(title_match.group(1).strip()) if title_match else ''

        # Extract structured fields from <dl><dt>Key</dt><dd>Value</dd></dl>
        # in the <div class="info"> block.
        info_match = re.search(
            r'<div\s+class=["\']info["\'][^>]*>(.*?)</div>',
            index_html, re.S,
        )
        info_html = info_match.group(1) if info_match else ''

        def _dl_text(label):
            """Extract plain text from <dl><dt>label</dt><dd>text</dd></dl>."""
            m = re.search(
                r'<dt>\s*' + re.escape(label)
                + r'\s*</dt>\s*<dd>(.*?)</dd>',
                info_html, re.S,
            )
            if not m:
                return ''
            return html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()

        def _dl_links(label):
            """Extract link texts from <dl><dt>label</dt><dd><a>...</a>...</dd>."""
            m = re.search(
                r'<dt>\s*' + re.escape(label)
                + r'\s*</dt>\s*<dd>(.*?)</dd>',
                info_html, re.S,
            )
            if not m:
                return []
            return [
                html.unescape(t.strip())
                for t in re.findall(r'<a[^>]*>(.*?)</a>', m.group(1), re.S)
                if t.strip()
            ]

        # Author — prefer structured <dl> field, fall back to <title>
        author = _dl_text('作者')
        if not author:
            title_tag = re.search(r'<title>(.*?)</title>', index_html, re.S)
            if title_tag:
                author_m = re.search(r'-\s*(.+?)\s*著\s*-', title_tag.group(1))
                if author_m:
                    author = author_m.group(1).strip()
        if not title:
            title_tag = re.search(r'<title>(.*?)</title>', index_html, re.S)
            if title_tag:
                parts = title_tag.group(1).split(' - ')
                title = parts[0].strip() if parts else ''

        # Tags
        tags = _dl_links('标签')
        # Category
        category = _dl_links('分类')
        # Status
        status = _dl_text('状态')

        # Cover image
        cover_url = ''
        cover_match = re.search(
            r'<img[^>]+src=["\']([^"\'/][^"\']*?/data/cover/[^"\']*)["\'\s]',
            index_html, re.I,
        )
        if not cover_match:
            cover_match = re.search(
                r'<img[^>]+src=["\']([^"\']*?/data/cover/[^"\']*)["\'\s]',
                index_html, re.I,
            )
        if cover_match:
            cover_url = urllib.parse.urljoin(index_url, cover_match.group(1))

        # Description (synopsis text)
        description = ''
        desc_html = ''
        desc_match = re.search(
            r'<div\s+class=["\']desc["\'][^>]*>(.*?)</div>',
            index_html, re.S,
        )
        if desc_match:
            raw_desc = desc_match.group(1)
            # Strip the "desc-more" image and the "desc-content" wrapper
            inner = re.sub(r'<img[^>]*>', '', raw_desc)
            inner_m = re.search(
                r'<p[^>]*class=["\'][^"\']*desc-content[^"\']*["\'][^>]*>'
                r'(.*?)</p>',
                inner, re.S,
            )
            if inner_m:
                inner = inner_m.group(1)
            desc_text = html.unescape(
                re.sub(r'<[^>]+>', '', inner)
            ).strip()
            description = desc_text
            # Preserve <br> as HTML for the EPUB intro page
            desc_html = re.sub(
                r'<(?!br\s*/?)[^>]+>', '', inner
            ).strip()

        self.log(f"[Yeduji] Title: {title}")
        self.log(f"[Yeduji] Author: {author or 'Unknown'}")
        if tags:
            self.log(
                f"[Yeduji] Tags: {', '.join(tags[:8])}"
                + ('...' if len(tags) > 8 else '')
            )
        if category:
            self.log(f"[Yeduji] Category: {', '.join(category)}")

        # --- Chapter list (from /book/{id}/list/) ---
        list_url = f"{origin}/book/{book_id}/list/"
        try:
            list_html = self._yeduji_fetch(list_url)
        except Exception as e:
            self.log(f"ERROR: [Yeduji] Chapter list fetch failed: {e}")
            return None

        chapters = []
        # Pattern: <a data-chapterId="..." href="/book/ID/CHAP.html">
        #            <h4>ChapterName</h4>
        #            <small class="text-muted">VIP|免费</small>
        #          </a>
        for m in re.finditer(
            r'<a\s+data-chapterId=["\']([^"\']*)["\'\s][^>]*'
            r'href=["\']([^"\']*?/book/\d+/\d+\.html)["\'\s][^>]*>'
            r'(.*?)</a>',
            list_html, re.S,
        ):
            chapter_id = m.group(1)
            href = m.group(2)
            inner = m.group(3)

            # Chapter name from <h4>
            name_match = re.search(r'<h4[^>]*>(.*?)</h4>', inner, re.S)
            name = html.unescape(
                re.sub(r'<[^>]+>', '', name_match.group(1)).strip()
            ) if name_match else f'Chapter {chapter_id}'

            # VIP status from <small>VIP</small>
            is_vip = bool(re.search(
                r'<small[^>]*>\s*VIP\s*</small>', inner, re.I
            ))

            full_url = urllib.parse.urljoin(list_url, href)
            chapters.append({
                'url': full_url,
                'name': name,
                'fullName': name,
                'isVIP': is_vip,
                'isPaid': is_vip,
                'isAccessible': not is_vip,
                '_chapterId': chapter_id,
            })

        if not chapters:
            self.log("ERROR: [Yeduji] No chapters found on list page.")
            return None

        free_count = sum(1 for ch in chapters if not ch['isVIP'])
        vip_count = sum(1 for ch in chapters if ch['isVIP'])
        self.log(
            f"[Yeduji] Found {len(chapters)} chapters "
            f"({free_count} free, {vip_count} VIP)"
        )

        data = {
            'bookname': title or f'Book {book_id}',
            'author': author or 'Unknown',
            'coverUrl': cover_url,
            'description': description,
            'introduction': description,
            'introductionHTML': desc_html.replace('\n', '<br/>\n')
                                if desc_html else '',
            'tags': tags,
            'category': category,
            'status': status,
            'chapterCount': len(chapters),
            'chapters': chapters,
            '_yeduji': True,
            '_yeduji_origin': origin,
            '_yeduji_book_id': book_id,
        }
        self._book_data = data
        self._book_url = index_url
        return data

    def _yeduji_parse_chapter(self, chapter_url, chapter_name,
                              is_paid=False):
        """Fetch one Yeduji chapter's content.

        For VIP chapters, only the preview paragraphs are available.
        The VIP notice text is stripped from the output.

        Returns the standard chapter data dict or None on error.
        """
        try:
            ch_html = self._yeduji_fetch(chapter_url)
        except Exception as e:
            self.log(f"  [Yeduji] Chapter fetch error: {e}")
            return None

        # Title from <h1 class="title">
        title_match = re.search(
            r'<h1[^>]*class=["\']title["\'][^>]*>(.*?)</h1>',
            ch_html, re.S,
        )
        if not title_match:
            title_match = re.search(r'<h1[^>]*>(.*?)</h1>', ch_html, re.S)
        ch_title = html.unescape(
            title_match.group(1).strip()
        ) if title_match else chapter_name

        # Content from <div class="content">...</div>
        content_match = re.search(
            r'<div\s+class=["\']content["\'][^>]*>(.*?)</div>',
            ch_html, re.S,
        )
        if not content_match:
            self.log(
                f"  [Yeduji] No content div found for: {chapter_name}"
            )
            return None

        content_html_raw = content_match.group(1)

        # Detect if this is a VIP preview by looking for the notice.
        # The VIP notice is a <p> containing "以下内容为VIP专属".
        has_vip_notice = bool(re.search(
            r'以下内容为VIP专属', content_html_raw
        ))
        # Also detect the site promo line
        has_promo = bool(re.search(
            r'夜读集由爱发电', content_html_raw
        ))

        # Extract paragraphs, stripping VIP notice and promo lines
        paragraphs = []
        for p_match in re.finditer(
            r'<p[^>]*>(.*?)</p>', content_html_raw, re.S
        ):
            p_text = p_match.group(1).strip()
            plain = html.unescape(re.sub(r'<[^>]+>', '', p_text)).strip()
            # Skip VIP notice and promo lines
            if '以下内容为VIP专属' in plain:
                continue
            if '夜读集由爱发电' in plain:
                continue
            if '请先' in plain and '登录' in plain and '后查看完整内容' in plain:
                continue
            if plain:
                paragraphs.append((plain, p_text))

        if not paragraphs:
            self.log(
                f"  [Yeduji] No text content extracted: {chapter_name}"
            )
            return None

        # Log VIP preview warning
        if has_vip_notice or is_paid:
            self.log(
                f"  [Yeduji] ⚠ PREVIEW ONLY (VIP content): {chapter_name} "
                f"({len(paragraphs)} paragraphs)"
            )

        # Build output
        full_text = '\n'.join(p[0] for p in paragraphs)
        html_parts = []
        for plain, raw_html in paragraphs:
            # Use the original HTML fragment (preserving any inline formatting)
            html_parts.append(f'<p>{raw_html}</p>')
        content_html = '\n'.join(html_parts)

        result = {
            'chapterName': ch_title or chapter_name,
            'sourceChapterName': chapter_name,
            'contentText': full_text,
            'contentHtml': content_html,
            'images': [],
        }
        if has_vip_notice or is_paid:
            result['_preview'] = True
        return result


    def parse_book(self, url):
        """Navigate to the book URL and extract metadata + chapter list.

        Returns the parsed book dict or None on error.
        """
        if self.is_1qxs(url):
            self.log(
                '[1qxs] Detected 1qxs URL, using the direct HTTP scraper.'
            )
            return self._1qxs_parse_book(url)
        if self.is_69shuba(url):
            self.log(
                '[69shuba] Detected 69shuba.tw URL, using native scraper.'
            )
            return self._69shuba_parse_book(url)
        if self.is_global_novelpia(url):
            self.log(
                '[Global Novelpia] Detected Global Novelpia URL, using the '
                "integrated pia-scrap API scraper."
            )
            return self._global_novelpia_parse_book(url)
        if self.is_ridibooks(url):
            self.log(
                '[Ridi] Detected Ridibooks webnovel URL, using the '
                'contributed native scraper.'
            )
            return self._ridi_parse_book(url)
        if self.is_novelpia(url):
            self.log(
                '[Novelpia] Detected Novelpia URL, using the External '
                'Downloader browser profile.'
            )
            return self._novelpia_parse_book(url)
        # KakaoPage: use native scraper instead of JS rules
        if self.is_kakaopage(url):
            self.log("[KakaoPage] Detected KakaoPage URL, using native scraper.")
            return self._kakao_parse_book(url)
        if self.is_qidian(url):
            self.log("[Qidian] Detected Qidian URL, using native scraper.")
            return self._qidian_parse_book(url)
        if self.is_ntk_novel(url):
            return self._ntk_parse_book(url)
        if self.is_yeduji(url):
            self.log("[Yeduji] Detected Yeduji URL, using native scraper.")
            return self._yeduji_parse_book(url)
        if self.is_munpia(url):
            self.log("[Munpia] Detected Munpia URL, using native scraper.")
            return self._munpia_parse_book(url)

        if not self._gm_stubs_js or not self._rules_js or not self._bridge_js:
            self.log(
                "ERROR: gm_stubs.js, rules-lib.js, or bridge.js not found. "
                "Build the novel-downloader bundle first."
            )
            return None

        if not self._page:
            self.start()

        self._stop_requested = False
        self.log(f"Navigating to: {url}")

        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            self.log(f"ERROR: Page load failed: {e}")
            return None

        self.log("Page loaded. Injecting stubs and rules...")

        # Inject GM API stubs first (required by the rules bundle)
        try:
            self._install_bridge_bindings(self._page)
            self._page.evaluate(self._gm_stubs_js)
        except Exception as e:
            self.log(f"ERROR: GM stubs injection failed: {e}")
            return None

        # Inject the rules library
        try:
            self._page.evaluate(self._rules_js)
        except Exception as e:
            self.log(f"ERROR: Rules injection failed: {e}")
            return None

        # Inject the bridge
        try:
            self._page.evaluate(self._bridge_js)
        except Exception as e:
            self.log(f"ERROR: Bridge injection failed: {e}")
            return None

        # Check bridge is ready
        ready = self._page.evaluate("window.__ND_BRIDGE_READY === true")
        if not ready:
            self.log("ERROR: Bridge not ready after injection.")
            return None

        self.log("Rules injected. Parsing book...")

        # Call bookParse (async, returns promise)
        try:
            result_json = self._page.evaluate(
                "window.__ND_parseBook()"
            )
        except Exception as e:
            self.log(f"ERROR: bookParse failed: {e}")
            return None

        if not result_json:
            self.log("ERROR: bookParse returned empty result.")
            return None

        try:
            data = json.loads(result_json)
        except (json.JSONDecodeError, TypeError) as e:
            self.log(f"ERROR: Failed to parse bookParse result: {e}")
            return None

        if "error" in data:
            self.log(f"ERROR: bookParse error: {data['error']}")
            if "stack" in data:
                self.log(f"  Stack: {data['stack'][:200]}")
            return None

        data = self._apply_syosetu_amazon_cover_fallback(url, data)
        self._book_data = data
        self._book_url = url
        self.log(
            f"Book: {data.get('bookname', '?')} by {data.get('author', '?')} "
            f"— {data.get('chapterCount', 0)} chapters"
        )
        return data

    def parse_chapter(self, index, chapter_info, interval=0.5, page=None,
                      log_errors=True, interval_max=None):
        """Parse a single chapter's content.

        Args:
            index: Chapter index (0-based).
            chapter_info: Dict with 'url', 'name', 'isVIP', 'isPaid'.
            interval: Minimum delay in seconds after fetching.
            interval_max: Maximum delay. ``None`` keeps the legacy fixed
                          delay represented by ``interval``.
            page: Specific Playwright page to use (for parallel downloads).
                  Defaults to the primary page.

        Returns the parsed chapter dict or None on error.
        """
        if self._stop_requested:
            return None

        if self._book_data and self._book_data.get('_1qxs'):
            url = chapter_info.get('url', '')
            name = (
                chapter_info.get('fullName', '')
                or chapter_info.get('name', '')
            )
            result = self._1qxs_parse_chapter(url, name)
            self._sleep_interval(interval, interval_max)
            return result

        if self._book_data and self._book_data.get('_69shuba'):
            url = chapter_info.get('url', '')
            name = (
                chapter_info.get('fullName', '')
                or chapter_info.get('name', '')
            )
            result = self._69shuba_parse_chapter(url, name)
            self._sleep_interval(interval, interval_max)
            return result

        if self._book_data and self._book_data.get('_global_novelpia'):
            url = chapter_info.get('url', '')
            name = (
                chapter_info.get('fullName', '')
                or chapter_info.get('name', '')
            )
            result = self._global_novelpia_parse_chapter(url, name)
            self._sleep_interval(interval, interval_max)
            return result

        if self._book_data and self._book_data.get('_ridibooks'):
            url = chapter_info.get('url', '')
            name = (
                chapter_info.get('fullName', '')
                or chapter_info.get('name', '')
            )
            result = self._ridi_parse_chapter(
                url,
                name,
                page=page or self._page,
            )
            self._sleep_interval(interval, interval_max)
            return result

        if self._book_data and self._book_data.get('_novelpia'):
            url = chapter_info.get('url', '')
            name = (
                chapter_info.get('fullName', '')
                or chapter_info.get('name', '')
            )
            result = self._novelpia_parse_chapter(
                url,
                name,
                page=page or self._page,
            )
            result = self._novelpia_tag_chapter_result(result, chapter_info)
            self._sleep_interval(interval, interval_max)
            return result

        # KakaoPage: use native viewer scraping
        if self._book_data and self._book_data.get('_kakaopage'):
            url = chapter_info.get('url', '')
            name = chapter_info.get('name', '')
            full_name = chapter_info.get('fullName', '') or name
            target_page = page or self._page
            result = self._kakao_parse_chapter(url, full_name,
                                               page=target_page)
            self._sleep_interval(interval, interval_max)
            return result
        if self._book_data and self._book_data.get('_ntk_novel'):
            url = chapter_info.get('url', '')
            name = chapter_info.get('fullName', '') or chapter_info.get('name', '')
            target_page = page or self._page
            result = self._ntk_parse_chapter(url, name, page=target_page)
            self._sleep_interval(interval, interval_max)
            return result
        if self._book_data and self._book_data.get('_yeduji'):
            url = chapter_info.get('url', '')
            name = chapter_info.get('fullName', '') or chapter_info.get('name', '')
            is_paid = chapter_info.get('isPaid', False)
            result = self._yeduji_parse_chapter(url, name, is_paid=is_paid)
            self._sleep_interval(interval, interval_max)
            return result
        if self._book_data and self._book_data.get('_munpia'):
            url = chapter_info.get('url', '')
            name = chapter_info.get('fullName', '') or chapter_info.get('name', '')
            result = self._munpia_parse_chapter(url, name, page=page)
            self._sleep_interval(interval, interval_max)
            return result
        if self._book_data and self._book_data.get('_qidian'):
            url = chapter_info.get('url', '')
            name = chapter_info.get('fullName', '') or chapter_info.get('name', '')
            result = self._qidian_parse_chapter(url, name, page=page)
            self._sleep_interval(
                interval,
                interval_max,
                minimum=self._QIDIAN_MIN_INTERVAL,
            )
            return result

        url = chapter_info.get('url', '')
        name = chapter_info.get('name', '')
        is_vip = chapter_info.get('isVIP', False)
        is_paid = chapter_info.get('isPaid', False)
        if self._sfacg_should_prefer_app_api():
            data = self._sfacg_parse_chapter_app_api(url, name)
            if data:
                self._sleep_interval(interval, interval_max)
                return data

        target_page = page or self._page
        if target_page is None:
            # Primary page lost — try to recover
            if page is None and self._ensure_page():
                target_page = self._page
            else:
                if log_errors:
                    self.log(f"  [{index + 1}] No browser page available.")
                return None

        # Escape strings for JS (handle quotes and backslashes)
        def js_escape(s):
            return (s or '').replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '')

        script = (
            f"window.__ND_parseChapter("
            f"'{js_escape(url)}', '{js_escape(name)}', "
            f"{'true' if is_vip else 'false'}, "
            f"{'true' if is_paid else 'false'}"
            f")"
        )

        try:
            result_json = target_page.evaluate(script)
        except Exception as e:
            if log_errors:
                self.log(f"  [{index + 1}] Parse error: {e}")
            return None

        self._sleep_interval(interval, interval_max)

        if not result_json:
            if log_errors:
                self.log(f"  [{index + 1}] Empty result for: {name}")
            return None

        try:
            data = json.loads(result_json)
        except (json.JSONDecodeError, TypeError) as e:
            if log_errors:
                self.log(f"  [{index + 1}] JSON parse error for {name}: {e}")
            return None

        if "error" in data:
            if log_errors:
                self.log(f"  [{index + 1}] Error: {data['error']}")
            return None

        return data

    def parse_chapter_batch(self, batch_info, interval=0.5,
                            _skip_sfacg_app=False, success_callback=None,
                            interval_max=None):
        """Parse multiple chapters concurrently via JS Promise.all.

        Args:
            batch_info: List of dicts with 'url', 'name', 'isVIP', 'isPaid'.
            interval: Minimum delay between sequential requests.
            interval_max: Maximum delay. ``None`` preserves fixed-delay calls.
            success_callback: Optional ``(batch_index, result)`` callback used
                              for live completion logging.

        Returns list of parsed chapter dicts (or None for failures).
        The browser fires all HTTP requests in parallel.
        """
        if self._stop_requested or not batch_info:
            return [None] * len(batch_info)

        def report_success(index, result):
            if (
                not result
                or result.get('_locked')
                or success_callback is None
            ):
                return
            try:
                success_callback(index, result)
            except Exception:
                pass

        if self._book_data and self._book_data.get('_1qxs'):
            from concurrent.futures import ThreadPoolExecutor

            def fetch_1qxs(chapter):
                if self._stop_requested:
                    return None
                return self._1qxs_parse_chapter(
                    chapter.get('url', ''),
                    chapter.get('fullName', '') or chapter.get('name', ''),
                )

            # batch_info is already sized from the user's thread setting.
            max_workers = max(1, len(batch_info))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                return list(executor.map(fetch_1qxs, batch_info))

        if self._book_data and self._book_data.get('_69shuba'):
            from concurrent.futures import ThreadPoolExecutor

            def fetch_69shuba(chapter):
                if self._stop_requested:
                    return None
                return self._69shuba_parse_chapter(
                    chapter.get('url', ''),
                    chapter.get('fullName', '') or chapter.get('name', ''),
                )

            # The dialog already sizes each batch from the user's thread setting,
            # so run every item in that batch concurrently without a site cap.
            max_workers = max(1, len(batch_info))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                return list(executor.map(fetch_69shuba, batch_info))

        if self._book_data and self._book_data.get('_global_novelpia'):
            return self._global_novelpia_parse_chapter_batch(
                batch_info,
                success_callback=success_callback,
            )

        if self._book_data and self._book_data.get('_ridibooks'):
            options = {
                'interval': interval,
                'success_callback': success_callback,
            }
            if interval_max is not None:
                options['interval_max'] = interval_max
            return self._ridi_parse_chapter_batch_parallel(
                batch_info, **options
            )

        # Novelpia is intentionally sequential. One navigation produces one
        # viewer request; there is no speculative concurrency here.
        if self._book_data and self._book_data.get('_novelpia'):
            results = []
            for index, chapter in enumerate(batch_info):
                if self._stop_requested:
                    results.append(None)
                    continue
                result = self._novelpia_parse_chapter(
                    chapter.get('url', ''),
                    (
                        chapter.get('fullName', '')
                        or chapter.get('name', '')
                    ),
                    page=self._page,
                )
                result = self._novelpia_tag_chapter_result(result, chapter)
                results.append(result)
                report_success(index, result)
                if index < len(batch_info) - 1:
                    self._sleep_interval(interval, interval_max)
            return results

        # KakaoPage: no batch API — fall back to sequential downloads
        if self._book_data and self._book_data.get('_kakaopage'):
            results = []
            for i, ch in enumerate(batch_info):
                if self._stop_requested:
                    results.append(None)
                    continue
                data = self._kakao_parse_chapter(
                    ch.get('url', ''),
                    ch.get('fullName', '') or ch.get('name', ''),
                    page=self._page
                )
                results.append(data)
                if i < len(batch_info) - 1:
                    self._sleep_interval(interval, interval_max)
            return results
        # Yeduji: sequential urllib fetches (no browser needed)
        if self._book_data and self._book_data.get('_yeduji'):
            results = []
            for i, ch in enumerate(batch_info):
                if self._stop_requested:
                    results.append(None)
                    continue
                data = self._yeduji_parse_chapter(
                    ch.get('url', ''),
                    ch.get('fullName', '') or ch.get('name', ''),
                    is_paid=ch.get('isPaid', False),
                )
                results.append(data)
                if i < len(batch_info) - 1:
                    self._sleep_interval(interval, interval_max)
            return results
        if self._book_data and self._book_data.get('_munpia'):
            return self._munpia_parse_chapter_batch_parallel(batch_info)
        # Qidian: render one chapter per browser page, up to the UI thread
        # count that the dialog used to size this batch.
        if self._book_data and self._book_data.get('_qidian'):
            return self._qidian_parse_chapter_batch_parallel(batch_info)
        if self._book_data and self._book_data.get('_ntk_novel'):
            if self._ntk_browser_fallback:
                if self._book_data.get('_ntk_kind') == 'webtoon':
                    results = []
                    for index, chapter in enumerate(batch_info):
                        result = self._ntk_fetch_webtoon_chapter_browser(
                            chapter.get('url', ''),
                            chapter.get('fullName', '')
                            or chapter.get('name', ''),
                        )
                        results.append(result)
                        report_success(index, result)
                        if index < len(batch_info) - 1:
                            self._sleep_interval(interval, interval_max)
                    return results

                options = {
                    'interval': interval,
                    'success_callback': success_callback,
                }
                if interval_max is not None:
                    options['interval_max'] = interval_max
                results = self._ntk_fetch_chapter_batch_browser(
                    batch_info, **options
                )
                return results

            from concurrent.futures import ThreadPoolExecutor

            def fetch_one(ch):
                if self._stop_requested:
                    return None
                state = self._ntk_clone_api_state() or self._ntk_api_state
                if self._book_data.get('_ntk_kind') == 'webtoon':
                    return self._ntk_fetch_webtoon_chapter(
                        ch.get('url', ''),
                        ch.get('fullName', '') or ch.get('name', ''),
                        state=state,
                    )
                return self._ntk_fetch_chapter_api(
                    ch.get('url', ''),
                    ch.get('fullName', '') or ch.get('name', ''),
                    state=state,
                )

            max_workers = max(1, min(5, len(batch_info)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                results = list(executor.map(fetch_one, batch_info))
            for index, result in enumerate(results):
                report_success(index, result)
            return results

        if (not _skip_sfacg_app
                and self._sfacg_should_prefer_app_api()):
            from concurrent.futures import ThreadPoolExecutor
            try:
                import requests
            except Exception as e:
                self.log(f"[SFACG] App API unavailable: {e}")
                requests = None
            cookie_header = self._sfacg_app_cookie_header()

            max_workers = max(1, min(len(batch_info), 16))
            launch_delays = [0.0]
            for _index in range(1, len(batch_info)):
                launch_delays.append(
                    launch_delays[-1]
                    + self._random_interval_delay(interval, interval_max)
                )

            def fetch_sfacg_app(item):
                offset, ch = item
                if launch_delays[offset] > 0:
                    time.sleep(launch_delays[offset])
                return self._sfacg_parse_chapter_app_api(
                    ch.get('url', ''),
                    ch.get('name', ''),
                    requests_module=requests,
                    cookie_header=cookie_header,
                )

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                results = list(executor.map(
                    fetch_sfacg_app,
                    enumerate(batch_info),
                ))

            fallback_indices = [
                i for i, result in enumerate(results)
                if result is None
            ]
            if not fallback_indices:
                return results

            fallback_batch = [batch_info[i] for i in fallback_indices]
            fallback_options = {
                'interval': interval,
                '_skip_sfacg_app': True,
            }
            if interval_max is not None:
                fallback_options['interval_max'] = interval_max
            fallback_results = self.parse_chapter_batch(
                fallback_batch, **fallback_options
            )
            for offset, result in zip(fallback_indices, fallback_results):
                results[offset] = result
            return results

        # Ensure the browser page is still alive for non-app or fallback fetches.
        if not self._ensure_page():
            self.log("  Batch aborted: no browser page available.")
            return [None] * len(batch_info)

        # Build JSON payload for the JS batch function
        payload = json.dumps(batch_info, ensure_ascii=False)

        try:
            result_json = self._page.evaluate(
                f"window.__ND_parseChapterBatch('{self._js_escape(payload)}')"
            )
        except Exception as e:
            self.log(f"  Batch parse error: {e}")
            return [None] * len(batch_info)

        if not result_json:
            return [None] * len(batch_info)

        try:
            # result_json is a JSON string containing an array of JSON strings
            raw_results = json.loads(result_json)
        except (json.JSONDecodeError, TypeError) as e:
            self.log(f"  Batch JSON error: {e}")
            return [None] * len(batch_info)

        # Parse each individual result
        parsed = []
        for r in raw_results:
            if not r:
                parsed.append(None)
                continue
            try:
                data = json.loads(r) if isinstance(r, str) else r
                if "error" in data:
                    parsed.append(None)
                else:
                    parsed.append(data)
            except (json.JSONDecodeError, TypeError):
                parsed.append(None)

        return parsed

    @staticmethod
    def _js_escape(s):
        """Escape a string for embedding in a JS single-quoted string."""
        return (s or '').replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '')

    def parse_all_chapters(self, chapters, interval=0.5,
                           start_idx=0, end_idx=None,
                           progress_callback=None, interval_max=None):
        """Parse all chapters sequentially.

        Args:
            chapters: List of chapter info dicts from parse_book().
            interval: Minimum delay between chapter fetches.
            interval_max: Maximum delay; omitted means a fixed interval.
            start_idx: First chapter index (0-based).
            end_idx: Last chapter index (exclusive). None = all.
            progress_callback: Optional fn(current, total) for progress updates.

        Returns list of parsed chapter dicts (None entries for failures).
        """
        if end_idx is None:
            end_idx = len(chapters)

        selected = chapters[start_idx:end_idx]
        total = len(selected)
        results = []

        interval, normalized_max = ExternalScraper._normalize_interval_range(
            interval, interval_max
        )
        interval_display = (
            f'{interval:g}–{normalized_max:g}s'
            if interval != normalized_max
            else f'{interval:g}s'
        )
        self.log(
            f"Downloading {total} chapters (interval: {interval_display})..."
        )

        for i, ch in enumerate(selected):
            if self._stop_requested:
                self.log("Download stopped by user.")
                break

            name = ch.get('name', f'Chapter {start_idx + i + 1}')
            self.log(f"  [{i + 1}/{total}] {name}")

            chapter_number = start_idx + i + 1
            options = {'interval': interval}
            if interval_max is not None:
                options['interval_max'] = normalized_max
            data = self.parse_chapter(start_idx + i, ch, **options)
            if isinstance(data, dict):
                data.setdefault('_chapter_number', chapter_number)
            results.append(data)

            if progress_callback:
                progress_callback(i + 1, total)

        return results
