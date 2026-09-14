/**
 * ============================================================================
 * TVBox Spider 框架示例（高级扩展，可选）
 * ============================================================================
 * 适用场景：
 *   - 内置接口(苹果CMS)不满足需求，想直接抓取某个网页/站点数据时使用
 *   - 与 tvbox_server.py 配合：编译为 spider.jar 后托管，
 *     在 config.json 的 "spider" 字段填入地址，并把 sites[].type 改为 1
 *
 * 五个标准入口（TVBox 加载 spider 后会依次调用）：
 *   home()      -> 返回首页推荐（{class: [...], list: [...]}）
 *   category()  -> 分类列表分页（支持 filter 筛选）
 *   detail()    -> 视频详情（含播放列表 vod_play_url）
 *   search()    -> 关键词搜索
 *   play()      -> 播放地址处理（可做防盗链/解析）
 * ============================================================================
 */

const CONFIG = {
    // 目标站点根地址（替换为你有权抓取的站点）
    host: "https://example.com",
    timeout: 8000,
    ua: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
};

const UA = { "User-Agent": CONFIG.ua };

// 通用请求封装
function http(url, headers = {}) {
    try {
        let resp = req(url, { headers: Object.assign({}, UA, headers), timeout: CONFIG.timeout });
        return resp.content || "";
    } catch (e) {
        log("请求失败: " + url + " -> " + e);
        return "";
    }
}

/** 首页推荐 */
async function home(filter) {
    let html = http(CONFIG.host + "/");
    // 演示：只做空骨架，实际按目标站 HTML 结构解析
    let classArr = [{ "type_id": 1, "type_name": "电影" }, { "type_id": 2, "type_name": "剧集" }];
    let videos = [];
    return { class: classArr, list: videos };
}

/** 首页/分类推荐列表（列表页数据，TVBox 首页展示用） */
async function homeVod() {
    return category({ type: 1, page: 1 });
}

/** 分类列表 */
async function category(filter) {
    let type = filter.type || 1;
    let page = filter.page || 1;
    let html = http(`${CONFIG.host}/list/${type}-${page}.html`);
    // 解析列表 -> 组装 vod 数组（字段见 Vod 说明）
    let videos = [];
    return videos;
}

/** 视频详情 */
async function detail({ ids, wd }) {
    if (wd) {
        return search(wd); // 详情接口同时承载搜索
    }
    let html = http(`${CONFIG.host}/vod/${ids}.html`);
    // 解析出 vod_name / vod_pic / vod_actor / vod_content / vod_play_from / vod_play_url
    let vod = {
        vod_id: String(ids),
        vod_name: "",
        vod_pic: "",
        vod_actor: "",
        vod_director: "",
        vod_content: "",
        vod_play_from: "m3u8",       // 多线路: "线路A$$$线路B"
        vod_play_url: "",            // "第1集$url#第2集$url"
    };
    return [vod];
}

/** 搜索 */
async function search(wd) {
    let html = http(`${CONFIG.host}/search/` + encodeURIComponent(wd) + ".html");
    let videos = [];
    return videos;
}

/** 播放地址处理 */
async function play(flag, id, flags) {
    return { parse: 0, url: id }; // parse=0 直连播放；可改 1 走解析
}
