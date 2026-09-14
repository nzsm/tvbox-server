# TVBox 影视点播接口 · 流畅优化版

纯 Python 标准库实现的自建 TVBox 点播接口，零第三方依赖，单文件即可运行。
升级版新增：**多源并发聚合 / 结果缓存 / 多线路合并 / 内置管理页 / 单源容错**。

| 地址 | 用途 |
|---|---|
| `http://你的地址:8080/config.json` | **配置订阅链接**，填入 TVBox「设置 → 配置地址」 |
| `http://你的地址:8080/api.php/provide/vod/` | 点播接口（苹果CMS 标准），TVBox 自动请求 |
| `http://你的地址:8080/admin` | **管理页**：启停数据源、增删影视数据 |

---

## 一、快速开始

```bash
# 1. 启动服务（需 Python 3.8+）
python tvbox_server.py

# 2. 浏览器验证
#    打开 http://127.0.0.1:8080/admin 看到管理台即成功
```

TVBox 客户端：`设置 → 配置地址` 粘贴 `http://127.0.0.1:8080/config.json`。

## 二、局域网 / 公网使用

1. **局域网**：查电脑局域网 IP（如 `192.168.1.100`），把 `tvbox_server.py` 顶部 `BASE_URL` 改为 `http://192.168.1.100:8080`，重启后 TVBox 填入该地址；防火墙放行 8080 端口。
2. **公网**：放到有公网 IP 的服务器上，或 nginx 反代：

```nginx
server {
    listen 80;
    server_name tv.example.com;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

3. **Docker**：

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY tvbox_server.py /app/
EXPOSE 8080
CMD ["python", "tvbox_server.py"]
```

```bash
docker build -t tvbox-server .
docker run -d -p 8080:8080 --name tvbox tvbox-server
```

## 三、流畅优化版的新能力

### 1. 多数据源聚合
顶部 `SOURCES` 列表可配置多个源（`type=demo` 内置演示 / `type=cms` 对接苹果CMS）。
请求时**并发**查询所有启用源，**单源超时或挂掉自动跳过**，不影响整体返回。

```python
SOURCES = [
    {"id": "demo", "name": "内置演示数据", "type": "demo", "api": "", "enabled": True},
    {"id": "cms1", "name": "我的CMS源", "type": "cms",
     "api": "https://your-cms.example.com/api.php/provide/vod/", "enabled": False},
]
```

### 2. 结果缓存
列表 / 详情 / 搜索带 TTL 缓存（默认 60 秒，`CACHE_TTL` 可调）。
重复请求直接命中缓存秒回，TVBox 反复拉列表不再打满你的源。

### 3. 多线路自动合并
同一部影片如果多个源都有，自动合并为多线路：
`vod_play_from = 线路A$$$线路B`、`vod_play_url = A的内容$$$B的内容`，
播放时 TVBox 客户端一键切线路，单源失效仍有备用。

### 4. 内置管理页（/admin）
浏览器打开管理页，无需改代码即可：
- 查看运行状态（数据源数、影视条目、缓存命中、请求数、运行时长）
- **启停数据源**（关闭的源不再被请求）
- **增删影视数据**（内置演示源，改完立即生效）
- **批量导入**：粘贴 JSON 数组或选择本地 JSON 文件，一次导入几百条

批量导入 JSON 格式（必填 `name`、`play_url`；可选 `pic/remarks/year/actor/director/content/type_name/play_from`）：

```json
[
  {"name": "影片A", "type_name": "电影", "remarks": "1080P", "play_url": "正片$https://x.com/a.mp4"},
  {"name": "剧集B", "type_name": "电视剧", "remarks": "全10集",
   "play_url": "第1集$https://x.com/b1.mp4#第2集$https://x.com/b2.mp4"}
]
```

> 注意：管理页增删的影视数据保存在内存中，服务重启后恢复初始数据。
> 如需持久化，可自行把 `DemoSource` 的数据落盘（如存 JSON 文件）。

### 5. 并发与容错参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `CACHE_TTL` | 60 | 缓存秒数 |
| `MAX_WORKERS` | 4 | 并发聚合线程数 |
| `UPSTREAM_TIMEOUT` | 8 | 单个上游源请求超时（秒） |

## 四、对接自己的数据源

### 1. 替换内置演示数据
在 `tvbox_server.py` 中改 `DemoSource.__init__` 的 `self._vods`，或在管理页里直接添加。

### 2. 对接苹果CMS 站点
```python
SOURCES = [
    {"id": "demo", "name": "内置演示数据", "type": "demo", "api": "", "enabled": True},
    {"id": "cms1", "name": "我的CMS源", "type": "cms",
     "api": "https://你的站点.com/api.php/provide/vod/", "enabled": True},
]
```

### 3. 自定义数据源
继承 `DataSource` 并实现 `page / detail / search` 三个方法，把实例加入 `MultiSource` 即可。

## 五、播放地址格式（关键约定）

| 场景 | 格式 | 示例 |
|---|---|---|
| 单集 | `名称$地址` | `正片$https://x.com/movie.mp4` |
| 多集 | `名称$地址#名称$地址` | `第1集$https://x.com/01.mp4#第2集$https://x.com/02.mp4` |
| 多线路 | 线路1内容`$$$`线路2内容 | 多集串 + `$$$` + 另一组多集串 |
| 线路名 | `vod_play_from` | `m3u8`、`线路A$$$线路B` |

## 六、接口自测

```bash
# 配置
curl "http://127.0.0.1:8080/config.json"
# 列表（连续请求两次可看到缓存命中）
curl "http://127.0.0.1:8080/api.php/provide/vod/?ac=videolist"
# 详情
curl "http://127.0.0.1:8080/api.php/provide/vod/?ac=detail&ids=1"
# 搜索
curl "http://127.0.0.1:8080/api.php/provide/vod/?ac=detail&wd=星海"
# 管理 API
curl "http://127.0.0.1:8080/admin/api/stats"
```

## 七、版权与合规提醒

- 本框架是纯技术实现，不包含任何侵权内容；演示数据为占位符；
- 接入第三方站点 / 爬虫前，请确认你有权使用其内容与接口；
- 不要接入来源不明、不可审计的远程脚本源（未知 jar/spider 可能夹带恶意逻辑）。
