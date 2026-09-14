#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
 TVBox 影视点播接口 · 自建完整框架
 纯 Python 标准库实现，零第三方依赖，单文件即可运行
============================================================================
 功能：
   1. /config.json            -> TVBox 配置订阅地址（填入 TVBox 设置即可）
   2. /api.php/provide/vod/   -> 苹果CMS 标准点播接口（列表 / 详情 / 搜索）
   3. 数据源可插拔：
        - DemoSource  内置演示数据，跑通全流程（替换为你自己的数据即可）
        - CmsSource   对接任意苹果CMS 站点（需自行确认版权合规）
        - 也可按 DataSource 接口自定义新源（数据库 / 爬虫 / 其他 CMS）

 运行：
   python tvbox_server.py

 使用（TVBox 客户端）：
   设置 -> 配置地址 -> 粘贴下列地址之一
   本地测试 : http://127.0.0.1:8080/config.json
   局域网   : http://<电脑局域网IP>:8080/config.json
   公网     : https://你的域名/config.json

 播放地址格式（vod_play_url）：
   单集     : 正片$https://xxx/video.mp4
   多集     : 第1集$https://xxx/01.mp4#第2集$https://xxx/02.mp4
   多线路   : 线路A内容$$$线路B内容  （与 vod_play_from 线路名一一对应）
============================================================================
"""

import json
import socket
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# 一、服务配置（部署时修改这里）
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"          # 监听所有网卡
PORT = 8080                # 服务端口

# 部署后改为公网地址，例如 https://tv.example.com；
# 局域网使用请改为电脑的局域网 IP，例如 http://192.168.1.100:8080
BASE_URL = f"http://127.0.0.1:{PORT}"

SITE_KEY = "mysite"        # 站点唯一标识（TVBox 内部使用，勿与其它源重复）
SITE_NAME = "我的点播源"   # 站点在 TVBox 中的显示名称
PAGE_SIZE = 20             # 每页条数

# 数据源选择：demo = 内置演示数据；cms = 对接苹果CMS（需填 CMS_API）
DATA_SOURCE = "demo"
CMS_API = "https://your-cms.example.com/api.php/provide/vod/"


# ---------------------------------------------------------------------------
# 二、数据模型
# ---------------------------------------------------------------------------
class Vod:
    """单条影视数据，字段对齐苹果CMS / TVBox 约定"""

    def __init__(
        self,
        vod_id, name, pic="", remarks="", year="", area="",
        actor="", director="", content="", type_name="电影",
        play_from="m3u8", play_url="", status=1,
    ):
        self.vod_id = str(vod_id)
        self.vod_name = name
        self.vod_pic = pic
        self.vod_remarks = remarks
        self.vod_year = year
        self.vod_area = area
        self.vod_actor = actor
        self.vod_director = director
        self.vod_content = content
        self.type_name = type_name
        self.vod_play_from = play_from      # 播放线路名，多线路用 $$$ 分隔
        self.vod_play_url = play_url        # 集名$地址#集名$地址
        self.vod_status = status

    def to_list_dict(self) -> dict:
        """列表接口字段（列表页展示用）"""
        return {
            "vod_id": self.vod_id,
            "vod_name": self.vod_name,
            "vod_pic": self.vod_pic,
            "vod_remarks": self.vod_remarks,
            "type_name": self.type_name,
        }

    def to_detail_dict(self) -> dict:
        """详情接口字段（进入播放页时使用）"""
        return {
            "vod_id": self.vod_id,
            "vod_name": self.vod_name,
            "vod_sub": "",
            "vod_status": self.vod_status,
            "vod_pic": self.vod_pic,
            "vod_actor": self.vod_actor,
            "vod_director": self.vod_director,
            "vod_content": self.vod_content,
            "vod_remarks": self.vod_remarks,
            "vod_year": self.vod_year,
            "vod_area": self.vod_area,
            "type_name": self.type_name,
            "vod_play_from": self.vod_play_from,
            "vod_play_url": self.vod_play_url,
        }


# ---------------------------------------------------------------------------
# 三、数据源抽象与实现
# ---------------------------------------------------------------------------
class DataSource:
    """数据源抽象基类：实现 page / detail / search 三个方法即可接入新源"""

    def page(self, pg: int = 1, t: str = "") -> Tuple[int, List[Vod]]:
        """分页列表，返回 (总数, 本页数据)"""
        raise NotImplementedError

    def detail(self, ids: str) -> List[Vod]:
        """按 ID 查详情，ids 支持逗号分隔多个"""
        raise NotImplementedError

    def search(self, wd: str) -> List[Vod]:
        """按关键词搜索"""
        raise NotImplementedError


class DemoSource(DataSource):
    """内置演示数据源：用于跑通完整流程，请把内容替换为你自己的数据"""

    def __init__(self):
        self._vods = [
            Vod(
                vod_id=1,
                name="示例电影·星海远征",
                pic="https://picsum.photos/seed/tvbox1/300/420",
                remarks="1080P 国语中字",
                year="2024",
                area="中国大陆",
                actor="演示演员A / 演示演员B",
                director="演示导演C",
                content="这是一部用于演示 TVBox 点播接口完整流程的示例影片。"
                        "在实际使用中，请将本数据替换为你有权提供的内容。",
                type_name="电影",
                play_from="m3u8",
                play_url=("正片$https://media.example.com/demo/xinghai.mp4"
                          "#预告$https://media.example.com/demo/xinghai_trailer.mp4"),
            ),
            Vod(
                vod_id=2,
                name="示例剧集·山间日记",
                pic="https://picsum.photos/seed/tvbox2/300/420",
                remarks="全24集",
                year="2023",
                area="中国大陆",
                actor="演示演员D",
                director="演示导演E",
                content="演示用的多集剧集数据，用于展示 # 分隔多集、$$$ 分隔多线路的格式。",
                type_name="电视剧",
                play_from="m3u8",
                play_url=(
                    "第1集$https://media.example.com/demo/shanjian/01.mp4"
                    "#第2集$https://media.example.com/demo/shanjian/02.mp4"
                    "#第3集$https://media.example.com/demo/shanjian/03.mp4"
                    "$$$"
                    "第1集$https://media.example.com/demo/shanjian/l1/01.mp4"
                    "#第2集$https://media.example.com/demo/shanjian/l1/02.mp4"
                ),
            ),
        ]
        self._ids = {v.vod_id: v for v in self._vods}

    def page(self, pg: int = 1, t: str = "") -> Tuple[int, List[Vod]]:
        start = (pg - 1) * PAGE_SIZE
        return len(self._vods), self._vods[start:start + PAGE_SIZE]

    def detail(self, ids: str) -> List[Vod]:
        result = []
        for vid in ids.split(","):
            v = self._ids.get(vid.strip())
            if v:
                result.append(v)
        return result

    def search(self, wd: str) -> List[Vod]:
        return [v for v in self._vods if wd in v.vod_name]


class CmsSource(DataSource):
    """
    对接任意苹果CMS 站点（聚合类站点）。
    注意：仅用于接入你有权使用的内容，接入前请确认版权与使用条款。
    """

    def __init__(self, cms_api: str):
        self.api = cms_api.rstrip("/") + "/"

    def _get(self, params: dict) -> dict:
        url = self.api + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "TVBox/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def page(self, pg: int = 1, t: str = "") -> Tuple[int, List[Vod]]:
        data = self._get({"ac": "videolist", "pg": pg, "t": t})
        total = int(data.get("total", 0))
        vods = [self._from_dict(d) for d in data.get("list", [])]
        return total, vods

    def detail(self, ids: str) -> List[Vod]:
        data = self._get({"ac": "detail", "ids": ids})
        return [self._from_dict(d) for d in data.get("list", [])]

    def search(self, wd: str) -> List[Vod]:
        data = self._get({"ac": "detail", "wd": wd})
        return [self._from_dict(d) for d in data.get("list", [])]

    @staticmethod
    def _from_dict(d: dict) -> Vod:
        return Vod(
            vod_id=d.get("vod_id", ""),
            name=d.get("vod_name", ""),
            pic=d.get("vod_pic", ""),
            remarks=d.get("vod_remarks", ""),
            year=d.get("vod_year", ""),
            area=d.get("vod_area", ""),
            actor=d.get("vod_actor", ""),
            director=d.get("vod_director", ""),
            content=d.get("vod_content", ""),
            type_name=d.get("type_name", ""),
            play_from=d.get("vod_play_from", "m3u8"),
            play_url=d.get("vod_play_url", ""),
        )


def get_source() -> DataSource:
    """按配置选择数据源（可在此扩展你的自定义源）"""
    if DATA_SOURCE == "cms":
        return CmsSource(CMS_API)
    return DemoSource()


# ---------------------------------------------------------------------------
# 四、点播接口（苹果CMS 标准，TVBox 直接兼容）
# ---------------------------------------------------------------------------
class VideoApi:
    """处理 /api.php/provide/vod/ 的所有请求"""

    def __init__(self, source: DataSource):
        self.source = source

    def handle(self, params: Dict[str, str]) -> dict:
        ac = params.get("ac", "videolist")
        if ac == "videolist":
            return self._list(params)
        if ac == "detail":
            return self._detail(params)
        return {"code": 0, "msg": f"不支持的 ac 参数: {ac}"}

    def _list(self, params: Dict[str, str]) -> dict:
        try:
            pg = max(1, int(params.get("pg", "1") or "1"))
        except ValueError:
            pg = 1
        t = params.get("t", "")
        total, vods = self.source.page(pg, t)
        pagecount = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        return {
            "code": 1,
            "msg": "数据列表",
            "page": pg,
            "pagecount": pagecount,
            "limit": str(PAGE_SIZE),
            "total": str(total),
            "list": [v.to_list_dict() for v in vods],
        }

    def _detail(self, params: Dict[str, str]) -> dict:
        ids = (params.get("ids") or "").strip()
        wd = (params.get("wd") or "").strip()
        if wd:
            vods, msg = self.source.search(wd), "搜索结果"
        elif ids:
            vods, msg = self.source.detail(ids), "视频详情"
        else:
            return {"code": 0, "msg": "缺少 ids 或 wd 参数"}
        return {
            "code": 1,
            "msg": msg,
            "page": 1,
            "pagecount": 1,
            "limit": "20",
            "total": str(len(vods)),
            "list": [v.to_detail_dict() for v in vods],
        }


# ---------------------------------------------------------------------------
# 五、TVBox 配置订阅（/config.json）
# ---------------------------------------------------------------------------
def make_tvbox_config() -> dict:
    """生成 TVBox 可识别的完整配置结构"""
    api = f"{BASE_URL}/api.php/provide/vod/"
    return {
        # spider 字段：留空使用内置解析；高级玩法可指向自建 spider.jar
        "spider": "",
        # 壁纸：TVBox 首页背景图（可换成你自己的图片地址）
        "wallpaper": f"{BASE_URL}/wallpaper.jpg",
        # 站点列表：type=0 普通API源；type=1 爬虫源(spider)
        "sites": [
            {
                "key": SITE_KEY,
                "name": SITE_NAME,
                "type": 0,
                "api": api,
                "searchable": 1,    # 支持搜索
                "quickSearch": 1,   # 支持快捷搜索
                "filterable": 1,    # 支持筛选
                "ext": "",
            }
        ],
        # 解析接口列表（用于 m3u8 等需要解析的场景，可选）
        "parses": [],
        # 播放线路显示名
        "flags": ["1080P", "4K", "线路1", "线路2"],
        # 电视直播分组（可选）：填入后 TVBox 首页出现“直播”入口
        "lives": [
            {
                "group": "央视",
                "channels": [
                    {"name": "CCTV-1", "urls": ["https://media.example.com/live/cctv1.m3u8"]},
                ],
            }
        ],
        # 首页推荐分类（可选）：key=分类名，api=该分类列表地址
        "home": [
            {"key": "最新", "api": f"{api}?ac=videolist"},
        ],
        # 播放器参数（可选）
        "ijk": {
            "ijkplayer": {
                "player": {"enable-accurate-seek": True},
            }
        },
        # DNS-over-HTTPS（可选，用于部分网络环境）
        "doh": {"urls": []},
        # 全局请求头（可选）
        "header": {},
    }


# ---------------------------------------------------------------------------
# 六、HTTP 服务
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    api = VideoApi(get_source())

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/config.json":
            self._json(make_tvbox_config())
        elif path in ("/api.php/provide/vod", "/api.php/provide/vod/"):
            params = urllib.parse.parse_qs(parsed.query)
            params = {k: v[0] for k, v in params.items()}
            self._json(self.api.handle(params))
        elif path == "/":
            self._text(200, "TVBox 点播接口服务运行中\n配置订阅地址: /config.json")
        else:
            self._text(404, "Not Found")

    def _json(self, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, text: str):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def get_lan_ip() -> str:
    """获取本机局域网 IP（用于打印内网使用地址）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    server = HTTPServer((HOST, PORT), Handler)
    lan = get_lan_ip()
    print("=" * 62)
    print(" TVBox 影视点播接口服务已启动")
    print("=" * 62)
    print(f" 配置订阅地址 : {BASE_URL}/config.json")
    print(f" 局域网地址   : http://{lan}:{PORT}/config.json")
    print(f" 点播接口     : {BASE_URL}/api.php/provide/vod/")
    print(f" 数据源模式   : {DATA_SOURCE}")
    print("-" * 62)
    print(" 请在 TVBox / 影视TV 等客户端中：")
    print("   设置 -> 配置地址 -> 粘贴上面的【配置订阅地址】")
    print("   （电视/手机访问局域网时，请使用【局域网地址】并修改")
    print("     tvbox_server.py 里的 BASE_URL 为本机局域网 IP）")
    print(" 按 Ctrl+C 停止服务")
    print("=" * 62)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
