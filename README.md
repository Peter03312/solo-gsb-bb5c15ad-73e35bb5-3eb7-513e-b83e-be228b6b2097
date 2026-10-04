# 数据集派生许可判定服务

为研究团队提供的**数据集派生许可判定服务**，技术栈：Python 3.11 · FastAPI · SQLite。

- 登记数据集**版本**时，可固定引用零个或多个**已存在的父版本**；父版本引用一经创建即不可修改。
- 为版本登记按 **UTC 时刻**生效的**许可变更**（每次变更给出当时刻起生效的完整“允许用途集合”）。
- 查询某版本在某 UTC 时刻可否用于某用途时，取**自身及全部祖先**各自“不晚于该时刻的最近一次变更”，
  沿这些版本取**用途交集**；任一版本没有有效许可、或不允许该用途即拒绝，
  返回判定结果、所依据的全部变更，以及**所有阻断版本**及原因。

## 1. 快速开始（Docker Compose，推荐）

前置要求：已安装 Docker 与 Docker Compose（`docker compose version` 可用）。

```bash
docker compose up --build -d          # 构建并后台启动；启动时自动建表并写入演示数据
curl http://localhost:8080/health     # {"status":"ok"}
docker compose logs -f license-service
docker compose down                   # 停止（数据保留在命名卷 license-data 中）
```

首次启动自动完成：

1. 安装 Python 依赖（构建镜像阶段）；
2. 初始化 SQLite 表结构（`/data/licensing.db`，WAL 模式）；
3. 写入一套幂等的演示数据（可通过 `SEED_DEMO_DATA=false` 关闭）。

### 访问地址与端口

| 用途 | 地址 |
| --- | --- |
| 服务 Base URL | http://localhost:8080 |
| 健康检查 | http://localhost:8080/health |
| Swagger UI（可在线调用） | http://localhost:8080/docs |
| OpenAPI 描述 | http://localhost:8080/openapi.json |

## 2. 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_PATH` | `/data/licensing.db` | SQLite 数据文件路径，Compose 下持久化到命名卷 `license-data` |
| `HOST` | `0.0.0.0` | 服务监听地址 |
| `PORT` | `8080` | 服务监听端口（容器内；修改时需同步调整 compose 的端口映射） |
| `SEED_DEMO_DATA` | `true` | 启动时是否写入演示数据，幂等：库非空则跳过；生产环境建议 `false` |

## 3. HTTP API

请求/响应均为 JSON；时间统一使用**带时区的 ISO-8601**（如 `2026-03-01T00:00:00+00:00`），
服务内部归一化为 UTC。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/versions` | 登记版本并固定父版本引用 |
| GET | `/versions` | 列出全部版本（含父引用与全部祖先） |
| GET | `/versions/{versionId}` | 查询单个版本 |
| POST | `/license-changes` | 登记一条许可变更 |
| GET | `/license-changes?versionId=` | 列出许可变更（可按版本过滤） |
| GET | `/check?versionId=&use=&at=` | **许可判定** |

状态码约定：`201` 新建；`200` 幂等重放（响应体 `replayed=true`）；
`404` 版本不存在；`409` 冲突；`422` 请求参数不合法。错误体形如：

```json
{"error": {"code": "CONFLICT", "message": "...", "details": {}}}
```

### 3.1 登记版本

```bash
curl -X POST http://localhost:8080/versions \
  -H 'Content-Type: application/json' \
  -d '{"versionId": "ds-v2", "parentIds": ["ds-v1"]}'
```

- `parentIds` 只能引用**已存在**的版本（可空数组，表示根版本）；重复值自动去重。
- 相同 `versionId` + 相同父引用集合重复提交：返回 `200` 与既有记录（`replayed=true`）。
- 相同 `versionId` 但父引用不同：返回 `409`（父引用创建后不可修改）。

### 3.2 登记许可变更

```bash
curl -X POST http://localhost:8080/license-changes \
  -H 'Content-Type: application/json' \
  -d '{
    "changeId": "LC-V2-INIT",
    "versionId": "ds-v2",
    "effectiveAt": "2026-01-01T00:00:00+00:00",
    "allowedUses": ["research", "education"]
  }'
```

- 相同 `changeId` 且**内容完全相同**（版本/生效时刻/用途集合一致，用途顺序不敏感）：
  返回 `200` 与**同一结果**（`replayed=true`，含相同 `contentHash`）。
- 相同 `changeId` 但**内容不同**：返回 `409`。
- 同版本同一生效时刻提交**不同变更**（不同 `changeId` 或不同用途集合）：返回 `409`。
- 变更只登记、不修改、不删除，保证判定结果可追溯。

### 3.3 许可判定（核心接口）

```bash
curl -G http://localhost:8080/check \
  --data-urlencode "versionId=ds-v3" \
  --data-urlencode "use=research" \
  --data-urlencode "at=2026-03-01T00:00:00+00:00"
```

响应（允许）：

```json
{
  "versionId": "ds-v3",
  "use": "research",
  "at": "2026-03-01T00:00:00.000000+00:00",
  "allowed": true,
  "basisChanges": [
    {"versionId": "ds-v3", "changeId": "LC-V3-INIT", "effectiveAt": "...", "allowedUses": ["research"]},
    {"versionId": "ds-v2", "changeId": "LC-V2-INIT", "effectiveAt": "...", "allowedUses": ["research", "education"]},
    {"versionId": "ds-v1", "changeId": "LC-V1-INIT", "effectiveAt": "...", "allowedUses": ["research", "commercial"]}
  ],
  "blockingVersions": [],
  "consideredVersions": ["ds-v3", "ds-v2", "ds-v1"],
  "intersectionAllowedUses": ["research"]
}
```

拒绝时 `allowed=false`，`blockingVersions` 列出**全部**阻断版本及原因：

- `NO_EFFECTIVE_LICENSE`：该版本在 `at` 之前没有任何已生效变更（许可缺口）；
- `USE_NOT_ALLOWED`：该版本生效变更的用途集合不含所查用途（响应中同时给出其依据变更）。

## 4. 演示数据与端到端示例

`SEED_DEMO_DATA=true` 时自动创建：

```
ds-v1 ──► ds-v2 ──► ds-v3
```

许可时间线（UTC）：

| 版本 | 2026-01-01 起 | 2026-06-01 起 |
| --- | --- | --- |
| ds-v1 | research, commercial | （不变） |
| ds-v2 | research, education | research（收回 education） |
| ds-v3 | research | （不变） |

可直接验证：

```bash
# research：三版本交集包含 research → 允许
curl -G http://localhost:8080/check --data-urlencode versionId=ds-v3 \
  --data-urlencode use=research --data-urlencode at=2026-03-01T00:00:00+00:00

# commercial：v2、v3 均阻断
curl -G http://localhost:8080/check --data-urlencode versionId=ds-v3 \
  --data-urlencode use=commercial --data-urlencode at=2026-03-01T00:00:00+00:00

# 2025 年：所有版本都还没有有效许可 → 全部 NO_EFFECTIVE_LICENSE
curl -G http://localhost:8080/check --data-urlencode versionId=ds-v3 \
  --data-urlencode use=research --data-urlencode at=2025-06-01T00:00:00+00:00

# 2026-07：v2 已收紧，education 沿链被拒
curl -G http://localhost:8080/check --data-urlencode versionId=ds-v3 \
  --data-urlencode use=education --data-urlencode at=2026-07-01T00:00:00+00:00
```

## 5. Python 调用示例

```python
import requests

BASE = "http://localhost:8080"

requests.post(f"{BASE}/versions", json={"versionId": "ds-v1", "parentIds": []}).raise_for_status()
requests.post(f"{BASE}/versions", json={"versionId": "ds-v2", "parentIds": ["ds-v1"]}).raise_for_status()

requests.post(f"{BASE}/license-changes", json={
    "changeId": "LC1", "versionId": "ds-v1",
    "effectiveAt": "2026-01-01T00:00:00+00:00",
    "allowedUses": ["research", "commercial"],
}).raise_for_status()

resp = requests.get(f"{BASE}/check", params={
    "versionId": "ds-v2", "use": "research",
    "at": "2026-03-01T00:00:00+00:00",
})
print(resp.json()["allowed"])            # True/False
print(resp.json()["blockingVersions"])   # 全部阻断版本
```

## 6. 本地开发与测试

不使用容器时：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_PATH=./data/licensing.db SEED_DEMO_DATA=true PORT=8080
python -m app.init_db     # 仅建表（可选，启动时也会自动执行）
python -m app.seed        # 写入演示数据（可选）
uvicorn app.main:app --host 0.0.0.0 --port 8080

# 测试（临时数据库，互不污染）
pip install pytest httpx
python -m pytest tests/ -q
```

## 7. 项目结构

```
.
├── app/
│   ├── main.py          # FastAPI 路由、错误处理、启动初始化
│   ├── services.py      # 核心规则：版本登记、变更幂等/冲突、祖先闭包、交集判定
│   ├── models.py        # 请求/响应 Pydantic 模型
│   ├── database.py      # SQLite 连接、WAL、表结构
│   ├── time_util.py     # UTC 时刻归一化
│   ├── seed.py          # 演示数据（幂等）
│   └── init_db.py       # 建表命令行入口
├── tests/test_api.py    # 18 项集成测试
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh        # 建表 + 演示数据 + 启动
└── requirements.txt
```

## 8. 关键设计说明

- **祖先闭包**：多父引用形成 DAG，按引用层次 BFS 展开并去重（菱形依赖中的共同祖先只计一次）。
  登记时父版本必须已存在，因此不可能出现环。
- **时刻语义**：每次许可变更是某时刻起生效的**完整快照**（非增量）；判定取每版本
  `effective_at <= at` 中最近的一条（同时刻有同一变更保证，存储层唯一索引兜底）。
- **存储一致性**：`license_changes(version_id, effective_at)` 唯一索引；
  `version_parents` 只提供写入接口，父引用不可修改；变更内容以 SHA-256 哈希去重。
- **并发**：SQLite 开启 WAL 与 `busy_timeout`，所有写操作在唯一约束上做竞争兜底。
