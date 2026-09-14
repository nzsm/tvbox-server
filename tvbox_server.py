#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 TVBox 影视点播接口 · 流畅优化版（纯标准库 · 单文件 · 零依赖）
================================================================================
 相比基础版新增能力：
   1. 多数据源聚合  ：并发请求多个源，单源超时/挂掉自动跳过，不影响整体返回
   2. 结果缓存      ：列表 / 详情 / 搜索带 TTL 缓存，重复请求秒回
   3. 多线路合并    ：同一影片来自多个源 -> vod_play_from 线路A$$$线路B，
                     播放时客户端一键切换线路，单源失效仍有备用
   4. 内置管理页    ：/admin 网页上查看状态、启停数据源、增删影视数据，无需改代码
   5. 请求统计      ：请求数 / 缓存命中 / 源状态一目了然

 运行：
   python tvbox_server.py
   默认监听 0.0.0.0:8080

 地址：
   TVBox 订阅     http://127.0.0.1:8080/config.json
   点播接口       http://127.0.0.1:8080/api.php/provide/vod/
   管理页         http://127.0.0.1:8080/admin

 注意：
   - 演示数据 / 播放地址均为占位，请替换为你有权分发的内容
   - 接入第三方站点前请确认版权与使用条款
================================================================================
"""

import json
import socket
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 一、服务配置（部署时修改这里）
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"
PORT = 8080

# BASE_URL 留空 = 按访问请求自动检测（局域网 / 公网通用，无需改动）；
# 若部署在固定地址（如 https://tv.example.com），填上会优先使用
BASE_URL = ""

SITE_KEY = "mysite"        # 站点唯一标识
SITE_NAME = "我的点播源"   # TVBox 中显示名称
PAGE_SIZE = 20             # 每页条数
CACHE_TTL = 60             # 缓存秒数
MAX_WORKERS = 4            # 并发聚合线程数
UPSTREAM_TIMEOUT = 8       # 上游源请求超时（秒）

# ---------------------------------------------------------------------------
# 数据源列表：type=demo 内置演示数据；type=cms 对接苹果CMS 站点
# enabled=false 的源不会被请求（可在 /admin 管理页里随时切换，无需改代码）
# ---------------------------------------------------------------------------
SOURCES = [
    {"id": "demo", "name": "内置演示数据", "type": "demo", "api": "", "enabled": True},
    # 示例：对接苹果CMS（填入你有权使用的站点，取消注释并改为 enabled=True 即启用）
    # {"id": "cms1", "name": "我的CMS源", "type": "cms",
    #  "api": "https://your-cms.example.com/api.php/provide/vod/", "enabled": False},
]


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
        self.vod_play_from = play_from      # 线路名，多线路用 $$$ 分隔
        self.vod_play_url = play_url        # 集名$地址#集名$地址
        self.vod_status = status

    def to_list_dict(self) -> dict:
        return {
            "vod_id": self.vod_id,
            "vod_name": self.vod_name,
            "vod_pic": self.vod_pic,
            "vod_remarks": self.vod_remarks,
            "type_name": self.type_name,
        }

    def to_detail_dict(self) -> dict:
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


def merge_vods(vods: List[Vod]) -> List[Vod]:
    """按片名合并多源结果：同一影片自动合并为多线路（线路A$$$线路B）"""
    merged: Dict[str, Vod] = {}
    for v in vods:
        if not v.vod_name:
            continue
        key = v.vod_name.strip()
        if key in merged:
            base = merged[key]
            if v.vod_play_from and v.vod_play_url:
                base.vod_play_from += "$$$" + v.vod_play_from
                base.vod_play_url += "$$$" + v.vod_play_url
            if not base.vod_pic and v.vod_pic:
                base.vod_pic = v.vod_pic
            if not base.vod_remarks and v.vod_remarks:
                base.vod_remarks = v.vod_remarks
            if not base.vod_content and v.vod_content:
                base.vod_content = v.vod_content
            if not base.vod_year and v.vod_year:
                base.vod_year = v.vod_year
            if not base.vod_actor and v.vod_actor:
                base.vod_actor = v.vod_actor
        else:
            merged[key] = v
    return list(merged.values())


# ---------------------------------------------------------------------------
# 三、数据源层
# ---------------------------------------------------------------------------
class DataSource:
    """数据源抽象基类"""

    def page(self, pg: int = 1, t: str = "") -> Tuple[int, List[Vod]]:
        raise NotImplementedError

    def detail(self, ids: str) -> List[Vod]:
        raise NotImplementedError

    def search(self, wd: str) -> List[Vod]:
        raise NotImplementedError


class DemoSource(DataSource):
    """内置演示数据源：跑通全流程用，数据可在 /admin 管理页里增删"""

    def __init__(self):
        self._lock = threading.Lock()
        self._next_id = 100
        self._vods: List[Vod] = [
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

    def page(self, pg: int = 1, t: str = "") -> Tuple[int, List[Vod]]:
        with self._lock:
            vods = list(self._vods)
        start = (pg - 1) * PAGE_SIZE
        return len(vods), vods[start:start + PAGE_SIZE]

    def detail(self, ids: str) -> List[Vod]:
        with self._lock:
            pool = {v.vod_id: v for v in self._vods}
        result = []
        for vid in ids.split(","):
            v = pool.get(vid.strip())
            if v:
                result.append(v)
        return result

    def search(self, wd: str) -> List[Vod]:
        with self._lock:
            return [v for v in self._vods if wd in v.vod_name]

    # -- 管理页使用 --
    def list_all(self) -> List[Vod]:
        with self._lock:
            return list(self._vods)

    def add(self, name, pic="", remarks="", year="", actor="", director="",
            content="", type_name="电影", play_url="", play_from="m3u8") -> Vod:
        with self._lock:
            self._next_id += 1
            v = Vod(
                vod_id=self._next_id, name=name, pic=pic, remarks=remarks,
                year=year, actor=actor, director=director, content=content,
                type_name=type_name, play_from=play_from, play_url=play_url,
            )
            self._vods.append(v)
            return v

    def add_batch(self, items: List[dict]) -> int:
        """批量导入：items 为 [{name, play_url, ...}, ...]，跳过缺必填项的条目"""
        count = 0
        with self._lock:
            for it in items:
                name = (it.get("name") or "").strip()
                play_url = (it.get("play_url") or "").strip()
                if not name or not play_url:
                    continue
                self._next_id += 1
                self._vods.append(Vod(
                    vod_id=self._next_id,
                    name=name,
                    pic=it.get("pic", "").strip(),
                    remarks=it.get("remarks", "").strip(),
                    year=it.get("year", "").strip(),
                    actor=it.get("actor", "").strip(),
                    director=it.get("director", "").strip(),
                    content=it.get("content", "").strip(),
                    type_name=(it.get("type_name") or "电影").strip() or "电影",
                    play_from=it.get("play_from", "m3u8").strip() or "m3u8",
                    play_url=play_url,
                ))
                count += 1
        return count

    def remove(self, vod_id: str) -> bool:
        with self._lock:
            before = len(self._vods)
            self._vods = [v for v in self._vods if v.vod_id != vod_id]
            return len(self._vods) < before


class CmsSource(DataSource):
    """对接苹果CMS 站点（聚合类）。单源超时/异常时返回空，不影响整体。
    注意：仅接入你有权使用的内容，接入前确认版权与条款。"""

    def __init__(self, cms_api: str, src_id: str = "cms", src_name: str = "CMS源",
                 timeout: int = UPSTREAM_TIMEOUT):
        self.api = cms_api.rstrip("/") + "/"
        self.src_id = src_id
        self.src_name = src_name
        self.timeout = timeout

    def _get(self, params: dict) -> Optional[dict]:
        url = self.api + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "TVBox/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def page(self, pg: int = 1, t: str = "") -> Tuple[int, List[Vod]]:
        data = self._get({"ac": "videolist", "pg": pg, "t": t})
        if not data:
            return 0, []
        return int(data.get("total", 0)), [self._from(d) for d in data.get("list", [])]

    def detail(self, ids: str) -> List[Vod]:
        data = self._get({"ac": "detail", "ids": ids})
        if not data:
            return []
        return [self._from(d) for d in data.get("list", [])]

    def search(self, wd: str) -> List[Vod]:
        data = self._get({"ac": "detail", "wd": wd})
        if not data:
            return []
        return [self._from(d) for d in data.get("list", [])]

    @staticmethod
    def _from(d: dict) -> Vod:
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


class MultiSource:
    """多源并发聚合：同时请求所有启用源，单源失败自动跳过，结果合并去重"""

    def __init__(self, sources: List[dict]):
        self._sources = sources
        self._demo = DemoSource()
        self._pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    # -- 管理页使用 --
    def get_demo(self) -> DemoSource:
        return self._demo

    def get_sources(self) -> List[dict]:
        return self._sources

    def set_enabled(self, src_id: str, enabled: bool) -> bool:
        for s in self._sources:
            if s["id"] == src_id:
                s["enabled"] = bool(enabled)
                return True
        return False

    # -- 内部 --
    def _make(self, cfg: dict) -> DataSource:
        if cfg.get("type") == "cms":
            return CmsSource(cfg["api"], cfg["id"], cfg.get("name", cfg["id"]))
        return self._demo

    def _collect(self, method: str, *args) -> List[Vod]:
        def run(cfg: dict):
            try:
                src = self._make(cfg)
                if method == "page":
                    _, vods = src.page(*args)
                elif method == "detail":
                    vods = src.detail(*args)
                else:
                    vods = src.search(*args)
                return vods or []
            except Exception as e:
                print(f"[源 {cfg.get('id')} {method} 失败] {type(e).__name__}: {e}")
                return []

        enabled = [c for c in self._sources if c.get("enabled")]
        futures = [self._pool.submit(run, c) for c in enabled]
        all_vods: List[Vod] = []
        for f in futures:
            try:
                all_vods.extend(f.result(timeout=UPSTREAM_TIMEOUT + 3))
            except Exception:
                pass  # 单个源超时不影响整体
        return all_vods

    def page(self, pg: int = 1, t: str = "") -> Tuple[int, List[Vod]]:
        merged = merge_vods(self._collect("page", pg, t))
        total = len(merged)
        start = (pg - 1) * PAGE_SIZE
        return total, merged[start:start + PAGE_SIZE]

    def detail(self, ids: str) -> List[Vod]:
        return merge_vods(self._collect("detail", ids))

    def search(self, wd: str) -> List[Vod]:
        return merge_vods(self._collect("search", wd))


# ---------------------------------------------------------------------------
# 四、TTL 缓存
# ---------------------------------------------------------------------------
class TtlCache:
    def __init__(self, ttl: int = CACHE_TTL):
        self._d: Dict[str, Tuple[dict, float]] = {}
        self._ttl = ttl
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            item = self._d.get(key)
            if item and time.time() - item[1] < self._ttl:
                self.hits += 1
                return item[0]
            self.misses += 1
            return None

    def set(self, key: str, value: dict):
        with self._lock:
            self._d[key] = (value, time.time())

    def clear(self):
        with self._lock:
            self._d.clear()

    def size(self) -> int:
        return len(self._d)


# ---------------------------------------------------------------------------
# 五、点播接口（苹果CMS 标准）
# ---------------------------------------------------------------------------
class VideoApi:
    def __init__(self, source: MultiSource, cache: TtlCache):
        self.source = source
        self.cache = cache

    def handle(self, params: Dict[str, str]) -> dict:
        ac = params.get("ac", "videolist")
        if ac not in ("videolist", "detail"):
            return {"code": 0, "msg": f"不支持的 ac 参数: {ac}"}
        key = ac + "|" + urllib.parse.urlencode(sorted(params.items()))
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        result = self._list(params) if ac == "videolist" else self._detail(params)
        if result.get("code") == 1:
            self.cache.set(key, result)
        return result

    def _list(self, params: Dict[str, str]) -> dict:
        try:
            pg = max(1, int(params.get("pg", "1") or "1"))
        except ValueError:
            pg = 1
        t = params.get("t", "")
        total, vods = self.source.page(pg, t)
        pagecount = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        return {
            "code": 1, "msg": "数据列表", "page": pg, "pagecount": pagecount,
            "limit": str(PAGE_SIZE), "total": str(total),
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
            "code": 1, "msg": msg, "page": 1, "pagecount": 1,
            "limit": "20", "total": str(len(vods)),
            "list": [v.to_detail_dict() for v in vods],
        }


# ---------------------------------------------------------------------------
# 六、TVBox 配置订阅
# ---------------------------------------------------------------------------
def make_tvbox_config(host: str = "") -> dict:
    base = (BASE_URL or "").rstrip("/")
    if not base and host:
        base = "http://" + host
    api = f"{base}/api.php/provide/vod/"
    return {
        "spider": "",
        "wallpaper": f"{base}/wallpaper.jpg",
        "sites": [
            {
                "key": SITE_KEY, "name": SITE_NAME, "type": 0, "api": api,
                "searchable": 1, "quickSearch": 1, "filterable": 1, "ext": "",
            }
        ],
        "parses": [],
        "flags": ["1080P", "4K", "线路1", "线路2"],
        "lives": [],
        "home": [{"key": "最新", "api": f"{api}?ac=videolist"}],
        "ijk": {"ijkplayer": {"player": {"enable-accurate-seek": True}}},
        "doh": {"urls": []},
        "header": {},
    }


# ---------------------------------------------------------------------------
# 七、管理页（HTML）
# ---------------------------------------------------------------------------
ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TVBox 点播管理台</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'PingFang SC','Microsoft YaHei',Arial,sans-serif;background:#F4F3EE;color:#1A1B1C;padding:20px;line-height:1.5}
  .wrap{max-width:960px;margin:0 auto}
  h1{font-size:20px;margin-bottom:4px}
  .sub{font-size:13px;color:#6B7280;margin-bottom:18px}
  .cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}
  .card{flex:1 1 140px;min-width:130px;background:#fff;border:1px solid #E4E3DD;border-radius:12px;padding:12px}
  .card .k{font-size:12px;color:#6B7280}
  .card .v{font-size:20px;font-weight:600;margin-top:2px}
  .panel{background:#fff;border:1px solid #E4E3DD;border-radius:12px;padding:14px;margin-bottom:18px}
  .panel h2{font-size:15px;margin-bottom:10px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #F0EFEA;word-break:break-all}
  th{color:#6B7280;font-weight:500;font-size:12px}
  .tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;background:#EAF3FB;color:#2F7FA8}
  .tag.off{background:#F5F5F3;color:#9AA5B1}
  .btn{display:inline-block;border:none;border-radius:8px;padding:6px 12px;font-size:12.5px;cursor:pointer;background:#EAF3FB;color:#2F7FA8}
  .btn.danger{background:#FDECEC;color:#EA6668}
  .btn.primary{background:#2F7FA8;color:#fff}
  input,select,textarea{width:100%;padding:7px 9px;border:1px solid #E4E3DD;border-radius:8px;font-size:13px;font-family:inherit}
  .grid{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}
  .grid>div{flex:1 1 200px;min-width:0}
  .tip{font-size:12px;color:#6B7280;margin-top:8px}
  .msg{font-size:13px;color:#52C41A;margin:8px 0 0}
</style>
</head>
<body>
<div class="wrap">
  <h1>TVBox 点播管理台</h1>
  <div class="sub">自建点播接口 · 流畅优化版 · 数据源与影视数据都在这里管理，改完立即生效</div>

  <div class="cards" id="cards"></div>

  <div class="panel">
    <h2>数据源</h2>
    <table>
      <thead><tr><th>ID</th><th>名称</th><th>类型</th><th>状态</th><th>操作</th></tr></thead>
      <tbody id="srcBody"></tbody>
    </table>
    <div class="tip">关闭某个源后，列表 / 搜索 / 详情都会跳过它；演示源数据不会被清空。</div>
  </div>

  <div class="panel">
    <h2>影视数据（内置演示源）</h2>
    <table>
      <thead><tr><th>ID</th><th>名称</th><th>分类</th><th>备注</th><th>操作</th></tr></thead>
      <tbody id="vodBody"></tbody>
    </table>
  </div>

  <div class="panel">
    <h2>添加影视</h2>
    <div class="grid">
      <div><label style="font-size:12px;color:#6B7280">片名 *</label><input id="f_name" placeholder="示例：我的影片"></div>
      <div><label style="font-size:12px;color:#6B7280">分类</label><input id="f_type" placeholder="电影 / 电视剧 / 综艺"></div>
      <div><label style="font-size:12px;color:#6B7280">备注</label><input id="f_remarks" placeholder="1080P 全24集"></div>
      <div><label style="font-size:12px;color:#6B7280">年份</label><input id="f_year" placeholder="2024"></div>
    </div>
    <div style="margin-bottom:10px"><label style="font-size:12px;color:#6B7280">海报 URL</label><input id="f_pic" placeholder="https://…/poster.jpg（可留空）"></div>
    <div style="margin-bottom:10px"><label style="font-size:12px;color:#6B7280">播放地址 *（第1集$https://…/01.mp4#第2集$https://…/02.mp4，多线路用 $$$ 分隔）</label>
      <textarea id="f_url" rows="2" placeholder="正片$https://…/movie.mp4"></textarea></div>
    <div style="margin-bottom:10px"><label style="font-size:12px;color:#6B7280">简介</label><textarea id="f_content" rows="2" placeholder="影片简介（可留空）"></textarea></div>
    <button class="btn primary" onclick="addVod()">添加影视</button>
    <div class="msg" id="msg"></div>
  </div>

  <div class="panel">
    <h2>批量导入影视</h2>
    <div style="margin-bottom:10px">
      <label style="font-size:12px;color:#6B7280">选择 JSON 文件（.json / .txt，UTF-8 编码）</label>
      <input type="file" id="imp_file" accept=".json,.txt,application/json" onchange="pickFile(this)" style="margin-top:4px">
    </div>
    <div style="margin-bottom:10px">
      <label style="font-size:12px;color:#6B7280">或直接粘贴 JSON 数组（必填字段：name、play_url）</label>
      <textarea id="imp_json" rows="6" placeholder='[&#10;  {"name":"影片A","type_name":"电影","remarks":"1080P","play_url":"正片$https://x.com/a.mp4"},&#10;  {"name":"剧集B","type_name":"电视剧","remarks":"全10集","play_url":"第1集$https://x.com/b1.mp4#第2集$https://x.com/b2.mp4"}&#10;]'></textarea>
    </div>
    <button class="btn primary" onclick="importVods()">批量导入</button>
    <div class="msg" id="impMsg"></div>
    <div class="tip">可选字段：pic（海报）、remarks（备注）、year（年份）、actor（演员）、director（导演）、content（简介）、type_name（分类）、play_from（线路名）。多集用 # 分隔，多线路用 $$$ 分隔。</div>
  </div>
</div>
<script>
(function(){
  var API = '/admin/api';
  function el(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function loadStats(){
    fetch(API+'/stats').then(function(r){return r.json();}).then(function(s){
      var cards = [
        {k:'数据源', v:s.sources},
        {k:'启用源', v:s.enabled},
        {k:'影视条目', v:s.vods},
        {k:'缓存命中', v:s.hits},
        {k:'请求总数', v:s.requests},
        {k:'运行时长', v:s.uptime}
      ];
      el('cards').innerHTML = cards.map(function(c){
        return '<div class="card"><div class="k">'+c.k+'</div><div class="v">'+esc(c.v)+'</div></div>';
      }).join('');
    }).catch(function(){});
  }
  function loadSources(){
    fetch(API+'/sources').then(function(r){return r.json();}).then(function(list){
      el('srcBody').innerHTML = list.map(function(s){
        var on = s.enabled;
        return '<tr><td>'+esc(s.id)+'</td><td>'+esc(s.name)+'</td><td><span class="tag">'+esc(s.type)+'</span></td>'+
          '<td>'+(on?'<span class="tag">启用</span>':'<span class="tag off">停用</span>')+'</td>'+
          '<td><button class="btn" onclick="toggleSource(\''+esc(s.id)+'\','+(on?'false':'true')+')">'+(on?'停用':'启用')+'</button></td></tr>';
      }).join('');
    }).catch(function(){});
  }
  function loadVods(){
    fetch(API+'/vods').then(function(r){return r.json();}).then(function(list){
      el('vodBody').innerHTML = list.map(function(v){
        return '<tr><td>'+esc(v.vod_id)+'</td><td>'+esc(v.vod_name)+'</td><td>'+esc(v.type_name)+'</td><td>'+esc(v.vod_remarks)+'</td>'+
          '<td><button class="btn danger" onclick="delVod(\''+esc(v.vod_id)+'\')">删除</button></td></tr>';
      }).join('');
    }).catch(function(){});
  }
  window.toggleSource = function(id, enabled){
    fetch(API+'/sources', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:id, enabled:enabled})})
      .then(function(){loadStats();loadSources();});
  };
  window.delVod = function(id){
    fetch(API+'/vods?id='+encodeURIComponent(id), {method:'DELETE'})
      .then(function(){loadStats();loadVods();});
  };
  window.addVod = function(){
    var body = {
      name: el('f_name').value.trim(),
      type_name: el('f_type').value.trim() || '电影',
      remarks: el('f_remarks').value.trim(),
      year: el('f_year').value.trim(),
      pic: el('f_pic').value.trim(),
      play_url: el('f_url').value.trim(),
      content: el('f_content').value.trim()
    };
    if(!body.name || !body.play_url){el('msg').textContent='片名和播放地址不能为空';return;}
    fetch(API+'/vods', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
      .then(function(r){return r.json();}).then(function(res){
        if(res.code===1){
          el('msg').textContent='添加成功，已生效';
          ['f_name','f_type','f_remarks','f_year','f_pic','f_url','f_content'].forEach(function(i){el(i).value='';});
          loadStats();loadVods();
        }else{el('msg').textContent=res.msg||'添加失败';}
      });
  };
  window.importVods = function(){
    var txt = el('imp_json').value.trim();
    if(!txt){el('impMsg').textContent='请先粘贴 JSON 数据或选择文件';return;}
    var arr;
    try{ arr = JSON.parse(txt); }catch(e){ el('impMsg').textContent='JSON 格式错误：'+e.message; return; }
    if(!Array.isArray(arr)){el('impMsg').textContent='需要 JSON 数组，例如 [{"name":"影片","play_url":"正片$https://…"}]';return;}
    fetch(API+'/vods/import', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(arr)})
      .then(function(r){return r.json();}).then(function(res){
        el('impMsg').textContent = res.msg;
        if(res.code===1){el('imp_json').value='';loadStats();loadVods();}
      }).catch(function(){el('impMsg').textContent='导入失败，请重试';});
  };
  window.pickFile = function(input){
    var f = input.files[0]; if(!f) return;
    var rd = new FileReader();
    rd.onload = function(){ el('imp_json').value = rd.result; el('impMsg').textContent='已读取文件，点击“批量导入”生效'; };
    rd.onerror = function(){ el('impMsg').textContent='读取文件失败'; };
    rd.readAsText(f, 'utf-8');
  };
  loadStats();loadSources();loadVods();
  setInterval(loadStats, 10000);
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 八、HTTP 服务
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    multi = MultiSource(list(SOURCES))
    cache = TtlCache(CACHE_TTL)
    api = VideoApi(multi, cache)
    started = time.time()
    requests_total = 0
    _stat_lock = threading.Lock()

    # -- 路由 --
    def do_GET(self):
        self._bump()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/config.json":
            self._json(make_tvbox_config(self.headers.get("Host", "")))
        elif path in ("/api.php/provide/vod", "/api.php/provide/vod/"):
            params = urllib.parse.parse_qs(parsed.query)
            params = {k: v[0] for k, v in params.items()}
            self._json(self.api.handle(params))
        elif path == "/admin":
            self._html(ADMIN_HTML)
        elif path.startswith("/admin/api/"):
            self._admin_get(path, parsed)
        elif path == "/":
            self._text(200, "TVBox 点播接口运行中\n配置订阅: /config.json\n管理页  : /admin")
        else:
            self._text(404, "Not Found")

    def do_POST(self):
        self._bump()
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/admin/api/sources":
            body = self._read_json()
            ok = self.multi.set_enabled(body.get("id", ""), bool(body.get("enabled", False)))
            self._json({"code": 1 if ok else 0, "msg": "已更新" if ok else "源不存在"})
        elif parsed.path == "/admin/api/vods/import":
            body = self._read_json()
            items = body if isinstance(body, list) else body.get("list", [])
            n = self.multi.get_demo().add_batch(items if isinstance(items, list) else [])
            self.cache.clear()
            self._json({"code": 1 if n > 0 else 0, "msg": f"已导入 {n} 条", "imported": n})
        elif parsed.path == "/admin/api/vods":
            body = self._read_json()
            v = self.multi.get_demo().add(
                name=body.get("name", "").strip(),
                pic=body.get("pic", "").strip(),
                remarks=body.get("remarks", "").strip(),
                year=body.get("year", "").strip(),
                content=body.get("content", "").strip(),
                type_name=body.get("type_name", "电影").strip() or "电影",
                play_url=body.get("play_url", "").strip(),
            )
            self.cache.clear()
            self._json({"code": 1, "msg": "已添加", "vod_id": v.vod_id})
        else:
            self._text(404, "Not Found")

    def do_DELETE(self):
        self._bump()
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/admin/api/vods":
            params = urllib.parse.parse_qs(parsed.query)
            vid = params.get("id", [""])[0]
            ok = self.multi.get_demo().remove(vid)
            self.cache.clear()
            self._json({"code": 1 if ok else 0, "msg": "已删除" if ok else "条目不存在"})
        else:
            self._text(404, "Not Found")

    # -- 管理 API --
    def _admin_get(self, path: str, parsed):
        if path == "/admin/api/stats":
            self._json({
                "sources": len(self.multi.get_sources()),
                "enabled": sum(1 for s in self.multi.get_sources() if s.get("enabled")),
                "vods": len(self.multi.get_demo().list_all()),
                "hits": self.cache.hits,
                "misses": self.cache.misses,
                "cache_size": self.cache.size(),
                "requests": self.requests_total,
                "uptime": self._fmt_uptime(int(time.time() - self.started)),
            })
        elif path == "/admin/api/sources":
            self._json([{"id": s["id"], "name": s["name"], "type": s["type"], "enabled": s.get("enabled", False)}
                        for s in self.multi.get_sources()])
        elif path == "/admin/api/vods":
            self._json([{"vod_id": v.vod_id, "vod_name": v.vod_name, "type_name": v.type_name,
                         "vod_remarks": v.vod_remarks} for v in self.multi.get_demo().list_all()])
        else:
            self._text(404, "Not Found")

    # -- 工具 --
    def _bump(self):
        with self._stat_lock:
            type(self).requests_total += 1

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _fmt_uptime(sec: int) -> str:
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h}时{m}分{s}秒"

    def _json(self, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    lan = get_lan_ip()
    print("=" * 62)
    print(" TVBox 点播接口 · 流畅优化版 已启动")
    print("=" * 62)
    print(f" 配置订阅地址 : http://{lan}:{PORT}/config.json")
    print(f" 局域网地址   : http://{lan}:{PORT}/config.json")
    print(f" 点播接口     : http://{lan}:{PORT}/api.php/provide/vod/")
    print(f" 管理页       : http://{lan}:{PORT}/admin")
    print(f" 数据源数量   : {len(SOURCES)} 个（启用 {sum(1 for s in SOURCES if s.get('enabled'))} 个）")
    print("-" * 62)
    print(" TVBox 使用：设置 -> 配置地址 -> 粘贴【配置订阅地址】")
    print(" 管理页使用：浏览器打开【管理页】地址，可启停源、增删影视")
    print(" 按 Ctrl+C 停止服务")
    print("=" * 62)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
