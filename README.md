# TVBox 影视点播接口 · 自建完整框架

纯 Python 标准库实现的自建 TVBox 点播接口，零第三方依赖，单文件即可运行。
部署后你会得到两个地址：

| 地址 | 用途 |
|---|---|
| `http://你的地址:8080/config.json` | **配置订阅链接**，填入 TVBox「设置 → 配置地址」 |
| `http://你的地址:8080/api.php/provide/vod/` | 点播接口（苹果CMS 标准），TVBox 自动请求 |

---

## 一、快速开始（本地测试）

```bash
# 1. 启动服务（需 Python 3.8+）
python tvbox_server.py

# 2. 浏览器验证
#    打开 http://127.0.0.1:8080/config.json 能看到配置 JSON 即成功
```

TVBox 客户端中：`设置 → 配置地址` 粘贴 `http://127.0.0.1:8080/config.json`
（模拟器/电脑上用 127.0.0.1 即可；电视、手机等其它设备必须用局域网地址，见下）。

## 二、局域网 / 公网使用

**局域网（电视和电脑连同一个路由器）：**

1. 查看电脑局域网 IP，例如 `192.168.1.100`；
2. 修改 `tvbox_server.py` 顶部：`BASE_URL = "http://192.168.1.100:8080"`；
3. 重启服务，TVBox 填入 `http://192.168.1.100:8080/config.json`；
4. 若电视访问不到，检查 Windows 防火墙是否放行 8080 端口。

**公网（手机流量也能看）：**

- 方案 A：把 `tvbox_server.py` 放到一台有公网 IP 的服务器上运行；
- 方案 B：用 nginx 反向代理：

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

- 方案 C：Docker 部署：

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

公网使用记得配 HTTPS（证书可免费申请），并修改 `BASE_URL` 为 `https://tv.example.com`。

## 三、对接自己的数据源

### 1. 替换内置演示数据（最简单）

打开 `tvbox_server.py`，修改 `DemoSource.__init__` 里的 `self._vods` 列表，
按 `Vod(...)` 字段填入你自己的影视数据即可。

### 2. 对接苹果CMS 站点

```python
# 修改顶部配置：
DATA_SOURCE = "cms"
CMS_API = "https://你的站点.com/api.php/provide/vod/"
```

### 3. 自定义数据源（数据库 / 爬虫 / 其它接口）

继承 `DataSource` 并实现三个方法，再在 `get_source()` 里返回它：

```python
class MySource(DataSource):
    def page(self, pg=1, t=""):        # 返回 (总数, [Vod, ...])
        ...
    def detail(self, ids):             # 返回 [Vod, ...]
        ...
    def search(self, wd):              # 返回 [Vod, ...]
        ...

def get_source():
    return MySource()                  # 替换原有 return
```

## 四、播放地址格式（关键约定）

| 场景 | 格式 | 示例 |
|---|---|---|
| 单集 | `名称$地址` | `正片$https://x.com/movie.mp4` |
| 多集 | `名称$地址#名称$地址` | `第1集$https://x.com/01.mp4#第2集$https://x.com/02.mp4` |
| 多线路 | 线路1内容`$$$`线路2内容 | 上面的多集串 + `$$$` + 另一组多集串 |
| 线路名 | `vod_play_from` | `m3u8`、`线路A$$$线路B`（与 play_url 的线路一一对应） |

播放地址支持 `mp4 / m3u8 / flv / rtmp / 磁力 / 直链` 等，TVBox 播放器按协议自动处理。

## 五、可选扩展：TVBox Spider（JS 爬虫源）

`spider_example.js` 是一个 TVBox Spider 框架示例（高级玩法）。
把它和 `spider.jar` 一起托管到任意静态服务器，然后在 `config.json` 的
`spider` 字段填入地址、`sites[].type` 改为 `1` 即可启用。

## 六、版权与合规提醒

- 本框架是纯技术实现，本身不包含任何侵权内容；
- 接入第三方站点 / 爬虫前，请确认你有权使用其内容与接口；
- 演示数据中的播放地址为占位符，请替换为你自己有权限分发的内容。

## 七、接口自测（可选）

```bash
# 配置
curl "http://127.0.0.1:8080/config.json"
# 列表
curl "http://127.0.0.1:8080/api.php/provide/vod/?ac=videolist"
# 详情
curl "http://127.0.0.1:8080/api.php/provide/vod/?ac=detail&ids=1"
# 搜索
curl "http://127.0.0.1:8080/api.php/provide/vod/?ac=detail&wd=星海"
```
