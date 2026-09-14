// 从 HTML 提取 core 脚本并验证去重/合并逻辑
const fs = require('fs');
const html = fs.readFileSync('C:/Users/nzsm/Doubao/chats/2026-09-14/new-chat/tvbox-server/影视整合去重工具.html', 'utf8');
const m = html.match(/<script id="core">([\s\S]*?)<\/script>/);
if (!m) { console.error('未找到 core 脚本'); process.exit(1); }
eval(m[1]); // 定义 normalizeName / splitLines / fillEmptyFields / dedupMerge

let pass = 0, fail = 0;
function assert(name, cond) {
  if (cond) { pass++; console.log('PASS', name); }
  else { fail++; console.log('FAIL', name); }
}

// 用例10：非法转义修复
{
  const src = '{"a":"it\'s \\x ok","b":"quote \\" keep \\\\ back \\n newline \\u4e2d \\/ slash"}';
  const out = fixEscapes(src);
  const obj = JSON.parse(out);
  assert('转义修复: \\\' 变 \'', obj.a === "it's x ok");
  assert('转义修复: \\x 变 x', obj.a.indexOf('x ok') !== -1);
  assert('转义修复: \\" 保留', obj.b.indexOf('quote "') !== -1);
  assert('转义修复: \\\\ 保留', obj.b.indexOf('\\ back') !== -1);
  assert('转义修复: \\n 保留', obj.b.indexOf('\n') !== -1);
  assert('转义修复: \\u 保留', obj.b.indexOf('中') !== -1);
  assert('转义修复: \\/ 保留', obj.b.indexOf('slash') !== -1);
}

// 用例11：完整链路（注释 + 非法转义 + 正常数据）
{
  const src = `// 注释头
[
  {"name":"影片A", "play_url":"正片$https://x.com/a.mp4"},
  {"name":"It\\'s OK", "play_url":"正片$https://x.com/b.mp4"} /* 尾部注释 */
]`;
  const cleaned = stripJsonComments(src);
  const fixed = fixEscapes(cleaned);
  const arr = JSON.parse(fixed);
  assert('完整链路: 解析为数组 2 条', Array.isArray(arr) && arr.length === 2);
  assert('完整链路: URL 完整', arr[0].play_url === '正片$https://x.com/a.mp4');
  assert('完整链路: 非法转义修复', arr[1].name === "It's OK");
}

// 用例13：苹果CMS 格式（{list:[...]} + vod_* 字段）
{
  const pool = new Map();
  const data = {code:1, list:[
    {vod_id:'1', vod_name:'CMS片A', vod_pic:'http://p/1.jpg', vod_remarks:'HD', type_name:'电影', vod_play_from:'m3u8', vod_play_url:'正片$http://v/1.mp4'},
    {vod_id:'2', vod_name:'CMS片A', vod_play_url:'正片$http://v/1b.mp4'}
  ]};
  const res = resolveItems(data);
  assert('CMS格式: 识别为 vods', res.type === 'vods');
  const r = dedupMerge(res.items, pool);
  assert('CMS格式: added=1 merged=1', r.added === 1 && r.merged === 1);
  const it = pool.get(normalizeName('CMS片A'));
  assert('CMS格式: vod_play_url 字段映射', it.play_url.indexOf('http://v/1.mp4') !== -1);
  assert('CMS格式: 线路合并', splitLines(it.play_url).length === 2);
}

// 用例14：站点配置识别（不导入，明确提示）
{
  const data = {spider:'./jar/x', sites:[{key:'a',type:3},{key:'b',type:0},{key:'c',type:3}]};
  const res = resolveItems(data);
  assert('站点配置: 识别为 siteconfig', res.type === 'siteconfig');
  assert('站点配置: 统计 3 个源', res.info.sites === 3);
}

// 用例15：未知对象
{
  const res = resolveItems({foo:1, bar:'x'});
  assert('未知对象: 识别为 unknown', res.type === 'unknown');
}

// 用例16：数组直接识别
{
  const res = resolveItems([{name:'a', play_url:'u'}]);
  assert('数组: 识别为 vods', res.type === 'vods' && res.info.count === 1);
}

// 用例17：真实顺财配置端到端识别（文件存在时）
{
  const fs2 = require('fs');
  try {
    let s = fs2.readFileSync('C:/Users/nzsm/Doubao/chats/2026-09-14/new-chat/shuncai_full.txt', 'utf8');
    const idx = s.indexOf('{');
    s = s.slice(idx);                       // 去掉下载包装头部
    const cleaned = removeStrayText(fixEscapes(removeTrailingCommas(sanitizeWhitespace(stripJsonComments(s)))));
    const data = JSON.parse(cleaned);
    const res = resolveItems(data);
    assert('顺财真实文件: 剥注释+清洗+修转义后可解析', true);
    assert('顺财真实文件: 识别为 siteconfig', res.type === 'siteconfig');
    // 真实站点数 302；此前按原文文本正则数的 309 含注释/字符串内容里的 key，属口径偏差
    assert('顺财真实文件: 统计 302 个源', res.info.sites === 302);
  } catch (e) {
    assert('顺财真实文件: 端到端可解析', false);
    console.log('  诊断:', e.message);
  }
}

// 用例18：U+00A0 不间断空格等非法空白清洗
{
  const src = '{"a":1,\u00A0"b":2\u3000,"c":"x\u00A0y\uFEFF"}';
  const out = sanitizeWhitespace(src);
  const obj = JSON.parse(out);
  assert('空白清洗: U+00A0 键前可解析', obj.a === 1 && obj.b === 2);
  assert('空白清洗: 字符串内 U+00A0 转普通空格', obj.c === 'x y');
}

// 用例19：结构级杂散字符清理（{H 孤立字母），且不误伤字符串内容
{
  const src = '{"ext": {H\n"站名": "222av.me", "note": "含, A和{B的文字"},"d":1}';
  const out = removeStrayText(src);
  const obj = JSON.parse(out);
  assert('杂散字符: { 后孤立 H 被删', obj.ext['站名'] === '222av.me');
  assert('杂散字符: 字符串内 , A 不误伤', obj.ext.note === '含, A和{B的文字');
  assert('杂散字符: 后续字段正常', obj.d === 1);
}

// 用例20：误粘入的对话文字被清除（模拟顺财文件中的情况）
{
  const src = '{"list":[{"x":1},\n{\n对于这个链接，你的工具能不能完善下，我用你的工具各种问题，能不能完美解决啊，给我生成好的工具\n"name":"抖音嗅探","regex":["item_id="]}]}';
  const out = removeStrayText(src);
  const obj = JSON.parse(out);
  assert('对话文字: 清除后解析成功', obj.list[1].name === '抖音嗅探');
  assert('对话文字: 前序对象保留', obj.list[0].x === 1);
}

// 用例21：嵌套结构不被误删
{
  const src = '{"a":{"b":[1,2]},"c":[{"d":3}], "e": {"f": 4}}';
  const out = removeStrayText(src);
  const obj = JSON.parse(out);
  assert('嵌套结构: 深层对象保留', obj.a.b.length === 2 && obj.c[0].d === 3 && obj.e.f === 4);
}

// 用例22：TVBox 配置合并去重（跨配置去重、配置内保留）
{
  const c1 = {spider:'./jar/a', logo:'logo1', sites:[{key:'A',name:'源A'},{key:'B',name:'源B'},{key:'B',name:'源B2'}], lives:[{key:'L1',name:'直播1'}]};
  const c2 = {logo:'logo2', sites:[{key:'B',name:'源B'},{key:'C',name:'源C'}], lives:[{key:'L1',name:'直播1'},{key:'L2',name:'直播2'}], parses:[{name:'p1'}]};
  const m = mergeConfigs([c1, c2]);
  assert('配置合并: 配置内 B 重复保留、跨配置 B/C 去重为 4', m.sites.length === 4);
  assert('配置合并: lives 去重为 2', m.lives.length === 2);
  assert('配置合并: parses 保留 1', m.parses.length === 1);
  assert('配置合并: 顶层字段取第一份', m.logo === 'logo1' && m.spider === './jar/a');
}

// 用例23：源测试链接提取
{
  const a = {key:'a', name:'A', api:'csp_XBPQ', jar:'https://example.com/spider.jar'};
  const b = {key:'b', name:'B', api:'https://api.example.com/vod.php', ext:''};
  const c = {key:'c', name:'C', api:'csp_AppRJ', ext:'http://ext.example.com/x.json'};
  const d = {key:'d', name:'D', api:'csp_DrPy', ext:{url:'https://obj.example.com/ext'}};
  const e = {key:'e', name:'E', api:'csp_X'};
  assert('测速链接: jar 优先', srcTestUrl(a) === 'https://example.com/spider.jar');
  assert('测速链接: api 为 http 时用 api', srcTestUrl(b) === 'https://api.example.com/vod.php');
  assert('测速链接: ext 字符串 URL', srcTestUrl(c) === 'http://ext.example.com/x.json');
  assert('测速链接: ext 对象 url', srcTestUrl(d) === 'https://obj.example.com/ext');
  assert('测速链接: 无链接返回空', srcTestUrl(e) === '');
}

// 用例24：尾逗号清理（数组/对象尾逗号删除，字符串内不受影响）
{
  const src = '{"list":["区", ] , "a":1,}';
  const out = removeTrailingCommas(src);
  const obj = JSON.parse(out);
  assert('尾逗号: 数组尾逗号删除', obj.list.length === 1 && obj.list[0] === '区');
  assert('尾逗号: 对象尾逗号删除', obj.a === 1);
  const src2 = '{"a":"x, ] y","b":[1, ]}';
  const obj2 = JSON.parse(removeTrailingCommas(src2));
  assert('尾逗号: 字符串内不受影响', obj2.a === 'x, ] y' && obj2.b.length === 1);
}

// 用例12：注释剥离
{
  const src = `//接口内容来自网络数位大佬
{
  /* 块注释
     多行 */
  "name": "影片A", // 行注释
  "play_url": "正片$https://x.com/a.mp4?x=1//2",
  "note": "保留 /* 字符串内 */ 内容"
}`;
  const out = stripJsonComments(src);
  assert('注释剥离: 行注释移除', out.indexOf('接口内容来自网络') === -1);
  assert('注释剥离: 块注释移除', out.indexOf('块注释') === -1);
  assert('注释剥离: 字符串内 https:// 保留', out.indexOf('https://x.com/a.mp4') !== -1);
  assert('注释剥离: 字符串内 /* */ 保留', out.indexOf('/* 字符串内 */') !== -1);
  const obj = JSON.parse(out);
  assert('注释剥离: 解析成功', obj.name === '影片A');
  assert('注释剥离: URL 完整', obj.play_url === '正片$https://x.com/a.mp4?x=1//2');
}

// 用例1：完全重复 → dup
{
  const pool = new Map();
  const r = dedupMerge([
    {name:'星海远征', play_url:'正片$https://x.com/a.mp4'},
    {name:'星海远征', play_url:'正片$https://x.com/a.mp4'}
  ], pool);
  assert('完全重复: added=1', r.added === 1);
  assert('完全重复: dup=1', r.dup === 1);
  assert('完全重复: merged=0', r.merged === 0);
  assert('完全重复: 池内 1 条', pool.size === 1);
}

// 用例2：同名不同地址 → merged 合并线路
{
  const pool = new Map();
  const r = dedupMerge([
    {name:'星海远征', play_url:'正片$https://x.com/a.mp4'},
    {name:'星海远征', play_url:'正片$https://x.com/b.mp4'}
  ], pool);
  assert('同名异线路: merged=1', r.merged === 1);
  assert('同名异线路: added=1', r.added === 1);
  assert('同名异线路: 线路合并为2条', splitLines(pool.get(normalizeName('星海远征')).play_url).length === 2);
}

// 用例3：同名不同线路且第二条带新线路 + 重复线路混合
{
  const pool = new Map();
  dedupMerge([
    {name:'剧', play_url:'L1$u1$$$L2$u2'},
    {name:'剧', play_url:'L2$u2$$$L3$u3'}   // L2 重复，L3 新增
  ], pool);
  const lines = splitLines(pool.get(normalizeName('剧')).play_url);
  assert('混合: 合并后 3 条线路', lines.length === 3);
  assert('混合: 无重复线路', new Set(lines).size === 3);
}

// 用例4：归一化（空格/全角/大小写）
{
  const pool = new Map();
  const r = dedupMerge([
    {name:' 星海远征 ', play_url:'正片$u1'},
    {name:'星海远征', play_url:'正片$u1'},
    {name:'ｓｔａｒ', play_url:'正片$u2'},
    {name:'STAR', play_url:'正片$u2'}
  ], pool);
  assert('归一化: 全角/空格/大小写识别为重复 dup=2', r.dup === 2);
  assert('归一化: added=2', r.added === 2);
}

// 用例5：无效条目跳过
{
  const pool = new Map();
  const r = dedupMerge([
    {name:'', play_url:'正片$u'},
    {name:'无名', play_url:''},
    {name:null, play_url:null}
  ], pool);
  assert('无效条目: invalid=3', r.invalid === 3);
  assert('无效条目: 池内 0 条', pool.size === 0);
}

// 用例6：重复导入同一批 → 第二次全 dup
{
  const pool = new Map();
  const batch = [
    {name:'A', play_url:'正片$u1'},
    {name:'B', play_url:'第1集$v1#第2集$v2'}
  ];
  const r1 = dedupMerge(batch, pool);
  const r2 = dedupMerge(batch, pool);
  assert('二次导入: 第一次 added=2', r1.added === 2);
  assert('二次导入: 第二次 dup=2', r2.dup === 2);
  assert('二次导入: 池内仍 2 条', pool.size === 2);
}

// 用例7：字段补全（空字段被填充，非空不被覆盖）
{
  const pool = new Map();
  dedupMerge([
    {name:'片', type_name:'电影', remarks:'1080P', play_url:'正片$u1'},
    {name:'片', type_name:'', remarks:'4K', play_url:'正片$u2'}
  ], pool);
  const it = pool.get(normalizeName('片'));
  assert('字段补全: type_name 保留原有', it.type_name === '电影');
  assert('字段补全: 非空 remarks 不被覆盖', it.remarks === '1080P');
}

// 用例9：空字段被填充
{
  const pool = new Map();
  dedupMerge([
    {name:'片', content:'', play_url:'正片$u1'},
    {name:'片', content:'简介来了', play_url:'正片$u2'}
  ], pool);
  const it = pool.get(normalizeName('片'));
  assert('空字段被填充: content', it.content === '简介来了');
}

// 用例8：vod_id 字段被剔除
{
  const pool = new Map();
  dedupMerge([{name:'片', vod_id:'99', play_url:'正片$u'}], pool);
  const it = pool.get(normalizeName('片'));
  assert('vod_id 剔除', it.vod_id === undefined);
}

console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
process.exit(fail ? 1 : 0);
