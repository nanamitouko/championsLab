# 冠军终端

本项目通过 Flask 提供网页与 API，并使用 SQLite 保存静态规则、中文翻译和赛季 Battle Data。

## Docker 本地运行

```powershell
docker compose up -d --build
```

打开 <http://localhost:4173>。首次创建空数据库时，服务会自动抓取当前赛季；也可以在网页点击“更新赛季数据”。

```powershell
docker compose ps
docker compose logs -f
docker compose down
```

SQLite 文件位于容器的 `/data/champions.sqlite3`，由 `champions_data` 命名卷持久化。普通 `docker compose down` 不会删除数据；只有显式执行 `docker compose down -v` 才会删除数据卷。

## API

- `GET /api/health`：服务与数据库状态。
- `GET /api/bootstrap`：前端展示和计算所需的全部数据。
- `POST /api/refresh`：从 Battle Data 和 PokéAPI 抓取并事务更新 SQLite。

## 测试

```powershell
docker compose exec -T champions python -m unittest -v test_app.py
node --check dist/app.js
```
