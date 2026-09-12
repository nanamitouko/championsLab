# 冠军终端

本项目通过 Flask 提供网页与 API，并使用 SQLite 保存静态规则、中文翻译和赛季 Battle Data。

## Docker 本地运行

```powershell
docker compose up -d --build
```

打开 <http://localhost:4173>。端口仅绑定到 `127.0.0.1`，适合本机访问或由 Nginx 反向代理。首次创建空数据库时，服务会自动抓取当前赛季；也可以在网页点击“更新赛季数据”。

```powershell
docker compose ps
docker compose logs -f
docker compose down
```

SQLite 文件位于容器的 `/data/champions.sqlite3`，宝可梦图标缓存位于 `/data/sprites`，二者均由 `champions_data` 命名卷持久化。普通 `docker compose down` 不会删除数据；只有显式执行 `docker compose down -v` 才会删除数据卷。

## API

- `GET /api/health`：服务、数据库与图标预热状态。
- `GET /api/bootstrap`：轻量公共规则与当前赛季元数据。
- `GET /api/usage?format=Doubles`：指定对战形式的 TOP 50 榜单。
- `GET /api/calculator`：伤害计算器所需的形态、道具和招式数据。
- `GET /api/sprites/<cache-key>`：服务器本地缓存的宝可梦图标。
- `POST /api/refresh`：从 Battle Data 和 PokéAPI 抓取并事务更新 SQLite，仅返回新赛季元数据。

JSON、JavaScript 和 CSS 会按客户端支持情况压缩；公共数据接口支持 ETag 和条件请求。页面只加载当前功能所需的数据，其余数据在网络允许时空闲预取。

## 测试

```powershell
docker compose exec -T champions python -m unittest -v test_app.py
node --check dist/app.js
```
